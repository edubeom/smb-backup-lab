import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta

SPEC=importlib.util.spec_from_file_location('backup',Path(__file__).resolve().parents[1]/'src/g1_backup.py')
b=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(b)

class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'source';self.source.mkdir(mode=0o700)
        self.repo=self.root/'mount'/'server'/'g1';self.repo.mkdir(parents=True)
        self.password=self.root/'pass';self.password.write_text('synthetic-test-key-only\n');self.password.chmod(0o600)
        self.now=datetime.now(b.KST)
        (self.source/'hello.txt').write_text('가상 백업 자료\n')
        (self.source/'PREPARED.json').write_text(json.dumps({'complete':True,'host':socket.gethostname(),'prepared_at':self.now.isoformat()}))
        self.c={'enabled':True,'host':socket.gethostname(),'mount':str(self.root/'mount'),'mount_source':'//example/backup',
                'repository':str(self.repo),'repository_id':'test-repository-id-1234','work':str(self.root/'work'),
                'prepared':str(self.source),'password_file':str(self.password),'reserve_bytes':1,'max_repository_bytes':10**8,
                'archive_layout':'bundle-v1'}
        (self.repo/'.g1-repository-id').write_text(self.c['repository_id'])
    def tearDown(self): self.tmp.cleanup()
    def cfg(self):
        p=self.root/'config.json';p.write_text(json.dumps(self.c));return p
    def success(self, dt, suffix='1234abcd'):
        rid=dt.strftime('%Y%m%dT%H%M%S%f')+'-'+suffix
        (self.repo/(rid+'.tar.gz.gpg')).write_bytes(b'fake-retention-fixture')
        (self.repo/(rid+'.success.json')).write_text(json.dumps({'run_id':rid,'status':'verified','day':dt.astimezone(b.KST).date().isoformat(),'finished_at':dt.isoformat()}))
        return rid
    def test_disabled_configuration(self):
        self.c['enabled']=False
        with self.assertRaisesRegex(b.BackupError,'disabled'):b.read_config(self.cfg())
    def test_wrong_host(self):
        self.c['host']='another-server'
        with self.assertRaisesRegex(b.BackupError,'host'):b.read_config(self.cfg())
    def test_overlapping_work(self):
        self.c['work']=str(self.source/'nested')
        with self.assertRaisesRegex(b.BackupError,'overlap'):b.read_config(self.cfg())
    def test_missing_mount_never_creates_repository(self):
        with self.assertRaises(b.BackupError):b.check_mount(self.root/'missing','//example/backup')
        self.assertFalse((self.root/'missing').exists())
    def test_wrong_mount_source(self):
        fake=unittest.mock.Mock(stdout=json.dumps({'filesystems':[{'target':self.c['mount'],'source':'//wrong/backup','fstype':'cifs','options':'rw'}]}))
        with patch.object(b.os.path,'ismount',return_value=True),patch.object(b.subprocess,'run',return_value=fake):
            with self.assertRaisesRegex(b.BackupError,'identity'):b.check_mount(self.c['mount'],self.c['mount_source'])
    def test_wrong_repository_marker(self):
        (self.repo/'.g1-repository-id').write_text('wrong')
        with patch.object(b,'check_mount'):
            with self.assertRaisesRegex(b.BackupError,'marker'):b.guard(self.c)
    def test_world_readable_key(self):
        self.password.chmod(0o644)
        with self.assertRaises(b.BackupError):b.private_file(self.password)
    def test_symlink_key(self):
        link=self.root/'link';link.symlink_to(self.password)
        with self.assertRaises(b.BackupError):b.private_file(link)
    def test_concurrent_lock(self):
        with b.locked(self.c['work']):
            with self.assertRaisesRegex(b.BackupError,'active'):
                with b.locked(self.c['work']):pass
    def test_recent_five_successful_days_and_duplicates(self):
        ids=[self.success(self.now-timedelta(days=i)) for i in range(7)]
        duplicate=self.success(self.now-timedelta(minutes=1),'abcdef12')
        (self.repo/'failed.partial').write_text('failure')
        p=b.retention_plan(self.repo)
        self.assertEqual(p['keep'],ids[:5]);self.assertEqual(set(p['delete_candidates']),{*ids[5:],duplicate})
        self.assertFalse(p['deletion_enabled']);self.assertEqual(len(list(self.repo.glob('*.tar.gz.gpg'))),8)
    def test_korean_day_boundary(self):
        dt=datetime.fromisoformat('2026-10-19T15:01:00+00:00')
        self.success(dt)
        self.assertEqual(len(b.retention_plan(self.repo)['keep']),1)
    def test_partial_not_a_success(self):
        (self.repo/'something.partial').write_text('partial')
        self.assertEqual(b.retention_plan(self.repo)['keep'],[])
    def test_stale_prepared_receipt(self):
        (self.source/'PREPARED.json').write_text(json.dumps({'complete':True,'host':self.c['host'],'prepared_at':(self.now-timedelta(days=1)).isoformat()}))
        with patch.object(b,'guard'):
            with self.assertRaisesRegex(b.BackupError,'stale'):b.transfer(self.c)
    def test_symlinks_rejected_from_bundle(self):
        (self.source/'link').symlink_to(self.password)
        with self.assertRaises(b.BackupError):b.build_bundle(self.source,self.root/'archive')
    def test_malicious_archive_does_not_escape(self):
        archive=self.root/'bad.tar.gz'
        with tarfile.open(archive,'w:gz') as t:
            i=tarfile.TarInfo('../escape');i.size=4;t.addfile(i,io.BytesIO(b'evil'))
        with self.assertRaises(b.BackupError):b.verify_bundle(archive,self.root/'restore')
        self.assertFalse((self.root/'escape').exists())
    def test_restore_never_overwrites(self):
        archive=self.root/'good.tar.gz';b.build_bundle(self.source,archive)
        dest=self.root/'restore';dest.mkdir()
        with self.assertRaisesRegex(b.BackupError,'overwrite'):b.verify_bundle(archive,dest)
    def test_corrupt_payload_rejected(self):
        archive=self.root/'bad.tar.gz'
        with tarfile.open(archive,'w:gz') as t:
            for name,data in [('payload/file',b'changed'),('manifest.json',json.dumps({'file':{'sha256':'0'*64,'size':7,'mode':0o600}}).encode())]:
                i=tarfile.TarInfo(name);i.size=len(data);i.mode=0o600;t.addfile(i,io.BytesIO(data))
        with self.assertRaisesRegex(b.BackupError,'verification'):b.verify_bundle(archive)
    @unittest.skipUnless(shutil.which('gpg'),'gpg required')
    def test_real_encryption_transfer_restore_and_same_day_skip(self):
        with patch.object(b,'guard'):
            record=b.transfer(self.c)
            again=b.transfer(self.c)
        self.assertEqual(record['status'],'verified');self.assertEqual(again['status'],'already-successful-today')
        archive=self.repo/(record['run_id']+'.tar.gz.gpg')
        self.assertNotIn('가상'.encode(),archive.read_bytes())
        dest=self.root/'recovered';b.restore(archive,self.password,dest)
        self.assertEqual((dest/'hello.txt').read_bytes(),(self.source/'hello.txt').read_bytes())
        self.assertEqual(len(list(self.repo.glob('*.success.json'))),1)
    def test_failed_encryption_keeps_existing_success(self):
        existing=self.success(self.now-timedelta(days=1))
        with patch.object(b,'guard'),patch.object(b,'crypt',side_effect=b.BackupError('simulated encryption failure')):
            with self.assertRaises(b.BackupError):b.transfer(self.c)
        self.assertEqual(b.retention_plan(self.repo)['keep'],[existing])
    def test_space_budget_keeps_existing_success(self):
        existing=self.success(self.now-timedelta(days=1));self.c['max_repository_bytes']=1
        def fake(src,dst,*args,**kw):shutil.copyfile(src,dst)
        with patch.object(b,'guard'),patch.object(b,'crypt',side_effect=fake):
            with self.assertRaisesRegex(b.BackupError,'budget'):b.transfer(self.c)
        self.assertEqual(b.retention_plan(self.repo)['keep'],[existing])

