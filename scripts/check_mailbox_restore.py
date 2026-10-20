#!/usr/bin/env python3
"""Read an online mailbox export in an isolated container. No host data mounts."""
import argparse
import json
from pathlib import Path
import subprocess

WORKER=r'''
import json,os,pathlib,subprocess,sys,tarfile,tempfile
root=pathlib.Path(tempfile.mkdtemp(prefix='g1-mail-readback-',dir='/tmp'))
archive=root/'export.tar.gz'
with archive.open('xb') as out:
    import shutil
    shutil.copyfileobj(sys.stdin.buffer,out)
exports=root/'exports';exports.mkdir()
with tarfile.open(archive) as t:
    for entry in t.getmembers():
        path=pathlib.PurePosixPath(entry.name)
        if path.is_absolute() or '..' in path.parts or not (entry.isfile() or entry.isdir()):
            raise RuntimeError('unsafe export member')
    t.extractall(exports,filter='data')
users=json.loads((exports/'users.json').read_text())
for path,dirs,files in os.walk(root):
    os.chown(path,8,12)
    for f in files:os.chown(os.path.join(path,f),8,12)
os.setgid(12);os.setuid(8)
verified=0
for index in users:
    if not index.isdigit():raise RuntimeError('invalid mailbox index')
    home=exports/'maildirs'/index
    if not home.is_dir():raise RuntimeError('maildir missing')
    conf=root/(index+'.conf')
    conf.write_text(f'base_dir = {root}/run\nstate_dir = {root}/state\n'
        f'log_path = /dev/stderr\ninfo_log_path = /dev/stderr\n'
        f'mail_uid = 8\nmail_gid = 12\nfirst_valid_uid = 1\nmail_home = {home}\n'
        f'mail_location = maildir:{home}\nmail_plugins = zlib\nmaildir_stat_dirs = yes\n'
        'ssl = no\nnamespace inbox {\n inbox = yes\n}\nplugin {\n sieve =\n}\n')
    # Full message read stays in process memory; no addresses/body/flags in logs.
    result=subprocess.run(['doveadm','-c',str(conf),'fetch','uid flags text','all'],
        env=dict(os.environ,USER='mail',HOME=str(home)),capture_output=True,timeout=60)
    if result.returncode or result.stderr.strip():raise RuntimeError('isolated mailbox readback failed; private diagnostic suppressed')
    verified+=1
print(json.dumps({'status':'isolated-mailbox-readback-verified','mailbox_count':verified,
    'network':'none','host_mounts':False,'production_mailu_login_or_delivery_tested':False}))
'''

def verify(archive):
    with Path(archive).open('rb') as data:
        result=subprocess.run(['docker','run','--rm','-i','--pull=never','--network','none',
            '--read-only','--tmpfs','/tmp:size=512m','--entrypoint','python3',
            'ghcr.io/mailu/dovecot:2024.06','-c',WORKER],stdin=data,capture_output=True,timeout=600)
    if result.returncode:
        raise RuntimeError('isolated mailbox readback failed; private diagnostic suppressed')
    return json.loads(result.stdout)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive',type=Path)
    a=p.parse_args()
    print(json.dumps(verify(a.archive),ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
