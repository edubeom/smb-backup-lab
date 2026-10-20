# 공유기 백업의 PostgreSQL 복구 시험

첫 전체 백업 `20261020T040000000000-1234abcd`은 사용자 실행 결과로 성공을 확인했다. 다음 도구는 그 성공본을 바꾸지 않고 읽어서 **실행 중 PostgreSQL의 globals와 모든 DB 덤프**를 새 시험 컨테이너에 복원한다. 운영 DB·서비스·원본 볼륨·공유기 백업에는 쓰지 않는다.

## 실행

G1 개인 터미널에서 실행한다. 에이전트 셸은 sudo 인증이 필요하고 `/etc/g1-backup`·공유기 경로가 root 전용이므로 이 단계는 사용자 터미널 인증이 필요하다. 비밀번호/키를 채팅으로 보내거나 파일 권한을 공개로 바꾸지 않는다.

```bash
sudo python3 /project/smb-backup-lab/scripts/verify_postgres.py \
  --snapshot-id 20261020T040000000000-1234abcd --run
```

마지막 JSON 결과만 알려 준다. `--run`을 빼면 백업 크기/공간 보고만 하고 DB 시험을 시작하지 않는다. 실패 원문에는 DB 내용이 포함될 수 있으므로 root 전용 시험 폴더의 진단 파일을 채팅/영상에 그대로 복사하지 않는다.

## 검증하는 순서

1. mount/저장소 ID와 고정 성공 ID·objects-v2 상태·구성 파일 존재·크기를 검사한다. 실행 모드에서는 성공본의 **전체 암호화 파일 SHA-256**도 다시 읽어 비교한다.
2. 성공본의 PostgreSQL 구성요소와 Docker 이미지 명세만 선택 복호화한다. catalog/globals/덤프는 새로운 `/var/lib/g1-backup/work/verify-pg-RUN_ID-UUID` 아래 모은다. 기존 prepared 덤프를 재사용하지 않는다.
3. 명세에 기록된 PostgreSQL 이미지 SHA-256 ID가 로컬에 설치되어 있어야 한다. 자동 pull/load하지 않는다. 새 컨테이너는 외부 네트워크 none, 공개 포트 없음, host bind 없음, 기존 볼륨 재사용 없음으로 실행한다. CPU 1개·메모리 2GiB 상한, postgres 사용자·추가 권한 없음으로 제한한다.
4. 데이터는 새 컨테이너의 `/tmp/g1-verify-pgdata`에 생성한다. 공식 이미지의 새 익명 기본 볼륨은 사용하지 않는다. 백업 SQL의 원래 계정을 복원할 수 있도록 시험용 관리 계정은 매번 무작위로 만든다.
5. `psql -X --set=ON_ERROR_STOP=1`로 globals를 복원한다. 원래 postgres 역할과 시험 관리자가 달라 중복 역할 오류를 무시할 필요가 없다. 사용자·소유권·ACL을 빼는 옵션을 사용하지 않는다.
6. `pg_restore --exit-on-error --create`로 catalog의 모든 DB를 복원한다. 기본 postgres DB는 **새 시험 클러스터의 빈 초기 DB에 한해** 교체한다. 운영 postgres DB를 대상으로 연결하지 않는다. DB 이름을 연결 문자열로 해석할 수 없도록 제한하고 catalog/덤프 누락·경로 탈출을 거부한다.
7. 각 DB의 로컬 일반 테이블·materialized view를 실제로 count하며 인덱스 유효성·시퀀스/확장 개수를 검사한다. 행 내용·DB/테이블 이름·비밀번호 해시는 결과에 출력하지 않는다. 완료 결과에는 DB별 번호와 검사 개수만 포함한다.
8. 자신이 생성한 ID와 무작위 소유 라벨을 재확인한 컨테이너만 회수한다. `docker prune`이나 기존 컨테이너 제어는 없다. 원래 실행 중이던 컨테이너의 ID·실행 여부·시작 시각·재시작 횟수를 비교한다. 다른 복구 시험 컨테이너는 이 비교에서 제외한다.
9. root 전용 결과와 `last-postgres-verification.json`을 남긴다. 공유기 백업·기존 평문 수집/복원 자료는 삭제하지 않는다. timer는 켜지 않는다.

## 결과 읽기

- `status=shared-snapshot-postgresql-restore-verified`: 고정 공유기 성공본에서 역할과 모든 DB를 새 PostgreSQL에 실제 복원·읽기 검사했다.
- `cipher_hashes_verified=true`: 공유기 암호화 파일 전체를 다시 읽어 검사했다.
- `source_container_states_unchanged=true`: 기존 컨테이너 상태가 시험 전후 같다. 외부 사용자의 모든 요청에 대한 가용성 측정은 아니다.
- `capacity.snapshot_gib`: 이번 성공본 암호화 파일의 합계.
- `five_similar_sets_gib`: 같은 크기의 최근 성공 5개 예상량.
- `six_similar_sets_gib`: 새 백업 검증 후 오래된 것 정리 전에 필요한 6개 예상량. **삭제 승인을 의미하지 않는다.**
- `repository_used_bytes`: 실패/미확정 파일을 포함한 전용 저장소 현재 사용량. 향후 증가·성공 metadata·다른 프로그램의 공용 공간 사용도 고려해야 한다.

## 확인 범위와 남은 일

합성 데이터 2개 DB를 실제 PostgreSQL 16.15 이미지에서 dump/복원했다. 역할/소유권/명시 SELECT 권한, 행 합계와 serial 시퀀스 값, 손상 덤프 실패와 시험 컨테이너 회수를 확인했다. 재현은 `scripts/check_postgres_fixture.py --image-id <설치된 SHA256 ID>`로 한다. 이 합성 자료 시험을 실제 공유기 성공본 복구로 표시하지 않는다.

사용자 root 실행 결과로 실제 성공본 PostgreSQL 5개 복원·읽기와 암호화 hash 재검증을 확인했다. [실제 결과와 공간 측정](postgres-restore-result.md)을 따른다. 처음 백업에는 원본 덤프 시점의 테이블 행 수/해시 기준이 없어 복구 후 행 수를 원본과 대조했다고 주장하지 않는다. 현재 검증은 덤프 오류 없는 복원과 복원 DB의 실제 읽기다. 애플리케이션 로그인·업무 조회, 원본과 스키마/권한을 별도 대조하는 시험, PostgreSQL 15/Qdrant의 보존 볼륨 복구, Mailu 전체 로그인·첨부·송수신 차단 환경의 복구는 별도로 남아 있다.

원본에 추가 확장/비표준 tablespace/외부 데이터가 있다면 같은 이미지와 전용 복구 설계가 필요할 수 있다. 도구가 이를 무시하며 성공 처리하지 않는다. 현재 지원 밖의 DB 이름·이미지 누락·SQL/확장 오류는 실패로 남긴다.

공식 근거: [PostgreSQL 16 pg_dumpall](https://www.postgresql.org/docs/16/app-pg-dumpall.html), [SQL 덤프 복원과 ON_ERROR_STOP](https://www.postgresql.org/docs/16/backup-dump.html). globals에는 비밀번호 해시가 포함될 수 있어 보호된 시험 환경에서만 사용한다.
