# 프로젝트별 백업과 시스템 설정 — objects-v2

이번 사용자 요청: 서버의 SSH/Cloudflare 등 시스템 설정을 함께 백업하고 `/project`는 가능한 프로젝트별 파일로 분리한다.

새 저장은 **프로젝트/시스템 구성요소별로 각각 암호화**한다. 이 형식의 첫 전체 성공본과 선택 파일/메일 읽기 검증은 사용자 실행 결과로 확인했다. [첫 실행 기록](first-backup-result.md)을 따른다. 기존 bundle-v1 읽기/보관 호환성은 유지한다. 기존 백업을 변환하거나 삭제하지 않는다.

## 공유기 저장 형태

```text
/mnt/router-backup/server/g1/
  RUN_ID.success.json                 # 모든 구성요소 통과 후에만 생성
  RUN_ID.objects/
    _manifest.json.gpg                # 원본 해시·파일 모드·구성 목록 (암호화)
    projects/
      demo-web/files.tar.gz.gpg
      bomts-main/files.tar.gz.gpg
      demo-mail/files.tar.gz.gpg    # 설정, live data 제외
      demo-mail/online-mailboxes.tar.gz.gpg
      demo-mail/databases/admin/main.db.gpg
      demo-mail/databases/webmail/roundcube.db.gpg
      demo-mail/redis.rdb.gpg
      demo-mail/auxiliary.tar.gz.gpg
      demo-mail/online-export.json.gpg
      demo-notes/files.tar.gz.gpg
      demo-notes/databases/Story.db.gpg
      g1-backup/files.tar.gz.gpg
      ...각 프로젝트...
      _root-entries.tar.gz.gpg         # /project 바로 아래 일반 파일·symlink
      catalog.json.gpg
    system/
      etc.tar.gz.gpg                   # /etc 전체
      operator-config.tar.gz.gpg       # /home/*, /root의 운영 설정
      catalog.json.gpg
      packages.txt.gpg
      runtime-state.json.gpg
      ...설치된 도구의 네트워크·방화벽·서비스 목록...
    databases/postgresql/
      globals.sql.gpg
      db-000.dump.gpg
      ...DB별 전용 덤프...
      catalog.json.gpg
    docker/
      metadata.json.gpg
      runtime-private.json.gpg
      images/IMAGE_DIGEST.tar.gpg      # 이미지별 저장, 프로젝트 간 공통 참조
      volumes/cold-000.tar.gz.gpg
      volumes.json.gpg
```

`RUN_ID.partial/`은 실패/중단된 미확정 자료다. 성공 날짜 수에 넣지 않으며 자동 삭제하지 않는다. 각 파일을 전송한 후 공유기에서 다시 읽어 복호화하고 원본 해시와 비교한다. 모든 구성요소와 암호화 manifest가 검증된 뒤 묶음을 `.objects`로 바꾸고 성공 기록을 만든다. 한 구성요소 실패도 전체 일별 성공으로 처리하지 않는다.

보관 정책은 파일 5개가 아니라 **검증된 성공 날짜 5개**다. 해당 날짜의 프로젝트·DB·시스템 자료가 하나의 일별 묶음이다. 상한 50GiB는 실패/미확정 파일을 포함한 G1 전용 저장소 합계를 기준으로 검사한다. 이전 정상본을 먼저 지워 공간을 만들지 않는다. 자동 정리와 timer는 여전히 비활성화다.

## 시스템 설정 범위

| 위치/자료 | 복구 용도 |
|---|---|
| `/etc/ssh` 전체 | SSH 서버 설정, drop-in, 호스트 키 |
| `/etc/cloudflared` 전체 | Cloudflare Tunnel 설정·인증 자료 |
| `/etc/systemd`, `/etc/netplan`, `/etc/fstab` | 서비스 자동 실행, 네트워크, 마운트 구성 |
| `/etc/ufw`, nftables 등 `/etc`의 실제 설정 | 방화벽 구성 |
| `/etc/passwd`, group, shadow 등 | 사용자·그룹·권한 복구 참고 |
| `/etc/g1-backup` | 백업 설정과 비밀, 암호화된 백업 안에만 포함 |
| `/home/*` 및 `/root`의 `.ssh`, `.cloudflared`, `.config/systemd`, `.pm2/dump.pm2`, `.docker/config.json` | 운영자 SSH/터널/사용자 서비스/PM2/레지스트리 구성 |
| 패키지 버전·서비스 enable 목록·IP/route·가능한 방화벽 runtime 출력 | 재설치 및 당시 상태 비교 |

