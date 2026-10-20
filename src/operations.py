"""Maintenance requires a private, repository-bound operator approval receipt."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import stat

from g1_backup import BackupError, KST, RUN_ID, atomic_json, guard, locked, private_file, retention_plan
from object_backup import validate_record

CONSENT = '매일 백업과 성공 5일 정리 동의'


def policy_path(c):
    return Path(c['password_file']).parent/'operations.json'


def approval(c):
    path = policy_path(c)
    if not path.exists() and not path.is_symlink():
        return None
    data = json.loads(private_file(path).read_text())
    expected = {'schema': 1, 'host': c['host'], 'repository': c['repository'],
                'repository_id': c['repository_id'], 'keep_success_days': 5,
                'cleanup_new_verified_staging': True, 'operator_consent': CONSENT}
    if any(data.get(k) != v for k, v in expected.items()):
        raise BackupError('운영 승인 범위가 현재 저장소와 다릅니다. 자동 정리를 거부합니다.')
    dt = datetime.fromisoformat(data['approved_at'])
    if dt.tzinfo is None or dt > datetime.now(KST):
        raise BackupError('운영 승인 시각이 올바르지 않습니다.')
    return data


def new_policy(c):
    return {'schema': 1, 'host': c['host'], 'repository': c['repository'],
            'repository_id': c['repository_id'], 'keep_success_days': 5,
            'cleanup_new_verified_staging': True, 'operator_consent': CONSENT,
            'approved_at': datetime.now(KST).isoformat()}


def checked_set(c, rid):
    """Validate hashes AND exact tree; unknown files/symlinks prevent deletion."""
    if not RUN_ID.fullmatch(rid):
        raise BackupError('invalid maintenance run ID')
    path = Path(c['repository'])/(rid+'.success.json')
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
        raise BackupError('success record is redirected')
    record = json.loads(path.read_text())
    if record.get('run_id') != rid or record.get('status') != 'verified' or record.get('layout') != 'objects-v2':
        raise BackupError('automatic retention supports verified objects-v2 only')
    root = validate_record(c['repository'], record, verify_hashes=True)
    expected = set(record['objects'])
    directories = set()
    for name in expected:
        directories.update(str(p) for p in Path(name).parents if str(p) != '.')
    actual_files = set(); actual_dirs = set()
    for p in root.rglob('*'):
        mode = p.lstat().st_mode
        name = p.relative_to(root).as_posix()
        if stat.S_ISDIR(mode): actual_dirs.add(name)
        elif stat.S_ISREG(mode): actual_files.add(name)
        else: raise BackupError('unexpected object type; no automatic deletion')
    if actual_files != expected or actual_dirs != directories:
        raise BackupError('unknown files or directories in backup; no automatic deletion')
    return path, root, record


def prune_after_success(c, new_result):
    if approval(c) is None:
        return {'status': 'approval-required', 'deleted': []}
    if new_result.get('status') != 'verified':
        raise BackupError('retention requires a newly verified backup from this run')
    guard(c)
    with locked(c['work']):
        plan = retention_plan(c['repository'])
        rid = new_result.get('run_id', '')
        if rid not in plan['keep']:
            raise BackupError('new success must be one of the retained days')
        # Validate ALL survivors/candidates before the first mutation.
        records = {r: checked_set(c, r) for r in plan['keep']+plan['delete_candidates']}
        if records[rid][2] != new_result:
            raise BackupError('new success differs from its committed record')
        deleted = []
        for old in plan['delete_candidates']:
            guard(c)
            path, root, record = checked_set(c, old)
            # Remove the success declaration first: a crash leaves uncounted orphan
            # objects, never a damaged object set still advertised as successful.
            path.unlink()
            for name in record['objects']:
                guard(c)
                (root/name).unlink()
            for folder in sorted((p for p in root.rglob('*') if p.is_dir()),
                                 key=lambda p: len(p.parts), reverse=True):
                folder.rmdir()
            root.rmdir()
            deleted.append(old)
            atomic_json(Path(c['work'])/'last-retention.json', {
                'at': datetime.now(KST).isoformat(), 'new_run_id': rid,
                'deleted': deleted, 'kept': plan['keep']})
        return {'status': 'retention-checked', 'deleted': deleted, 'kept': plan['keep']}


def cleanup_new_prepared(c, path, existing_before, new_result):
    if approval(c) is None:
        return {'status': 'approval-required', 'deleted': False}
    path = Path(path)
    parent = Path(c['prepared']).parent
    if (new_result.get('status') != 'verified' or path.name in existing_before or
        path.parent != parent or path != path.resolve() or
        not re.fullmatch(r'prepared-\d{8}T\d{6}-[a-f0-9]{8}', path.name)):
        raise BackupError('refusing cleanup of a previous or unexpected staging directory')
    mode = path.lstat()
    if not stat.S_ISDIR(mode.st_mode) or mode.st_uid != os.geteuid() or mode.st_mode & 0o077:
        raise BackupError('staging cleanup requires a private owned directory')
    receipt = json.loads((path/'PREPARED.json').read_text())
    if receipt.get('complete') is not True or receipt.get('host') != c['host']:
        raise BackupError('staging receipt mismatch')
    for p in path.rglob('*'):
        if not (stat.S_ISREG(p.lstat().st_mode) or stat.S_ISDIR(p.lstat().st_mode)):
            raise BackupError('unexpected staging object; refusing cleanup')
    guard(c)
    _, _, record = checked_set(c, new_result['run_id'])
    if record != new_result:
        raise BackupError('staging cleanup requires the committed new verified backup')
    shutil.rmtree(path)
    return {'status': 'new-verified-staging-removed', 'deleted': True}
