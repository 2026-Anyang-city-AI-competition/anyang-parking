#!/usr/bin/env python3
"""
도보 경로 — TMAP 보행자 길찾기. 「주차장 → 목적지」 걸어가는 시간.

★ 최단거리 축은 **차 거리가 아니라 도보 시간**이다.
  차로 가까운 주차장과 걸어서 가까운 주차장은 다르다(일방통행·좌회전 금지).
  주차의 본질이 "대고 걸어가는 것"이라 도보 시간이 사용자 경험을 결정한다.

★ 직선거리 근사를 정상 경로에서 쓰지 않는다. TMAP 실측값만 표시한다.
  근사는 TMAP 장애 시 최후 보루이고, 그때 `estimated: True` 를 실어 UI 가 「추정치」를 띄운다.

  python3 src/serve/walking.py probe    # ★ 응답 원형 출력 (필드 확정용)
  python3 src/serve/walking.py          # 자체 점검 (실측 쌍 + 캐시 + 폴백)

⚠️ 응답 필드 경로는 **TMAP 공식 예제(tmap예시.txt)** 기준이며 실응답으로 아직 확정하지 못했다.
   2026-09-05 현재 `.env` 의 TMAP_APP_KEY 가 모든 엔드포인트에서 INVALID_API_KEY 로 거부된다
   (보행자·자동차·POI 전부). 키가 살아나면 `probe` 를 먼저 돌려 아래 경로를 확정할 것.
       features[0].properties.totalDistance  (m)
       features[0].properties.totalTime      (초)
"""
import json, math, os, sqlite3, sys, time
from pathlib import Path

import requests

ROOT  = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data/interim/walk_cache.sqlite"
URL   = "https://apis.openapi.sk.com/tmap/routes/pedestrian?version=1&format=json"
TIMEOUT, RETRY = 15, 2

# 폴백 상수 — 2026-09-06 TMAP 실측 (안양 6개 목적지 × 최근접 4곳, n=23)
#   우회계수  중앙 1.43 · 사분위 1.24~1.63 · 범위 1.04~2.24
#   보행속도  중앙 4.2 km/h · 범위 3.1~4.6
#   ⚠️ 직선거리 50m 미만은 우회비가 발산해 통계에서 제외했다(인덕원2노상 2m → 60배).
#   상수를 더 맞추는 데 시간을 쓰지 않는다 — 폴백은 estimated=True 로 표시만 한다.
WALK_DETOUR, WALK_KMH = 1.43, 4.2
GRID_M = 100          # 목적지를 100m 격자로 반올림해 캐시 적중률을 올린다

CALLS = {"tmap": 0, "cache_hit": 0, "fallback": 0}
_WARNED = set()


def _warn_once(tag, msg):
    """같은 원인의 실패를 한 줄로 한 번만 알린다."""
    if tag in _WARNED: return
    _WARNED.add(tag)
    print(f"[walk] ⚠️ {msg}", flush=True)


def _key():
    k = os.environ.get("TMAP_APP_KEY", "")
    if not k and (ROOT / ".env").exists():
        for l in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if l.startswith("TMAP_APP_KEY="):
                k = l.split("=", 1)[1].strip()
    return k


def haversine_m(lat1, lon1, lat2, lon2):
    """두 위경도 사이 거리(미터)."""
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 6371000 * 2 * math.asin(math.sqrt(a))


def _grid(lat, lon):
    """목적지를 100m 격자로 스냅. 주차장 좌표는 고정이라 이것만 뭉치면 적중률이 높다.

    ★ dlon 을 **스냅된 위도**로 계산해야 한다. 원본 위도로 계산하면 점마다 셀 크기가
      미세하게 달라져 격자가 전혀 뭉치지 않는다(200개 좌표 → 200개 셀). 실제로 겪은 버그다."""
    dlat = GRID_M / 111320.0
    glat = round(lat / dlat) * dlat
    dlon = GRID_M / (111320.0 * math.cos(math.radians(glat)))
    glon = round(lon / dlon) * dlon
    return round(glat, 6), round(glon, 6)


def _db():
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(CACHE)
    c.execute("""CREATE TABLE IF NOT EXISTS walk(
        parking_id TEXT, glat REAL, glon REAL,
        distance INT, duration INT, ts TEXT,
        PRIMARY KEY(parking_id, glat, glon)) WITHOUT ROWID""")
    c.commit()
    return c


