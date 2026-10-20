"""Restore trusted dumps into a new, disposable PostgreSQL with no external network."""
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from g1_backup import BackupError

IMAGE_ID=re.compile(r'^sha256:[a-f0-9]{64}$')
DB_NAME=re.compile(r'^[A-Za-z0-9_-]{1,63}$')
LABEL='g1-backup.restore-token'


def call(args,stage,input=None,stdin=None,diagnostics=None,timeout=1800):
    try:
        result=subprocess.run(args,input=input,stdin=stdin,stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BackupError(f'{stage}: timed out; private output suppressed') from None
    if result.returncode:
        if diagnostics is not None:
            path=Path(diagnostics)/('error-'+uuid.uuid4().hex+'.txt')
            with path.open('xb') as f:f.write(result.stderr)
            path.chmod(0o600)
        raise BackupError(f'{stage}: exit {result.returncode}; private diagnostic suppressed')
    return result.stdout


def database_name(value):
    # Never let a database name be interpreted as a libpq connection string or URI.
    if not isinstance(value,str) or not DB_NAME.fullmatch(value):
        raise BackupError('unsupported database name in dump catalog')
    return value


class IsolatedPostgres:
    def __init__(self,image,diagnostics=None):
        if not IMAGE_ID.fullmatch(image):raise BackupError('require the recorded immutable image ID')
        self.image=image;self.token=uuid.uuid4().hex;self.cid=None
        self.admin='g1_verify_'+self.token[:16];self.control=self.admin
        self.diagnostics=diagnostics

    def command(self,args,stage,**kwargs):
        return call(args,stage,diagnostics=self.diagnostics,**kwargs)

    def inspect_owned(self):
        if not self.cid or not re.fullmatch(r'[a-f0-9]{64}',self.cid):
            raise BackupError('no owned test container')
        data=json.loads(self.command(['docker','inspect',self.cid],'inspect test container'))[0]
        if data['Id']!=self.cid or (data['Config'].get('Labels') or {}).get(LABEL)!=self.token:
            raise BackupError('container ownership mismatch; refusing control or removal')
        return data

    def check_isolation(self):
        data=self.inspect_owned();host=data['HostConfig']
        if data['Image']!=self.image or host.get('NetworkMode')!='none' or host.get('Privileged'):
            raise BackupError('test container isolation mismatch')
        if host.get('Binds') or host.get('PortBindings') or any(data['NetworkSettings'].get('Ports',{}).values()):
            raise BackupError('test container has a bind mount or published port')
        # Official image declares an unused anonymous PGDATA volume. It must be new;
        # no named/production volumes or bind mounts are accepted.
        for mount in data.get('Mounts',[]):
            if (mount['Type']!='volume' or mount['Destination']!='/var/lib/postgresql/data'
                or not re.fullmatch(r'[a-f0-9]{64}',mount.get('Name',''))):
                raise BackupError('unexpected persistent mount in restore test')

    def __enter__(self):
        self.command(['docker','image','inspect',self.image],'check installed backup image')
        args=['docker','create','--pull=never','--name','g1-pg-restore-'+self.token,
              '--label',LABEL+'='+self.token,'--network','none','--user','postgres',
              '--cpus','1','--memory','2g','--memory-swap','2g','--pids-limit','256',
              '--cap-drop','ALL','--security-opt','no-new-privileges',
              '-e','PGDATA=/tmp/g1-verify-pgdata','-e','POSTGRES_HOST_AUTH_METHOD=trust',
              '-e','POSTGRES_USER='+self.admin,'-e','POSTGRES_DB='+self.control,
              self.image,'postgres','-c','listen_addresses=',
              '-c','shared_buffers=64MB','-c','max_connections=20',
              '-c','max_parallel_workers_per_gather=0']
        self.cid=self.command(args,'create isolated PostgreSQL').decode().strip()
        try:
            self.check_isolation()
            self.command(['docker','start',self.cid],'start newly created test PostgreSQL')
            until=time.monotonic()+90
            while time.monotonic()<until:
                # pg_isready can succeed on the temporary init server: require the
                # final postgres PID 1 before accepting socket readiness.
                ready=subprocess.run(['docker','exec',self.cid,'sh','-c',
                    'test "$(cat /proc/1/comm)" = postgres && pg_isready -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"'],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
                if ready.returncode==0:return self
                if not self.inspect_owned()['State']['Running']:
                    raise BackupError('isolated PostgreSQL stopped before becoming ready')
                time.sleep(.5)
            raise BackupError('isolated PostgreSQL readiness timed out')
        except BaseException:
            self.close();raise

    def close(self):
        if self.cid is None:return
        self.inspect_owned()
        self.command(['docker','rm','--force','--volumes',self.cid],'remove this run test container')
        self.cid=None

    def __exit__(self,*args):self.close()

    def sql(self,database,sql=None,file=None):
        database_name(database)
        args=['docker','exec','-i','--user','postgres',self.cid,'psql','-X','-qAt',
              '--set=ON_ERROR_STOP=1','--username='+self.admin,'--dbname='+database]
        if file is not None:
            with Path(file).open('rb') as f:return self.command(args,'restore globals',stdin=f)
        return self.command(args,'query isolated database',input=sql.encode())

    def restore(self,directory):
        directory=Path(directory)
        catalog=json.loads((directory/'catalog.json').read_text())
        if not catalog or len(set(catalog.values()))!=len(catalog):
            raise BackupError('invalid PostgreSQL catalog')
        for name in catalog.values():
            database_name(name)
            if name in (self.admin,self.control,'template0','template1'):
                raise BackupError('dump catalog collides with test control database')
        expected=set()
        for relative in catalog:
            if not re.fullmatch(r'databases/postgresql/db-[0-9]{3}\.dump',relative):
                raise BackupError('invalid dump file in catalog')
            expected.add(Path(relative).name)
        actual={p.name for p in directory.glob('*.dump')}
        if expected!=actual:raise BackupError('dump files and catalog differ')
        # A unique bootstrap superuser lets globals recreate the original postgres
        # role without suppressing duplicate-role errors or dropping ownership/ACLs.
        self.sql(self.control,file=directory/'globals.sql')
        results=[]
        for ordinal,(relative,name) in enumerate(sorted(catalog.items()),1):
            print(f'[DB 복원] {ordinal}/{len(catalog)} — 역할·소유권·데이터 검사',file=sys.stderr,flush=True)
            # Drop only initdb's empty postgres DB, inside this fresh test cluster.
            if name=='postgres':self.sql(self.control,'DROP DATABASE postgres;')
            with (directory/Path(relative).name).open('rb') as f:
                self.command(['docker','exec','-i','--user','postgres',self.cid,
                    'pg_restore','--exit-on-error','--create','--username='+self.admin,
                    '--dbname='+self.control],f'restore database #{ordinal}',stdin=f)
            audit=json.loads(self.sql(name,AUDIT_SQL))
            if audit['invalid_indexes']:
                raise BackupError(f'restored database #{ordinal} has invalid indexes')
            results.append(dict(database_ordinal=ordinal,**audit))
        self.check_isolation()
        return {'status':'isolated-postgresql-restore-verified','database_count':len(results),
            'databases':results,'globals_restored':True,'ownership_and_acl_restore_requested':True,
            'network':'none','published_ports':False,'host_bind_mounts':False,
            'production_volumes_reused':False,'source_row_count_baseline_available':False,
            'application_login_or_queries_validated':False}


AUDIT_SQL='''
CREATE TEMP TABLE g1_audit_counts(rows_read bigint);
DO $g1$
DECLARE item record; n bigint;
BEGIN
 FOR item IN SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE c.relkind IN ('r','m') AND n.nspname NOT IN ('pg_catalog','information_schema')
  AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%'
 LOOP
  EXECUTE format('SELECT count(*) FROM %I.%I',item.nspname,item.relname) INTO n;
  INSERT INTO g1_audit_counts VALUES(n);
 END LOOP;
END $g1$;
SELECT json_build_object('tables_read',(SELECT count(*) FROM g1_audit_counts),
 'rows_read',(SELECT coalesce(sum(rows_read),0) FROM g1_audit_counts),
 'invalid_indexes',(SELECT count(*) FROM pg_index WHERE NOT indisvalid),
 'sequence_count',(SELECT count(*) FROM pg_sequences),
 'extension_count',(SELECT count(*) FROM pg_extension));
'''
