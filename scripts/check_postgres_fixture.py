#!/usr/bin/env python3
"""Exercise real pg_dump/restore, roles, data and a corrupt archive in isolation."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from g1_backup import BackupError
from postgres_restore import IsolatedPostgres,call


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--image-id',required=True);a=p.parse_args()
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix='g1-pg-fixture-') as td:
        root=Path(td)
        with IsolatedPostgres(a.image_id,diagnostics=root) as source:
            source.sql(source.control,'CREATE ROLE postgres SUPERUSER LOGIN; CREATE ROLE fixture_owner NOLOGIN; CREATE ROLE fixture_reader NOLOGIN; CREATE DATABASE fixture_db OWNER fixture_owner;')
            source.sql('fixture_db','CREATE TABLE fixture_data(id serial PRIMARY KEY,value integer NOT NULL); INSERT INTO fixture_data(value) VALUES(7),(11); ALTER TABLE fixture_data OWNER TO fixture_owner; GRANT SELECT ON fixture_data TO fixture_reader;')
            source.sql('postgres','CREATE TABLE fixture_note(value text); INSERT INTO fixture_note VALUES(\'synthetic fixture\');')
            (root/'globals.sql').write_bytes(call(['docker','exec',source.cid,'pg_dumpall','--username='+source.admin,'--globals-only'],'fixture globals dump'))
            catalog={}
            for i,name in enumerate(('postgres','fixture_db')):
                path=root/f'db-{i:03d}.dump';catalog['databases/postgresql/'+path.name]=name
                path.write_bytes(call(['docker','exec',source.cid,'pg_dump','--username='+source.admin,'-Fc','--dbname='+name],'fixture database dump'))
            (root/'catalog.json').write_text(json.dumps(catalog))
        with IsolatedPostgres(a.image_id,diagnostics=root) as dest:
            proof=dest.restore(root)
            assert proof['database_count']==2
            assert sum(row['rows_read'] for row in proof['databases'])==3
            assert dest.sql('fixture_db','SET ROLE fixture_reader; SELECT sum(value) FROM fixture_data;').strip()==b'18'
            assert dest.sql('fixture_db','SELECT last_value FROM fixture_data_id_seq;').strip()==b'2'
            assert dest.sql('fixture_db',"SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname='fixture_data';").strip()==b'fixture_owner'
        (root/'db-001.dump').write_bytes(b'synthetic-corrupt-archive')
        failed=False
        with IsolatedPostgres(a.image_id,diagnostics=root) as dest:
            try:dest.restore(root)
            except BackupError:failed=True
        assert failed,'corrupt dump must fail the restore'
        print(json.dumps({'fixture_only':True,'database_count':2,'globals_roles_ownership_read_grant':'verified',
            'rows_and_sequence':'verified','corrupt_archive_rejected':True,'network':'none',
            'production_volumes_reused':False,'new_test_containers_removed':True},indent=2))
    return 0
if __name__=='__main__':raise SystemExit(main())