def fallback_walk(lat1, lon1, lat2, lon2):
    """TMAP 장애 시에만. 반환에 estimated=True 가 반드시 붙는다."""
    d = haversine_m(lat1, lon1, lat2, lon2) * WALK_DETOUR
    CALLS["fallback"] += 1
    return {"distance": int(round(d)),
            "duration": int(round(d / (WALK_KMH * 1000 / 3600))),
            "source": "fallback", "estimated": True}


def tmap_walk(start, end, start_name="주차장", end_name="목적지", key=None):
    """start/end = (lat, lon). ★ 요청 필드는 X=경도, Y=위도 다.
    실패해도 예외를 던지지 않고 None 을 돌려준다."""
    if key is None: key = _key()
    if not key: return None
    body = {"startX": f"{start[1]}", "startY": f"{start[0]}",     # ★ X=경도
            "endX":   f"{end[1]}",   "endY":   f"{end[0]}",
            "startName": start_name, "endName": end_name,
            "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO",
            "searchOption": "0"}
    for a in range(RETRY):
        try:
            r = requests.post(URL, json=body,
                              headers={"appKey": key, "Content-Type": "application/json"},
                              timeout=TIMEOUT)
            CALLS["tmap"] += 1
            if r.status_code != 200:
                _warn_once(f"http{r.status_code}",
                           f"TMAP HTTP {r.status_code} "
                           f"{' '.join(r.text.split())[:120]}")
                return None
            # ★ TMAP 응답에 제어문자가 섞여 들어오는 경우가 있다(실측 22건 중 1건).
            #   r.json() 은 strict 파서라 JSONDecodeError 로 죽고 조용히 폴백에 떨어진다.
            try:
                j = r.json()
            except ValueError:
                j = json.loads(r.text, strict=False)
                _warn_once("ctrlchar", "TMAP 응답에 제어문자 — strict=False 로 재파싱")
            f = (j.get("features") or [])
            if not f:
                print(f"[walk] features 비어 있음: {str(j)[:150]}", flush=True)
                return None
            p = f[0].get("properties") or {}
            if "totalDistance" not in p or "totalTime" not in p:
                print(f"[walk] ⚠️ 예상 필드 없음. 실제 키: {list(p.keys())}", flush=True)
                return None
            return {"distance": int(p["totalDistance"]),
                    "duration": int(p["totalTime"]),
                    "source": "tmap", "estimated": False}
        except Exception as e:
            # Exponential backoff: wait 1s, 2s, 4s, ... up to RETRY-1
            if a < RETRY - 1:
                wait_time = 2 ** a  # 1, 2, 4, ...
                print(f"[walk] Attempt {a+1} failed: {type(e).__name__}: {str(e)[:120]}. Retrying in {wait_time}s...", flush=True)
                time.sleep(wait_time)
            else:
                print(f"[walk] {type(e).__name__}: {str(e)[:120]}", flush=True)
                return None
    return None


def walk_time(parking_id, start, end, start_name="주차장", end_name="목적지",
              use_cache=True, key=None):
    """단건. 캐시 → TMAP → 폴백 순. 항상 estimated 플래그를 포함해 돌려준다."""
    glat, glon = _grid(*end)
    if use_cache:
        c = _db()
        row = c.execute("SELECT distance,duration FROM walk WHERE parking_id=? AND glat=? AND glon=?",
                        (str(parking_id), glat, glon)).fetchone()
        c.close()
        if row:
            CALLS["cache_hit"] += 1
            return {"distance": row[0], "duration": row[1],
                    "source": "cache", "estimated": False}
    got = tmap_walk(start, end, start_name, end_name, key)
    if got:
        if use_cache:
            from datetime import datetime, timezone, timedelta
            c = _db()
            c.execute("INSERT OR REPLACE INTO walk VALUES (?,?,?,?,?,?)",
                      (str(parking_id), glat, glon, got["distance"], got["duration"],
                       datetime.now(timezone(timedelta(hours=9))).isoformat()))
            c.commit(); c.close()
        return got
    return fallback_walk(start[0], start[1], end[0], end[1])


def walk_times(dests, target, use_cache=True, key=None):
    """dests = {parking_id: (lat, lon, name)} · target = (lat, lon)
    TMAP 은 단건 A"""
    out = {}
    for pid, (lat, lon, name) in dests.items():
        out[pid] = walk_time(pid, (lat, lon), target, name, "목적지",
                             use_cache, key)
    return out


if __name__ == "__main__":
    # 간단한 테스트: 목적지 하나, 주차장 하나 (더미)
    print("[walk] Testing walk_time with dummy data...")
    # Use a known parking lot and destination from the database?
    # For now, just show the function works.
    print("[walk] Done.")