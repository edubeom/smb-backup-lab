# 실제 서버 적용 전 운영 안내

현재: **첫 전체 백업·선택 설정 파일 복원·메일 DB/7개 우편함 읽기 성공**을 사용자 실행 결과로 확인. 전체 서비스 복구 시험과 일일 예약/자동 정리는 아직 남아 있다.

## 권장: 연결 준비 도구

현재 공유기와의 비인증 협상에서 SMB 2.0.2를 확인했다. G1의 기본 SMB 2.1 이상 협상과 맞지 않아 오류 22가 발생할 수 있으므로 도구의 기본값은 `--smb-version 2.0`이다. [실제 진단 결과](diagnostic-smb.md) 참조.

G1의 개인 터미널에서 다음 명령을 실행한다. sudo 암호와 공유 암호는 터미널 안에서만 입력하고 채팅/영상에 기록하지 않는다.

```bash
sudo python3 /project/smb-backup-lab/scripts/setup_router.py --apply --smb-version 2.0
```

### 연결에 실패했을 때 비밀번호 다시 입력

```bash
sudo python3 /project/smb-backup-lab/scripts/setup_router.py --apply --reset-smb-password
```

`[sudo]` 암호는 G1의 로그인 암호다. 도구 안의 **공유 비밀번호**는 공유기 계정 `backup-user`의 암호이며 서로 다를 수 있다. 공유 암호는 두 번 입력받고 화면에 표시하지 않는다. 최대 3회 다시 시도하며, **입력한 인증으로 실제 연결 검사에 성공한 뒤에만** 기존 공유 인증 파일을 교체한다. 실패·취소 시 기존 인증을 유지하고 임시 입력 파일은 정리한다. 원본 비밀·백업 자료는 건드리지 않는다.

일반 --apply도 저장된 인증으로 연결하다 인증/권한 오류 13이 발생할 때만 재입력을 안내한다. 오류 22와 네트워크/미상 오류는 암호를 반복해서 묻지 않고 즉시 중단한다. 비밀번호 외에 계정/폴더 권한·공유 설정도 원인일 수 있으므로 실패 시 표시되는 숫자 오류만 알려 준다. 원문 mount 출력이나 암호는 표시하지 않는다. 이미 공유가 연결되어 있으면 재입력을 위해 연결을 끊지 않고 중단한다.

이 도구는 필요한 경우 cifs-utils를 설치하고, 비밀번호를 표시하지 않는 입력으로 인증 파일을 새로 만들고, 지정한 공유를 연결한다. 이미 있는 인증을 우선 재사용하며, 재입력 시에는 연결 검사에 성공한 값만 저장한다. 새 전용 폴더에 한해 저장소 ID와 별도 암호화 키, 실제 hostname을 반영한 config를 생성한다. 기존 저장소가 있는데 로컬 config가 없으면 자동 인수하지 않고 조사를 위해 멈춘다. 기존 config는 보존한다.

연결 후 실제 공유 여유 공간을 보여 주고 **G1 최대 사용량(GiB)을 직접 묻는다. 기본값 없이 입력해야 하며 10GiB 예비 공간을 남긴다.** 약 900GB 전체를 G1에 할당하지 않는다. 백업·메일 중단·파일 삭제·fstab 변경·timer 활성화는 수행하지 않는다.

기본 실행 `python3 scripts/setup_router.py`는 읽기 전용 계획 확인이다. 사용자 개인 터미널의 --apply 완료 출력과 G1의 실제 SMB 2.0 마운트를 확인했다. 사용자 선택 상한은 50GiB다. 에이전트 셸의 sudo는 여전히 별도 터미널 인증이 필요하며 첫 전체 백업과 선택 파일/우편함 읽기는 사용자 실행 결과로 확인했다. 전체 서비스 복구는 남아 있다. 인증 실패 시 기존 파일을 보존하므로 비밀번호를 수정해야 한다면 서버에서 `sudoedit /etc/g1-backup/smb-credentials`를 사용한다. 키를 G1 밖 암호 관리자에도 보관해야 한다. 키 값은 도구가 출력하지 않는다.

실행 뒤 채팅에는 비밀번호 대신 **“연결 준비 완료” 또는 비밀 없는 오류 상태**만 알려 준다. 이후 실제 연결/권한/용량을 재검증하고 중단 없는 수집을 수행한다.

## 사용자 터미널에서 준비할 자격증명과 권한

현재 Codex 셸은 sudo가 비밀번호를 요구한다. 비밀번호를 채팅에 적지 말고 G1의 개인 터미널에서 아래 작업을 수행한다. 다음 명령은 아직 실행하지 않았다.

