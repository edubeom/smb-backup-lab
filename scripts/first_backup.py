#!/usr/bin/env python3
"""First online backup and selected-file restore, without stopping any service."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import sys
import tarfile
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from g1_backup import BackupError,KST,guard,private_file,read_config,retention_plan
from daily import daily
from object_backup import restore_objects
from check_mailbox_restore import verify as verify_mailboxes


def preflight(c,write_probe=False):
    if c.get('archive_layout','objects-v2')!='objects-v2':
        raise BackupError('first backup requires the project-separated objects-v2 layout')
    guard(c);private_file(c['password_file'])
    result={'host':socket.gethostname(),'mount_verified':True,'encryption_key_present':True,
            'archive_layout':'objects-v2','max_repository_gib':c['max_repository_bytes']/1024**3,
            'shared_free_gib':shutil.disk_usage(c['repository']).free/1024**3,
            'local_free_gib':shutil.disk_usage(c['work']).free/1024**3,
            'system_settings':['/etc (SSH, Cloudflare, network, systemd, firewall)',
                               '/home/* and /root operator SSH/Cloudflare settings'],
            'projects':'one encrypted archive per /project/<directory>, plus dedicated database exports',
            'services_interrupted':False,
            'mail_collection':'online per-mailbox Dovecot export, SQLite backup, Redis RDB; SMTP queue must be empty at checks',
            'consistency':'per-component points, no atomic common instant',
            'backup_deletion_enabled':False,'timer_enabled_by_this_tool':False}
    if write_probe:
        probe=Path(c['repository'])/('.write-probe-'+uuid.uuid4().hex)
        data=os.urandom(32);created=False
        try:
            with probe.open('xb') as f:
                created=True;f.write(data);f.flush();os.fsync(f.fileno())
            guard(c)
            if probe.read_bytes()!=data:raise BackupError('shared write/read probe mismatch')
            result['shared_write_read_probe']='passed'
        finally:
            if created:probe.unlink(missing_ok=True)
    return result


def copy_test_member(archive,member,target):
    with tarfile.open(archive,'r:gz') as t:
        entry=t.getmember(member)
        if not entry.isfile():raise BackupError('restore test expected a regular file')
        with t.extractfile(entry) as src,Path(target).open('xb') as dst:
            shutil.copyfileobj(src,dst)
    Path(target).chmod(0o600)


def check_restored_mail(path):
    path=Path(path)/'projects/demo-mail'
    for relative in ('admin/main.db','webmail/roundcube.db'):
        source=path/'databases'/relative
        db=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
        try:
            if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:
                raise BackupError('restored mail SQLite integrity check failed')
        finally:db.close()
    try:return verify_mailboxes(path/'online-mailboxes.tar.gz')
    except RuntimeError:raise BackupError('isolated mailbox restore readback failed; private output suppressed')


def run(c):
    if os.geteuid()!=0 or not sys.stdin.isatty():
        raise BackupError('서버 개인 터미널에서 sudo로 실행해야 합니다.')
    print(json.dumps(preflight(c,write_probe=True),ensure_ascii=False,indent=2),flush=True)
    print('메일·웹·DB를 계속 실행한 채 시스템 설정과 프로젝트별 파일·DB·우편함을 암호화 백업합니다.',flush=True)
    print('메일은 사용자별 복사이며 전체 공통 시점은 아닙니다. SMTP 대기열이 관측되면 중단 없이 백업 실패로 남깁니다.',flush=True)
    print('복구 키는 G1 외부에도 보관하세요. 기존 백업 삭제와 timer 활성화는 하지 않습니다.',flush=True)
    result=daily(c,maintenance=False)  # This review tool always promises no deletion.
    record=result.get('backup',result)
    rid=record['run_id']
    snapshot=Path(c['repository'])/(rid+'.success.json')
    restored=Path(c['work'])/('first-restore-'+rid+'-'+uuid.uuid4().hex[:8])
    restored.mkdir(mode=0o700)
    restore_objects(snapshot,c['password_file'],restored/'project',project='g1-backup')
    restore_objects(snapshot,c['password_file'],restored/'system',component='system/etc.tar.gz')
    copy_test_member(restored/'project/projects/g1-backup/files.tar.gz','g1-backup/README.md',restored/'project-readme.md')
    for name in ('ssh/sshd_config','cloudflared/config.yml'):
        copy_test_member(restored/'system/system/etc.tar.gz','etc/'+name,restored/('restored-'+name.replace('/','-')))
    restore_objects(snapshot,c['password_file'],restored/'mail',project='demo-mail')
    mail_result=check_restored_mail(restored/'mail')
    return {'status':'backup-selected-files-and-mailbox-readback-verified','run_id':rid,
            'restore_path':str(restored),'verified_files':['project README','SSH config','Cloudflare config'],
            'mail_sqlite_integrity':'passed','mailbox_readback':mail_result,
            'service_restore_validated':False,'services_interrupted':False,
            'next':'PostgreSQL/Mailu isolated service restore test; this tool did not enable timer or perform retention deletion.'}


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='/etc/g1-backup/backup.json')
    p.add_argument('--run',action='store_true',help='online backup, selected-file and mailbox restore readback; no service interruption')
    a=p.parse_args()
    try:
        c=read_config(a.config)
        result=run(c) if a.run else preflight(c)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (Exception,KeyboardInterrupt) as error:
        print(json.dumps({'status':'failed','reason':str(error) if isinstance(error,BackupError) else type(error).__name__,
                          'action':'Check Mailu health and private staging. No existing backup was deleted.'},ensure_ascii=False))
        return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
