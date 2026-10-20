import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from g1_backup import BackupError
import postgres_restore as pg
import verify_postgres as verify

IMAGE='sha256:'+'a'*64

class RestoreSafetyTests(unittest.TestCase):
    def test_connection_strings_and_paths_are_not_database_names(self):
        for name in ('host=example dbname=x','postgresql://example/db','../x','x\ny','',None):
            with self.assertRaises(BackupError):pg.database_name(name)
        self.assertEqual(pg.database_name('synthetic_db-1'),'synthetic_db-1')

    def test_mutable_image_tag_is_rejected(self):
        with self.assertRaises(BackupError):pg.IsolatedPostgres('postgres:16')

    def test_owner_mismatch_cannot_remove_any_container(self):
        instance=pg.IsolatedPostgres(IMAGE);instance.cid='b'*64
        response=json.dumps([{'Id':instance.cid,'Config':{'Labels':{pg.LABEL:'different-run'}}}]).encode()
        with patch.object(instance,'command',return_value=response) as command:
            with self.assertRaisesRegex(BackupError,'ownership'):instance.close()
        self.assertEqual(len(command.call_args_list),1)
        self.assertEqual(command.call_args.args[0][:2],['docker','inspect'])

    def test_bind_port_and_named_volume_are_rejected(self):
        instance=pg.IsolatedPostgres(IMAGE)
        base={'Image':IMAGE,'HostConfig':{'NetworkMode':'none'},'NetworkSettings':{'Ports':{}},'Mounts':[]}
        scenarios=[{'HostConfig':{'NetworkMode':'bridge'}},
                   {'HostConfig':{'NetworkMode':'none','PortBindings':{'5432/tcp':[{}]}}},
                   {'Mounts':[{'Type':'bind','Destination':'/var/lib/postgresql/data','Name':'source'}]},
                   {'Mounts':[{'Type':'volume','Destination':'/var/lib/postgresql/data','Name':'production-volume'}]}]
        for replacement in scenarios:
            with patch.object(instance,'inspect_owned',return_value=dict(base,**replacement)):
                with self.assertRaises(BackupError):instance.check_isolation()

    def test_corrupt_catalog_never_executes_sql(self):
        instance=pg.IsolatedPostgres(IMAGE)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            cases=[{'../escape.dump':'fixture'}, {'databases/postgresql/db-000.dump':'host=source'}]
            for catalog in cases:
                (root/'catalog.json').write_text(json.dumps(catalog))
                with patch.object(instance,'sql') as sql:
                    with self.assertRaises(BackupError):instance.restore(root)
                    sql.assert_not_called()

    def test_dump_omission_never_executes_globals(self):
        instance=pg.IsolatedPostgres(IMAGE)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            (root/'catalog.json').write_text(json.dumps({'databases/postgresql/db-000.dump':'fixture'}))
            with patch.object(instance,'sql') as sql:
                with self.assertRaisesRegex(BackupError,'differ'):instance.restore(root)
                sql.assert_not_called()

    def test_failure_keeps_raw_diagnostic_private(self):
        with tempfile.TemporaryDirectory() as td:
            result=SimpleNamespace(returncode=1,stderr=b'synthetic private SQL value',stdout=b'')
            with patch.object(pg.subprocess,'run',return_value=result):
                with self.assertRaises(BackupError) as error:pg.call(['fixture'],'restore database #1',diagnostics=td)
            self.assertNotIn('synthetic private',str(error.exception))
            files=list(Path(td).iterdir());self.assertEqual(len(files),1)
            self.assertEqual(files[0].read_bytes(),result.stderr)
            self.assertEqual(files[0].stat().st_mode&0o777,0o600)

    def test_nonroot_never_opens_shared_snapshot(self):
        with patch.object(verify.os,'geteuid',return_value=1000),patch.object(verify,'snapshot_record') as read:
            with self.assertRaises(BackupError):verify.verify({},'not-read',True)
            read.assert_not_called()

    def test_snapshot_traversal_never_reads_mount(self):
        with patch.object(verify,'guard') as guard:
            with self.assertRaises(BackupError):verify.snapshot_record({},'../../outside')
            guard.assert_not_called()

    def test_capacity_includes_six_sets_before_pruning(self):
        with tempfile.TemporaryDirectory() as td:
            c={'repository':td,'work':td,'max_repository_bytes':550}
            result=verify.capacity(c,{'bytes':100})
            self.assertTrue(result['five_sets_fit_budget_at_current_size'])
            self.assertFalse(result['six_sets_fit_budget_at_current_size'])
            self.assertFalse(result['backup_deletion_performed'])

    def test_runtime_comparison_excludes_other_restore_test_containers(self):
        rows=[{'id':'source','running':True,'started':'synthetic-time','restarts':0,'labels':None},
              {'id':'other-test','running':True,'started':'synthetic-time','restarts':0,'labels':{pg.LABEL:'another-run'}}]
        with patch.object(verify,'call',side_effect=[b'source other-test',('\n'.join(json.dumps(r) for r in rows)).encode()]):
            self.assertEqual(verify.source_states(),{'source':(True,'synthetic-time',0)})

    def test_plan_never_starts_a_container(self):
        with patch.object(verify.os,'geteuid',return_value=0),patch.object(verify,'snapshot_record',return_value=(Path('/synthetic'),{})),patch.object(verify,'capacity',return_value={}),patch.object(verify,'IsolatedPostgres') as server:
            result=verify.verify({},'synthetic',run=False)
            self.assertFalse(result['postgres_restore_executed'])
            server.assert_not_called()

if __name__=='__main__':unittest.main()