```bash
sudo apt-get update
sudo apt-get install cifs-utils gnupg
sudo install -d -m 0700 /etc/g1-backup /var/lib/g1-backup
sudo install -d -m 0700 /var/lib/g1-backup/work
sudo install -d -m 0755 /mnt/router-backup
sudo install -m 0600 /dev/null /etc/g1-backup/smb-credentials
sudoedit /etc/g1-backup/smb-credentials
```

인증 파일에는 `username=backup-user`, 다음 줄에 `password=`와 실제 비밀번호를 **편집기 안에서만** 입력한다. 채팅·쉘 명령 인자·캡처 화면에 입력하지 않는다. 이미 인증 파일이 있으면 위 install 명령으로 덮어쓰지 말고 기존 상태부터 확인한다.

```bash
sudo mount -t cifs //192.0.2.1/backup /mnt/router-backup \
  -o credentials=/etc/g1-backup/smb-credentials,nosuid,nodev,noexec,file_mode=0600,dir_mode=0700,vers=2.0
```

확인된 SMB 2.0.2에 맞게 vers=2.0으로 연결한다. 실패 시 SMB1로 낮추지 말고 원인을 확인한다. 이 단계에서는 fstab/자동 마운트를 변경하지 않는다. 연결 후 공유 주소·파일시스템·기존 `server` 내용과 실제 여유 공간을 확인한다. 비밀 있을 수 있는 목록 전체는 영상에 노출하지 않는다.

## 저장소와 키 확정

1. `server/g1` 기존 내용 확인. 비어 있는 새 전용 영역을 정하고 `.g1-repository-id`에 새 랜덤 식별자를 기록한다. 기존 폴더/marker가 있으면 덮어쓰지 않는다.
2. 암호화용 긴 무작위 비밀번호는 공유 로그인 비밀번호와 별도로 만든다. `/etc/g1-backup/archive-passphrase`에 root 0600으로 직접 저장한다. G1 밖의 암호 관리자에도 별도로 보관하고 그것으로 복호화 가능한지 시험한다. 이 파일은 Git/영상에 넣지 않는다.
3. `config/backup.example.json`을 `/etc/g1-backup/backup.json`으로 복사하고 root 0600으로 둔다. 실제 hostname, 전용 저장소와 ID를 맞추고 용량 예산·예비 공간을 실측으로 확정한다. 예시의 100GiB/10GiB는 승인된 할당이 아니다. 공유기의 약 900GB는 전체 공용 용량이다.
4. `enabled=true`는 아래 실제 실행 전 점검 후 설정한다. 키·config·staging·목적지가 서로 잘못 겹치면 도구가 거부한다.

## 권장 첫 백업 실행

현재 공유 연결과 G1 상한 50GiB가 준비되어 있다. 최신 저장 형식은 프로젝트별·시스템별 독립 암호화 파일(objects-v2)이며 config에 archive_layout이 없는 기존 설정도 기본으로 이 형식을 사용한다.

```bash
sudo python3 /project/smb-backup-lab/scripts/first_backup.py --run
```

사용자의 “메일 중단 없이 진행”에 맞춰 **중단 동의 질문과 서비스 stop/start 경로를 제거**했다. 메일·웹·DB를 계속 실행하며 사전 검사 → 온라인 수집 → 프로젝트별 암호화/공유기 읽기 검사 → SSH·Cloudflare·프로젝트 파일 시험 복원과 공유기에서 되읽은 메일 DB 무결성·격리 우편함 읽기를 수행한다. 기존 중단 승인 파일은 읽지 않으며 예전 `--mail-stop-approval` 옵션은 더 이상 받지 않는다.

아무 작업 없이 사전 상태만 확인하려면 --run을 빼고 실행한다. [프로젝트별 저장 형태](backup-layout.md), [메일 온라인 수집과 복구](mail-online.md)를 먼저 읽는다. 복구 키를 서버 밖에 보관하는 일은 별도로 필요하다. 비밀번호나 키를 채팅으로 보내지 않는다.

## 중단 없는 수집의 범위

- Mailu: 우편함별 Dovecot 단방향 백업, 계정/웹메일 SQLite 전용 내보내기, Redis RDB, 설정·TLS·DKIM·필터 자료. SMTP 대기열이 수집 전후 비어 있어야 한다. 메시지가 대기 중이거나 수집이 실패하면 **서비스를 계속 실행하고 백업을 실패로 남긴다**.
- PostgreSQL: globals와 DB별 custom dump. Story.db: SQLite online backup. 실행 중 DB 파일을 그대로 tar로 만들지 않는다.
- `/project`: 프로젝트별 파일. `.git`/`.env` 포함, 캐시 제외. Mailu의 live data 전체 복사는 하지 않는다.
- 시스템: `/etc` 전체와 사용자/root 운영 설정, 패키지·네트워크·방화벽 상태.
- Docker: 이미지, 실행 구성, 현재 사용하지 않는 볼륨. 볼륨이 도중에 활성화되면 실패한다.

