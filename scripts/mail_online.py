#!/usr/bin/env python3
"""Online Mailu export. No service control and no live Maildir/SQLite file copy."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import uuid
from zoneinfo import ZoneInfo
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from g1_backup import BackupError,atomic_json

# Executed as the existing mail UID inside its application container. Configuration
# applies only to these standalone doveadm processes, never to the running daemon.
WORKER=r'''
import json,os,pathlib,re,shutil,subprocess,tempfile,datetime

def export(users):
    root=pathlib.Path(tempfile.mkdtemp(prefix='g1-mail-online-',dir='/tmp'))
    os.chmod(root,0o700)
    try:
        exports=root/'exports';exports.mkdir(mode=0o700)
        mapping={};sieve=[]
        for i,user in enumerate(users):
            if not re.fullmatch(r'[A-Za-z0-9_.+@-]+',user):
                raise RuntimeError('unsupported mailbox identifier')
            home=pathlib.Path('/mail')/user
            if home.is_symlink() or not home.is_dir() or home.resolve()!=home:
                raise RuntimeError('mailbox source missing or redirected')
            dest=exports/'maildirs'/f'{i:04d}'
            dest.parent.mkdir(mode=0o700,exist_ok=True)
            # Disable quota/FTS and sieve replication in the standalone reader.
            # Sieve files are archived separately. Production daemon config is untouched.
            conf=root/f'{i:04d}.conf'
            conf.write_text(f'base_dir = {root}/run\nstate_dir = {root}/state\n'
                f'log_path = /dev/stderr\ninfo_log_path = /dev/stderr\n'
                f'mail_uid = {os.geteuid()}\nmail_gid = {os.getegid()}\nfirst_valid_uid = 1\n'
                f'mail_home = {home}\nmail_location = maildir:{home}\nmail_plugins = zlib\nmaildir_stat_dirs = yes\nmaildir_copy_with_hardlinks = no\n'
                'ssl = no\nnamespace inbox {\n inbox = yes\n}\nplugin {\n sieve =\n}\n')
            env=dict(os.environ,HOME=str(home),USER='mail')
            for attempt in range(3):
                result=subprocess.run(['doveadm','-c',str(conf),'backup','maildir:'+str(dest)],
                    env=env,capture_output=True,timeout=600)
                if result.returncode==0 and not result.stderr.strip():break
                if result.returncode!=2:raise RuntimeError('online mailbox export failed; diagnostic suppressed')
            else:raise RuntimeError('mailbox kept changing; no successful export')
            mapping[f'{i:04d}']={'user':user,'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
            for part in ('sieve','.dovecot.sieve'):
                path=home/part
                if path.exists() or path.is_symlink():sieve.append(user+'/'+part)
        if sieve:
            result=subprocess.run(['tar','-czf',str(exports/'sieve.tar.gz'),'-C','/mail','--',*sieve],capture_output=True,timeout=300)
            if result.returncode:raise RuntimeError('sieve changed or could not be archived')
        (exports/'users.json').write_text(json.dumps(mapping))
        print(json.dumps({'path':str(root),'mailbox_count':len(mapping)}))
    except BaseException:
        shutil.rmtree(root)
        raise
'''


def command(args,output=None,input=None,timeout=900):
    result=subprocess.run(args,input=input,stdout=output or subprocess.PIPE,
                          stderr=subprocess.PIPE,timeout=timeout)
    if result.returncode:
        raise BackupError(f'online mail command failed (exit {result.returncode}); private output suppressed')
    return result.stdout


def services(containers,mail_root):
    result={}
    for item in containers:
        labels=item['Config'].get('Labels') or {}
        if labels.get('com.docker.compose.project.working_dir')!=str(mail_root):continue
        name=labels.get('com.docker.compose.service')
        if item['State']['Running']:
            if name in result:raise BackupError('duplicate running Mailu service')
            result[name]=item['Id']
    if not {'imap','admin','webmail','smtp','redis'}<=result.keys():
        raise BackupError('required running Mailu services missing; no service will be started')
    return result


def queue_empty(smtp):
    data=command(['docker','exec',smtp,'postqueue','-j']).decode()
    # Unknown/malformed/nonempty output fails closed. No queue is modified.
    for line in data.splitlines():
        if line.strip():
            try:json.loads(line)
            except ValueError:raise BackupError('SMTP queue status not understood')
            raise BackupError('SMTP queue is not empty; online export deferred without stopping mail')


def mailbox_users(imap):
    users=command(['docker','exec',imap,'doveadm','user','*']).decode().splitlines()
    if not users or len(set(users))!=len(users) or any(not re.fullmatch(r'[A-Za-z0-9_.+@-]+',u) for u in users):
        raise BackupError('mailbox enumeration empty or unsupported; private output suppressed')
    return sorted(users)


def validate_imap(imap):
    for setting,expected in [('mail_location','maildir:/mail/%u'),('mail_home','/mail/%u')]:
        value=command(['docker','exec',imap,'doveconf','-h',setting]).decode().strip()
        if value!=expected:raise BackupError('unsupported Mailu mailbox layout; no live copy attempted')
    # Additional explicit/shared namespaces need a separate export design.
    config=command(['docker','exec',imap,'doveconf','-n']).decode()
    namespaces=re.findall(r'^namespace\s+([^\s{]+)\s*\{',config,re.M)
    if namespaces!=['inbox']:raise BackupError('unsupported extra/shared mail namespace')


def cleanup(imap,path,uid):
    if not re.fullmatch(r'/tmp/g1-mail-online-[a-z0-9_]+',path):
        raise BackupError('refusing cleanup outside this run temporary mailbox directory')
    command(['docker','exec','-u',uid,imap,'python3','-c',
             'import shutil,sys;shutil.rmtree(sys.argv[1])',path])


def export_mailboxes(imap,target,users):
    validate_imap(imap)
    uid=command(['docker','exec',imap,'id','-u','mail']).decode().strip()
    gid=command(['docker','exec',imap,'id','-g','mail']).decode().strip()
    if not uid.isdigit() or not gid.isdigit() or uid=='0':raise BackupError('unsupported mail UID/GID')
    identity=uid+':'+gid
    program=WORKER+'\nexport('+repr(users)+')\n'
    data=json.loads(command(['docker','exec','-i','-u',identity,imap,'python3','-'],input=program.encode()))
    path=data['path']
    if not re.fullmatch(r'/tmp/g1-mail-online-[a-z0-9_]+',path):raise BackupError('invalid worker export path')
    try:
        with Path(target).open('xb') as output:
            command(['docker','exec','-u',identity,imap,'tar','-czf','-','-C',path+'/exports','.'],output)
    finally:cleanup(imap,path,identity)
    return {'mailbox_count':data['mailbox_count'],'mail_uid':int(uid),'mail_gid':int(gid)}


def sqlite_export(source,target):
    target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if target.exists():raise BackupError('SQLite export target already exists')
    src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=30)
    dst=sqlite3.connect(target)
    try:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise BackupError('mail SQLite integrity check failed')
    finally:src.close();dst.close()


def redis_export(redis,target):
    # Replication snapshot: Redis keeps serving while generating this RDB.
    rdb='/tmp/g1-online-'+uuid.uuid4().hex+'.rdb'
    created=False
    try:
        command(['docker','exec',redis,'test','!','-e',rdb])
        created=True
        command(['docker','exec',redis,'redis-cli','--rdb',rdb])
        command(['docker','exec',redis,'redis-check-rdb',rdb])
        with Path(target).open('xb') as f:command(['docker','exec',redis,'cat',rdb],f)
    finally:
        if created:command(['docker','exec',redis,'rm','-f','--',rdb])


def collect(out,mail_root,containers,archive):
    svc=services(containers,mail_root);queue_empty(svc['smtp'])
    users=mailbox_users(svc['imap'])
    target=out/'projects/demo-mail';target.mkdir(mode=0o700,parents=True)
    start=datetime.now(ZoneInfo('Asia/Seoul')).isoformat()
    # No live data is included in the configuration tar.
    archive(target/'files.tar.gz',mail_root.parent,[mail_root.name],[mail_root.name+'/data'])
    for relative in ('admin/main.db','webmail/roundcube.db'):
        sqlite_export(mail_root/'data'/relative,target/'databases'/relative)
    mail=export_mailboxes(svc['imap'],target/'online-mailboxes.tar.gz',users)
    archive_fn=['tar','-tzf',str(target/'online-mailboxes.tar.gz')]
    command(archive_fn)
    # Ancillary persistent files. Mutable caches and queue files are never presented
    # as a consistent live filesystem snapshot. Virus definitions can be downloaded.
    members=[p for p in ('data/certs','data/dkim','data/filter','data/admin/fetchmail') if (mail_root/p).exists()]
    if members:archive(target/'auxiliary.tar.gz',mail_root,members)
    redis_export(svc['redis'],target/'redis.rdb')
    if mailbox_users(svc['imap'])!=users:raise BackupError('mail account set changed during export; retry later')
    queue_empty(svc['smtp'])
    receipt={'method':'online-maildir-dsync-and-sqlite-backup','started_at':start,
        'completed_at':datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),**mail,
        'smtp_queue':'observed empty before and after; no queue messages archived',
        'consistency':'per-mailbox and per-database points; no common atomic instant',
        'auxiliary':'live ancillary files, not transactional; clamav and indexes can be rebuilt',
        'services_interrupted':False,'isolated_mail_service_restore_validated':False}
    atomic_json(target/'online-export.json',receipt)
    return receipt
