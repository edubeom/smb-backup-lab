import importlib.util
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
import g1_backup as core
import object_backup as objects

spec=importlib.util.spec_from_file_location('component_prepare',ROOT/'scripts/prepare.py')
prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)


class CollectorComponentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.projects=self.root/'project';self.projects.mkdir()
        self.out=self.root/'staging';self.out.mkdir()

    def tearDown(self):self.tmp.cleanup()

    def test_projects_are_separate_and_include_env_git_not_cache(self):
        for name in ('alpha','beta'):
            p=self.projects/name;p.mkdir()
            (p/'source.txt').write_text(name)
            (p/'.env').write_text('SYNTHETIC_FIXTURE=yes')
            (p/'.git').mkdir();(p/'.git/HEAD').write_text('synthetic-history')
            (p/'node_modules').mkdir();(p/'node_modules/cache').write_text('rebuildable')
        prepare.collect_projects(self.out,self.projects)
        for name in ('alpha','beta'):
            with tarfile.open(self.out/f'projects/{name}/files.tar.gz') as t:
                names=t.getnames()
                self.assertIn(name+'/.env',names);self.assertIn(name+'/.git/HEAD',names)
                self.assertNotIn(name+'/node_modules/cache',names)
                self.assertNotIn(('beta' if name=='alpha' else 'alpha')+'/source.txt',names)
                self.assertEqual(t.extractfile(name+'/source.txt').read().decode(),name)

    def test_top_symlink_preserved_without_copying_external_data(self):
        outside=self.root/'outside';outside.mkdir();(outside/'secret').write_text('synthetic-external')
        (self.projects/'link').symlink_to(outside,target_is_directory=True)
        prepare.collect_projects(self.out,self.projects)
        with tarfile.open(self.out/'projects/_root-entries.tar.gz') as t:
            self.assertTrue(t.getmember('link').issym())
            self.assertNotIn('link/secret',t.getnames())

    def test_sqlite_live_file_excluded_and_separate_dump_retained(self):
        p=self.projects/'demo-notes';p.mkdir()
        (p/'Story.db').write_text('synthetic-live-db');(p/'Story.db-wal').write_text('synthetic-wal')
        target=self.out/'projects/demo-notes/databases';target.mkdir(parents=True)
        (target/'Story.db').write_text('synthetic-consistent-dump')
        prepare.collect_projects(self.out,self.projects)
        with tarfile.open(self.out/'projects/demo-notes/files.tar.gz') as t:
            self.assertNotIn('demo-notes/Story.db',t.getnames())
            self.assertNotIn('demo-notes/Story.db-wal',t.getnames())
        self.assertEqual((target/'Story.db').read_text(),'synthetic-consistent-dump')

    def test_mail_project_requires_consistent_export(self):
        mail=self.projects/'demo-mail';mail.mkdir()
        with self.assertRaises(RuntimeError):prepare.collect_projects(self.out,self.projects,mail)

    def test_ssh_cloudflare_and_root_operator_settings_included(self):
        fs=self.root/'filesystem'
        paths=['etc/ssh/sshd_config','etc/ssh/ssh_host_test_key','etc/cloudflared/config.yml',
               'etc/netplan/network.yaml','etc/systemd/system/example.service','etc/fstab',
               'home/operator/.ssh/authorized_keys','home/operator/.cloudflared/credential.json',
               'root/.ssh/config']
        for name in paths:
            p=fs/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('synthetic-config')
        prepare.collect_system(self.out,fs)
        with tarfile.open(self.out/'system/etc.tar.gz') as t:
            for name in paths[:6]:self.assertIn(name,t.getnames())
        with tarfile.open(self.out/'system/operator-config.tar.gz') as t:
            for name in paths[6:]:self.assertIn(name,t.getnames())

    def test_staging_inside_source_rejected(self):
        with self.assertRaises(RuntimeError):prepare.collect_projects(self.projects/'bad',self.projects)


class ObjectTransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'prepared';self.source.mkdir(mode=0o700)
        for project in ('alpha','beta'):
            p=self.source/f'projects/{project}/files.tar.gz';p.parent.mkdir(parents=True)
            p.write_bytes(('synthetic-'+project).encode())
        (self.source/'PREPARED.json').write_text(json.dumps({'complete':True,'host':socket.gethostname(),
            'prepared_at':datetime.now(core.KST).isoformat()}))
        self.repo=self.root/'repo';self.repo.mkdir()
        self.key=self.root/'key';self.key.write_text('synthetic-ephemeral-fixture\n');self.key.chmod(0o600)
        self.c={'repository':str(self.repo),'work':str(self.root/'work'),'prepared':str(self.source),
                'password_file':str(self.key),'host':socket.gethostname(),'reserve_bytes':1,
                'max_repository_bytes':10**8,'archive_layout':'objects-v2'}

    def tearDown(self):self.tmp.cleanup()

    @staticmethod
    def fake_crypt(source,target,*args,**kwargs):shutil.copyfile(source,target)

    def fake_transfer(self):
        with patch.object(objects,'guard'),patch.object(objects,'crypt',side_effect=self.fake_crypt):
            return objects.transfer_objects(self.c)

    def test_separate_objects_committed_as_one_daily_success(self):
        record=self.fake_transfer();rid=record['run_id']
        self.assertTrue((self.repo/(rid+'.objects/projects/alpha/files.tar.gz.gpg')).is_file())
        self.assertTrue((self.repo/(rid+'.objects/projects/beta/files.tar.gz.gpg')).is_file())
        self.assertEqual(len(list(self.repo.glob('*.success.json'))),1)
        self.assertEqual(core.retention_plan(self.repo)['keep'],[rid])

    def test_mid_transfer_failure_never_commits_success(self):
        self.fake_transfer()
        # Model a fresh repository to test an interrupted first run.
        other=self.root/'failed-repo';other.mkdir();self.c['repository']=str(other)
        calls=0
        def fail(src,dst,*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==3:raise core.BackupError('synthetic transfer interruption')
            shutil.copyfile(src,dst)
        with patch.object(objects,'guard'),patch.object(objects,'crypt',side_effect=fail):
            with self.assertRaises(core.BackupError):objects.transfer_objects(self.c)
        self.assertEqual(list(other.glob('*.success.json')),[])
        self.assertEqual(len(list(self.repo.glob('*.success.json'))),1)

    def test_only_requested_project_restored(self):
        record=self.fake_transfer();dest=self.root/'restore'
        with patch.object(objects,'crypt',side_effect=self.fake_crypt):
            result=objects.restore_objects(self.repo/(record['run_id']+'.success.json'),self.key,dest,'alpha')
        self.assertEqual(result['file_count'],1)
        self.assertEqual((dest/'projects/alpha/files.tar.gz').read_bytes(),b'synthetic-alpha')
        self.assertFalse((dest/'projects/beta').exists())

    def test_only_requested_system_component_restored(self):
        path=self.source/'system/etc.tar.gz';path.parent.mkdir();path.write_bytes(b'synthetic-settings')
        record=self.fake_transfer();dest=self.root/'system-restore'
        with patch.object(objects,'crypt',side_effect=self.fake_crypt):
            result=objects.restore_objects(self.repo/(record['run_id']+'.success.json'),self.key,dest,
                                           component='system/etc.tar.gz')
        self.assertEqual(result['file_count'],1)
        self.assertEqual((dest/'system/etc.tar.gz').read_bytes(),b'synthetic-settings')
        self.assertFalse((dest/'projects').exists())

    def test_corrupted_object_is_rejected(self):
        record=self.fake_transfer();rid=record['run_id']
        path=self.repo/(rid+'.objects/projects/alpha/files.tar.gz.gpg')
        path.write_bytes(b'x'*path.stat().st_size)
        with patch.object(objects,'crypt',side_effect=self.fake_crypt):
            with self.assertRaises(core.BackupError):objects.restore_objects(self.repo/(rid+'.success.json'),self.key,self.root/'restore','alpha')
        self.assertFalse((self.root/'restore').exists())

    def test_missing_component_invalidates_daily_success(self):
        record=self.fake_transfer();rid=record['run_id']
        (self.repo/(rid+'.objects/projects/beta/files.tar.gz.gpg')).unlink()
        with self.assertRaises(core.BackupError):core.retention_plan(self.repo)

    def test_repository_quota_counts_nested_objects(self):
        partial=self.repo/'old.partial';partial.mkdir();(partial/'old.gpg').write_bytes(b'x'*1024)
        self.c['max_repository_bytes']=1000
        with patch.object(objects,'guard'),patch.object(objects,'crypt',side_effect=self.fake_crypt):
            with self.assertRaises(core.BackupError):objects.transfer_objects(self.c)
        self.assertEqual(list(self.repo.glob('*.success.json')),[])
        self.assertEqual((partial/'old.gpg').stat().st_size,1024)

    def test_path_escape_and_smb_symlink_rejected(self):
        for name in ('../escape','/absolute','a/../escape','a//b','a\\b'):
            with self.assertRaises(core.BackupError):objects.safe_relative(name)
        (self.repo/'link').symlink_to(self.root)
        with self.assertRaises(core.BackupError):objects.object_path(self.repo,'link/escape')

    @unittest.skipUnless(shutil.which('gpg'),'gpg required')
    def test_real_encryption_and_selective_project_restore(self):
        with patch.object(objects,'guard'):record=objects.transfer_objects(self.c)
        rid=record['run_id'];snapshot=self.repo/(rid+'.success.json')
        encrypted=self.repo/(rid+'.objects/projects/alpha/files.tar.gz.gpg')
        self.assertNotIn(b'synthetic-alpha',encrypted.read_bytes())
        result=objects.restore_objects(snapshot,self.key,self.root/'real-restore','alpha')
        self.assertEqual(result['file_count'],1)
        self.assertEqual((self.root/'real-restore/projects/alpha/files.tar.gz').read_bytes(),b'synthetic-alpha')


sys.path.insert(0,str(ROOT/'scripts'))
import first_backup as first


class FirstBackupGateTests(unittest.TestCase):
    def test_no_interruption_or_input_and_restores_selected_files(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);work=root/'work';work.mkdir()
            project_tar=root/'project.tar.gz';system_tar=root/'system.tar.gz'
            for archive,files in [(project_tar,{'g1-backup/README.md':b'synthetic-project'}),
                                  (system_tar,{'etc/ssh/sshd_config':b'synthetic-ssh','etc/cloudflared/config.yml':b'synthetic-cloudflare'})]:
                with tarfile.open(archive,'w:gz') as t:
                    for name,data in files.items():
                        m=tarfile.TarInfo(name);m.size=len(data);t.addfile(m,io.BytesIO(data))
            c={'repository':str(root/'repo'),'work':str(work),'password_file':'not-read-in-fixture'}
            rid='20261020T040000000000-1234abcd'
            seen=[]
            def backup(config,maintenance=True):
                self.assertFalse(maintenance)
                seen.append(config)
                return {'backup':{'status':'verified','run_id':rid}}
            def restore(snapshot,key,dest,project=None,component=None):
                dest=Path(dest)
                relative='projects/g1-backup/files.tar.gz' if project else 'system/etc.tar.gz'
                target=dest/relative;target.parent.mkdir(parents=True)
                shutil.copyfile(project_tar if project else system_tar,target)
            with patch.object(first.os,'geteuid',return_value=0),patch.object(first.sys.stdin,'isatty',return_value=True),patch.object(first,'preflight',return_value={}),patch.object(first,'retention_plan',return_value={'keep':[]}),patch('builtins.input',side_effect=AssertionError('no stop consent prompt allowed')),patch.object(first,'daily',side_effect=backup),patch.object(first,'restore_objects',side_effect=restore),patch.object(first,'check_restored_mail',return_value={'mailbox_count':1}) as mail_check,patch('sys.stdout'):
                result=first.run(c)
            mail_check.assert_called_once()
            self.assertEqual(seen,[c])
            self.assertEqual(list(work.glob('first-mail-approval-*')),[])
            self.assertFalse(result['services_interrupted'])
            self.assertFalse(result['service_restore_validated'])
            restored=Path(result['restore_path'])
            self.assertEqual((restored/'restored-ssh-sshd_config').read_bytes(),b'synthetic-ssh')
            self.assertEqual((restored/'restored-cloudflared-config.yml').read_bytes(),b'synthetic-cloudflare')

    def test_nonroot_never_runs_preflight_or_daily(self):
        with patch.object(first.os,'geteuid',return_value=1000),patch.object(first,'preflight') as preflight,patch.object(first,'daily') as run:
            with self.assertRaises(RuntimeError):first.run({})
            preflight.assert_not_called();run.assert_not_called()

    def test_online_collection_failure_does_not_restore_or_create_approval(self):
        with tempfile.TemporaryDirectory() as td:
            c={'repository':td,'work':td}
            with patch.object(first.os,'geteuid',return_value=0),patch.object(first.sys.stdin,'isatty',return_value=True),patch.object(first,'preflight',return_value={}),patch.object(first,'daily',side_effect=core.BackupError('queue not empty')),patch.object(first,'restore_objects') as restore,patch('sys.stdout'):
                with self.assertRaisesRegex(RuntimeError,'queue'):first.run(c)
                restore.assert_not_called()
            self.assertEqual(list(Path(td).iterdir()),[])

    def test_probe_reads_back_and_only_cleans_its_own_file(self):
        with tempfile.TemporaryDirectory() as td:
            repo=Path(td);(repo/'existing-backup').write_text('synthetic-original')
            c={'repository':td,'work':td,'password_file':'not-read-in-fixture','max_repository_bytes':50*1024**3}
            with patch.object(first,'guard'),patch.object(first,'private_file'):
                result=first.preflight(c,write_probe=True)
            self.assertEqual(result['shared_write_read_probe'],'passed')
            self.assertEqual([p.name for p in repo.iterdir()],['existing-backup'])
            self.assertEqual((repo/'existing-backup').read_text(),'synthetic-original')

    def test_selected_file_restore_does_not_follow_tar_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            archive=Path(td)/'test.tar.gz'
            with tarfile.open(archive,'w:gz') as t:
                m=tarfile.TarInfo('etc/ssh/sshd_config');m.type=tarfile.SYMTYPE;m.linkname='/outside';t.addfile(m)
            with self.assertRaises(RuntimeError):first.copy_test_member(archive,'etc/ssh/sshd_config',Path(td)/'target')
            self.assertFalse((Path(td)/'target').exists())


if __name__=='__main__':unittest.main()
