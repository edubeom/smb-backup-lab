"""기록 보기 페이지 만들기 -- dev-notes 기록과 저장(git) 목록, 데이터 설계(data-light 정본이 있으면)를 한 화면에서 보는 HTML 파일 한 개.

    python portal_tool.py build [--root .] [--out dev-notes/portal.html]
    python portal_tool.py where "<오류 글·파일 이름·화면·표 이름>" [--root .]   # 고칠 곳 -- 기능 지도에서 기능·파일·표를 찾는다

'한눈에'(내 시스템 한눈에): 기능 → 파일 → 표 그림, 구성(어디서 돌고 무엇을 쓰나), 쓰는 도구와 버전(package.json·requirements.txt 등에서 읽음),
기능 지도에 없는 파일·지도에는 있는데 없는 파일. 문제가 생기면 코드 전체를 훑지 않고 여기서 고칠 곳부터 찾는다.

- 서버·로그인 없이 브라우저로 파일을 연다. 인터넷에 올리지 않는다.
- 프로젝트 안에 만들면 .gitignore 에 넣는다 (기록에서 다시 만들 수 있는 파일이라 저장할 필요가 없다).
- 파이썬 표준 라이브러리만 쓴다 (Windows·macOS·Linux).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:   # Windows 콘솔에서 한글이 깨지지 않게
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent
NOTES = ["NOW", "FEATURES", "DECISIONS", "LOG", "NAMES", "LEARNED"]
KINDS = ["기능", "수정", "모양", "정리", "데이터", "중간 저장", "되돌리기", "준비"]
DEFAULT_OUT = "dev-notes/portal.html"
MAX_FEATURE_FILES = 400
MODEL = "data/model.light.json"          # 짝 스킬 data-light 의 정본
COL_KEYS = ("name", "label", "type", "required", "unique", "choices", "ref", "on_delete", "key", "personal", "about")


def _git(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env)
    except FileNotFoundError:
        return ""
    return r.stdout.rstrip() if r.returncode == 0 else ""      # 앞 공백은 남긴다 (git status 의 " M 파일")


def _project_name(now: str, root: Path) -> str:
    m = re.search(r"## 이 프로젝트는\s*\n+([^\n#<][^\n]*)", now or "")
    line = (m.group(1).strip() if m else "") or root.name
    return line if len(line) <= 60 else line[:58] + "…"


def _rel(target: Path, out: Path) -> str:
    try:
        return Path(os.path.relpath(target, out.parent)).as_posix()
    except ValueError:            # Windows 에서 드라이브가 다르면
        return target.resolve().as_uri()


def _access(v):
    """표의 access: 한 값("members") 또는 읽기·쓰기 두 개 목록(["read", "own"], data-light 0.4.1)."""
    if isinstance(v, list):
        return [str(x) for x in v if isinstance(x, str)]
    return str(v) if v else ""


def _works(v) -> list[dict]:
    """하는 일 (data-light 0.7.0): 일 · 누가 · 화면 · 쓰는 표와 그 표에 하는 일(C·R·U·D)."""
    out = []
    for w in v if isinstance(v, list) else []:
        if not isinstance(w, dict) or not w.get("name"):
            continue
        scr = w.get("screen")
        scr = [str(x) for x in scr if x] if isinstance(scr, list) else ([str(scr)] if scr else [])
        uses = {str(k): str(o) for k, o in (w.get("uses") or {}).items()} if isinstance(w.get("uses"), dict) else {}
        out.append({"name": str(w["name"]), "who": str(w.get("who") or ""), "say": str(w.get("say") or ""), "screen": scr, "uses": uses})
    return out


def _product(v) -> dict | None:
    if not isinstance(v, dict):
        return None
    kinds = [str(x) for x in v.get("kinds") or [] if isinstance(x, str)] if isinstance(v.get("kinds"), list) else []
    return {"kinds": kinds, "main": str(v.get("main") or ""), "said": str(v.get("said") or ""), "main_said": str(v.get("main_said") or "")}


def data_summary(root: Path, out: Path) -> dict | None:
    """data-light 정본의 구조만 (샘플 행·기본값 밖의 값은 담지 않는다 -- 페이지는 구조를 보여 주는 곳)."""
    p = root / MODEL
    if not p.is_file():
        return None
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
        tables = []
        for t in m.get("tables") or []:
            cols = [{k: c[k] for k in COL_KEYS if k in c} for c in (t.get("columns") or []) if isinstance(c, dict)]
            uniq = [[str(x) for x in u] for u in (t.get("unique") or []) if isinstance(u, list)]   # 두 칸 이상 묶은 중복 금지
            tables.append({"name": t.get("name", ""), "label": t.get("label", ""), "about": t.get("about", ""),
                           "timestamps": t.get("timestamps", True) is not False, "columns": cols, "unique": uniq,
                           "access": _access(t.get("access"))})     # 누가 이 표를 쓰나 (Supabase 규칙)
        goals = [{"say": g.get("say", ""), "sql": g.get("sql", "")} for g in (m.get("goals") or []) if isinstance(g, dict)]
        info = {"file": MODEL, "project": m.get("project", ""), "db": m.get("db", "sqlite"), "tables": tables,
                "goals": goals, "not_doing": [str(x) for x in (m.get("not_doing") or [])],   # 성공 기준의 기대 결과(샘플 값)는 담지 않는다
                "signup": str(m["signup"]) if m.get("signup") else "",
                "product": _product(m.get("product")),          # 만드는 것 (data-light 0.5.0, 사용자 말 그대로)
                "works": _works(m.get("works")),                # 하는 일 (data-light 0.7.0)
                "outside": [str(x) for x in m["outside"] if isinstance(x, str)] if isinstance(m.get("outside"), list) else []}
    except (ValueError, AttributeError, TypeError) as e:
        info = {"file": MODEL, "error": f"정본을 읽지 못함: {e}", "tables": []}
    info["links"] = {n: _rel(root / "data" / "out" / n, out) for n in ("book.html", "erd.md", "spec.md", "schema.sql")
                     if (root / "data" / "out" / n).is_file()}
    return info


# ---------------------------------------------------------------- 내 시스템 한눈에 (기능 지도 FEATURES.md + 데이터 정본 + 매니페스트)
CODE_EXT = (".html", ".htm", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".py", ".sql", ".php", ".rb", ".go", ".json")
SKIP_DIRS = ("dev-notes/", "manual/", "data/out/", ".claude/", ".agents/", ".data-light/", ".github/", "node_modules/", "examples/", "tests/", "test/")
PRUNE = frozenset({"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".next", ".nuxt", "vendor", "site-packages", "coverage"})
MAX_WALK = 5000
SKIP_FILES = ("package-lock.json", "package.json", "tsconfig.json", "data/model.light.json", "data/model.light.json.layout.json", "data/import_plan.json")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _section(text: str, title: str) -> str:
    m = re.search(r"\n##\s*" + re.escape(title) + r"[^\n]*\n([\s\S]*?)(?=\n##\s|$)", "\n" + (text or ""))
    return m.group(1) if m else ""


def _rows(block: str) -> list[list[str]]:
    out = []
    for ln in block.splitlines():
        if not ln.strip().startswith("|") or re.match(r"^\s*\|[\s:|-]+\|\s*$", ln) or "(예)" in ln:
            continue
        out.append(_cells(ln))
    return out[1:] if out else out                     # 첫 줄은 머리


FILE_RX = re.compile(r"[\w.\-/]*\w\.[A-Za-z0-9]{1,6}\b|[\w.\-]+(?:/[\w.\-]+)*/(?=\s|$|[,)])")


def _files(cell: str) -> list[str]:
    """칸 글에서 파일·폴더 이름만 (설명 괄호는 뺀다): 'index.html (화면 틀), store.js' → index.html, store.js"""
    got = [m.group(0) for m in FILE_RX.finditer((cell or "").replace("`", " "))]
    return list(dict.fromkeys(x for x in got if not re.match(r"^v?\d+(\.\d+)+$", x)))


def feature_map(text: str) -> dict:
    """FEATURES.md -- 기능 표(기능·상태·쓰는 파일) · 함께 쓰는 파일 · 구성 · 데이터(표 → 쓰는 기능)."""
    text = re.sub(r"<!--[\s\S]*?-->", "", text or "")
    head = text.split("\n## ", 1)[0]
    feats = []
    for c in _rows(head):
        if len(c) >= 3 and c[0] and c[0] != "기능":
            feats.append({"name": c[0], "state": c[1], "files": _files(c[2]), "check": c[3] if len(c) > 3 else ""})
    shared = []
    for ln in _section(text, "함께 쓰는 파일").splitlines():
        if ln.strip().startswith("-") and "(예)" not in ln:
            shared += _files(ln.strip()[1:])
    parts = [{"part": c[0], "what": c[1] if len(c) > 1 else "", "where": c[2] if len(c) > 2 else "", "files": _files(c[3]) if len(c) > 3 else []}
             for c in _rows(_section(text, "구성")) if c and c[0] and c[0] != "부분"]
    tables = [{"name": c[0].strip("`"), "label": c[1] if len(c) > 1 else "", "features": [x.strip() for x in re.split(r"[,、]\s*", c[2]) if x.strip()] if len(c) > 2 else []}
              for c in _rows(_section(text, "데이터")) if c and c[0] and c[0] != "표"]
    return {"features": feats, "shared": shared, "parts": parts, "tables": tables}


def _ver(spec) -> str:
    m = re.search(r"(\d+(?:\.\d+)*)", str(spec or ""))
    return m.group(1) if m else str(spec or "")


def tools(root: Path) -> list[dict]:
    """쓰는 도구와 버전 -- 프로젝트의 파일에서 직접 읽는다 (AI 가 적은 글이 아니라). 많으면 앞의 40개."""
    out = []

    def add(name, ver, f):
        if len(out) < 40:
            out.append({"name": str(name), "version": _ver(ver), "file": f})
    p = root / "package.json"
    if p.is_file():
        try:
            d = json.loads(p.read_text(encoding="utf-8-sig"))
            for k, v in (d.get("engines") or {}).items():
                add(k, v, "package.json")
            for sect in ("dependencies", "devDependencies"):
                for k, v in (d.get(sect) or {}).items():
                    add(k, v, "package.json")
        except (ValueError, AttributeError):
            pass
    for name in ("requirements.txt", "requirements-dev.txt"):
        p = root / name
        if p.is_file():
            for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*(?:==|>=|~=|<=|>|<)\s*([^\s,;#]+)", ln.split("#", 1)[0].strip())
                if m:
                    add(m.group(1), m.group(2), name)
    p = root / "pyproject.toml"
    if p.is_file():
        t = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'(?m)^\s*requires-python\s*=\s*"([^"]+)"', t)
        if m:
            add("python", m.group(1), "pyproject.toml")
        for m in re.finditer(r'"\s*([A-Za-z0-9_.\-]+)\s*(?:\[[^\]]*\])?\s*(?:==|>=|~=)\s*([^",;\s]+)', t):
            add(m.group(1), m.group(2), "pyproject.toml")
    for name, tool in ((".python-version", "python"), (".nvmrc", "node"), (".node-version", "node")):
        p = root / name
        if p.is_file():
            add(tool, p.read_text(encoding="utf-8", errors="replace").strip(), name)
    p = root / "supabase" / "config.toml"
    if p.is_file():
        m = re.search(r"(?m)^\s*major_version\s*=\s*(\d+)", p.read_text(encoding="utf-8", errors="replace"))
        if m:
            add("postgres (Supabase)", m.group(1), "supabase/config.toml")
    for f in sorted(root.glob("*.html"))[:20]:                   # CDN 으로 불러오는 라이브러리 (예: @supabase/supabase-js@2.45.0)
        for src in re.findall(r"""src=["']([^"']+)["']""", f.read_text(encoding="utf-8", errors="replace")):
            m = re.search(r"(@[\w.\-]+/[\w.\-]+|[\w.\-]+)@(\d+(?:\.\d+)*)", src)
            if m:
                add(m.group(1), m.group(2), f.name)
    seen, uniq = set(), []
    for t in out:
        k = (t["name"].lower(), t["file"])
        if k not in seen:
            seen.add(k)
            uniq.append(t)
    return uniq


def _project_files(root: Path) -> list:
    """프로젝트 파일 (git 이 추적하는 것 + 아직 저장 안 한 새 파일, git 이 아니면 폴더를 걷는다).
    node_modules · .venv · dist 같은 폴더는 어느 깊이에 있어도 들어가지 않는다 (걷지도 않는다 -- 큰 폴더에서 느려지지 않게)."""
    out = _git(root, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    if not out:
        for dp, dirs, files in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in PRUNE and not d.startswith("."))
            rel = Path(dp).relative_to(root).as_posix()
            out += [f if rel == "." else f"{rel}/{f}" for f in sorted(files)]
            if len(out) >= MAX_WALK:
                break
    return sorted(f for f in out if f and not any(part in PRUNE for part in f.split("/")))[:MAX_WALK]


def system_map(root: Path, notes: dict, data: dict | None) -> dict:
    """내 시스템 한눈에 -- 기능 → 파일 → 표, 구성, 쓰는 도구와 버전, 기능 지도에 없는 파일 · 지도에는 있는데 없는 파일."""
    fm = feature_map(notes.get("FEATURES") or "")
    listed = {f for x in fm["features"] for f in x["files"]} | set(fm["shared"]) | {f for p in fm["parts"] for f in p["files"]}
    tracked = _project_files(root)
    have = set(tracked) | {Path(f).name for f in tracked}
    missing = sorted(f for f in listed if not f.endswith("/") and "*" not in f and not (root / f).exists() and f not in have
                     and not any(t.endswith("/" + f) for t in tracked))
    names = {Path(f).name for f in listed}
    unlisted = sorted(f for f in tracked if f.lower().endswith(CODE_EXT) and not f.startswith(SKIP_DIRS) and f not in SKIP_FILES
                      and f not in listed and Path(f).name not in names and not f.startswith("."))[:40]
    works = (data or {}).get("works") or []
    return dict(fm, tools=tools(root), missing=missing, unlisted=unlisted,
                works=[{"name": w["name"], "screen": w["screen"], "uses": sorted(w["uses"])} for w in works])


def where(root: Path, query: str) -> dict:
    """고칠 곳 찾기 (light) -- 오류 글·파일 이름·화면·표 이름에서 기능 지도의 기능 · 파일 · 표로. 먼저 볼 파일을 준다."""
    notes = {n: ((root / "dev-notes" / f"{n}.md").read_text(encoding="utf-8") if (root / "dev-notes" / f"{n}.md").is_file() else None) for n in NOTES}
    data = data_summary(root, root / DEFAULT_OUT)
    m = system_map(root, notes, data)
    q = (query or "").strip()
    ql = q.lower()
    toks = [t for t in re.split(r"[\s,;:()\[\]{}'\"=<>]+", ql) if len(t) >= 2]
    hits, read = [], []

    def score_text(*xs):
        # names inside the query count too: Korean particles ("메모가", "가계부는") keep the token from matching the name
        names = [str(x).lower() for x in xs if x]
        s = " ".join(names)
        return (3 if ql and ql in s else 0) + sum(1 for t in toks if t in s) + sum(4 for n in names if len(n) >= 2 and n in ql)
    files_in_q = {Path(x).name.lower() for x in re.findall(r"[\w./\\-]+\.[A-Za-z0-9]{1,5}", q)}
    for f in m["features"]:
        sc = score_text(f["name"]) + (5 if any(Path(x).name.lower() in files_in_q for x in f["files"]) else 0)
        if sc:
            hits.append({"kind": "기능", "name": f["name"], "state": f["state"], "files": f["files"], "score": sc,
                         "tables": [t["name"] for t in m["tables"] if f["name"] in t["features"]]})
    for t in m["tables"]:
        sc = score_text(t["name"], t["label"])
        if sc:
            hits.append({"kind": "표", "name": t["name"], "label": t["label"], "features": t["features"], "score": sc,
                         "files": [x for f in m["features"] if f["name"] in t["features"] for x in f["files"]]})
    for w in m["works"]:
        sc = score_text(w["name"], " ".join(w["screen"]))
        if sc:
            hits.append({"kind": "하는 일", "name": w["name"], "screen": w["screen"], "tables": w["uses"], "score": sc, "files": []})
    for p in m["parts"]:
        sc = score_text(p["part"], p["what"], p["where"]) + (5 if any(Path(x).name.lower() in files_in_q for x in p["files"]) else 0)
        if sc:
            hits.append({"kind": "구성", "name": p["part"], "what": p["what"], "where": p["where"], "files": p["files"], "score": sc})
    sh = [x for x in m["shared"] if Path(x).name.lower() in files_in_q]
    if sh:
        hits.append({"kind": "함께 쓰는 파일", "name": ", ".join(sh), "files": sh, "score": 4,
                     "note": "여러 기능이 같이 쓰는 파일 -- 고치면 모든 기능(" + ", ".join(f["name"] for f in m["features"]) + ")을 점검한다"})
    hits.sort(key=lambda h: -h["score"])
    for h in hits[:5]:
        read += h.get("files") or []
    if (root / MODEL).is_file() and any(h["kind"] in ("표", "하는 일") for h in hits[:5]):
        read.append(MODEL)
    return {"query": q, "found": hits[:5], "read": list(dict.fromkeys(read))[:8],
            "unlisted": m["unlisted"][:10], "note": "" if hits else "찾지 못했다 -- 기능 이름·화면 이름·파일 이름·표 이름으로 다시 찾거나, 기능 지도(FEATURES.md)를 채운다"}


def collect(root: Path, out: Path) -> dict:
    notes = {}
    for n in NOTES:
        p = root / "dev-notes" / f"{n}.md"
        notes[n] = p.read_text(encoding="utf-8") if p.is_file() else None
    logdir = root / "dev-notes" / "log"
    archive = sorted([*logdir.glob("_흐름/*.md"), *logdir.glob("*.md")], key=lambda p: p.stem, reverse=True) if logdir.is_dir() else []
    feature_logs = []
    if logdir.is_dir():
        for p in sorted(logdir.glob("*/*.md"), key=lambda p: (p.stem, p.parent.name), reverse=True)[:MAX_FEATURE_FILES]:
            if p.parent.name.startswith("_"):
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            feature_logs.append({"feature": p.parent.name, "date": p.stem,
                                 "text": text if len(text) <= 60000 else text[:60000] + "\n\n… (잘림 -- 파일을 열어 본다)"})
    saves = []
    for line in _git(root, "log", "-80", "--format=%h%x00%cI%x00%s").splitlines():
        h, when, msg = (line.split("\x00") + ["", ""])[:3]
        kind = next((k for k in KINDS if msg.startswith(k + ":")), "")
        saves.append({"hash": h, "at": when, "msg": msg, "kind": kind})
    dirty = [ln[3:] for ln in _git(root, "status", "--porcelain").splitlines() if ln[3:] and not ln[3:].startswith("dev-notes/portal")]
    manual = root / "manual" / "index.html"
    manual_href = _rel(manual, out) if manual.is_file() else None
    data = data_summary(root, out)
    return {"project": _project_name(notes["NOW"], root), "folder": str(root), "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "notes": notes, "archive": [{"name": p.stem, "text": p.read_text(encoding="utf-8")} for p in archive[:24]],
            "feature_logs": feature_logs,
            "saves": saves, "dirty": dirty, "remote": bool(_git(root, "remote")), "manual": manual_href,
            "data": data, "map": system_map(root, notes, data)}


def _ensure_ignored(root: Path, out: Path) -> str | None:
    try:
        rel = out.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None
    gi = root / ".gitignore"
    have = gi.read_text(encoding="utf-8").splitlines() if gi.is_file() else []
    if rel in have or "/" + rel in have:
        return None
    gi.write_text("\n".join([*have, "# 기록 보기 페이지 (dev-notes 에서 다시 만든다)", rel]) + "\n", encoding="utf-8")
    return rel


def build(root: Path, out: Path) -> dict:
    root, out = root.resolve(), (out if out.is_absolute() else root / out).resolve()
    data = collect(root, out)
    html = (HERE / "templates" / "portal.html").read_text(encoding="utf-8")
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    title = (data["project"] + " -- 기록").replace("&", "&amp;").replace("<", "&lt;")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html.replace("__TITLE__", title).replace("__DATA__", blob), encoding="utf-8")
    return {"out": str(out), "uri": out.as_uri(), "ignored": _ensure_ignored(root, out), "saves": len(data["saves"])}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="기록 보기 페이지 만들기")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--root", default=".")
    b.add_argument("--out", default=DEFAULT_OUT)
    w = sub.add_parser("where", help="고칠 곳 찾기 -- 오류 글·파일·화면·표 이름 → 기능 · 파일 · 표")
    w.add_argument("query", nargs="+")
    w.add_argument("--root", default=".")
    w.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "where":
        r = where(Path(a.root).resolve(), " ".join(a.query))
        if a.json:
            print(json.dumps(r, ensure_ascii=False, indent=1))
            return 0 if r["found"] else 1
        print(f"[찾기] {r['query']}")
        for h in r["found"]:
            extra = " · ".join(x for x in [h.get("state", ""), ("표 " + ", ".join(h["tables"])) if h.get("tables") else "",
                                           ("쓰는 기능 " + ", ".join(h["features"])) if h.get("features") else "",
                                           ("화면 " + ", ".join(h["screen"])) if h.get("screen") else "", h.get("what", ""), h.get("where", ""),
                                           h.get("note", "")] if x)
            print(f"- {h['kind']} {h['name']}" + (f" -- {extra}" if extra else ""))
        if r["found"]:
            print("먼저 볼 파일: " + (", ".join(r["read"]) if r["read"] else "(기능 지도에 파일이 없다 -- FEATURES.md 의 '쓰는 파일'을 채운다)"))
        if r["unlisted"]:
            print("기능 지도에 없는 파일: " + ", ".join(r["unlisted"]) + " -- FEATURES.md 의 기능·함께 쓰는 파일·구성에 넣는다")
        if r["note"]:
            print(r["note"])
        return 0 if r["found"] else 1
    rep = build(Path(a.root), Path(a.out))
    print(f"[OK] 기록 보기 페이지: {rep['out']}")
    print(f"     브라우저로 열기: {rep['uri']}")
    if rep["ignored"]:
        print(f"[OK] .gitignore 에 {rep['ignored']} 추가 (저장하지 않는 파일)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
