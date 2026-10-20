"""보기 서버 -- 원본을 읽어 두고(마지막 정상본 유지), 바뀌면 알린다. 웹 틀과 무관한 Viewer + 표준 라이브러리 HTTP 서버.

콘솔(FastAPI)은 같은 Viewer 를 불러 /viewer 아래에 붙인다 (dataconsole.app).
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, layout, sources

STATIC = Path(__file__).with_name("static")
MAX_BODY = 2_000_000
STREAM_SECONDS = 55          # 변경 알림 연결은 이만큼 뒤 닫는다 -- 브라우저가 다시 잇는다 (서버 종료·연결 수가 막히지 않게)


def local_host(host: str | None, port: int | None = None) -> bool:
    """Host 머리가 이 컴퓨터 이름인지 (DNS 리바인딩 막기). port 를 주면 포트까지 맞아야 한다."""
    h = (host or "").strip().lower()
    for name in ("127.0.0.1", "localhost", "[::1]"):
        if h == name and port is None:
            return True
        if h.startswith(name + ":") and h[len(name) + 1:].isdigit() and (port is None or h == f"{name}:{port}"):
            return True
    return False


class Viewer:
    """원본 파일 묶음. refresh() 가 바뀐 파일만 다시 읽는다. 읽다가 깨지면 마지막 정상본을 두고 오류만 붙인다."""

    def __init__(self, paths, *, poll: float = 1.0, layout_dir=None):
        if not paths:
            raise ValueError("원본을 하나 이상 준다 (model.json · model.light.json · 정의서 .xlsx · .dbm · postgresql://…)")
        self.sources = []
        for p in paths:
            src = sources.make_source(str(p) if sources.is_db(str(p)) else p, layout_dir=layout_dir)   # 모르는 형식은 시작할 때 바로 알린다
            if src.id not in [x.id for x in self.sources]:
                self.sources.append(src)
        self.paths = [s.path for s in self.sources if isinstance(s, sources.FileSource)]
        self.poll = poll
        self.version = 0
        self._stamp: dict = {}
        self._views: dict = {}
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()           # 지켜보기·다시 읽기·위치 저장이 한 번에 하나씩 (옛 결과가 새 결과를 덮지 않게)
        self._changed = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread = None
        self.refresh()

    # ---------------------------------------------------------------- 읽기
    @staticmethod
    def _stat(p: Path):
        try:
            st = p.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    def refresh(self, force: bool = False) -> bool:
        with self._refresh_lock:
            return self._refresh(force)

    def _refresh(self, force: bool) -> bool:
        changed = False
        for src in self.sources:
            sid = src.id
            if force:
                src.refresh_now()
            stamp = (src.stamp(), self._stat(layout.layout_path(src.layout_base)))
            if not force and self._stamp.get(sid) == stamp:
                continue
            last = self._views.get(sid)
            gone = src.missing()
            stem = getattr(src, "path", Path(src.layout_base)).stem
            if gone:
                view = dict(last or {"tables": [], "relations": [], "project": stem}, error=gone)
            else:
                try:
                    view = src.read()
                    view["error"] = None
                except sources.SourceError as exc:
                    view = dict(last or {"tables": [], "relations": [], "project": stem}, error=str(exc))
                    if last and last.get("tables"):
                        view["error"] += " -- 마지막으로 읽은 내용을 보여 준다"
            kind = view.get("kind") if view.get("kind") in ("light",) else src.kind
            view.update(id=sid, kind=kind, path=src.display, name=src.name)
            saved = layout.load(src.layout_base)
            view["layout_saved"] = bool(saved)
            view["layout"] = layout.load_all(src.layout_base)          # 단계별 위치·고정·직전 배치 (화면이 단계마다 쓴다)
            view.setdefault("levels", {"concept": None, "logical": None})
            view["layout_file"] = layout.layout_path(src.layout_base).name
            tables = []
            for t in view["tables"]:                # file_pos: 원본(.dbm)에 적힌 위치, pos: 화면에 쓸 위치
                fp = t["file_pos"] if "file_pos" in t else t.get("pos")
                tables.append(dict(t, file_pos=fp, pos=saved.get(t["key"]) or fp))
            view["tables"] = tables
            with self._lock:
                self._stamp[sid] = stamp
                self._views[sid] = view
            changed = True
        if changed:
            with self._changed:
                self.version += 1
                self._changed.notify_all()
        return changed

    def model(self) -> dict:
        with self._lock:
            return {"viewer_version": __version__, "version": self.version,
                    "sources": [self._views[s.id] for s in self.sources]}

    def save_layout(self, source_id: str, positions, stage: str = "physical", pinned=None, previous=None, algo=None) -> dict:
        with self._lock:
            view = self._views.get(source_id)
        src = next((s for s in self.sources if s.id == source_id), None)
        if view is None or src is None:
            raise layout.LayoutError("모르는 원본이다")
        if stage == "physical":
            known = [t["key"] for t in view["tables"]]
        else:
            lv = (view.get("levels") or {}).get(stage)
            if not lv:
                raise layout.LayoutError(f"이 원본에는 {stage} 모델이 없다 (미작성)")
            known = [n["key"] for n in lv["nodes"]]
        with self._refresh_lock:
            clean = layout.save(src.layout_base, positions, known, stage=stage, pinned=pinned, previous=previous, algo=algo)
        self.refresh()
        return {"saved": len(clean), "file": layout.layout_path(src.layout_base).name, "stage": stage}

    # ---------------------------------------------------------------- 지켜보기
    def wait_change(self, seen: int, timeout: float) -> int:
        with self._changed:
            if self.version == seen:
                self._changed.wait(timeout)
            return self.version

    def start_watch(self):
        if self._thread:
            return

        def loop():
            while not self._stop.wait(self.poll):
                try:
                    self.refresh()
                except Exception:  # noqa: BLE001 -- 지켜보기는 멈추지 않는다
                    pass
        self._thread = threading.Thread(target=loop, name="dataviewer-watch", daemon=True)
        self._thread.start()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def stop(self):
        self._stop.set()
        with self._changed:
            self._changed.notify_all()


def html_attr(s: str) -> str:
    return str(s).replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def page(*, token: str, base: str = "", live: bool = True, save: bool = True, nonce: str | None = None) -> tuple[str, str]:
    """화면 HTML 과 CSP 헤더 값. save=False 면 옮긴 위치를 이 화면에서만 쓴다 (저장하지 않는다)."""
    nonce = nonce or secrets.token_urlsafe(16)
    html = (STATIC / "viewer.html").read_text(encoding="utf-8")
    cfg = json.dumps({"token": token, "base": base, "live": live, "save": save, "version": __version__})
    html = html.replace("__NONCE__", nonce).replace("__BASE__", html_attr(base)).replace("__CONFIG__", cfg.replace("</", "<\\/"))
    csp = (f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; worker-src 'self'; "
           "img-src 'self' data: blob:; font-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'")
    return html, csp


# ---------------------------------------------------------------- 단독 서버
def make_server(viewer: Viewer, host: str = "127.0.0.1", port: int = 8820, *, live: bool = True):
    token = secrets.token_urlsafe(24)

    class Handler(BaseHTTPRequestHandler):
        server_version = "dataviewer/" + __version__
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):          # 조용히
            pass

        def _host_ok(self) -> bool:         # DNS 리바인딩 막기 -- 이 컴퓨터 이름으로 온 요청만
            return local_host(self.headers.get("Host"), self.server.server_address[1])

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else (body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "이 컴퓨터(127.0.0.1·localhost) 주소로만 연다"})
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                html, csp = page(token=token, live=live)
                return self._send(200, html, "text/html; charset=utf-8", {"Content-Security-Policy": csp, "Referrer-Policy": "no-referrer"})
            if path == "/erdlayout.js":                      # 배치 계산 (화면 스크립트 + Worker 가 같은 파일)
                return self._send(200, (STATIC / "erdlayout.js").read_bytes(), "text/javascript; charset=utf-8")
            if path == "/api/model":
                return self._send(200, viewer.model())
            if path == "/api/events":
                return self._events()
            return self._send(404, {"error": "없는 주소"})

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            seen = viewer.version
            end = time.monotonic() + STREAM_SECONDS
            try:
                self.wfile.write(f"retry: 1000\nevent: hello\ndata: {seen}\n\n".encode())
                self.wfile.flush()
                while not viewer.stopped and time.monotonic() < end:
                    v = viewer.wait_change(seen, min(15, max(0.1, end - time.monotonic())))
                    msg = f"event: change\ndata: {v}\n\n" if v != seen else ": ping\n\n"
                    seen = v
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, {"error": "이 컴퓨터(127.0.0.1·localhost) 주소로만 연다"})
            if not secrets.compare_digest(self.headers.get("X-Viewer-Token") or "", token):
                return self._send(403, {"error": "화면에서 연 요청이 아니다 (토큰이 없다)"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            if n < 0 or n > MAX_BODY:
                return self._send(413, {"error": "요청이 너무 크다"})
            raw = self.rfile.read(n)
            path = self.path.split("?", 1)[0]
            if path == "/api/reload":
                viewer.refresh(force=True)
                return self._send(200, {"version": viewer.version})
            if path == "/api/layout":
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                    out = viewer.save_layout(str(body.get("source") or ""), body.get("positions"), str(body.get("stage") or "physical"),
                                             body.get("pinned"), body.get("previous"), body.get("algo"))
                except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                    return self._send(400, {"error": "JSON 이 아니다"})
                except (layout.LayoutError, TypeError, ValueError) as exc:
                    return self._send(400, {"error": str(exc) if isinstance(exc, layout.LayoutError) else "위치 요청 형식이 맞지 않다"})
                return self._send(200, out)
            return self._send(404, {"error": "없는 주소"})

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = Server((host, port), Handler)
    srv.token = token
    return srv


def serve_in_thread(viewer: Viewer, port: int = 0, *, live: bool = True):
    """시험·내장용: 백그라운드로 띄우고 (server, url) 를 돌려준다."""
    srv = make_server(viewer, port=port, live=live)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def wait_until(fn, timeout=5.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(step)
    return False
