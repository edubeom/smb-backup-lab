# 메일 중단 없는 수집과 복구

사용자 결정: **“메일 중단 없이 진행하고 싶습니다”**. 이전의 중단 승인 방식은 폐기했다. 현재 수집기에는 서비스 stop/start/restart 경로가 없고, 기존 메일 backup.sh도 호출하지 않는다. 메일·웹·PostgreSQL은 계속 실행한다.

## 무엇을 어떻게 보관하나

모두 `projects/demo-mail/` 구성요소로 각각 암호화한다. 계정명·주소·DB·본문·키는 Git/화면에 넣지 않는다.

| 구성요소 | 수집 방법 | 복구 위치/용도 |
|---|---|---|
| files.tar.gz | data를 제외한 Mailu 프로젝트 | Compose/env/secrets/acme/override |
| online-mailboxes.tar.gz | Dovecot 2.3의 단방향 `backup` | 우편함·UID/UIDVALIDITY·읽음/중요 표시·폴더 구독 |
| 내보내기 안의 users.json | 숫자 폴더와 계정 매핑 (암호화 백업 안) | maildirs/0000 등을 원래 사용자 위치에 대응 |
| 내보내기 안의 sieve.tar.gz | 사용자별 필터 파일·활성 symlink | 각 사용자 sieve 및 .dovecot.sieve |
| databases/admin/main.db | SQLite online backup + integrity_check | data/admin/main.db 계정/도메인 등 |
| databases/webmail/roundcube.db | SQLite online backup + integrity_check | data/webmail/roundcube.db |
| redis.rdb | Redis replication RDB + redis-check-rdb | data/redis/dump.rdb |
| auxiliary.tar.gz | certs/dkim/filter/admin-fetchmail 존재 경로 | TLS/DKIM와 부가 상태, live 파일의 변경 경고는 실패 |
| online-export.json | 시점·방법·메일 UID/GID·제한 | 복구 근거, 비밀 없는 결과 요약 |

실행 중 `/mail`을 tar로 그대로 복사하지 않는다. 현재 실제 설정 `maildir:/mail/%u`, home `/mail/%u`, inbox namespace를 확인한 경우만 진행한다. 추가/shared namespace는 지원되지 않아 실패로 남긴다. UID/GID는 실제 컨테이너의 mail 사용자를 조회한다(현재 8:12).

각 우편함은 컨테이너 안의 **새 임시 폴더**로 복사한다. 별도 프로세스에만 쓰는 설정에서 quota/검색 색인 플러그인을 제외하고 zlib 읽기를 유지한다. `.dovecot.sieve`를 우편함으로 오인하지 않도록 `maildir_stat_dirs=yes`, 메시지 hardlink 공유를 피하도록 `maildir_copy_with_hardlinks=no`를 쓴다. 필터는 별도 수집한다. 운영 Dovecot 설정·daemon·계정 비밀번호를 변경하지 않는다. `sync`나 역방향 `-R`을 사용하지 않는다. 전용 복사 과정의 정상적인 내부 색인 갱신/짧은 잠금 및 부하는 생길 수 있다.

Dovecot 종료 0 및 오류 출력 없음만 통과한다. 동기화 중 변경을 뜻하는 종료 2는 같은 새 목적지에서 최대 3회 확인하고 계속 실패하면 성공본을 만들지 않는다. 본문/진단 원문은 공개 로그에 내보내지 않는다. 새로 만든 임시 컨테이너 폴더만 회수한다. 이전 백업·실패 수집 폴더는 자동 삭제하지 않는다.

## 시점과 대기열의 제한

이 방식은 **각 우편함·각 DB의 복구 자료**다. 메일/계정 DB/Redis/프로젝트 전체가 같은 순간의 원자적 스냅샷은 아니다. 수집 도중 변경되거나 새로 수신한 메일은 사용자별 완료 시점에 따라 다음 일별 백업에 포함될 수 있다. antispam 부가 파일은 live 자료이며 트랜잭션 일관성을 주장하지 않는다. ClamAV 정의·검색 색인은 다시 생성한다.

SMTP 대기열은 시작·끝에 `postqueue -j`로 비어 있음을 확인한다. 대기 메시지를 온라인으로 일관되게 덤프하는 기능은 없으므로 **대기열이 보이면 이번 백업을 실패로 남긴다**. 잘못된 출력도 실패다. 메일 flush/hold/삭제·서비스 중단으로 강제하지 않는다. 두 검사 사이에 통과한 메일까지 같은 시점으로 묶는 보장은 없다. 지속적으로 바쁜 서버에서는 이 조건이 첫/일일 백업을 지연시킬 수 있으며 별도 큐 복구 설계가 필요하다.

