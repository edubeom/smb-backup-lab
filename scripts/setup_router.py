#!/usr/bin/env python3
"""Interactive root setup. Never stops services, starts backups, or deletes files."""
import argparse
import getpass
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import stat
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from g1_backup import BackupError, check_mount, private_file

MOUNT = Path('/mnt/router-backup')
SOURCE = '//192.0.2.1/backup'
SECURE = Path('/etc/g1-backup')
STATE = Path('/var/lib/g1-backup')
REPO = MOUNT / 'server/g1'
SMB_VERSIONS = ('2.0', '2.1', '3.0', '3.0.2', '3.1.1', 'default')


class MountFailure(BackupError):
    def __init__(self, exit_code, system_error=None):
        self.system_error = system_error
        if system_error == 13:
            guidance = '인증 또는 공유 접근 권한 문제입니다. 암호를 다시 입력할 수 있습니다.'
        elif system_error == 22:
            guidance = 'SMB 버전·마운트 옵션·서버 설정을 확인해야 합니다. 암호 재입력은 중단합니다.'
        else:
            guidance = '연결 설정과 네트워크를 확인해야 합니다. 암호 재입력은 중단합니다.'
        super().__init__(f'SMB 연결 실패 (종료 코드 {exit_code}, 오류 번호 {system_error if system_error is not None else "unknown"}). {guidance}')