PREP_SPEC=importlib.util.spec_from_file_location('prepare',Path(__file__).resolve().parents[1]/'scripts/prepare.py')
prep=importlib.util.module_from_spec(PREP_SPEC);PREP_SPEC.loader.exec_module(prep)

class CollectorGateTests(unittest.TestCase):
    def test_non_root_rejected_before_any_service_command(self):
        with patch.object(prep.os,'geteuid',return_value=1000),patch.object(prep,'quiet') as command:
            with self.assertRaisesRegex(prep.BackupError,'root'):prep.prepare({},'/not-used')
            command.assert_not_called()
    def test_legacy_stop_approval_rejected_without_reading_or_service_command(self):
        with patch.object(prep.os,'geteuid',return_value=0),patch.object(prep,'quiet') as command:
            with self.assertRaisesRegex(prep.BackupError,'disabled'):prep.prepare({},'/old-approval')
            command.assert_not_called()

DAILY_SPEC=importlib.util.spec_from_file_location('daily',Path(__file__).resolve().parents[1]/'scripts/daily.py')
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
daily_module=importlib.util.module_from_spec(DAILY_SPEC);DAILY_SPEC.loader.exec_module(daily_module)

class DailyTests(unittest.TestCase):
    setUp = SafetyTests.setUp
    tearDown = SafetyTests.tearDown
    success = SafetyTests.success
    def test_daily_does_not_stop_mail_again_after_success(self):
        self.success(self.now)
        with patch.object(daily_module,'guard'),patch.object(daily_module,'prepare') as collect:
            result=daily_module.daily(self.c)
            self.assertEqual(result['status'],'already-successful-today');collect.assert_not_called()
    def test_daily_collection_failure_does_not_transfer(self):
        with patch.object(daily_module,'guard'),patch.object(daily_module,'prepare',side_effect=b.BackupError('online mailbox export failed')),patch.object(daily_module,'transfer') as upload:
            with self.assertRaises(RuntimeError):daily_module.daily(self.c)
            upload.assert_not_called()
    def test_daily_uses_fresh_prepared_directory(self):
        with patch.object(daily_module,'guard'),patch.object(daily_module,'prepare',return_value=self.source),patch.object(daily_module,'transfer',return_value={'status':'verified'}) as upload:
            daily_module.daily(self.c)
            self.assertEqual(upload.call_args.args[0]['prepared'],str(self.source))

if __name__=='__main__':unittest.main()
