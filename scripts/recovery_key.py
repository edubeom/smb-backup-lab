#!/usr/bin/env python3
"""Verify an operator's offsite key readback. Never display or change a key."""
import argparse
from datetime import datetime
import getpass
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
import warnings
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from g1_backup import BackupError,KST,atomic_json,crypt,digest,locked,private_file,read_config
from object_backup import object_path
from verify_postgres import snapshot_record


def verify_readback(c,rid,entered,operator_attested=False):
    if operator_attested is not True:
        raise BackupError('외부 보관 후 암호 관리자에서 다시 복사한 키로 검증해야 합니다.')
    if not entered or '\n' in entered or '\r' in entered:
        raise BackupError('복구 키는 비어 있지 않은 한 줄이어야 합니다.')
    original=private_file(c['password_file']).read_bytes().rstrip(b'\r\n')
    if not hmac.compare_digest(entered.encode('utf-8'),original):
        raise BackupError('기존 암호화 복구 키와 다릅니다. 공유 비밀번호나 sudo 암호가 아닌지 확인하세요. 키는 변경하지 않았습니다.')
    snapshot,record=snapshot_record(c,rid)
    name='_manifest.json.gpg'
    encrypted=object_path(Path(c['repository'])/(rid+'.objects'),name)
    if digest(encrypted)!=record['objects'][name]['sha256']:
        raise BackupError('암호화 명세의 해시가 다릅니다. 확인 기록을 만들지 않았습니다.')
    with locked(Path(c['work'])/'key-verification'):
        with tempfile.TemporaryDirectory(prefix='key-readback-',dir=c['work']) as td:
            td=Path(td);key=td/'external-passphrase';home=td/'gpg';home.mkdir(mode=0o700)
            with key.open('x',encoding='utf-8') as f:f.write(entered+'\n')
            key.chmod(0o600)
            # Use the re-entered key, not the resident key, for real shared decrypt.
            crypt(encrypted,td/'manifest.json',key,home,decrypt=True)
            manifest=json.loads((td/'manifest.json').read_text())
            if manifest.get('schema')!=2 or not manifest.get('files'):
                raise BackupError('복호화된 백업 명세가 올바르지 않습니다.')
        result={'status':'offsite-key-readback-and-shared-manifest-verified','run_id':rid,
            'verified_at':datetime.now(KST).isoformat(),
            'offsite_storage_attested_by_operator':True,'key_readback_matches_current_key':True,
            'shared_encrypted_manifest_decrypted_with_readback_key':True,
            'offsite_provider_independently_inspected':False,
            'key_displayed_or_changed':False,'existing_backups_deleted':False,
            'timer_enabled_by_this_tool':False}
        # Receipts contain only evidence/status, never key values or fingerprints.
        atomic_json(Path(c['work'])/('key-verification-'+rid+'-'+uuid.uuid4().hex[:8]+'.json'),result)
        atomic_json(Path(c['work'])/'offsite-key-verification.json',result)
    return result


def plan():
    return {'status':'instructions-only','key_read':False,
        'steps':['Turn off recording/screen sharing before opening the key in your personal terminal.',
                 'Store the existing /etc/g1-backup/archive-passphrase value in an offsite password manager.',
                 'Copy it back from that manager and use --verify-offsite --snapshot-id RUN_ID.',
                 'The verification prompt is hidden. Never send the key to chat or Git.'],
        'does_not':'generate a new key, display a key, stop services, delete backups or enable a timer'}


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='/etc/g1-backup/backup.json')
    p.add_argument('--snapshot-id')
    p.add_argument('--verify-offsite',action='store_true',help='attest offsite storage and paste a hidden key readback')
    a=p.parse_args()
    try:
        if not a.verify_offsite:
            print(json.dumps(plan(),ensure_ascii=False,indent=2));return 0
        if os.geteuid()!=0 or not sys.stdin.isatty():
            raise BackupError('G1 개인 터미널에서 sudo로 실행해야 합니다. 키는 채팅에 보내지 마세요.')
        if not a.snapshot_id:raise BackupError('--snapshot-id가 필요합니다.')
        c=read_config(a.config)
        snapshot_record(c,a.snapshot_id)  # Validate the shared source before asking for a key.
        print('기존 키를 G1 외부 암호 관리자에 저장한 뒤 그곳에서 다시 복사해 붙여넣으세요. 값은 표시하지 않습니다.',flush=True)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',getpass.GetPassWarning)
                entered=getpass.getpass('외부 보관한 암호화 복구 키 (표시 안 됨): ')
        except getpass.GetPassWarning:
            raise BackupError('숨김 입력을 보장할 수 없어 중단했습니다. 개인 터미널에서 다시 실행하세요.') from None
        result=verify_readback(c,a.snapshot_id,entered,operator_attested=True)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (Exception,KeyboardInterrupt) as error:
        print(json.dumps({'status':'failed','reason':str(error) if isinstance(error,BackupError) else type(error).__name__,
                          'key_displayed_or_changed':False,'existing_backups_deleted':False},ensure_ascii=False))
        return 1
if __name__=='__main__':raise SystemExit(main())
