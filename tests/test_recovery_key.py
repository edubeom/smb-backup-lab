from datetime import datetime
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
import g1_backup as core
import object_backup as objects
import recovery_key as key

class RecoveryKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'prepared';self.source.mkdir()
        (self.source/'fixture.txt').write_text('synthetic backup content')
        (self.source/'PREPARED.json').write_text(json.dumps({'complete':True,'host':socket.gethostname(),
            'prepared_at':datetime.now(core.KST).isoformat()}))
        self.repo=self.root/'repo';self.repo.mkdir()
        self.password=self.root/'passphrase';self.password.write_text('synthetic-offsite-key-not-real\n');self.password.chmod(0o600)
        self.c={'repository':str(self.repo),'prepared':str(self.source),'work':str(self.root/'work'),
                'password_file':str(self.password),'host':socket.gethostname(),'reserve_bytes':1,
                'max_repository_bytes':10**8,'archive_layout':'objects-v2'}

    def tearDown(self):self.tmp.cleanup()

    def backup(self):
        with patch.object(objects,'guard'):return objects.transfer_objects(self.c)

    def test_plan_never_reads_or_displays_key(self):
        with patch.object(key,'private_file') as read:
            result=key.plan();read.assert_not_called()
            self.assertFalse(result['key_read'])
            self.assertNotIn('synthetic-offsite-key',json.dumps(result))

    def test_operator_attestation_required_before_key_read(self):
        with patch.object(key,'private_file') as read:
            with self.assertRaises(core.BackupError):key.verify_readback({},'not-read','synthetic')
            read.assert_not_called()

    def test_wrong_key_leaves_original_and_no_receipt(self):
        before=self.password.read_bytes()
        with self.assertRaises(core.BackupError) as error:
            key.verify_readback(self.c,'not-read','synthetic-wrong-key',True)
        self.assertNotIn('synthetic-wrong-key',str(error.exception))
        self.assertEqual(self.password.read_bytes(),before)
        self.assertFalse((Path(self.c['work'])/'offsite-key-verification.json').exists())

    def test_real_shared_decrypt_uses_readback_without_key_in_receipt_or_output(self):
        record=self.backup();rid=record['run_id'];before=self.password.read_bytes()
        output=io.StringIO()
        with patch.object(key,'snapshot_record',return_value=(self.repo/(rid+'.success.json'),record)),patch('sys.stdout',output),patch.object(key,'crypt',wraps=key.crypt) as decrypt:
            result=key.verify_readback(self.c,rid,'synthetic-offsite-key-not-real',True)
        self.assertNotEqual(Path(decrypt.call_args.args[2]),self.password)
        self.assertTrue(decrypt.call_args.kwargs['decrypt'])
        self.assertTrue(result['shared_encrypted_manifest_decrypted_with_readback_key'])
        self.assertFalse(result['offsite_provider_independently_inspected'])
        self.assertEqual(self.password.read_bytes(),before)
        receipt=(Path(self.c['work'])/'offsite-key-verification.json').read_text()
        self.assertNotIn('synthetic-offsite-key-not-real',receipt+output.getvalue())
        self.assertEqual(list(Path(self.c['work']).glob('key-readback-*')),[])
        self.assertEqual(len(list(self.repo.glob('*.success.json'))),1)

    def test_corrupted_manifest_never_creates_receipt(self):
        record=self.backup();rid=record['run_id']
        (self.repo/(rid+'.objects/_manifest.json.gpg')).write_bytes(b'synthetic corruption')
        with patch.object(key,'snapshot_record',return_value=(self.repo/(rid+'.success.json'),record)):
            with self.assertRaises(core.BackupError):key.verify_readback(self.c,rid,'synthetic-offsite-key-not-real',True)
        self.assertFalse((Path(self.c['work'])/'offsite-key-verification.json').exists())

    def test_verify_cli_without_tty_never_loads_config_or_prompts(self):
        with patch.object(sys,'argv',['recovery_key.py','--verify-offsite','--snapshot-id','not-read']),patch.object(key.os,'geteuid',return_value=0),patch.object(key.sys.stdin,'isatty',return_value=False),patch.object(key,'read_config') as read,patch.object(key.getpass,'getpass') as prompt,patch('sys.stdout',io.StringIO()):
            self.assertEqual(key.main(),1)
            read.assert_not_called();prompt.assert_not_called()

    def test_empty_or_multiline_input_is_rejected_before_reading_key(self):
        for entered in ('','synthetic\nsecond-line','synthetic\rsecond-line'):
            with patch.object(key,'private_file') as read:
                with self.assertRaises(core.BackupError):key.verify_readback({},'not-read',entered,True)
                read.assert_not_called()

    def test_getpass_echo_fallback_is_rejected_without_verification(self):
        with patch.object(sys,'argv',['recovery_key.py','--verify-offsite','--snapshot-id','synthetic']),patch.object(key.os,'geteuid',return_value=0),patch.object(key.sys.stdin,'isatty',return_value=True),patch.object(key,'read_config',return_value={}),patch.object(key,'snapshot_record'),patch.object(key.getpass,'getpass',side_effect=key.getpass.GetPassWarning('synthetic fallback')),patch.object(key,'verify_readback') as verify,patch('sys.stdout',io.StringIO()):
            self.assertEqual(key.main(),1)
            verify.assert_not_called()

    def test_verify_cli_nonroot_never_loads_config_or_prompts(self):
        with patch.object(sys,'argv',['recovery_key.py','--verify-offsite','--snapshot-id','not-read']),patch.object(key.os,'geteuid',return_value=1000),patch.object(key,'read_config') as read,patch.object(key.getpass,'getpass') as prompt,patch('sys.stdout',io.StringIO()):
            self.assertEqual(key.main(),1)
            read.assert_not_called();prompt.assert_not_called()

if __name__=='__main__':unittest.main()
