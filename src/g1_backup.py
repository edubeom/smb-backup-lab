#!/usr/bin/env python3
"""Encrypted transfer of a prepared, consistent backup bundle. No delete command."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import tarfile
import tempfile
import uuid
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
RUN_ID = re.compile(r'^\d{8}T\d{12}-[a-f0-9]{8}$')

class BackupError(RuntimeError):
    pass

def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()

def private_file(path):
    p = Path(path)
    s = p.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_mode & 0o077 or s.st_uid != os.geteuid():
        raise BackupError('credential must be a regular file owned by the runner, mode 0600 or stricter')
    if not p.read_bytes().strip():
        raise BackupError('credential is empty')
    return p

def check_mount(mount, source):
    mount = Path(mount)
    if mount.is_symlink() or not os.path.ismount(mount):
        raise BackupError('expected SMB mount is absent; refusing local fallback')
    r = subprocess.run(['findmnt','--json','--mountpoint',str(mount),'-o','TARGET,SOURCE,FSTYPE,OPTIONS'],
                       capture_output=True, text=True, check=True)
    rows = json.loads(r.stdout).get('filesystems', [])
    if len(rows) != 1:
        raise BackupError('ambiguous mount')
    fs = rows[0]
    if fs['target'] != str(mount) or fs['source'] != source or fs['fstype'] != 'cifs':
        raise BackupError('mount identity does not match')
    if 'rw' not in fs.get('options','').split(','):
        raise BackupError('mount is not writable')

def read_config(path):
    p = Path(path)
    c = json.loads(p.read_text())
    if c.get('enabled') is not True:
        raise BackupError('production config is disabled')
    if c.get('host') != socket.gethostname():
        raise BackupError('unexpected host')
    for key in ('mount','repository','work','prepared','password_file'):
        raw = Path(c[key])
        if not raw.is_absolute() or raw != raw.resolve():
            raise BackupError(f'{key} must be an absolute canonical path')
        if key in ('work','prepared','password_file') and raw.is_relative_to(Path(__file__).resolve().parents[1]):
            raise BackupError('private runtime data must stay outside the project')
    mount, repo = Path(c['mount']), Path(c['repository'])
    if repo == mount or not repo.is_relative_to(mount):
        raise BackupError('repository must be strictly below the mount')
    for key in ('work','prepared','password_file'):
        path = Path(c[key])
        if path.is_relative_to(repo) or repo.is_relative_to(path):
            raise BackupError(f'{key} overlaps repository')
    prepared, work = Path(c['prepared']), Path(c['work'])
    if prepared.is_relative_to(work) or work.is_relative_to(prepared):
        raise BackupError('prepared and work must not overlap')
    if not re.fullmatch(r'[a-zA-Z0-9-]{16,100}',c['repository_id']):
        raise BackupError('invalid repository identifier')
    if c['reserve_bytes'] < 0 or c['max_repository_bytes'] <= 0:
        raise BackupError('invalid capacity budget')
    if c.get('archive_layout','objects-v2') not in ('objects-v2','bundle-v1'):
        raise BackupError('unknown archive layout')
    return c

def guard(c):
    check_mount(c['mount'], c['mount_source'])
    repo = Path(c['repository'])
    if repo != repo.resolve() or not repo.is_dir():
        raise BackupError('repository missing or redirected')
    marker = repo / '.g1-repository-id'
    if marker.is_symlink() or marker.read_text().strip() != c['repository_id']:
        raise BackupError('repository marker mismatch')

def atomic_json(path, data):
    p = Path(path)
    temp = p.with_name(p.name+'.partial-'+uuid.uuid4().hex)
    try:
        with temp.open('x') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,p)
    finally:
        temp.unlink(missing_ok=True)

def crypt(source, dest, password, home, decrypt=False):
    # No key on command line/environment. Isolated GPG home; no passphrase cache.
    password = private_file(password)
    args = ['gpg','--homedir',str(home),'--batch','--yes','--no-symkey-cache',
            '--pinentry-mode','loopback','--passphrase-fd','0','--output',str(dest)]
    args += ['--decrypt',str(source)] if decrypt else ['--symmetric','--cipher-algo','AES256',str(source)]
    with password.open('rb') as f:
        r = subprocess.run(args, stdin=f, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if r.returncode:
        raise BackupError('GPG decryption/encryption failed (sensitive tool output suppressed)')

def build_bundle(source, archive):
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise BackupError('prepared source must be a real directory')
    entries = {}
    for p in sorted(source.rglob('*')):
        s = p.lstat()
        if not stat.S_ISREG(s.st_mode) and not stat.S_ISDIR(s.st_mode):
            raise BackupError('prepared bundle may contain only regular files and directories')
        if p.is_file():
            entries[p.relative_to(source).as_posix()] = {'sha256':digest(p),'size':s.st_size,'mode':stat.S_IMODE(s.st_mode)}
    if not entries:
        raise BackupError('empty prepared source')
    with tarfile.open(archive,'w:gz',format=tarfile.PAX_FORMAT) as t:
        t.add(source, arcname='payload',recursive=True)
        raw = (json.dumps(entries,sort_keys=True)+'\n').encode()
        import io
        info = tarfile.TarInfo('manifest.json')
        info.mode=0o600
        info.size=len(raw)
        t.addfile(info,io.BytesIO(raw))
    return entries

def verify_bundle(archive, destination=None):
    with tarfile.open(archive,'r:gz') as t:
        members = t.getmembers()
        names = [x.name for x in members]
        if len(names) != len(set(names)) or names.count('manifest.json') != 1:
            raise BackupError('duplicate or missing archive manifest')
        for m in members:
            if not (m.isfile() or m.isdir()) or m.name.startswith('/') or '..' in Path(m.name).parts:
                raise BackupError('unsafe archive member')
            if m.name != 'manifest.json' and m.name != 'payload' and not m.name.startswith('payload/'):
                raise BackupError('unexpected archive member')
        manifest = json.load(t.extractfile('manifest.json'))
        actual = {m.name.removeprefix('payload/'):m for m in members if m.isfile() and m.name.startswith('payload/')}
        if set(actual) != set(manifest):
            raise BackupError('manifest member mismatch')
        for name,m in actual.items():
            expected=manifest[name]
            h=hashlib.sha256()
            with t.extractfile(m) as f:
                for chunk in iter(lambda:f.read(1024*1024),b''):
                    h.update(chunk)
            if h.hexdigest()!=expected['sha256'] or m.size!=expected['size'] or m.mode!=expected['mode']:
                raise BackupError('payload verification failed')
        if destination is not None:
            dest = Path(destination)
            if dest.exists():
                raise BackupError('restore destination already exists; never overwrite')
            dest.mkdir(mode=0o700,parents=False)
            # Checked regular members only; no privileged archive extraction.
            for name,m in actual.items():
                out=dest/name
                out.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                with t.extractfile(m) as f, out.open('xb') as target:
                    shutil.copyfileobj(f,target)
                out.chmod(manifest[name]['mode'] & 0o777)
            for name,v in manifest.items():
                if digest(dest/name)!=v['sha256']:
                    raise BackupError('restored file verification failed')
        return manifest

def retention_plan(repo, keep=5):
    """One verified representative per Korean day; plan only, never deletes."""
    days={}
    for p in Path(repo).glob('*.success.json'):
        d=json.loads(p.read_text())
        rid=d.get('run_id','')
        if d.get('status')!='verified' or not RUN_ID.fullmatch(rid) or p.name!=rid+'.success.json':
            raise BackupError('invalid success record; refusing retention plan')
        dt=datetime.fromisoformat(d['finished_at'])
        if dt.tzinfo is None:
            raise BackupError('success time has no timezone')
        date=dt.astimezone(KST).date().isoformat()
        if d['day']!=date:
            raise BackupError('incomplete success record')
        if d.get('layout')=='objects-v2':
            from object_backup import validate_record
            validate_record(repo,d)
        elif not (Path(repo)/(rid+'.tar.gz.gpg')).is_file():
            raise BackupError('incomplete success record')
        days.setdefault(date,[]).append(d)
    representatives=[]; duplicates=[]
    for date in sorted(days,reverse=True):
        ordered=sorted(days[date],key=lambda d:datetime.fromisoformat(d['finished_at']).timestamp(),reverse=True)
        representatives.append(ordered[0]['run_id'])
        duplicates.extend(d['run_id'] for d in ordered[1:])
    return {'keep':representatives[:keep], 'delete_candidates':representatives[keep:]+duplicates,
            'deletion_enabled':False, 'note':'Plan only; maintenance requires a separate repository-bound operator approval.'}

@contextmanager
def locked(work):
    work=Path(work)
    work.mkdir(mode=0o700,parents=True,exist_ok=True)
    if work.is_symlink() or work.stat().st_mode & 0o077:
        raise BackupError('work directory must be private')
    with (work/'.lock').open('a') as f:
        try:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise BackupError('another run is active') from None
        yield

def transfer(c, now=None):
    """Accept only prepared immutable exports. No services are stopped here."""
    if c.get('archive_layout','objects-v2')=='objects-v2':
        from object_backup import transfer_objects
        return transfer_objects(c,now)
    now = now or datetime.now(KST)
    repo=Path(c['repository']); work=Path(c['work']); source=Path(c['prepared'])
    private_file(c['password_file'])
    guard(c)
    receipt=json.loads((source/'PREPARED.json').read_text())
    if receipt.get('complete') is not True or receipt.get('host') != c['host']:
        raise BackupError('no complete prepared export receipt')
    prepared_at=datetime.fromisoformat(receipt['prepared_at'])
    if prepared_at.tzinfo is None or not 0 <= (now-prepared_at).total_seconds() <= c.get('prepared_max_age_seconds',21600):
        raise BackupError('stale or future prepared export')
    with locked(work):
        plan=retention_plan(repo)
        for rid in plan['keep']:
            d=json.loads((repo/(rid+'.success.json')).read_text())
            if d['day']==now.astimezone(KST).date().isoformat():
                return {'status':'already-successful-today','run_id':rid}
        rid=now.strftime('%Y%m%dT%H%M%S%f')+'-'+uuid.uuid4().hex[:8]
        with tempfile.TemporaryDirectory(prefix='run-',dir=work) as td:
            td=Path(td); home=td/'gpg'; home.mkdir(mode=0o700)
            archive=td/'bundle.tar.gz'
            estimate=sum(p.stat().st_size for p in source.rglob('*') if p.is_file())
            if shutil.disk_usage(work).free < c['reserve_bytes'] + estimate*4 + 1048576:
                raise BackupError('insufficient staging space before archive creation')
            entries=build_bundle(source,archive)
            if shutil.disk_usage(work).free < c['reserve_bytes'] + archive.stat().st_size*2:
                raise BackupError('insufficient staging space')
            encrypted=td/'bundle.gpg'
            crypt(archive,encrypted,c['password_file'],home)
            size=encrypted.stat().st_size
            guard(c)
            used=sum(p.stat().st_size for p in repo.iterdir() if p.is_file())
            if used+size > c['max_repository_bytes'] or shutil.disk_usage(repo).free < c['reserve_bytes']+size:
                raise BackupError('repository budget or free-space limit exceeded; existing backups retained')
            partial=repo/(rid+'.partial')
            final=repo/(rid+'.tar.gz.gpg')
            # Partial/uncommitted objects are kept for later inspection, never auto-pruned.
            with encrypted.open('rb') as src, partial.open('xb') as dst:
                shutil.copyfileobj(src,dst)
                dst.flush(); os.fsync(dst.fileno())
            guard(c)
            if digest(partial)!=digest(encrypted):
                raise BackupError('transfer checksum mismatch')
            decrypted=td/'remote-readback.tar.gz'
            crypt(partial,decrypted,c['password_file'],home,decrypt=True)
            verify_bundle(decrypted)
            # A successful readback includes actual extraction and file hash comparison.
            verify_bundle(decrypted,td/'restore-check')
            guard(c)
            os.replace(partial,final)
            finished=datetime.now(KST)
            record={'schema':1,'run_id':rid,'status':'verified','day':finished.date().isoformat(),
                    'finished_at':finished.isoformat(),'archive_sha256':digest(final),'bytes':size,
                    'file_count':len(entries),'scope':'prepared exports; service restore not yet validated'}
            atomic_json(repo/(rid+'.success.json'),record)
            atomic_json(work/'last-success.json',record)
            return record

def restore(archive,password,destination):
    with tempfile.TemporaryDirectory(prefix='g1-restore-') as td:
        td=Path(td);home=td/'gpg';home.mkdir(mode=0o700)
        clear=td/'bundle.tar.gz'
        crypt(archive,clear,password,home,decrypt=True)
        entries=verify_bundle(clear,destination)
        return {'status':'files-restored-and-verified','file_count':len(entries),
                'note':'Inner system archives and actual services require the restore runbook.'}

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('preflight','run','retention-plan'):
        p=sub.add_parser(command);p.add_argument('--config',required=True)
    p=sub.add_parser('restore-test')
    inputs=p.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--archive');inputs.add_argument('--snapshot')
    selection=p.add_mutually_exclusive_group()
    selection.add_argument('--project',help='restore only one project from an objects-v2 snapshot')
    selection.add_argument('--component',help='restore one component, for example system/etc.tar.gz')
    p.add_argument('--password-file',required=True);p.add_argument('--destination',required=True)
    a=parser.parse_args()
    try:
        if a.command=='restore-test':
            if a.snapshot:
                from object_backup import restore_objects
                result=restore_objects(a.snapshot,a.password_file,a.destination,a.project,a.component)
            else:
                if a.project or a.component:raise BackupError('component selection requires --snapshot')
                result=restore(a.archive,a.password_file,a.destination)
        else:
            c=read_config(a.config);guard(c)
            if a.command=='preflight':
                private_file(c['password_file'])
                result={'status':'mount-and-credential-checked','note':'Prepared exports and service consistency checked at run time.'}
            elif a.command=='retention-plan': result=retention_plan(c['repository'])
            else: result=transfer(c)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (BackupError,OSError,ValueError,KeyError,subprocess.SubprocessError,tarfile.TarError) as error:
        # Avoid accidental path/password leakage from low-level exceptions in shared logs.
        print(json.dumps({'status':'failed','reason':str(error) if isinstance(error,BackupError) else type(error).__name__ + ': validation or I/O failed (raw details suppressed)'}))
        return 1
    return 0

if __name__=='__main__':
    import sys
    sys.modules['g1_backup']=sys.modules[__name__]
    raise SystemExit(main())
