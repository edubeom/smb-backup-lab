"""python -m dataviewer 원본... [--port 8820] [--no-watch] [--no-open] [--layout-dir 폴더]

원본: model.json · model.light.json · 정의서 .xlsx · pgModeler .dbm · 실제 DB `postgresql://…[#스키마,스키마]`
(비밀번호가 든 접속 문자열은 명령줄 대신 `env:환경변수이름` 으로 -- 다른 사용자가 프로세스 목록에서 보지 않게)"""
from __future__ import annotations

import argparse
import io
import os
import sys
import webbrowser

from . import __version__, sources
from .server import Viewer, make_server


def main(argv=None) -> int:
    for s in (sys.stdout, sys.stderr):              # Windows 파이프는 cp1252 -- 한글이 깨지지 않게
        if isinstance(s, io.TextIOWrapper):
            s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="dataviewer", description="웹 ERD 뷰어 (보기 전용) -- model.json · 정의서 .xlsx · pgModeler .dbm")
    ap.add_argument("sources", nargs="+", help="원본 (여러 개를 한 화면에): 파일 또는 postgresql://… 또는 env:환경변수")
    ap.add_argument("--layout-dir", default=None, help="실제 DB 원본의 위치 파일을 둘 폴더 (기본: 지금 폴더)")
    ap.add_argument("--port", type=int, default=8820)
    ap.add_argument("--no-watch", action="store_true", help="실시간 반영을 끈다 (화면의 '적용' 버튼으로 다시 읽는다)")
    ap.add_argument("--no-open", action="store_true", help="브라우저를 열지 않는다")
    ap.add_argument("--version", action="version", version=f"dataviewer {__version__}")
    a = ap.parse_args(argv)
    specs = []
    for s in a.sources:
        if s.startswith("env:"):
            v = os.environ.get(s[4:])
            if not v:
                print(f"[dataviewer] 환경변수 {s[4:]} 가 비어 있다", file=sys.stderr)
                return 2
            s = v
        specs.append(s)
    try:
        viewer = Viewer(specs, layout_dir=a.layout_dir)
    except (sources.SourceError, ValueError) as exc:
        print(f"[dataviewer] {exc}", file=sys.stderr)
        return 2
    if not a.no_watch:
        viewer.start_watch()
    try:
        srv = make_server(viewer, port=a.port, live=not a.no_watch)
    except OSError as exc:
        print(f"[dataviewer] {a.port} 번 포트를 열 수 없다: {exc} (--port 로 바꾼다)", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    for v in viewer.model()["sources"]:
        state = f"오류: {v['error']}" if v.get("error") else f"테이블 {len(v['tables'])} · 관계 {len(v['relations'])}"
        print(f"[dataviewer] {v['name']} ({v['kind']}) -- {state}")
    print(f"[dataviewer] {url}  ({'실시간 반영' if not a.no_watch else '적용 버튼으로 반영'}, 끄려면 Ctrl+C)")
    if not a.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        viewer.stop()
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
