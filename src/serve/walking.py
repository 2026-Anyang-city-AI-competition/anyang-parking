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

# 폴백 상수 — ⚠️ 실측하지 못했다(TMAP 키 거부). 도보 우회계수는 통상 1.2~1.4.
#   TMAP 이 살아나면 `python3 src/serve/walking.py` 의 '직선거리 대비 비율' 로 확정할 것.
#   상수를 맞추는 데 시간을 쓰지 않는다 — 폴백은 estimated=True 로 표시만 한다.
WALK_DETOUR, WALK_KMH = 1.3, 4.0
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
            j = r.json()
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
            if a == RETRY - 1:
                print(f"[walk] {type(e).__name__}: {str(e)[:120]}", flush=True)
                return None
            time.sleep(1.5)
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
    TMAP 은 단건 API 라 후보 N곳이면 N회다. 캐시가 필수인 이유."""
    out = {}
    for pid, v in dests.items():
        lat, lon = v[0], v[1]
        nm = v[2] if len(v) > 2 else str(pid)
        out[pid] = walk_time(pid, (lat, lon), target, nm, "목적지", use_cache, key)
    return out


def probe():
    """★ 응답 원형을 그대로 출력한다. 필드명을 추측하지 않기 위해."""
    key = _key()
    print(f"TMAP_APP_KEY {'설정됨 (%d자)' % len(key) if key else '없음'}")
    if not key:
        sys.exit("TMAP_APP_KEY 가 없다. SK open API 에서 앱을 만들고 appKey 를 .env 에 넣을 것.")
    body = {"startX": "126.921732704925", "startY": "37.4010837585779",
            "endX": "126.922644", "endY": "37.401857",
            "startName": "안양역2노상", "endName": "안양역",
            "reqCoordType": "WGS84GEO", "resCoordType": "WGS84GEO", "searchOption": "0"}
    print("\n=== 요청 원형 ===")
    print(json.dumps(body, ensure_ascii=False, indent=1))
    r = requests.post(URL, json=body,
                      headers={"appKey": key, "Content-Type": "application/json"}, timeout=TIMEOUT)
    print(f"\n=== 응답 HTTP {r.status_code} · {len(r.content):,}B ===")
    t = r.text.strip()
    if not t.startswith("{"):
        print(t[:400]); return
    j = r.json()
    print("최상위 키:", list(j.keys()))
    if "error" in j:
        print("❌ 오류:", json.dumps(j["error"], ensure_ascii=False))
        print("\n키가 거부됐다. SK open API 콘솔에서 앱 상태와 appKey 를 확인할 것.")
        return
    f = j.get("features") or []
    print(f"features {len(f)}개")
    if f:
        print("★ features[0] 키:", list(f[0].keys()))
        print("★ features[0].properties:",
              json.dumps(f[0].get("properties"), ensure_ascii=False)[:500])


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        probe(); sys.exit()

    import sqlite3 as _s
    con = _s.connect(ROOT / "data/raw/parking.db")
    lots = {r[0]: (r[2], r[3], r[1]) for r in con.execute(
        "SELECT parking_id,name,lat,lng FROM lots WHERE parking_id IN (48,16,46,45,10)").fetchall()}
    con.close()
    PAIRS = [(48, (37.401857, 126.922644), "안양역"),
             (16, (37.394259, 126.956861), "안양시청"),
             (46, (37.389784, 126.950783), "범계역"),
             (45, (37.394240, 126.963808), "평촌역"),
             (10, (37.401494, 126.976680), "인덕원역")]
    print(f"{'주차장':<14}{'목적지':<9}{'직선m':>7}{'도보m':>7}{'분':>5}{'비율':>7}  출처")
    for pid, dest, dn in PAIRS:
        if pid not in lots: continue
        la, lo, nm = lots[pid]
        s = haversine_m(la, lo, *dest)
        r = walk_time(pid, (la, lo), dest, nm, dn)
        ratio = r["distance"] / s if s else float("nan")
        print(f"{nm[:12]:<14}{dn:<9}{s:>7.0f}{r['distance']:>7}{r['duration']/60:>5.0f}"
              f"{ratio:>7.2f}  {r['source']}"
              + ("  ⚠️추정" if r["estimated"] else ""))
    print(f"\n호출 카운트: {CALLS}")
    print("\n=== 캐시 적중 확인 (같은 질의 반복) ===")
    before = dict(CALLS)
    for pid, dest, dn in PAIRS:
        if pid in lots:
            la, lo, nm = lots[pid]
            walk_time(pid, (la, lo), dest, nm, dn)
    print(f"  TMAP 호출 증가 {CALLS['tmap']-before['tmap']} · "
          f"캐시 적중 증가 {CALLS['cache_hit']-before['cache_hit']} · "
          f"폴백 증가 {CALLS['fallback']-before['fallback']}")
    if CALLS["cache_hit"] == 0:
        print("  → TMAP 이 죽어 캐시가 비어 있다. 캐시 경로를 따로 검증한다.")

    print("\n=== 캐시 경로 단독 검증 (TMAP 없이) ===")
    pid, dest = 48, (37.401857, 126.922644)
    la, lo, nm = lots[pid]
    g = _grid(*dest)
    c = _db()
    c.execute("INSERT OR REPLACE INTO walk VALUES (?,?,?,?,?,?)",
              (str(pid), g[0], g[1], 171, 154, "TEST"))
    c.commit(); c.close()
    b = dict(CALLS)
    r = walk_time(pid, (la, lo), dest, nm, "안양역")
    print(f"  주입값 읽기: {r['distance']}m {r['duration']}초 source={r['source']} "
          f"estimated={r['estimated']}")
    print(f"  {'PASS' if CALLS['tmap']==b['tmap'] else 'FAIL'}  캐시 적중 시 TMAP 호출 0")
    print(f"  {'PASS' if CALLS['cache_hit']==b['cache_hit']+1 else 'FAIL'}  캐시 적중 카운트 +1")
    # 격자 스냅 효과 — 목적지를 조금씩 옮겨도 셀이 몇 개로 뭉치는가
    import random
    random.seed(42)
    pts = [(dest[0] + random.uniform(-0.0009, 0.0009),
            dest[1] + random.uniform(-0.0011, 0.0011)) for _ in range(200)]
    cells = {_grid(*p_) for p_ in pts}
    print(f"  반경 ~100m 안 200개 좌표 → 격자 셀 {len(cells)}개 "
          f"(스냅 없으면 200개). 적중률 배수 ≈ {200/len(cells):.0f}x")
    print("  ⚠️ 격자 경계를 걸친 두 점은 30m 차이여도 다른 셀이 된다. 구조상 정상.")
    c = _db(); c.execute("DELETE FROM walk WHERE ts='TEST'"); c.commit(); c.close()
