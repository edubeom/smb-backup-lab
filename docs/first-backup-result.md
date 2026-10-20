# 첫 전체 백업 실행 결과

2026-10-20 한국 시간, 사용자가 G1 개인 터미널에서 `first_backup.py --run`을 실행한 결과를 제공했다. 에이전트가 root 설정/비밀을 직접 열어 본 결과와 구분한다.

- 성공 ID: `20261020T040000000000-1234abcd`
- 결과: `backup-selected-files-and-mailbox-readback-verified`
- 올바른 CIFS mount/키 존재/50GiB 상한 확인, 공유기 새 임시 파일 쓰기·읽기 검사 통과.
- Mailu 온라인 내보내기, PostgreSQL 덤프, SQLite/프로젝트별 파일, SSH·Cloudflare를 포함한 설정, 비활성 볼륨·Docker 이미지 수집 완료.
- objects-v2 전체 구성요소 암호화·공유기 읽기/복호화/해시 검사를 통과하여 성공 기록 생성.
- 공유기 전송본에서 관리 프로젝트 README, SSH 서버 설정, Cloudflare 설정을 별도 경로에 복원. 운영 파일에는 적용하지 않음.
- 메일 SQLite 무결성 통과, 우편함 7개는 외부 네트워크/host bind 없는 격리 컨테이너에서 읽기 통과.
- services_interrupted=false. timer 활성화와 기존 백업 삭제 없음.

보호된 시험 복원 경로는 `/var/lib/g1-backup/work/first-restore-20261020T040000000000-1234abcd-05b08861`이다. 원본/평문 자료/인증 값은 Git에 넣지 않는다. 공개 영상에는 이 문서의 원본 경로/터미널 대신 별도 요약 화면을 사용한다.

## 후속 검증과 남은 확인

- 후속 사용자 root 검사에서 백업 2.819GiB, 같은 크기 5개 14.094GiB/6개 16.912GiB로 50GiB 안에 들어감을 확인했다. 결과는 [PG 복구 결과](postgres-restore-result.md)에 남겼다. 에이전트가 root 비밀을 직접 읽었다고 주장하지 않는다.
- PostgreSQL: 공유기 전송본 globals와 5개 DB 실제 격리 복원·읽기 통과. 원본 행 수 대조와 앱 로그인은 별도.
- Mailu 전체: 계정 로그인·폴더/첨부·키/필터·부가 서비스 복구. 우편함 읽기는 전체 서비스 시험을 대신하지 않는다.
- 복구 키 외부 보관 확인과 일일 04:00 Asia/Seoul 실행·mount/알림 설정.
- 최근 성공 5일 정책의 삭제 후보/정리 범위 확인. 아직 자동 삭제 없음.

첫 백업 자체는 성공했으며 `service_restore_validated=false`이므로 전체 복구 검증 완료로 표시하지 않는다.
