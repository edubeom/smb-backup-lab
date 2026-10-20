#!/usr/bin/env python3
"""Prepare online application exports and protected settings without stopping services."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import uuid
from zoneinfo import ZoneInfo
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
from g1_backup import BackupError, atomic_json, read_config, locked, guard
from mail_online import collect as collect_mail_online

MAIL_ROOT=Path('/project/demo-mail')
PG='demo-postgres'
PROJECT_ROOT=Path('/project')

def quiet(args, output=None):
    r=subprocess.run(args,stdout=output or subprocess.DEVNULL,stderr=subprocess.PIPE)
    if r.returncode:
        raise BackupError(f'collector command failed: {Path(args[0]).name} (exit {r.returncode}); raw output suppressed')

def inspect_containers():
    ids=subprocess.check_output(['docker','ps','-aq'],text=True).split()
    return json.loads(subprocess.check_output(['docker','inspect',*ids],text=True)) if ids else []

def archive(output, base, members, excludes=()):
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output.exists():raise BackupError('archive output already exists')
    args=['tar','--acls','--xattrs','--numeric-owner','-czf',str(output)]
    args += ['--exclude='+x for x in excludes]
    args += ['-C',str(base),'--',*members]
    quiet(args)
    quiet(['tar','-tzf',str(output)])

def collect_projects(out, project_root=PROJECT_ROOT, mail_root=MAIL_ROOT):
    """One archive per immediate project directory. Never follows top-level links."""
    projects=[]; root_entries=[]
    if out.resolve().is_relative_to(project_root.resolve()):
        raise BackupError('staging must be outside the project source tree')
    (out/'projects').mkdir(mode=0o700,parents=True,exist_ok=True)
    for entry in sorted(project_root.iterdir()):
        name=entry.name
        if name=='lost+found':
            projects.append({'project':name,'status':'excluded-filesystem-recovery-directory'})
            continue
        if entry.is_symlink() or not entry.is_dir():
            root_entries.append(name)
            projects.append({'project':name,'kind':'symlink' if entry.is_symlink() else 'root-file',
                             'archive':'projects/_root-entries.tar.gz'})
            continue
        relative=f'projects/{quote(name,safe="-_.")}/files.tar.gz'
        if entry==mail_root:
            if not (out/relative).is_file():
                raise BackupError('consistent Mailu export must exist before project collection')
            projects.append({'project':name,'archive':relative,'consistency':'online application exports; see online-export.json'})
            continue
        excludes=['*/node_modules','*/.next','*/.venv','*/__pycache__','*/.pytest_cache']
        if name=='demo-notes':
            excludes.append(name+'/Story.db*')
        if entry.resolve()==ROOT.resolve():
            excludes.extend([name+'/.manual-review',name+'/runtime',name+'/restore-test'])
        archive(out/relative,project_root,[name],excludes)
        item={'project':name,'archive':relative,'consistency':'live-files; tar warnings fail collection'}
        if name=='demo-notes':item['database']='projects/demo-notes/databases/Story.db'
        projects.append(item)
    if root_entries:
        archive(out/'projects/_root-entries.tar.gz',project_root,root_entries)
    atomic_json(out/'projects/catalog.json',{'source':str(project_root),'projects':projects,
                                            'note':'Top-level symlink targets are not traversed; review separately.'})
    return projects

def collect_system(out, filesystem_root=Path('/')):
    """Full /etc plus operator credentials outside /etc, in private staging only."""
    archive(out/'system/etc.tar.gz',filesystem_root,['etc'])
    homes=[]
    home_root=filesystem_root/'home'
    if home_root.is_dir():homes.extend(p for p in home_root.iterdir() if p.is_dir() and not p.is_symlink())
    if (filesystem_root/'root').is_dir():homes.append(filesystem_root/'root')
    members=[]
    for home in homes:
        for part in ('.ssh','.cloudflared','.config/systemd','.pm2/dump.pm2','.docker/config.json'):
            path=home/part
            if path.exists() or path.is_symlink():members.append(path.relative_to(filesystem_root).as_posix())
    if members:archive(out/'system/operator-config.tar.gz',filesystem_root,members)
    atomic_json(out/'system/catalog.json',{
        'etc_archive':'system/etc.tar.gz',
        'includes':['/etc/ssh','/etc/cloudflared','/etc/systemd','/etc/netplan','/etc/ufw',
                    '/etc/nftables.conf','/etc/fstab','/etc/passwd','/etc/group','/etc/shadow',
                    '/etc/g1-backup','other existing /etc files'],
        'operator_members':members,
        'restore_note':'Includes private keys. Extract to an isolated path and inspect before applying.'})

def collect_runtime_state(out):
    commands={'units':['systemctl','list-unit-files','--no-pager','--no-legend'],
              'addresses':['ip','-json','address'], 'routes':['ip','-json','route','show','table','all'],
              'nftables':['nft','list','ruleset'],
              'iptables':['iptables-save'],'ip6tables':['ip6tables-save']}
    status={}
    for name,args in commands.items():
        if not shutil.which(args[0]):status[name]='tool-not-installed';continue
        path=out/'system'/f'{name}.txt'
        with path.open('xb') as f:quiet(args,f)
        status[name]='captured'
    atomic_json(out/'system/runtime-state.json',status)

def prepare(c,approval_path=None):
    if os.geteuid()!=0:raise BackupError('collector requires root to read protected files')
    if approval_path is not None:raise BackupError('legacy stop-based collection is disabled; remove the approval argument')
    guard(c)
    now=datetime.now(ZoneInfo('Asia/Seoul'))
    parent=Path(c['prepared']).parent
    parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    if parent.stat().st_mode & 0o077:raise BackupError('staging parent must be private')
    out=parent/('prepared-'+now.strftime('%Y%m%dT%H%M%S')+'-'+uuid.uuid4().hex[:8])
    if shutil.disk_usage(parent).free < c['reserve_bytes']+c.get('prepare_required_free_bytes',20*1024**3):
        raise BackupError('insufficient free space for collection')
    with locked(c['work']):
        out.mkdir(mode=0o700)
        containers=inspect_containers()
        print('[수집 1/6] Mailu 온라인 내보내기 — 서비스 계속 실행',flush=True)
        mail_receipt=collect_mail_online(out,MAIL_ROOT,containers,archive)
        print('[수집 2/6] PostgreSQL 논리 덤프',flush=True)
        # Roles and dumps stay in private staging, and are encrypted before transfer.
        (out/'databases/postgresql').mkdir(mode=0o700,parents=True)
        with (out/'databases/postgresql/globals.sql').open('xb') as f:
            quiet(['docker','exec','-u','postgres',PG,'pg_dumpall','-U','postgres','--globals-only'],f)
        query="SELECT datname FROM pg_database WHERE NOT datistemplate ORDER BY datname"
        raw=subprocess.check_output(['docker','exec','-u','postgres',PG,'psql','-X','-U','postgres','-d','postgres','-At','-c',query],text=True)
        names=raw.splitlines()
        db_map={}
        for i,name in enumerate(names):
            filename=f'databases/postgresql/db-{i:03d}.dump';db_map[filename]=name
            with (out/filename).open('xb') as f:
                quiet(['docker','exec','-u','postgres',PG,'pg_dump','-U','postgres','-Fc','--dbname',name],f)
        atomic_json(out/'databases/postgresql/catalog.json',db_map)
        print('[수집 3/6] SQLite와 프로젝트별 파일',flush=True)
        story=Path('/project/demo-notes/Story.db')
        if story.exists():
            src=sqlite3.connect(story.as_uri()+'?mode=ro',uri=True,timeout=30)
            (out/'projects/demo-notes/databases').mkdir(mode=0o700,parents=True)
            dst=sqlite3.connect(out/'projects/demo-notes/databases/Story.db')
            try:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise BackupError('SQLite integrity check failed')
            finally:src.close();dst.close()
        collect_projects(out)
        print('[수집 4/6] SSH·Cloudflare·서버 설정',flush=True)
        collect_system(out)
        collect_runtime_state(out)
        print('[수집 5/6] 비활성 Docker 볼륨과 이미지',flush=True)
        # Preserve cold and unattached volumes; never copy a live database volume.
        (out/'docker').mkdir(mode=0o700,exist_ok=True)
        volume_names=subprocess.check_output(['docker','volume','ls','-q'],text=True).split()
        volumes=json.loads(subprocess.check_output(['docker','volume','inspect',*volume_names],text=True)) if volume_names else []
        running_sources={m['Source'] for d in containers if d['State']['Running'] for m in d['Mounts'] if m['Type']=='volume'}
        cold=[]
        for i,v in enumerate(volumes):
            if v['Mountpoint'] in running_sources:continue
            if any(d['State']['Running'] and any(m['Source']==v['Mountpoint'] for m in d['Mounts']) for d in inspect_containers()):raise BackupError('cold volume became active')
            name=f'docker/volumes/cold-{i:03d}.tar.gz'
            archive(out/name,v['Mountpoint'],['.'])
            if any(d['State']['Running'] and any(m['Source']==v['Mountpoint'] for m in d['Mounts']) for d in inspect_containers()):raise BackupError('cold volume became active during collection')
            cold.append({'archive':name,'volume_name':v['Name']})
        metadata=[]
        for d in containers:
            metadata.append({'name':d['Name'],'image':d['Config']['Image'],'image_id':d['Image'],
                             'running':d['State']['Running'],'mounts':d['Mounts']})
        atomic_json(out/'docker/metadata.json',metadata)
        # Full runtime config includes secrets: private staging and encrypted backup only.
        atomic_json(out/'docker/runtime-private.json',containers)
        images=sorted({d['Image'] for d in containers})
        if images:
            (out/'docker/images').mkdir(mode=0o700,parents=True)
            for image in images:
                with (out/'docker/images'/(image.removeprefix('sha256:')+'.tar')).open('xb') as f:
                    quiet(['docker','image','save',image],f)
        atomic_json(out/'docker/volumes.json',cold)
        with (out/'system/packages.txt').open('xb') as f:quiet(['dpkg-query','-W','-f=${Package}\t${Version}\n'],f)
        print('[수집 6/6] 수집 목록 확정 — 다음은 암호화/공유기 전송',flush=True)
        # No false claim that live project files and separate DBs share one point in time.
        atomic_json(out/'PREPARED.json',{'complete':True,'host':socket.gethostname(),
            'prepared_at':datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
            'mail_consistency':mail_receipt,
            'services_interrupted':False,
            'db_consistency':'per-database logical dumps, no cross-database common snapshot',
            'project_consistency':'live files; tar change warnings fail collection',
            'archive_layout':'objects-v2',
            'service_restore_validated':False})
    # Use an immutable unique directory. No previous prepared export is deleted.
    return out

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True)
    a=p.parse_args()
    try:
        c=read_config(a.config);out=prepare(c)
        print(json.dumps({'status':'prepared','prepared_path':str(out),'next':'Set config prepared to this immutable path, then run transfer.'}))
    except (BackupError,OSError,ValueError,KeyError,subprocess.SubprocessError) as e:
        print(json.dumps({'status':'failed','reason':str(e) if isinstance(e,BackupError) else type(e).__name__}))
        return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
