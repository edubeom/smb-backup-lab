#!/usr/bin/env python3
"""Print a small private operations report, never source data or key contents."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from g1_backup import BackupError, KST, private_file, read_config

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',default='/etc/g1-backup/backup.json');a=p.parse_args()
    try:
        if os.geteuid()!=0:raise BackupError('sudo로 실행하세요. 비밀번호·키는 출력하지 않습니다.')
        c=read_config(private_file(a.config));work=Path(c['work'])
        def receipt(name):
            path=work/name
            return json.loads(private_file(path).read_text()) if path.exists() else None
        success=receipt('last-success.json')
        output={'checked_at':datetime.now(KST).isoformat(),
                'last_success':{k:success[k] for k in ('run_id','day','finished_at','bytes') if k in success} if success else None,
                'daily':receipt('daily-status.json'),'last_daily_failure':receipt('last-daily-failure.json'),
                'last_retention':receipt('last-retention.json'),'installation':receipt('operations-installation.json')}
        r=subprocess.run(['systemctl','show','g1-backup.timer','-p','LoadState','-p','ActiveState',
                          '-p','UnitFileState','-p','NextElapseUSecRealtime'],capture_output=True,text=True)
        output['timer']={k:v for k,v in (x.split('=',1) for x in r.stdout.splitlines() if '=' in x)}
        print(json.dumps(output,ensure_ascii=False,indent=2));return 0
    except Exception as error:
        print(json.dumps({'status':'failed','reason':str(error) if isinstance(error,BackupError) else type(error).__name__},ensure_ascii=False));return 1

if __name__=='__main__':raise SystemExit(main())
