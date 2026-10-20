# 기능 지도
|기능|쓰는 파일|확인|
|---|---|---|
|백업 엔진|src/, scripts/prepare.py, scripts/daily.py|unittest 합성 암호화/복원/보관|
|연결/운영|scripts/setup_router.py, scripts/enable_daily.py, systemd/|합성 오류/동의 경계, 운영 적용은 별도|
|복구 검증|scripts/verify_postgres.py, src/postgres_restore.py|합성 시험 + 사례의 실제 실행 결과|
|강의 자료|scripts/build_broadcast.py, media/broadcast/2026-10-20/|26쪽·노트/PDF/화면 검사|

## 데이터 (표)
새 업무 DB 없음. 기존 DB는 백업과 격리 복원 대상으로 다룬다.
