import importlib.util
import io
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
import g1_backup as core
import operations as ops
import daily
import enable_daily as install


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.repo=self.root/'share/server/g1';self.repo.mkdir(parents=True)
        self.work=self.root/'state/work';self.work.mkdir(mode=0o700,parents=True)
        self.key=self.root/'secure/key';self.key.parent.mkdir(mode=0o700)
        self.key.write_text('synthetic-fixture-only');self.key.chmod(0o600)
        self.c={'host':'synthetic-host','repository':str(self.repo),'repository_id':'synthetic-repository-id',
                'work':str(self.work),'prepared':str(self.work.parent/'prepared'),'password_file':str(self.key)}
        self.guard=patch.object(ops,'guard');self.guard.start()
        self.deployment=patch.object(install,'DEPLOYMENT_ROOT',install.ROOT);self.deployment.start()
        self.now=datetime.now(core.KST)

    def tearDown(self):self.deployment.stop();self.guard.stop();self.tmp.cleanup()

    def approve(self):
        core.atomic_json(ops.policy_path(self.c),ops.new_policy(self.c))
        ops.policy_path(self.c).chmod(0o600)

    def success(self, days, suffix='1234abcd', minutes=0):
        dt=self.now-timedelta(days=days,minutes=minutes)
        rid=dt.strftime('%Y%m%dT%H%M%S%f')+'-'+suffix
        folder=self.repo/(rid+'.objects');folder.mkdir()
        contents={'_manifest.json.gpg':b'synthetic-encrypted-manifest',
                  'projects/fixture/files.tar.gz.gpg':b'synthetic-encrypted-data'}
        objects={}
        for name,data in contents.items():
            path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            objects[name]={'bytes':len(data),'sha256':core.digest(path)}
        record={'schema':2,'layout':'objects-v2','status':'verified','run_id':rid,
                'day':dt.date().isoformat(),'finished_at':dt.isoformat(),'objects':objects,
                'bytes':sum(v['bytes'] for v in objects.values())}
        core.atomic_json(self.repo/(rid+'.success.json'),record)
        return record

    def current_prepared(self):
        path=self.work.parent/'prepared-20261020T040000-1234abcd'
        path.mkdir(mode=0o700)
        core.atomic_json(path/'PREPARED.json',{'complete':True,'host':self.c['host']})
        (path/'synthetic-dump').write_bytes(b'fixture')
        return path

    def test_no_consent_no_prune_or_staging_cleanup(self):
        records=[self.success(i) for i in range(7)]
        path=self.current_prepared()
        self.assertEqual(ops.prune_after_success(self.c,records[0])['deleted'],[])
        self.assertFalse(ops.cleanup_new_prepared(self.c,path,set(),records[0])['deleted'])
        self.assertTrue(path.exists());self.assertEqual(len(list(self.repo.glob('*.success.json'))),7)

    def test_keep_five_dates_and_remove_duplicates_preserving_other_data(self):
        self.approve();records=[self.success(i) for i in range(7)]
        duplicate=self.success(0,'abcdef12',1)
        partial=self.repo/'failed.partial';partial.mkdir();(partial/'piece').write_bytes(b'keep')
        elsewhere=self.repo.parent/'other-program';elsewhere.mkdir();(elsewhere/'backup').write_text('keep')
        old=self.work.parent/'prepared-20261019T040000-1234abcd';old.mkdir();(old/'data').write_text('keep')
        result=ops.prune_after_success(self.c,records[0])
        self.assertEqual(set(result['deleted']),{records[5]['run_id'],records[6]['run_id'],duplicate['run_id']})
        self.assertEqual(core.retention_plan(self.repo)['keep'],[r['run_id'] for r in records[:5]])
        self.assertTrue((partial/'piece').exists());self.assertTrue((elsewhere/'backup').exists());self.assertTrue((old/'data').exists())

    def test_corrupt_survivor_prevents_all_deletion(self):
        self.approve();records=[self.success(i) for i in range(7)]
        p=self.repo/(records[2]['run_id']+'.objects')/'_manifest.json.gpg'
        p.write_bytes(b'X'*p.stat().st_size)
        with self.assertRaises(core.BackupError):ops.prune_after_success(self.c,records[0])
        self.assertEqual(len(list(self.repo.glob('*.success.json'))),7)

    def test_corrupt_candidate_prevents_all_deletion(self):
        self.approve();records=[self.success(i) for i in range(7)]
        p=self.repo/(records[6]['run_id']+'.objects')/'_manifest.json.gpg'
        p.write_bytes(b'X'*p.stat().st_size)
        with self.assertRaises(core.BackupError):ops.prune_after_success(self.c,records[0])
        self.assertEqual(len(list(self.repo.glob('*.success.json'))),7)

    def test_unknown_file_or_symlink_prevents_all_deletion(self):
        self.approve();records=[self.success(i) for i in range(6)]
        unknown=self.repo/(records[5]['run_id']+'.objects')/'unknown'
        unknown.write_text('preserve')
        with self.assertRaises(core.BackupError):ops.prune_after_success(self.c,records[0])
        unknown.unlink();unknown.symlink_to(self.key)
        with self.assertRaises(core.BackupError):ops.prune_after_success(self.c,records[0])
        self.assertEqual(len(list(self.repo.glob('*.success.json'))),6)

    def test_symlink_success_record_rejected(self):
        self.approve();record=self.success(0)
        path=self.repo/(record['run_id']+'.success.json');target=self.root/'foreign-record'
        path.rename(target);path.symlink_to(target)
        with self.assertRaises(core.BackupError):ops.prune_after_success(self.c,record)

    def test_different_repository_and_world_readable_policy_rejected(self):
        self.approve();other=dict(self.c,repository_id='different')
        with self.assertRaises(core.BackupError):ops.approval(other)
        ops.policy_path(self.c).chmod(0o644)
        with self.assertRaises(core.BackupError):ops.approval(self.c)

    def test_failed_or_uncommitted_result_never_prunes(self):
        self.approve();record=self.success(0)
        for bad in ({'status':'failed'},dict(record,bytes=-1)):
            with self.assertRaises(core.BackupError):ops.prune_after_success(self.c,bad)
        self.assertTrue((self.repo/(record['run_id']+'.success.json')).exists())

    def test_crash_does_not_advertise_partial_deletion_as_success(self):
        self.approve();records=[self.success(i) for i in range(6)]
        original=Path.unlink
        def fail_object(path,*args,**kwargs):
            if path.suffix=='.gpg':raise OSError('synthetic interrupted deletion')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',fail_object):
            with self.assertRaises(OSError):ops.prune_after_success(self.c,records[0])
        self.assertEqual(len(core.retention_plan(self.repo)['keep']),5)
        self.assertTrue((self.repo/(records[5]['run_id']+'.objects')).exists())

    def test_cleanup_only_current_new_verified_staging(self):
        self.approve();record=self.success(0);path=self.current_prepared()
        with self.assertRaises(core.BackupError):ops.cleanup_new_prepared(self.c,path,{path.name},record)
        self.assertTrue(path.exists())
        with self.assertRaises(core.BackupError):ops.cleanup_new_prepared(self.c,path,set(),{'status':'failed'})
        result=ops.cleanup_new_prepared(self.c,path,set(),record)
        self.assertTrue(result['deleted']);self.assertFalse(path.exists())
        self.assertTrue((self.repo/(record['run_id']+'.objects')).exists())

    def test_daily_failed_collection_never_calls_prune_and_records_failure(self):
        with patch.object(daily,'guard'),patch.object(daily,'private_file'),patch.object(daily,'prepare',side_effect=core.BackupError('synthetic failure')),patch.object(daily,'prune_after_success') as prune:
            with self.assertRaises(core.BackupError):daily.run_with_status(self.c)
            prune.assert_not_called()
        self.assertEqual(json.loads((self.work/'daily-status.json').read_text())['status'],'failed')
        self.assertTrue((self.work/'last-daily-failure.json').exists())

    def test_daily_failed_transfer_never_calls_prune(self):
        path=self.current_prepared()
        with patch.object(daily,'guard'),patch.object(daily,'private_file'),patch.object(daily,'prepare',return_value=path),patch.object(daily,'transfer',side_effect=core.BackupError('synthetic transfer failure')),patch.object(daily,'prune_after_success') as prune:
            with self.assertRaises(core.BackupError):daily.run_with_status(self.c)
            prune.assert_not_called()
        self.assertTrue(path.exists())

    def test_approved_daily_creates_verifies_then_prunes_and_cleans_only_new_staging(self):
        self.approve()
        old_records=[self.success(i) for i in range(1,6)]
        previous=self.work.parent/'prepared-20261019T040000-1234abcd';previous.mkdir()
        with patch.object(daily,'guard'),patch.object(daily,'prepare',side_effect=lambda c:self.current_prepared()),patch.object(daily,'transfer',side_effect=lambda c:self.success(0)):
            result=daily.run_with_status(self.c)
        self.assertEqual(result['maintenance']['deleted'],[old_records[-1]['run_id']])
        self.assertTrue(result['staging_cleanup']['deleted']);self.assertTrue(previous.exists())
        self.assertEqual(json.loads((self.work/'daily-status.json').read_text())['status'],'finished')

    def test_review_invocation_can_disable_even_previously_approved_maintenance(self):
        self.approve()
        for i in range(1,6):self.success(i)
        with patch.object(daily,'guard'),patch.object(daily,'prepare',side_effect=lambda c:self.current_prepared()),patch.object(daily,'transfer',side_effect=lambda c:self.success(0)),patch.object(daily,'prune_after_success') as prune,patch.object(daily,'cleanup_new_prepared') as clean:
            result=daily.daily(self.c,maintenance=False)
        prune.assert_not_called();clean.assert_not_called()
        self.assertEqual(len(core.retention_plan(self.repo)['delete_candidates']),1)
        self.assertFalse(result['staging_cleanup']['deleted'])

    def inspection_receipts(self):
        record=self.success(0)
        pg={'status':'shared-snapshot-postgresql-restore-verified','run_id':record['run_id'],
            'cipher_hashes_verified':True,'source_container_states_unchanged':True}
        key={'status':'offsite-key-readback-and-shared-manifest-verified','run_id':record['run_id'],
             'shared_encrypted_manifest_decrypted_with_readback_key':True,'offsite_storage_attested_by_operator':True}
        for name,data in (('last-postgres-verification.json',pg),('offsite-key-verification.json',key)):
            core.atomic_json(self.work/name,data);(self.work/name).chmod(0o600)
        self.c.update(reserve_bytes=0,max_repository_bytes=10**9,prepare_required_free_bytes=0)
        return record

    def inspection_private(self,path):
        return self.key if str(path)=='/etc/g1-backup/smb-credentials' else core.private_file(path)

    def test_root_inspection_reports_actual_candidates_without_writing(self):
        self.inspection_receipts()
        for i in range(1,7):self.success(i)
        before=sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*'))
        with patch.object(install,'guard'),patch.object(install,'private_file',side_effect=self.inspection_private):
            report=install.inspect(self.c)
        self.assertEqual(len(report['keep']),5);self.assertEqual(len(report['delete_candidates_now']),2)
        self.assertEqual(before,sorted(str(p.relative_to(self.root)) for p in self.root.rglob('*')))

    def test_inspection_refuses_mismatching_key_receipt(self):
        self.inspection_receipts()
        path=self.work/'offsite-key-verification.json';key=json.loads(path.read_text());key['run_id']='another'
        path.write_text(json.dumps(key))
        with patch.object(install,'guard'),patch.object(install,'private_file',side_effect=self.inspection_private):
            with self.assertRaises(core.BackupError):install.inspect(self.c)
        self.assertEqual(len(list(self.repo.glob('*.success.json'))),1)

    def test_inspection_refuses_capacity_shortage_without_pruning(self):
        record=self.inspection_receipts();self.c['max_repository_bytes']=record['bytes']
        with patch.object(install,'guard'),patch.object(install,'private_file',side_effect=self.inspection_private):
            with self.assertRaises(core.BackupError):install.inspect(self.c)
        self.assertEqual(len(list(self.repo.glob('*.success.json'))),1)

    def test_loaded_dropin_conflict_is_not_overwritten(self):
        units=self.root/'units';units.mkdir()
        result=subprocess.CompletedProcess([],0,'FragmentPath=\nDropInPaths=/etc/systemd/system/g1-backup.service.d/operator.conf\n','')
        with patch.object(install,'UNIT_DIR',units),patch.object(install.subprocess,'run',return_value=result):
            with self.assertRaises(core.BackupError):install.check_unit_conflicts({'g1-backup.service':'new'})
        self.assertEqual(list(units.iterdir()),[])

    def test_default_install_mode_does_not_read_secrets_or_write(self):
        with patch.object(sys,'argv',['enable_daily.py']),patch.object(install,'read_config') as read,patch.object(install,'apply') as apply,patch('sys.stdout'):
            self.assertEqual(install.main(),0);read.assert_not_called();apply.assert_not_called()

    def test_install_requires_root_and_tty_before_config_read(self):
        for uid,tty in ((1000,True),(0,False)):
            with patch.object(sys,'argv',['enable_daily.py','--apply']),patch.object(install.os,'geteuid',return_value=uid),patch.object(install.sys.stdin,'isatty',return_value=tty),patch.object(install,'read_config') as read,patch('sys.stdout'):
                self.assertEqual(install.main(),1);read.assert_not_called()

    def test_declined_consent_installs_nothing(self):
        units=self.root/'units';units.mkdir()
        with patch.object(install,'UNIT_DIR',units),patch.object(install,'unit_texts',return_value={'example.service':'fixture'}),patch.object(install,'inspect',return_value={'status':'plan'}),patch.object(install,'check_unit_conflicts'),patch.object(install,'command') as cmd,patch('builtins.input',return_value='no'),patch('sys.stdout'):
            with self.assertRaises(core.BackupError):install.apply(self.c)
        self.assertFalse(ops.policy_path(self.c).exists());self.assertEqual(list(units.iterdir()),[])
        self.assertEqual(cmd.call_count,1);self.assertEqual(cmd.call_args.args[0][:2],['systemd-analyze','verify'])

    def test_approved_install_enables_without_stop_unmount_or_immediate_backup(self):
        units=self.root/'units';units.mkdir()
        texts={install.MOUNT_UNIT:'fixture mount','g1-backup.service':'fixture service','g1-backup.timer':'fixture timer'}
        with patch.object(install,'UNIT_DIR',units),patch.object(install,'unit_texts',return_value=texts),patch.object(install,'inspect',return_value={}),patch.object(install,'check_unit_conflicts'),patch.object(install,'guard'),patch.object(install,'command',return_value='active') as cmd,patch('builtins.input',return_value=ops.CONSENT),patch('sys.stdout'):
            result=install.apply(self.c)
        calls=[c.args[0] for c in cmd.call_args_list]
        self.assertIn(['systemctl','start','g1-backup.timer'],calls)
        self.assertFalse(any(any(x in c for x in ('stop','restart','umount','remount','g1-backup.service')) for c in calls))
        self.assertEqual(set(p.name for p in units.iterdir()),set(texts));self.assertTrue(ops.approval(self.c))
        self.assertFalse(result['backups_deleted_by_installer']);self.assertFalse(result['mount_reboot_tested'])

    def test_short_y_accepts_without_rewriting_existing_long_form_approval(self):
        self.approve();before=ops.policy_path(self.c).read_bytes()
        units=self.root/'units';units.mkdir()
        texts={'g1-backup.service':'fixture service','g1-backup.timer':'fixture timer'}
        for reply in ('y','Y'):
            with self.subTest(reply=reply),patch.object(install,'UNIT_DIR',units),patch.object(install,'unit_texts',return_value=texts),patch.object(install,'inspect',return_value={}),patch.object(install,'check_unit_conflicts'),patch.object(install,'guard'),patch.object(install,'command',return_value='active'),patch('builtins.input',return_value=reply),patch('sys.stdout'):
                self.assertEqual(install.apply(self.c)['status'],'daily-backup-enabled')
            self.assertEqual(ops.policy_path(self.c).read_bytes(),before)
            self.assertEqual(ops.approval(self.c)['operator_consent'],ops.CONSENT)

    def test_empty_or_negative_reply_never_installs_or_stores_approval(self):
        units=self.root/'units';units.mkdir()
        for reply in ('','n','N','no'):
            with self.subTest(reply=reply),patch.object(install,'UNIT_DIR',units),patch.object(install,'unit_texts',return_value={'example.service':'fixture'}),patch.object(install,'inspect',return_value={}),patch.object(install,'check_unit_conflicts'),patch.object(install,'command') as cmd,patch('builtins.input',return_value=reply),patch('sys.stdout'):
                with self.assertRaises(core.BackupError):install.apply(self.c)
            self.assertFalse(ops.policy_path(self.c).exists());self.assertEqual(list(units.iterdir()),[])
            self.assertEqual(cmd.call_count,1);self.assertEqual(cmd.call_args.args[0][:2],['systemd-analyze','verify'])

    def test_unit_conflict_never_overwrites(self):
        units=self.root/'units';units.mkdir();path=units/'g1-backup.service';path.write_text('operator existing')
        with patch.object(install,'UNIT_DIR',units):
            with self.assertRaises(core.BackupError):install.check_unit_conflicts({'g1-backup.service':'new'})
        self.assertEqual(path.read_text(),'operator existing')

    def test_foreign_mount_config_not_rendered_into_units(self):
        with self.assertRaises(core.BackupError):install.unit_texts(self.c)

    def legacy_setup_config(self):
        # Immutable source from when the operator first created backup.json.
        path=ROOT/'tests/fixtures/legacy-backup.example.json'
        c=json.loads(path.read_text())
        c['smb_version']='2.0'  # setup_router added this explicitly at that stage.
        return c

    def test_historical_setup_config_uses_transfer_default_without_mutation(self):
        c=self.legacy_setup_config();before=dict(c)
        self.assertNotIn('archive_layout',c)
        units=install.unit_texts(c)
        self.assertEqual(units,install.unit_texts(dict(c,archive_layout='objects-v2')))
        self.assertEqual(c,before)

    def test_explicit_invalid_values_remain_rejected_with_names_only(self):
        original=self.legacy_setup_config()
        for key,value in (('archive_layout','bundle-v1'),('archive_layout',None),
                          ('smb_version','3.0'),('work','/synthetic-sensitive-path')):
            with self.subTest(key=key,value=value):
                with self.assertRaises(core.BackupError) as error:
                    install.unit_texts(dict(original,**{key:value}))
                self.assertIn(key,str(error.exception))
                if value:self.assertNotIn(value,str(error.exception))

    def test_legacy_configuration_reaches_review_without_any_installation(self):
        c=self.legacy_setup_config();progress={}
        with patch.object(install,'inspect',return_value={'status':'synthetic-plan'}),patch.object(install,'check_unit_conflicts'),patch.object(install,'command') as cmd,patch('builtins.input',return_value='no'),patch('sys.stdout'):
            with self.assertRaises(core.BackupError):install.apply(c,progress)
        self.assertEqual(progress,{'phase':'awaiting-consent','installation_started':False})
        self.assertEqual(cmd.call_count,1);self.assertEqual(cmd.call_args.args[0][:2],['systemd-analyze','verify'])

    def test_cli_reports_preinstall_failure_without_partial_install_warning(self):
        c=dict(self.legacy_setup_config(),archive_layout='bundle-v1');out=io.StringIO()
        with patch.object(sys,'argv',['enable_daily.py','--apply']),patch.object(install.os,'geteuid',return_value=0),patch.object(install.sys.stdin,'isatty',return_value=True),patch.object(install,'private_file'),patch.object(install,'read_config',return_value=c),patch.object(install,'inspect') as inspect,patch.object(install,'command') as cmd,patch('sys.stdout',out):
            self.assertEqual(install.main(),1)
        result=json.loads(out.getvalue())
        self.assertEqual(result['phase'],'configuration-validation');self.assertFalse(result['installation_started'])
        self.assertIn('설치 시작 전',result['note']);inspect.assert_not_called();cmd.assert_not_called()


if __name__=='__main__':unittest.main()
