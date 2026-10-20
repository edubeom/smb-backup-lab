"""Independent encrypted objects, committed as one successful daily backup set."""
from datetime import datetime
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import uuid

from g1_backup import (BackupError, KST, RUN_ID, atomic_json, crypt, digest, guard,
                       locked, private_file, retention_plan)


def safe_relative(name):
    if not isinstance(name,str) or not name or '\\' in name:
        raise BackupError('invalid object path')
    p=PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or str(p)!=name:
        raise BackupError('unsafe object path')
    return p


def object_path(root,name):
    safe_relative(name)
    path=Path(root)/name
    if not path.resolve().is_relative_to(Path(root).resolve()) or path.is_symlink():
        raise BackupError('object path leaves its backup directory')
    return path


def validate_record(repo,record,verify_hashes=False):
    rid=record.get('run_id','')
    if not RUN_ID.fullmatch(rid):raise BackupError('invalid object run ID')
    root=Path(repo)/(rid+'.objects')
    if root.is_symlink() or not root.is_dir():raise BackupError('object set missing or redirected')
    objects=record.get('objects',{})
    if not objects or '_manifest.json.gpg' not in objects:
        raise BackupError('object success record has no encrypted manifest')
    for name,metadata in objects.items():
        path=object_path(root,name)
        if not path.is_file() or path.stat().st_size!=metadata['bytes']:
            raise BackupError('committed object missing or size mismatch')
        if verify_hashes and digest(path)!=metadata['sha256']:
            raise BackupError('encrypted object checksum mismatch')
    return root


def source_manifest(source):
    entries={}
    for path in sorted(source.rglob('*')):
        mode=path.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise BackupError('prepared objects must be regular files or directories')
        if path.is_file():
            rel=path.relative_to(source).as_posix()
            safe_relative(rel)
            if rel=='_manifest.json':raise BackupError('reserved prepared object name')
            entries[rel]={'sha256':digest(path),'bytes':path.stat().st_size,
                          'mode':stat.S_IMODE(mode)}
    if not entries:raise BackupError('empty prepared source')
    return entries


def tree_bytes(root):
    total=0
    for path in Path(root).rglob('*'):
        if path.is_symlink():raise BackupError('repository contains a symlink')
        if path.is_file():total+=path.stat().st_size
    return total


def transfer_objects(c,now=None):
    now=now or datetime.now(KST)
    repo=Path(c['repository']);source=Path(c['prepared']);work=Path(c['work'])
    private_file(c['password_file']);guard(c)
    if source.is_symlink() or not source.is_dir():raise BackupError('invalid prepared directory')
    receipt=json.loads((source/'PREPARED.json').read_text())
    if receipt.get('complete') is not True or receipt.get('host')!=c['host']:
        raise BackupError('incomplete prepared export receipt')
    prepared_at=datetime.fromisoformat(receipt['prepared_at'])
    if prepared_at.tzinfo is None or not 0<=(now-prepared_at).total_seconds()<=c.get('prepared_max_age_seconds',21600):
        raise BackupError('stale or future prepared export')
    with locked(work):
        for rid in retention_plan(repo)['keep']:
            record=json.loads((repo/(rid+'.success.json')).read_text())
            if record['day']==now.astimezone(KST).date().isoformat():
                return {'status':'already-successful-today','run_id':rid}
        entries=source_manifest(source)
        largest=max(v['bytes'] for v in entries.values())
        if shutil.disk_usage(work).free<c['reserve_bytes']+largest*3+1048576:
            raise BackupError('insufficient staging space for object verification')
        rid=now.strftime('%Y%m%dT%H%M%S%f')+'-'+uuid.uuid4().hex[:8]
        partial=repo/(rid+'.partial')
        partial.mkdir(mode=0o700)
        used=tree_bytes(repo)
        objects={}
        with tempfile.TemporaryDirectory(prefix='objects-',dir=work) as td:
            td=Path(td);home=td/'gpg';home.mkdir(mode=0o700)

            def send(plain,name,expected_hash):
                nonlocal used
                safe_relative(name)
                encrypted=td/'encrypted';readback=td/'readback'
                encrypted.unlink(missing_ok=True);readback.unlink(missing_ok=True)
                crypt(plain,encrypted,c['password_file'],home)
                size=encrypted.stat().st_size
                guard(c)
                if used+size>c['max_repository_bytes'] or shutil.disk_usage(repo).free<c['reserve_bytes']+size:
                    raise BackupError('repository budget or free-space limit exceeded; existing backups retained')
                remote=object_path(partial,name)
                remote.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                with encrypted.open('rb') as src,remote.open('xb') as dst:
                    shutil.copyfileobj(src,dst);dst.flush();os.fsync(dst.fileno())
                guard(c)
                cipher_hash=digest(encrypted)
                if digest(remote)!=cipher_hash:raise BackupError('object transfer checksum mismatch')
                crypt(remote,readback,c['password_file'],home,decrypt=True)
                if digest(readback)!=expected_hash:raise BackupError('decrypted readback mismatch')
                objects[name]={'sha256':cipher_hash,'bytes':size};used+=size

            for name,metadata in entries.items():
                plain=source/name
                send(plain,name+'.gpg',metadata['sha256'])
                if digest(plain)!=metadata['sha256']:
                    raise BackupError('prepared source changed during transfer')
            manifest=td/'manifest.json'
            atomic_json(manifest,{'schema':2,'files':entries,'scope':receipt})
            send(manifest,'_manifest.json.gpg',digest(manifest))
        guard(c)
        final=repo/(rid+'.objects')
        os.rename(partial,final)
        finished=datetime.now(KST)
        record={'schema':2,'layout':'objects-v2','run_id':rid,'status':'verified',
                'day':finished.date().isoformat(),'finished_at':finished.isoformat(),
                'objects':objects,'file_count':len(entries),'bytes':sum(v['bytes'] for v in objects.values()),
                'scope':'all prepared components verified; service restore not yet validated'}
        if tree_bytes(repo)+len(json.dumps(record).encode())+1024>c['max_repository_bytes']:
            raise BackupError('repository budget exceeded by success metadata; existing backups retained')
        validate_record(repo,record)
        atomic_json(repo/(rid+'.success.json'),record)
        atomic_json(work/'last-success.json',record)
        return record