우편함·DB·프로젝트 전체에 걸친 공통 시점은 보장하지 않는다. 수집 중 새로 도착한 메일이나 변경은 각 수집 시점에 따라 다음 일별 백업에 들어갈 수 있다. 대기열을 안전하게 온라인 덤프하는 기능은 없고, 검사 시 비어 있는 경우만 진행한다. 이 제한을 숨겨 전체 원자적 스냅샷으로 표시하지 않는다.

고급 운영자가 수집만 수행할 명령:

```bash
sudo python3 /project/smb-backup-lab/scripts/prepare.py --config /etc/g1-backup/backup.json
```

고유 `prepared_path`에 평문 자료를 생성한다. 바로 전송하려면 출력 경로를 config의 prepared에 맞춰야 한다. 일반적인 첫 실행은 위 first_backup 도구가 새 경로를 자동 전달하므로 수동 수정하지 않아도 된다. 기존 수집 자료는 삭제하지 않는다. 변경 중인 tar, SQLite/Dovecot/Redis/DB 오류는 완료 receipt 생성을 막는다.

## 첫 암호화 전송과 복원

```bash
sudo python3 /project/smb-backup-lab/src/g1_backup.py preflight --config /etc/g1-backup/backup.json
sudo python3 /project/smb-backup-lab/src/g1_backup.py run --config /etc/g1-backup/backup.json
sudo python3 /project/smb-backup-lab/src/g1_backup.py retention-plan --config /etc/g1-backup/backup.json
```

공유기에서 전송본 전체를 다시 읽고 복호화·파일 해시 비교·빈 시험 경로 추출이 통과한 뒤 `.success.json`이 생긴다. 실패한 partial·미확정 암호화 파일은 성공 수에 넣지 않고 자동 삭제하지 않는다. 실패 로그에는 원문 비밀을 숨기고 상태 파일은 보호된 작업 폴더에 둔다.

서비스 복원 시험은 `docs/restore.md`대로 별도 인스턴스에서 진행한다. 파일 검증 성공만으로 서비스 복원 성공이라고 하지 않는다. 평문 시험 자료는 Git 밖의 root 전용 공간에 둔다.

## 예약 실행 준비

일일 실행기는 온라인 수집 → 암호화 전송 → 전송본 검증 → **승인된 경우만** 성공 5일/그 실행의 새 평문 수집본 정리를 연결한다. [일일 운영 안내](daily-operations.md)에 정확한 범위와 개인 터미널 적용 명령을 정리했다. timer/mount는 사용자17:28 적용으로 설치됐고 timer active/enabled를 직접 확인했다. 기존 수집/복원본과 다른 프로그램의 백업은 정리하지 않는다.

## 첫 전체 백업 이후

사용자 first_backup 실행은 성공했다. [첫 성공 기록](first-backup-result.md)과 [다음 PostgreSQL 복구 명령](postgres-restore-test.md)을 따른다. 같은 백업을 새로 만들기 위해 first_backup을 반복할 필요는 없다. 이후 사용자17:28 설치·정리 정책 승인으로 timer active/enabled가 됐다. 첫 예약 성공은 아직 대기다.

## PostgreSQL 복구 통과 후 키 보관

사용자 실행 결과로 PostgreSQL 5개 실제 복원과 첫 성공본 2.819GiB를 확인했다. 같은 크기 5/6개는 50GiB 안에 들어간다. 현재 복구 키 외부 보관은 미완료이며 [개인 터미널 보관·숨김 재입력 안내](recovery-key.md)를 따른다. 이 도구는 실제 키를 출력/교체하지 않는다.

## 외부 키 검증 후 일일 운영 준비

2026-10-20 16:06:37 KST 사용자 결과로 외부 보관 키 재입력·실제 공유기 명세 복호화 통과. 값은 수신하지 않았다. 일일 mount/timer와 승인 기반 정리 도구를 준비했으며 [일일 운영 절차](daily-operations.md)로 이어간다. 실제 설치 결과와 첫 예약 성공은 아직 미확인이다.

## 일일 예약 활성 확인

2026-10-20 17:28:34 KST 설치 성공. 다음2026-10-21 04:00 KST. 재실행은 필요 없다. 향후 설치 확인 입력은 y로 줄였고 Enter는 취소한다. 기존 승인 기록/정리 범위는 유지한다. [일일 운영 현재 상태](daily-operations.md)를 따른다.
