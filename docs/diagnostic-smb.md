# SMB 오류 22 원인 조사 — 2026-10-20

사용자는 공유 암호를 입력한 뒤 `mount` 종료 코드 32 / 시스템 오류 22를 보고했다. 이 숫자는 암호 오류로 단정할 근거가 아니며 기존 도구의 반복 재입력 안내가 적절하지 않았다.

## 실제 확인한 증거

- 서버: Linux 6.8.0-147-generic, mount.cifs 7.0.
- 읽을 수 있는 커널 journal에 `cifs_mount failed w/return code = -22` 및 기본 SMB 협상이 2.1 이상이라는 메시지 확인.
- sudo는 여전히 비밀번호 필요. 저장된 공유 암호를 출력하거나 다른 권한으로 읽어 오지 않음.
- `smbprotocol` 1.17.0의 Connection.connect로 **인증 없이 협상만** 수행. Session 로그인, Tree/share 접속, 파일 읽기/쓰기 없음.

| 요청 | 실제 결과 |
|---|---|
| 자동 협상 | 0x0202 (SMB 2.0.2) 선택 |
| SMB 2.0.2 명시 | 0x0202 협상 성공 |
| SMB 2.1 명시 | InvalidParameter |
| SMB 3.0 명시 | InvalidParameter |

증거 JSON: `media/smb-diagnosis.json`. 재현 코드: `scripts/diagnose_smb.py`. 진단용 라이브러리는 `/tmp/g1-smb-diagnostics` venv에만 설치했으며 백업 실행의 필수 의존성으로 추가하지 않았다.

현재 서버/공유기의 조합에서 기본 SMB 버전과 서버 지원 버전이 맞지 않는 사실을 확인했다. 다만 **인증 성공과 실제 마운트 성공은 아직 확인되지 않았다**. 버전을 맞춘 후 계정/암호/공유 권한 단계에서 다른 오류가 발생할 가능성은 남아 있다.

## 수정

- 이 공유기용 기본 마운트에 `vers=2.0`을 명시한다. 버전은 `--smb-version`으로 선택할 수 있으며 새 config에 기록한다.
- SMB 오류를 MountFailure로 분류한다. errno 13만 인증/권한 재입력 대상으로 삼고, 22 및 네트워크/미상 오류는 원인을 확인하도록 즉시 중단한다.
- 이미 저장된 인증이 있으면 다음 명령으로 먼저 그대로 시도한다. 불필요한 reset은 하지 않는다.

```bash
sudo python3 /project/smb-backup-lab/scripts/setup_router.py --apply --smb-version 2.0
```

원문 mount stderr·암호는 출력하지 않는다. 실패 시 입력 임시 자료만 정리하고 기존 인증/백업은 유지한다. 서비스 중단·백업 삭제·공유기 설정 변경은 수행하지 않았다.

## 참고

[Linux CIFS 공식 문서](https://docs.kernel.org/admin-guide/cifs/usage.html)는 기본 SMB 2.1 이상과 `vers` 선택을 설명한다. [smbprotocol 원본 README](https://github.com/jborean93/smbprotocol)는 저수준 Connection을 이용한 dialect 협상을 설명한다. 공유기 특정 지원 버전은 제조사 모델명만으로 추정하지 않고 위 실제 협상 결과를 근거로 했다.