def restore_objects(snapshot,password,destination,project=None,component=None):
    snapshot=Path(snapshot)
    if snapshot.is_symlink():raise BackupError('snapshot record cannot be a symlink')
    record=json.loads(snapshot.read_text())
    if record.get('layout')!='objects-v2' or record.get('status')!='verified':
        raise BackupError('not a verified object snapshot')
    if snapshot.name!=record.get('run_id','')+'.success.json':raise BackupError('snapshot filename mismatch')
    dest=Path(destination)
    if dest.exists() or dest.is_symlink():raise BackupError('restore destination exists; never overwrite')
    root=validate_record(snapshot.parent,record)
    private_file(password)
    with tempfile.TemporaryDirectory(prefix='g1-object-restore-') as td:
        td=Path(td);home=td/'gpg';home.mkdir(mode=0o700)
        def decrypt(name,target):
            safe_relative(name)
            if name not in record['objects']:raise BackupError('object missing from success record')
            remote=object_path(root,name)
            if digest(remote)!=record['objects'][name]['sha256']:raise BackupError('encrypted object checksum mismatch')
            crypt(remote,target,password,home,decrypt=True)
        decrypt('_manifest.json.gpg',td/'manifest.json')
        manifest=json.loads((td/'manifest.json').read_text())
        if manifest.get('schema')!=2:raise BackupError('unknown object manifest schema')
        entries=manifest['files']
        for name in entries:safe_relative(name)
        selected=entries
        if project is not None and component is not None:
            raise BackupError('choose a project or component, not both')
        if component is not None:
            safe_relative(component)
            if component not in entries:raise BackupError('requested component not present')
            selected={component:entries[component]}
        elif project is not None:
            from urllib.parse import quote
            if not project or '/' in project or '\\' in project or project in ('.','..'):
                raise BackupError('invalid project selector')
            prefix='projects/'+quote(project,safe='-_.')+'/'
            selected={k:v for k,v in entries.items() if k.startswith(prefix)}
            if not selected:raise BackupError('requested project not present')
        # Verify all selected data before publishing any restored output.
        restored=td/'restored';restored.mkdir(mode=0o700)
        for name,metadata in selected.items():
            target=object_path(restored,name);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            decrypt(name+'.gpg',target)
            if digest(target)!=metadata['sha256'] or target.stat().st_size!=metadata['bytes']:
                raise BackupError('restored object content mismatch')
            target.chmod(metadata['mode']&0o777)
        dest.mkdir(mode=0o700,parents=False)
        for path in restored.rglob('*'):
            target=dest/path.relative_to(restored)
            if path.is_dir():target.mkdir(mode=0o700,exist_ok=True)
            else:shutil.copy2(path,target)
        return {'status':'objects-restored-and-verified','file_count':len(selected),
                'project':project,'component':component,
                'note':'Inner project/system archives remain packed. Follow the restore runbook.'}