def new_file(path, content):
    """Exclusive creation; existing files and symlinks are never replaced."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


def secure_directory(path):
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    s = path.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.geteuid() or s.st_mode & 0o077:
        raise BackupError('private directory must be owned by the runner with mode 0700')


def existing_or_empty_repository(repo):
    if repo != repo.resolve():
        raise BackupError('repository path is redirected by a symlink')
    marker = repo / '.g1-repository-id'
    if marker.exists() or marker.is_symlink():
        if marker.is_symlink() or not marker.is_file():
            raise BackupError('repository marker must be a regular file')
        value = marker.read_text().strip()
        import re
        if not re.fullmatch(r'[a-zA-Z0-9-]{16,100}', value):
            raise BackupError('repository marker is invalid')
        return value
    if repo.exists() and (not repo.is_dir() or any(repo.iterdir())):
        raise BackupError('existing nonempty repository has no marker; inspect it before setup')
    return None


def prompt_budget(free_bytes):
    # The whole disk is shared; no silent 900GB or example 100GiB allocation.
    free_gib = free_bytes // (1024 ** 3)
    print(f'현재 공유 저장소 여유 공간: 약 {free_gib} GiB (다른 프로그램과 공용)')
    raw = input('G1 백업의 최대 사용량을 GiB 정수로 입력하세요 (기본값 없음): ').strip()
    if not raw.isdecimal():
        raise BackupError('enter an integer capacity budget')
    budget = int(raw) * 1024 ** 3
    reserve = 10 * 1024 ** 3
    if budget <= 0 or budget + reserve > free_bytes:
        raise BackupError('budget must leave at least 10 GiB free on the shared disk')
    return budget, reserve


def mount_with_credentials(credentials, smb_version='2.0'):
    if smb_version not in SMB_VERSIONS:
        raise BackupError('unsupported SMB version')
    args = ['mount', '-t', 'cifs', SOURCE, str(MOUNT), '-o',
            'credentials=' + str(credentials) + ',nosuid,nodev,noexec,file_mode=0600,dir_mode=0700,vers=' + smb_version]
    result = subprocess.run(args, capture_output=True)
    if result.returncode:
        # Never print raw tool output: it may contain sensitive input.
        import re
        match = re.search(rb'mount error\((\d+)\)', result.stderr or b'')
        code = int(match.group(1)) if match else None
        raise MountFailure(result.returncode, code)
    check_mount(str(MOUNT), SOURCE)


def connect_share(credentials, reset_password=False, smb_version='2.0'):
    existing = credentials.exists() or credentials.is_symlink()
    if existing:
        private_file(credentials)
    if os.path.ismount(MOUNT):
        check_mount(str(MOUNT), SOURCE)
        if reset_password:
            raise BackupError('공유가 이미 연결되어 있습니다. 연결을 끊거나 인증 파일을 바꾸지 않았습니다.')
        return
    if existing and not reset_password:
        try:
            mount_with_credentials(credentials, smb_version=smb_version)
            print('기존 인증으로 공유 연결을 확인했습니다.')
            return
        except BackupError as error:
            print(str(error))
            if os.path.ismount(MOUNT) or not isinstance(error, MountFailure) or error.system_error != 13:
                raise
            print('저장된 인증을 바꾸지 않고 비밀번호를 다시 입력받습니다.')
    for attempt in range(1, 4):
        print(f'공유 계정: backup-user · 새 비밀번호 입력 {attempt}/3')
        password = getpass.getpass('공유 비밀번호 (표시되지 않음): ')
        confirmation = getpass.getpass('같은 공유 비밀번호를 한 번 더 입력: ')
        if not password or any(ch in password for ch in '\r\n\x00') or password != confirmation:
            del password, confirmation
            print('입력이 비어 있거나 두 입력이 다릅니다. 저장하지 않았습니다.')
            continue
        fd, name = tempfile.mkstemp(prefix='.smb-candidate-', dir=credentials.parent)
        candidate = Path(name)
        try:
            with os.fdopen(fd, 'w') as f:
                f.write('username=backup-user\npassword=' + password + '\n')
                f.flush()
                os.fsync(f.fileno())
            del password, confirmation
            try:
                mount_with_credentials(candidate, smb_version=smb_version)
            except BackupError as error:
                print(str(error))
                if os.path.ismount(MOUNT) or not isinstance(error, MountFailure) or error.system_error != 13:
                    raise
                continue
            # The caller explicitly requested re-entry, or saved authentication failed.
            # Commit only authentication which just passed the actual mount check.
            os.replace(candidate, credentials)
            print('입력한 인증으로 연결을 확인하고 보호된 인증 파일에 저장했습니다.')
            return
        finally:
            candidate.unlink(missing_ok=True)
    raise BackupError('3회 연결/입력 확인에 실패했습니다. 기존 인증 파일은 유지했습니다. 재입력 명령으로 다시 시도할 수 있습니다.')


def apply(reset_password=False, smb_version='2.0'):
    if os.geteuid() != 0:
        raise BackupError('관리자 권한이 필요합니다. 서버 터미널에서 sudo로 실행하세요.')
    if not sys.stdin.isatty():
        raise BackupError('비밀번호는 개인 서버 터미널에서 직접 입력해야 합니다.')
    if not shutil.which('mount.cifs'):
        print('SMB 연결 도구를 설치합니다. 서비스 자동 재시작은 요청하지 않습니다.')
        env = dict(os.environ, DEBIAN_FRONTEND='noninteractive', NEEDRESTART_MODE='l')
        subprocess.run(['apt-get', 'install', '-y', '--no-install-recommends', 'cifs-utils'], env=env, check=True)
    if not shutil.which('mount.cifs') or not shutil.which('gpg'):
        raise BackupError('cifs-utils와 gnupg 설치가 필요합니다.')
    for path in (SECURE, STATE):
        secure_directory(path)
    secure_directory(STATE / 'work')
    if MOUNT != MOUNT.resolve():
        raise BackupError('mount path is redirected')
    MOUNT.mkdir(mode=0o755, exist_ok=True)
    if not os.path.ismount(MOUNT) and any(MOUNT.iterdir()):
        raise BackupError('unmounted target contains local files; refusing to hide them')
    credentials = SECURE / 'smb-credentials'
    print(f'SMB 연결 버전: {smb_version}')
    connect_share(credentials, reset_password=reset_password, smb_version=smb_version)
    check_mount(str(MOUNT), SOURCE)
    repository_id = existing_or_empty_repository(REPO)
    config_path = SECURE / 'backup.json'
    if config_path.exists() or config_path.is_symlink():
        private_file(config_path)
        current = json.loads(config_path.read_text())
        if current.get('repository_id') != repository_id or current.get('repository') != str(REPO):
            raise BackupError('existing configuration and repository disagree; nothing overwritten')
        if current.get('password_file') != str(SECURE / 'archive-passphrase'):
            raise BackupError('existing configuration uses another key; inspect it before setup')
        private_file(SECURE / 'archive-passphrase')
        print('기존 설정을 유지합니다. 백업 실행·자동 예약은 시작하지 않았습니다.')
        return
    if repository_id is not None:
        raise BackupError('existing repository without local config requires manual reconciliation; no adoption attempted')
    key = SECURE / 'archive-passphrase'
    # Never initialize a repository containing backups with an unrelated new key.
    if repository_id is not None and not (key.exists() or key.is_symlink()):
        raise BackupError('existing repository requires its original archive key; no new key generated')
    if key.exists() or key.is_symlink():
        private_file(key)
    budget, reserve = prompt_budget(shutil.disk_usage(MOUNT).free)
    if not key.exists():
        new_file(key, secrets.token_urlsafe(48) + '\n')
    if repository_id is None:
        REPO.mkdir(parents=True, mode=0o700, exist_ok=True)
        # Check again after the interactive capacity choice.
        if existing_or_empty_repository(REPO) is not None or any(REPO.iterdir()):
            raise BackupError('repository changed during setup; refusing initialization')
        repository_id = 'g1-' + secrets.token_hex(20)
        new_file(REPO / '.g1-repository-id', repository_id + '\n')
    config = json.loads((ROOT / 'config/backup.example.json').read_text())
    config.update(enabled=True, host=socket.gethostname(), mount=str(MOUNT), mount_source=SOURCE,
                  repository=str(REPO), repository_id=repository_id, work=str(STATE / 'work'),
                  prepared=str(STATE / 'prepared'), password_file=str(key),
                  max_repository_bytes=budget, reserve_bytes=reserve, smb_version=smb_version)
    new_file(config_path, json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    print('공유 연결과 보호된 설정을 준비했습니다. 백업·메일 중단·삭제·timer 활성화는 하지 않았습니다.')
    print('암호화 복구 키를 G1 외부의 암호 관리자에도 보관해야 합니다. 키 값은 화면에 출력하지 않았습니다.')
    print('이 채팅에는 비밀번호 대신 “연결 준비 완료”라고 알려 주세요.')


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply', action='store_true', help='run interactively as root; default is read-only plan')
    p.add_argument('--reset-smb-password', action='store_true', help='re-enter SMB password; save only after successful connection')
    p.add_argument('--smb-version', choices=SMB_VERSIONS, default='2.0',
                   help='SMB dialect; default 2.0 matches the current router negotiation')
    a = p.parse_args()
    if a.reset_smb_password and not a.apply:
        p.error('--reset-smb-password requires --apply')
    if not a.apply:
        print(json.dumps({'mode': 'read-only-plan', 'host': socket.gethostname(),
                          'smb_tool_available': bool(shutil.which('mount.cifs')),
                          'mounted': os.path.ismount(MOUNT), 'source': SOURCE,
                          'repository': str(REPO), 'starts_services_or_backups': False,
                          'smb_version': a.smb_version,
                          'next': 'sudo python3 /project/smb-backup-lab/scripts/setup_router.py --apply'},
                         ensure_ascii=False, indent=2))
        return 0
    try:
        apply(reset_password=a.reset_smb_password, smb_version=a.smb_version)
    except (BackupError, OSError, ValueError, subprocess.SubprocessError, EOFError, KeyboardInterrupt) as error:
        print(str(error) if isinstance(error, BackupError) else type(error).__name__ + ': 설정 실패 (원문 비밀은 숨김)', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
