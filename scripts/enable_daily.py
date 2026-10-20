#!/usr/bin/env python3
"""Review first, then install G1 mount/timer after explicit maintenance consent."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from g1_backup import BackupError, KST, atomic_json, guard, private_file, read_config, retention_plan
from object_backup import tree_bytes
from operations import CONSENT, approval, checked_set, new_policy, policy_path

MOUNT_UNIT=r'mnt-router\x2dbackup.mount'
UNIT_DIR=Path('/etc/systemd/system')
DEPLOYMENT_ROOT=Path('/project/smb-backup-lab')


def unit_texts(c):
    expected={'mount':'/mnt/router-backup','mount_source':'//192.0.2.1/backup',
              'repository':'/mnt/router-backup/server/g1','work':'/var/lib/g1-backup/work',
              'prepared':'/var/lib/g1-backup/prepared','password_file':'/etc/g1-backup/archive-passphrase',
              'smb_version':'2.0','archive_layout':'objects-v2'}
    # Older setup configs omit this field. Match the reader/transfer's existing
    # default without editing the protected config or weakening explicit values.
    effective=dict(c,archive_layout=c.get('archive_layout','objects-v2'))
    mismatches=[k for k,v in expected.items() if effective.get(k)!=v]
    if ROOT != DEPLOYMENT_ROOT:mismatches.append('project_root')
    if mismatches:
        raise BackupError('G1 설치 설정 불일치 항목: '+', '.join(mismatches)+
                          '. 기존 설정을 변경하지 않았습니다. 값이나 비밀 대신 항목 이름만 보고합니다.')
    mount='''[Unit]
Description=G1 router backup share (managed by g1-backup)
Wants=network-online.target
After=network-online.target

[Mount]
What=//192.0.2.1/backup
Where=/mnt/router-backup
Type=cifs
Options=credentials=/etc/g1-backup/smb-credentials,nosuid,nodev,noexec,file_mode=0600,dir_mode=0700,vers=2.0,_netdev,nofail
TimeoutSec=45

[Install]
WantedBy=remote-fs.target
'''
    return {MOUNT_UNIT:mount, **{n:(ROOT/'systemd'/n).read_text()
                               for n in ('g1-backup.service','g1-backup.timer')}}


def command(args):
    r=subprocess.run(args,capture_output=True,text=True)
    if r.returncode:
        raise BackupError(f'{Path(args[0]).name} 실패 (종료 {r.returncode}); 보호된 서버 journal을 확인하세요.')
    return r.stdout.strip()


def inspect(c):
    guard(c);private_file(c['password_file']);private_file('/etc/g1-backup/smb-credentials')
    pg=json.loads(private_file(Path(c['work'])/'last-postgres-verification.json').read_text())
    key=json.loads(private_file(Path(c['work'])/'offsite-key-verification.json').read_text())
    if (pg.get('status')!='shared-snapshot-postgresql-restore-verified' or
        pg.get('cipher_hashes_verified') is not True or
        pg.get('source_container_states_unchanged') is not True or
        key.get('status')!='offsite-key-readback-and-shared-manifest-verified' or
        key.get('shared_encrypted_manifest_decrypted_with_readback_key') is not True or
        key.get('offsite_storage_attested_by_operator') is not True or
        key.get('run_id')!=pg.get('run_id')):
        raise BackupError('같은 성공본의 실제 PostgreSQL 복구·외부 키 검증 기록이 필요합니다.')
    plan=retention_plan(c['repository'])
    _,_,record=checked_set(c,pg['run_id'])
    used=tree_bytes(c['repository'])
    import shutil
    shared_free=shutil.disk_usage(c['repository']).free
    local_free=shutil.disk_usage(c['work']).free
    if used+record['bytes']>c['max_repository_bytes'] or shared_free<c['reserve_bytes']+record['bytes']:
        raise BackupError('기존 성공본을 먼저 지우지 않고 새 백업을 만들 공간이 부족합니다.')
    if local_free<c['reserve_bytes']+c.get('prepare_required_free_bytes',20*1024**3):
        raise BackupError('새 수집본을 만들 로컬 여유 공간이 부족합니다. 기존 수집본은 삭제하지 않았습니다.')
    approval(c)  # Reject a mismatching previous consent receipt.
    return {'status':'operations-plan-checked','verified_run_id':pg['run_id'],
            'archive_layout':c.get('archive_layout','objects-v2'),
            'archive_layout_uses_existing_default':'archive_layout' not in c,
            'schedule':'04:00 Asia/Seoul; missed runs catch up once',
            'repository':c['repository'],'limit_gib':c['max_repository_bytes']/1024**3,
            'shared_free_gib':round(shared_free/1024**3,3),'local_free_gib':round(local_free/1024**3,3),
            'keep':plan['keep'],'delete_candidates_now':plan['delete_candidates'],
            'retention_rule':'only after NEW verified backup; newest 5 successful Korean dates',
            'staging_rule':'only a NEW prepared directory created by that successful daily run',
            'preserved':'other programs, /project/backup, demo-web-backups, previous staging/restores, partials/unknown files',
            'installation_deletes_backups':False,'services_interrupted':False,
            'full_mailu_service_restore_validated':False}


def check_unit_conflicts(texts):
    for name,text in texts.items():
        target=UNIT_DIR/name
        if target.is_symlink() or (target.exists() and target.read_text()!=text):
            raise BackupError('기존 systemd 파일이 계획과 다릅니다. 덮어쓰지 않았습니다.')
        r=subprocess.run(['systemctl','show',name,'-p','FragmentPath','-p','DropInPaths'],capture_output=True,text=True)
        properties=dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
        if properties.get('DropInPaths') or properties.get('FragmentPath','') not in ('',str(target)):
            raise BackupError('기존 systemd 단위 또는 추가 설정과 충돌합니다. 변경하지 않았습니다.')
    for line in Path('/etc/fstab').read_text().splitlines():
        fields=line.split('#',1)[0].split()
        if len(fields)>1 and fields[1]=='/mnt/router-backup':
            raise BackupError('기존 fstab 마운트 설정이 있습니다. 중복 설치하지 않았습니다.')


def apply(c,progress=None):
    progress=progress if progress is not None else {}
    progress.update(phase='configuration-validation',installation_started=False)
    texts=unit_texts(c)
    progress['phase']='repository-validation'
    plan=inspect(c)
    progress['phase']='unit-validation'
    check_unit_conflicts(texts)
    with tempfile.TemporaryDirectory(prefix='g1-unit-check-') as td:
        files=[]
        for name,text in texts.items():
            p=Path(td)/name;p.write_text(text);files.append(str(p))
        command(['systemd-analyze','verify',*files])
    print(json.dumps(plan,ensure_ascii=False,indent=2),flush=True)
    print('설치 자체는 백업을 삭제하지 않습니다. 이후 새 성공본 검증 뒤에만 위 범위의 자동 정리를 허용합니다.',flush=True)
    print('기존 평문 수집/복원본은 남깁니다. 실패한 실행은 기존 성공본을 정리하지 않습니다.',flush=True)
    print('위 계획대로 매일 백업과 성공 5일/이번 새 수집본 정리를 승인하려면 y를 입력하세요. Enter는 취소합니다.',flush=True)
    progress['phase']='awaiting-consent'
    reply=input('승인할까요? [y/N]: ').strip()
    # Keep the canonical stored approval unchanged: existing operator receipts
    # must remain valid when simplifying only the interactive response.
    if reply.casefold()!='y' and reply!=CONSENT:
        raise BackupError('승인하지 않아 설치·승인 저장·timer 시작을 하지 않았습니다.')
    # Recheck after a potentially long operator review.
    progress['phase']='pre-install-recheck'
    guard(c);check_unit_conflicts(texts)
    progress.update(phase='installation',installation_started=True)
    if approval(c) is None:
        atomic_json(policy_path(c),new_policy(c))
        policy_path(c).chmod(0o600)
    for name,text in texts.items():
        target=UNIT_DIR/name
        if not target.exists():
            with target.open('x') as f:f.write(text)
            target.chmod(0o644)
    command(['systemctl','daemon-reload'])
    # Enable the mount for future boots; DO NOT unmount/remount the active share.
    command(['systemctl','enable',MOUNT_UNIT,'g1-backup.timer'])
    command(['systemctl','start','g1-backup.timer'])
    result={'status':'daily-backup-enabled','at':datetime.now(KST).isoformat(),
            'timer_enabled':command(['systemctl','is-enabled','g1-backup.timer']),
            'timer_active':command(['systemctl','is-active','g1-backup.timer']),
            'next_run':command(['systemctl','show','g1-backup.timer','-p','NextElapseUSecRealtime','--value']),
            'schedule':'04:00 Asia/Seoul','mount_enabled_for_boot':True,
            'mount_reboot_tested':False,'first_scheduled_run_verified':False,
            'future_retention_approved':True,'backups_deleted_by_installer':False,
            'services_interrupted':False,'note':'Persistent timer may catch up; daily skips an already successful Korean date.'}
    atomic_json(Path(c['work'])/'operations-installation.json',result)
    return result


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='/etc/g1-backup/backup.json')
    p.add_argument('--inspect',action='store_true',help='root read-only inspection, no installation')
    p.add_argument('--apply',action='store_true',help='root private terminal; review the plan and explicitly approve with y')
    a=p.parse_args()
    progress={'phase':'arguments','installation_started':False}
    try:
        if not (a.inspect or a.apply):
            print(json.dumps({'status':'instructions-only','schedule':'04:00 Asia/Seoul',
                  'next':'sudo python3 /project/smb-backup-lab/scripts/enable_daily.py --apply',
                  'reads_secrets':False,'installs_or_deletes':False},ensure_ascii=False,indent=2));return 0
        if os.geteuid()!=0 or (a.apply and not sys.stdin.isatty()):
            raise BackupError('G1 개인 터미널에서 sudo로 실행하세요. 비밀번호는 채팅에 보내지 마세요.')
        progress['phase']='read-config'
        private_file(a.config)
        c=read_config(a.config)
        if a.apply:result=apply(c,progress)
        else:
            progress['phase']='read-only-inspection'
            result=inspect(c)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (Exception,KeyboardInterrupt) as error:
        print(json.dumps({'status':'failed','reason':str(error) if isinstance(error,BackupError) else type(error).__name__,
              **progress,
              'note':('설치 중 오류로 일부 설정이 적용됐을 수 있습니다. 상태를 확인하세요.'
                      if progress['installation_started'] else '설치 시작 전 중단했습니다. 승인·단위 파일 저장·timer 시작을 하지 않았습니다.')+
                     ' 설치 도구의 백업 삭제는 없습니다.'},ensure_ascii=False))
        return 1

if __name__=='__main__':raise SystemExit(main())
