"""웹 ERD 뷰어 -- 정본(model.json)·light 정본·정의서 엑셀·pgModeler .dbm·실제 PostgreSQL 을 읽어 브라우저에 그린다 (보기 전용).

- 원본은 고치지 않는다. 옮긴 위치만 원본 옆 `<원본>.layout.json` 에 남긴다 (git 에 넣어 팀이 같은 배치를 본다)
- 파일에 있는 것만 보인다 -- 이름이 비슷하다고 관계를 짐작해 그리지 않는다
- 원본을 고치면 1초 안에 다시 읽는다 (실시간을 끄면 '적용' 버튼). 깨진 원본은 마지막 정상 화면을 두고 오류만 알린다
- 표준 라이브러리만 (엑셀을 읽을 때만 openpyxl). 서버는 이 컴퓨터(127.0.0.1)에서만 연다

    python -m dataviewer db/model.json out/04_table_spec.xlsx other/03_model.dbm [--port 8820] [--no-watch]
"""
__version__ = "0.9.3"
