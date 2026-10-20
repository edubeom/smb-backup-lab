"""엔터티 위치 -- 원본 옆 `<원본 파일 이름>.layout.json` (원본은 고치지 않는다). 위치는 화면 정보일 뿐 모델·DB 변경이 아니다.

    {"layout_version": 2, "source": "model.json",
     "positions": {"acad.student": {"x": 40, "y": 60}},                 물리 모델 (1판과 같은 자리 -- 옛 뷰어도 읽는다)
     "views": {"concept": {"positions": {"C.student": {...}}}, "logical": {"positions": {...}}},
     "pinned": {"physical": ["acad.student"], "concept": [...]},        고정 -- 자동 배치가 옮기지 않는다
     "previous": {"physical": {...}},                                    직전 배치 (되돌리기)
     "algo": {"physical": {"version": "layered-1", "structure": "<노드·관계 지문>", "rels": "identifying,mandatory"}}}
                                                                         rels = 배치할 때 켜 둔 관계선 종류 (그 선만으로 배치했다)

단계(개념·논리·물리)마다 위치를 따로 둔다. 1판 파일은 그대로 읽는다 (positions = 물리).
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path

LAYOUT_VERSION = 2
STAGES = ("physical", "logical", "concept")
MAX_TABLES = 5000
LIMIT = 1_000_000          # 좌표 범위 (화면 밖으로 날아간 값 막기)


class LayoutError(ValueError):
    pass


def layout_path(source) -> Path:
    p = Path(source)
    return p.with_name(p.name + ".layout.json")


def _read(source) -> dict:
    try:
        data = json.loads(layout_path(source).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _points(pos) -> dict:
    out = {}
    if isinstance(pos, dict):
        for k, v in pos.items():
            try:
                out[str(k)] = clean_point(v)
            except LayoutError:
                continue
    return out


def _dict(v) -> dict:
    return v if isinstance(v, dict) else {}


def load(source, stage: str = "physical") -> dict:
    """저장된 위치 {key: {x, y}} -- 없거나 깨졌으면 빈 dict (화면은 자동 배치로 그린다). 모양이 어긋난 값은 건너뛴다."""
    data = _read(source)
    if stage == "physical":
        return _points(data.get("positions"))
    return _points(_dict(_dict(_dict(data.get("views")).get(stage)).get("positions")))


ALGO_KEYS = ("version", "structure", "mode", "rels")


def _algo(v) -> dict | None:
    if not isinstance(v, dict):
        return None
    out = {k: v[k] for k in ALGO_KEYS if isinstance(v.get(k), str) and len(v[k]) <= 80}
    return out or None


def _pins(v) -> list:
    return sorted({x for x in v if isinstance(x, str) and len(x) <= 500}) if isinstance(v, list) else []


def load_all(source) -> dict:
    """{stage: {positions, pinned, previous, algo}} -- 화면이 단계마다 따로 쓴다."""
    data = _read(source)
    out = {}
    for st in STAGES:
        out[st] = {"positions": load(source, st), "pinned": _pins(_dict(data.get("pinned")).get(st)),
                   "previous": _points(_dict(data.get("previous")).get(st)), "algo": _algo(_dict(data.get("algo")).get(st))}
    return out


def clean_point(v) -> dict:
    if not isinstance(v, dict):
        raise LayoutError("위치는 {x, y} 여야 한다")
    try:
        x, y = float(v["x"]), float(v["y"])
    except (KeyError, TypeError, ValueError):
        raise LayoutError("위치는 {x, y} 숫자여야 한다") from None
    if not (math.isfinite(x) and math.isfinite(y)) or abs(x) > LIMIT or abs(y) > LIMIT:
        raise LayoutError("위치 값이 범위를 벗어났다")
    return {"x": round(x, 1), "y": round(y, 1)}


def _clean_map(positions, known: set) -> dict:
    if not isinstance(positions, dict) or len(positions) > MAX_TABLES:
        raise LayoutError("positions 는 {엔터티: {x, y}} 여야 한다")
    return {k: clean_point(v) for k, v in positions.items() if k in known}


def save(source, positions: dict, known_keys, stage: str = "physical", pinned=None, previous=None, algo=None) -> dict:
    """원본에 있는 엔터티의 위치만 받아, 이미 저장된 위치(지금 원본에 잠시 없는 엔터티 것까지)에 합쳐 원자적으로 바꿔 쓴다.
    pinned 를 주면 그 단계의 고정 목록을 바꾼다 (원본에 있는 것만). previous 는 직전 배치 (되돌리기), algo 는 배치 판 정보."""
    if stage not in STAGES:
        raise LayoutError(f"stage 는 {'·'.join(STAGES)} 중 하나")
    known = set(known_keys)
    clean = _clean_map(positions, known)
    data = _read(source)
    f = layout_path(source)
    if stage == "physical":
        merged = dict(load(source))
        merged.update(clean)
        data["positions"] = dict(sorted(merged.items()))
    else:
        views = data.get("views")
        if not isinstance(views, dict):
            views = data["views"] = {}
        merged = dict(load(source, stage))
        merged.update(clean)
        views[stage] = {"positions": dict(sorted(merged.items()))}
    for k in ("pinned", "previous", "algo"):              # 깨진 파일의 모양이 어긋난 칸은 새로 쓴다
        if not isinstance(data.get(k), dict):
            data.pop(k, None)
    if pinned is not None:
        if not isinstance(pinned, list) or len(pinned) > MAX_TABLES or not all(isinstance(k, str) for k in pinned):
            raise LayoutError("pinned 는 엔터티 이름(글) 목록이어야 한다")
        old = set(_pins(data.get("pinned", {}).get(stage))) - known          # 지금 원본에 없는 것의 고정은 남겨 둔다
        data.setdefault("pinned", {})[stage] = sorted(old | {k for k in pinned if k in known})
    if previous is not None:
        data.setdefault("previous", {})[stage] = _clean_map(previous, known)
    if algo is not None:
        a = _algo(algo)
        if a is None or len(json.dumps(algo, default=str)) > 2000:
            raise LayoutError("algo 는 짧은 글 값(version·structure·mode)만 가진 객체여야 한다")
        data.setdefault("algo", {})[stage] = a
    data["layout_version"] = LAYOUT_VERSION
    data["source"] = Path(source).name
    order = ("layout_version", "source", "positions", "views", "pinned", "previous", "algo")
    body = json.dumps({k: data[k] for k in order if k in data}, ensure_ascii=False, indent=1) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=".layout-", suffix=".tmp", dir=str(f.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        for i in range(6):                              # Windows: 다른 프로그램(백신·동기화)이 잠깐 열고 있으면 잠시 뒤 다시
            try:
                os.replace(tmp, f)
                break
            except PermissionError:
                if i == 5:
                    raise
                time.sleep(0.05 * (i + 1))
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise LayoutError(f"위치 파일을 쓸 수 없다: {exc}") from None
    return clean
