"""Synthetic data only. Runs in a network-isolated container with no host mounts."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from contextlib import redirect_stdout

home=Path('/mail/fixture@example.invalid')
for part in ('cur','new','tmp','sieve'):(home/part).mkdir(parents=True,exist_ok=True)
message=b'From: synthetic@example.invalid\nTo: restore@example.invalid\nSubject: fixture\nMessage-ID: <fixture@example.invalid>\n\nrestore proof\n'
(home/'cur/fixture:2,FS').write_bytes(message)
(home/'subscriptions').write_text('INBOX\n')
(home/'sieve/example.sieve').write_text('keep;\n')
(home/'.dovecot.sieve').symlink_to('sieve/example.sieve')
for root,dirs,files in os.walk('/mail'):
    os.chown(root,8,12)
    for f in files:os.lchown(os.path.join(root,f),8,12)
os.setgid(12);os.setuid(8)
exec(compile(sys.stdin.read(),'online-worker','exec'))
buf=io.StringIO()
with redirect_stdout(buf):export(['fixture@example.invalid'])
result=json.loads(buf.getvalue());root=Path(result['path']);dest=root/'exports/maildirs/0000'
files=list((dest/'cur').iterdir())
assert len(files)==1 and files[0].read_bytes()==message,'message content'
assert set(files[0].name.split(':2,')[1])==set('FS'),'message flags'
assert (home/'cur/fixture:2,FS').read_bytes()==message,'source message unchanged'
srcuids=(home/'dovecot-uidlist').read_text().splitlines();dstuids=(dest/'dovecot-uidlist').read_text().splitlines()
assert srcuids[0]==dstuids[0],'UIDVALIDITY, next UID and mailbox GUID'
assert [x.split(' ',1)[0] for x in srcuids[1:]]==[x.split(' ',1)[0] for x in dstuids[1:]],'message UID'
def subscriptions(path):return [line for line in path.read_text().splitlines() if line and line!='V\t2']
assert subscriptions(home/'subscriptions')==subscriptions(dest/'subscriptions'),'subscriptions'
with tarfile.open(root/'exports/sieve.tar.gz') as t:
    assert t.extractfile('fixture@example.invalid/sieve/example.sieve').read()==b'keep;\n','sieve content'
    assert t.getmember('fixture@example.invalid/.dovecot.sieve').issym(),'active sieve link'
r=subprocess.run(['doveadm','-c',str(root/'0000.conf'),'-o','mail_location=maildir:'+str(dest),
                  'fetch','uid flags hdr.subject','all'],env=dict(os.environ,HOME=str(dest),USER='mail'),capture_output=True,text=True,timeout=25)
assert r.returncode==0 and 'fixture' in r.stdout and '\\Seen' in r.stdout and '\\Flagged' in r.stdout,'destination readback'
print(json.dumps({'fixture_only':True,'network':'none','messages':1,
    'content_flags_uids_subscriptions_sieve':'verified','destination_readback':'verified','source_message':'unchanged'}))
