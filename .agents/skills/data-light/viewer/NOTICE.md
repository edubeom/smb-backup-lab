# 웹 ERD 뷰어 (사본)

`dataviewer/` 는 data-expert-framework(전문판)의 `runtime/viewer/dataviewer` 를 정해 둔 커밋 그대로 담은 사본입니다
(`viewer.lock.json` 에 커밋과 파일 지문). 저작권자(sungbeom78)의 결정(2026-10-17)으로 **이 사본은 이 저장소의 MIT 라이선스**로 공개합니다.
전문판의 다른 부분은 공개 대상이 아닙니다.

- 쓰는 법: `python skills/data-light/scripts/datalight.py view data/model.light.json` → 브라우저가 열립니다 (보기 전용).
- 고치지 않습니다. 원본에서 고치고 `python tools/update_viewer.py --from ../data-expert-framework --ref <커밋>` 으로 다시 받습니다.
