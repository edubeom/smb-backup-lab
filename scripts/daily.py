#!/usr/bin/env python3
"""Daily orchestrator; fresh collection, encrypted transfer and approved maintenance.
Collection never stops services. Installation remains a separate operational step.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from g1_backup import BackupError, KST, atomic_json, read_config, guard, private_file, locked, retention_plan, transfer
from operations import approval as operations_approval, prune_after_success, cleanup_new_prepared
from prepare import prepare

def daily(c,approval=None,maintenance=True):
    if approval is not None:raise BackupError('legacy mail-stop approval is no longer accepted')
    guard(c);private_file(c['password_file'])
    with locked(Path(c['work'])/'daily'):
        # Catch-up and retry must not cause another collection after today's success.
        today=datetime.now(KST).date().isoformat()
        plan=retention_plan(c['repository'])
        for rid in plan['keep']:
            record=json.loads((Path(c['repository'])/(rid+'.success.json')).read_text())
            if record['day']==today:return {'status':'already-successful-today','run_id':rid}
        if maintenance:operations_approval(c)  # Reject invalid approval before collection.
        parent=Path(c['prepared']).parent
        existing_before={p.name for p in parent.iterdir()}
        prepared=prepare(c)
        run_config=dict(c,prepared=str(prepared))
        result=transfer(run_config)
        if maintenance:
            maintenance_result=prune_after_success(c,result)
            cleanup=cleanup_new_prepared(c,prepared,existing_before,result)
        else:
            maintenance_result={'status':'disabled-for-this-invocation','deleted':[]}
            cleanup={'status':'disabled-for-this-invocation','deleted':False}
        return {'backup':result,'retention':retention_plan(c['repository']),
                'maintenance':maintenance_result,'staging_cleanup':cleanup}

def run_with_status(c):
    # Include mount/credential failures in a private local status file.
    with locked(Path(c['work'])/'status'):
        path=Path(c['work'])/'daily-status.json'
        atomic_json(path,{'status':'running','started_at':datetime.now(KST).isoformat()})
        try:
            result=daily(c)
        except Exception as error:
            result={'status':'failed','finished_at':datetime.now(KST).isoformat(),
                    'reason':str(error) if isinstance(error,BackupError) else type(error).__name__,
                    'note':'Any committed backup remains valid; inspect maintenance separately.'}
            atomic_json(path,result)
            atomic_json(Path(c['work'])/'last-daily-failure.json',result)
            raise
        atomic_json(path,{'status':'finished','finished_at':datetime.now(KST).isoformat(),'result':result})
        return result

def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config',required=True)
    a=p.parse_args()
    try:
        c=read_config(a.config);result=run_with_status(c)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except Exception as error:
        print(json.dumps({'status':'failed','reason':str(error) if isinstance(error,BackupError) else type(error).__name__,
                          'action':'Check daily-status.json and the journal; maintenance may have partially completed.'}))
        return 1
    return 0
if __name__=='__main__':raise SystemExit(main())
