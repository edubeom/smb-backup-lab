#!/usr/bin/env python3
"""Run the actual online worker on synthetic mail, without network or host data."""
import json
from pathlib import Path
import subprocess
import mail_online
ROOT=Path(__file__).resolve().parents[1]

def main():
    # Use the already installed Mailu image; never pull a different version implicitly.
    fixture=(ROOT/'tests/fixtures/mail_online_container.py').read_text()
    result=subprocess.run(['docker','run','--rm','-i','--pull=never','--network','none',
        '--read-only','--tmpfs','/mail:size=64m','--tmpfs','/tmp:size=128m',
        '--entrypoint','python3','ghcr.io/mailu/dovecot:2024.06','-c',fixture],
        input=mail_online.WORKER.encode(),capture_output=True,timeout=90)
    # Diagnostics contain synthetic fixture values only.
    if result.returncode:
        print(result.stderr.decode(errors='replace'));return 1
    proof=json.loads(result.stdout)
    print(json.dumps(proof,ensure_ascii=False,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