## 확인한 것과 남은 것

- 네트워크 없는 Mailu Dovecot 시험 컨테이너, host mount 없음: 합성 메일의 본문·읽음/중요 표시·UID/UIDVALIDITY·구독·Sieve 보존 및 복사본 읽기 확인. `python3 scripts/check_online_mail.py`로 재현한다.
- 실제 G1의 7개 우편함을 중단 없이 수집하고 tar 읽기를 확인했다. 6.8초, 약 151KiB. 이 전후 Mailu 컨테이너 ID·시작 시각·재시작 횟수 불변. 평문은 Git 밖 0700 임시 경로에 보존한다. 이것은 공유기 첫 암호화 백업 전체가 아니다.
- 실제 내보내기에서 격리 우편함 읽기 시험을 수행할 수 있다: `python3 scripts/check_mailbox_restore.py /비공개/online-mailboxes.tar.gz`. 외부 네트워크/host mount 없이 일회용 복사본을 읽는다. 기본 tmpfs 512MiB이므로 큰 자료에는 별도 크기 설계가 필요하다.
- 실제 Redis 온라인 RDB도 113920 bytes 수집·검사했다. 실제 7개 우편함 복사본을 격리 환경에서 모두 읽었다. 사용자 첫 전체 실행에서 계정/웹메일 DB를 포함한 전체 수집과 공유기 전송본의 DB 무결성/7개 격리 우편함 읽기까지 통과했다. 아직 남음: Mailu 전체 로그인·폴더/첨부 확인, 외부 SMTP가 차단된 서비스 복구 시험. 우편함 읽기만으로 전체 Mailu 복구 완료라고 표시하지 않는다.

## 공유기 전송본에서 복구하는 순서

1. objects-v2의 `--project demo-mail` 선택 복원으로 **새 비공개 시험 폴더**에 각 파일을 꺼낸다. 공유기 전송본 해시/복호화를 확인한다. 운영 경로에는 쓰지 않는다.
2. files.tar.gz를 시험 디렉터리에 안전하게 해제한다. live data는 없으므로 아래 자료를 합쳐야 한다. 원래 Mailu 2024.06 이미지와 구성에 맞추고 외부 DNS/터널/SMTP/자동 수신/전달을 차단한다.
3. online-mailboxes.tar.gz를 별도 폴더에 해제한다. users.json의 숫자 매핑을 검토해 각 maildirs/숫자 폴더의 **내용**을 시험 Mailu의 data/mail/원래계정 아래 복원한다. 기존 운영 메일을 목적지로 지정하지 않는다. Sieve tar는 같은 data/mail 아래 별도로 해제한다. 경로 탈출·symlink 검토와 소유권 복원이 필요하다.
4. main.db와 roundcube.db를 해당 data/admin, data/webmail에 배치하고 SQLite integrity_check를 수행한다. SQLite 세 파일(db/wal/shm)의 live 사본을 섞지 않는다. Redis RDB는 시험 Redis의 data/redis/dump.rdb로 복구한다. auxiliary.tar.gz는 Mailu 프로젝트 기준 data 경로로 해제한다.
5. 우편함 소유권을 기록된 mail UID/GID로 맞춘다. 현재 8:12이나 버전/이미지 변경 시 재검증한다. 실제 설정·키의 소유권/권한을 확인하고 ClamAV/FTS 등 재생성 자료를 준비한다.
6. 외부 연결 없는 별도 Compose 프로젝트·별도 볼륨에서 의존 서비스 → admin/Redis → imap/기타 서비스를 시작한다. 기존 컨테이너나 production 볼륨/네트워크를 재사용하지 않는다. 계정 로그인, 폴더/UID/flags, 본문·첨부 읽기와 Sieve, DKIM/TLS를 확인한다. SMTP 외부 송신/수신은 시험하지 않는다.
7. 백업 ID·시험 결과·소요시간을 기록한다. 운영 전환/덮어쓰기/중단은 별도 사용자 확인 이후다. 아직 이 전체 절차는 실증되지 않았다.

근거: [Dovecot 2.3 기반 backup/sync 매뉴얼](https://manpages.debian.org/bookworm/dovecot-core/doveadm-backup.1.en.html), [Maildir 설정](https://doc.dovecot.org/2.3/configuration_manual/mail_location/Maildir/), [Sieve 파일 위치](https://doc.dovecot.org/2.3/configuration_manual/sieve/file/). 공식 단방향 백업은 목적지의 변경을 덮어쓸 수 있으므로 항상 이번 실행의 새 목적지만 사용한다.