설정은 전부 암호화한다. 비밀번호·인증서 개인키·SSH 키·메일 자료는 Git이나 영상에 넣지 않는다. 방화벽 runtime에는 Docker가 만든 규칙도 있으므로 복구 시 그대로 적용하지 않고 서비스 재구성과 함께 검토한다. `/etc` 밖의 별도 애플리케이션 설정/외부 symlink 대상은 추가 범위 조사가 필요할 수 있다.

## 프로젝트 단위 범위

- `/project` 바로 아래 실제 디렉터리를 프로젝트 단위로 아카이브한다. `.git`과 `.env`, 미추적 코드·업로드는 포함한다.
- node_modules, .next, .venv, __pycache__, .pytest_cache 등 재생성 캐시는 제외한다. 관리 프로젝트의 runtime/restore-test/.manual-review는 제외한다.
- Mailu는 live data를 뺀 설정 tar와 온라인 우편함/SQLite/Redis 내보내기를 같은 프로젝트 아래 분리한다. 실행 중인 전체 메일 트리를 tar로 복사하지 않는다. [중단 없는 메일 수집](mail-online.md)을 따른다.
- Story.db는 online backup을 별도로 만들고 live DB/WAL은 프로젝트 코드 아카이브에서 뺀다. PostgreSQL은 DB별 dump와 globals를 별도로 만든다. 프로젝트 tar만으로 외부 DB까지 복구된다고 가정하지 않는다.
- `/project/backup`, `demo-web-backups` 같은 기존 자료도 보존 백업 대상으로 분리한다. 원본 정리/삭제는 하지 않는다. lost+found는 파일시스템 복구 디렉터리로 제외한다.
- 최상위 symlink는 링크 자체를 `_root-entries`에 보존하고 외부 대상을 자동 순회하지 않는다. 파일명에 특수 문자가 있으면 저장 폴더 이름은 percent encoding하고 catalog에 원래 이름을 남긴다.

## 선택 복원

먼저 RUN_ID를 실제 성공 ID로 바꾼다. 아래 경로는 비어 있는 root 전용 시험 경로여야 한다. 운영 파일을 덮어쓰지 않는다.

```bash
sudo python3 /project/smb-backup-lab/src/g1_backup.py restore-test \
  --snapshot /mnt/router-backup/server/g1/RUN_ID.success.json \
  --project demo-web \
  --password-file /etc/g1-backup/archive-passphrase \
  --destination /var/lib/g1-backup/restore-demo-web
```

demo-web 폴더의 구성요소만 읽고 복호화·원본 해시 비교 후 복원한다. `files.tar.gz`는 이 단계에서 압축 상태로 남긴다. 다른 프로젝트를 복호화할 필요가 없다. 다만 성공 묶음의 구성요소 존재 여부는 전체적으로 검사한다.

```bash
sudo python3 /project/smb-backup-lab/src/g1_backup.py restore-test \
  --snapshot /mnt/router-backup/server/g1/RUN_ID.success.json \
  --component system/etc.tar.gz \
  --password-file /etc/g1-backup/archive-passphrase \
  --destination /var/lib/g1-backup/restore-system
```

시스템 설정은 시험 경로에만 복원하고 SSH/Cloudflare/네트워크 설정을 검토한 후 적용한다. GNU tar 안에는 원본 numeric UID/GID·권한·symlink·ACL/xattr가 보존되며 실제 적용 시험은 별도다. 운영 SSH 설정을 바로 덮어쓰면 접속이 끊길 수 있어 첫 파일 시험에서는 적용하지 않는다.

## 첫 실행

```bash
sudo python3 /project/smb-backup-lab/scripts/first_backup.py --run
```

root 설정과 전용 저장소를 검사하고 자체 임시 probe 파일로 쓰기/읽기를 확인한 뒤 **서비스 중단 없이** 수집한다. 중단 동의를 묻지 않으며 stop/start/restart 명령도 없다. 이미 오늘 성공한 백업이 있으면 재수집을 생략한다. SMTP 대기열이 관측되거나 온라인 내보내기에 실패하면 서비스를 계속 실행하고 성공 기록을 만들지 않는다. 각 우편함/DB의 시점은 다를 수 있다.

백업 완료 뒤 관리 프로젝트 README·SSH 서버 설정·Cloudflare 설정과 메일 프로젝트를 보호된 시험 폴더에 복원한다. 공유기에서 되읽은 메일 SQLite 무결성 검사와 네트워크 없는 격리 우편함 읽기도 수행한다. DB·메일 서비스를 별도 환경에서 시작하는 복구 시험은 이후 단계이며 이 파일 시험으로 대체하지 않는다. 기존 백업 삭제와 자동 실행 활성화는 하지 않는다. 복구 키를 G1 밖 안전한 장소에도 보관해야 한다.
