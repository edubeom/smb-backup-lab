#!/usr/bin/env python3
"""Measure one shared snapshot, then optionally restore PostgreSQL in isolation."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from g1_backup import BackupError,KST,RUN_ID,atomic_json,guard,locked,private_file,read_config
from object_backup import object_path,restore_objects,tree_bytes,validate_record
from postgres_restore import IsolatedPostgres,call


def snapshot_record(c,rid):
    if not RUN_ID.fullmatch(rid):raise BackupError('invalid snapshot ID')
    guard(c)
    path=Path(c['repository'])/(rid+'.success.json')
    if path.is_symlink():raise BackupError('snapshot record is redirected')
    record=json.loads(path.read_text())
    if record.get('run_id')!=rid or record.get('layout')!='objects-v2' or record.get('status')!='verified':
        raise BackupError('snapshot is not a verified objects-v2 success')
    validate_record(c['repository'],record)
    if record['bytes']!=sum(v['bytes'] for v in record['objects'].values()):
        raise BackupError('snapshot byte accounting mismatch')
    return path,record


def capacity(c,record):
    size=record['bytes'];limit=c['max_repository_bytes'];used=tree_bytes(c['repository'])
    return {'snapshot_bytes':size,'snapshot_gib':round(size/1024**3,3),
        'repository_used_bytes':used,'repository_limit_gib':limit/1024**3,
        'five_similar_sets_gib':round(5*size/1024**3,3),
        'six_similar_sets_gib':round(6*size/1024**3,3),
        'five_sets_fit_budget_at_current_size':5*size<=limit,
        'six_sets_fit_budget_at_current_size':6*size<=limit,
        'estimate_note':'Current snapshot size only; future growth, partials and metadata need extra space. Six sets allow verifying a new set before any approved removal.',
        'shared_free_gib':round(shutil.disk_usage(c['repository']).free/1024**3,3),
        'local_free_gib':round(shutil.disk_usage(c['work']).free/1024**3,3),
        'backup_deletion_performed':False}


def source_states():
    ids=call(['docker','ps','-q'],'list running source containers').decode().split()
    if not ids:return {}
    fmt='{"id":{{json .Id}},"running":{{json .State.Running}},"started":{{json .State.StartedAt}},"restarts":{{json .RestartCount}},"labels":{{json .Config.Labels}}}'
    raw=call(['docker','inspect','--format',fmt,*ids],'read source container states').decode()
    result={}
    for line in raw.splitlines():
        row=json.loads(line)
        if (row['labels'] or {}).get('g1-backup.restore-token'):continue
        result[row['id']]=(row['running'],row['started'],row['restarts'])
    return result


def materialize(snapshot,c,out,record):
    base=out/'postgresql';base.mkdir(mode=0o700)
    selected=[]
    for encrypted in sorted(record['objects']):
        if not encrypted.startswith('databases/postgresql/'):continue
        if not re.fullmatch(r'databases/postgresql/(?:catalog\.json|globals\.sql|db-[0-9]{3}\.dump)\.gpg',encrypted):
            raise BackupError('unexpected PostgreSQL object in success record')
        component=encrypted[:-4]
        dest=out/f'component-{len(selected):03d}'
        restore_objects(snapshot,c['password_file'],dest,component=component)
        # Move only our just-restored file, never any repository object.
        target=base/Path(component).name
        if target.exists():raise BackupError('duplicate PostgreSQL component')
        (dest/component).rename(target);selected.append(component)
    if not (base/'catalog.json').is_file() or not (base/'globals.sql').is_file():
        raise BackupError('snapshot is missing PostgreSQL catalog/globals')
    restore_objects(snapshot,c['password_file'],out/'metadata',component='docker/metadata.json')
    metadata=json.loads((out/'metadata/docker/metadata.json').read_text())
    matches=[row['image_id'] for row in metadata if row.get('name')=='/demo-postgres']
    if len(matches)!=1:raise BackupError('backup PostgreSQL image identity is ambiguous')
    return base,matches[0]


def verify(c,rid,run=False):
    if os.geteuid()!=0:raise BackupError('run in a personal G1 terminal using sudo; secrets must not be sent to chat')
    snapshot,record=snapshot_record(c,rid)
    result={'run_id':rid,'capacity':capacity(c,record),'production_services_interrupted':False,
            'timer_enabled_by_this_tool':False,'full_mailu_service_restore_validated':False}
    if not run:return dict(result,status='read-only-capacity-report',postgres_restore_executed=False)
    private_file(c['password_file'])
    largest=max(item['bytes'] for item in record['objects'].values())
    if shutil.disk_usage(c['work']).free<c['reserve_bytes']+max(8*1024**3,largest*3):
        raise BackupError('insufficient private workspace for isolated restore')
    with locked(Path(c['work'])/'service-verify'):
        out=Path(c['work'])/('verify-pg-'+rid+'-'+uuid.uuid4().hex[:8]);out.mkdir(mode=0o700)
        before=source_states()
        print('[복구 시험 1/3] 공유기 암호화 파일 해시·선택 복호화',file=sys.stderr,flush=True)
        validate_record(c['repository'],record,verify_hashes=True)
        directory,image=materialize(snapshot,c,out,record)
        print('[복구 시험 2/3] 새 격리 PostgreSQL에 역할·모든 DB 복원과 테이블 읽기',file=sys.stderr,flush=True)
        with IsolatedPostgres(image,diagnostics=out) as pg:pg_result=pg.restore(directory)
        print('[복구 시험 3/3] 시험 컨테이너 회수·운영 상태 비교',file=sys.stderr,flush=True)
        after=source_states()
        unchanged=all(after.get(cid)==state for cid,state in before.items())
        if not unchanged:raise BackupError('source container state changed during verification; inspect before claiming success')
        result.update(status='shared-snapshot-postgresql-restore-verified',postgresql=pg_result,
            cipher_hashes_verified=True,source_container_states_unchanged=True,
            tested_at=datetime.now(KST).isoformat(),private_result_path=str(out))
        atomic_json(out/'result.json',result)
        # A private pointer helps the next operational step locate this evidence.
        atomic_json(Path(c['work'])/'last-postgres-verification.json',result)
    return result


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='/etc/g1-backup/backup.json')
    p.add_argument('--snapshot-id',required=True)
    p.add_argument('--run',action='store_true',help='restore only into a new isolated test container')
    a=p.parse_args()
    try:
        if os.geteuid()!=0:raise BackupError('G1 개인 터미널에서 sudo로 실행해야 합니다. 비밀번호/키는 채팅에 보내지 마세요.')
        result=verify(read_config(a.config),a.snapshot_id,a.run)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (Exception,KeyboardInterrupt) as error:
        print(json.dumps({'status':'failed','reason':str(error) if isinstance(error,BackupError) else type(error).__name__,
                          'existing_backups_deleted':False,'timer_enabled':False},ensure_ascii=False))
        return 1
if __name__=='__main__':raise SystemExit(main())
