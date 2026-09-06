#!/usr/bin/env python3
"""
후보 주차장 선정 — 반경을 넓혀가며 최소 개수를 채운다.

  from src.serve.candidates import find_candidates
  find_candidates(lat, lon, min_n=5) -> {"lots":[...], "radius_used":1000, "unlabeled":[...]}

★ 반경을 고정하지 않는다. 석수역은 1km 안에 공영주차장이 1곳뿐이다(실측).
  1km → 5곳 미만이면 2km → 그래도 미만이면 3km. **3km 가 상한.**
  그래도 없으면 "주변에 공영주차장이 없습니다".

★ 순위에는 **라벨 있는 안양 89곳만** 넣는다.
  표준데이터에만 있는 무료 노외 36곳은 라벨이 없어 예측을 못 하므로
  순위 밖 「추정치」 배지로 따로 돌려준다(`unlabeled`).

  python3 src/serve/candidates.py     # 6개 목적지 자체 점검
"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB   = ROOT / "data/raw/parking.db"
STD  = ROOT / "data/raw/std_parking.csv"

RADII   = (1000, 2000, 3000)   # 3km 가 상한. 그 이상 확장 금지
MIN_N   = 5
DEAD_RANGE = 0.01              # 전 기간 점유율 변동폭이 이보다 작으면 '죽은 피드'

# ★ 89곳 중 22곳은 실시간 피드가 안 돈다 (변동폭 정확히 0.00, 6일 확인).
#   원인: **위탁 운영 14곳 전부**가 여기 해당하고 살아있는 67곳엔 위탁이 0곳이다.
#   나머지 8곳은 직영인데 특정 시점 값에서 얼어붙었다(12곳은 park_count==cell_cnt).
#   GITS 로도 못 살린다 — 같은 원천이라 GITS 역시 22곳 전부 변동폭 0.00 이다.
#   → 순위에서 빼고 「실시간 미제공」으로 표시한다.

def haversine_m(lat1, lon1, lat2, lon2):
    """직선거리(m). 후보를 추리는 용도다 — 표시용 거리가 아니다."""
    import math
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2 +
         math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 6371000 * 2 * math.asin(math.sqrt(a))


def _labeled_lots():
    """라벨 있는 안양 89곳. 실시간 관측이 있는 곳만."""
    if not DB.exists(): return []
    con = sqlite3.connect(DB)
    rows = con.execute("""
        SELECT l.parking_id, l.name, l.div, l.grade, l.cell_cnt, l.lat, l.lng,
               l.wdays_start, l.wdays_end, l.wend_start, l.wend_end, l.oneday_amt
        FROM lots l WHERE l.lat IS NOT NULL AND l.lng IS NOT NULL""").fetchall()
    cur = {r[0]: r[1] for r in con.execute("""
        SELECT parking_id, park_count FROM obs
        WHERE ts_kst = (SELECT MAX(ts_kst) FROM obs)""").fetchall()}
    con.close()
    # 죽은 피드 판정 — 전 기간 점유율 변동폭
    rng = {}
    for pid, mn, mx in con2.execute("""
            SELECT parking_id, MIN(1.0*park_count/cell_cnt), MAX(1.0*park_count/cell_cnt)
            FROM obs WHERE cell_cnt > 0 GROUP BY parking_id""").fetchall() \
            if (con2 := sqlite3.connect(DB)) else []:
        rng[pid] = (mx or 0) - (mn or 0)
    con2.close()
    cols = ("parking_id","name","div","grade","cell_cnt","lat","lng",
            "wdays_start","wdays_end","wend_start","wend_end","oneday_amt")
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        d["avail_now"] = cur.get(d["parking_id"])     # 현재 '주차 대수'
        d["occ_range"] = rng.get(d["parking_id"])
        d["dead_feed"] = (d["occ_range"] is not None and d["occ_range"] < DEAD_RANGE)
        d["labeled"] = not d["dead_feed"]             # 죽은 피드는 라벨로 못 쓴다
        out.append(d)
    return out


def _unlabeled_lots():
    """표준데이터에만 있는 곳 — 라벨이 없어 순위에 못 넣는다."""
    if not STD.exists(): return []
    import csv
    out = []
    with open(STD, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                lat, lon = float(row["위도"]), float(row["경도"])
            except (TypeError, ValueError, KeyError):
                continue
            out.append({"name": row.get("주차장명"), "lat": lat, "lng": lon,
                        "type": row.get("주차장유형"), "fee": row.get("요금정보"),
                        "cell_cnt": row.get("주차구획수"), "labeled": False})
    return out


def find_candidates(lat, lon, min_n=MIN_N, labeled=None, unlabeled=None):
    """반환 {"lots": [...], "radius_used": m, "unlabeled": [...], "exhausted": bool}
    lots 는 직선거리 오름차순. `straight_m` 를 각 항목에 붙인다."""
    allp = _labeled_lots() if labeled is None else labeled
    live = [d for d in allp if not d.get("dead_feed")]     # ★ 순위는 살아있는 곳만
    dead_near = []
    picked, used = [], RADII[-1]
    for r in RADII:
        picked = []
        for d in live:
            m = haversine_m(lat, lon, d["lat"], d["lng"])
            if m <= r:
                picked.append({**d, "straight_m": round(m)})
        if len(picked) >= min_n:
            used = r; break
    else:
        used = RADII[-1]                      # 3km 까지 넓혔는데도 부족
    picked.sort(key=lambda x: x["straight_m"])

    unl = []
    for d in (_unlabeled_lots() if unlabeled is None else unlabeled):
        m = haversine_m(lat, lon, d["lat"], d["lng"])
        if m <= used:
            unl.append({**d, "straight_m": round(m)})
    unl.sort(key=lambda x: x["straight_m"])

    # 죽은 피드는 순위 밖에서 「실시간 미제공」으로 보여준다
    for d in allp:
        if d.get("dead_feed"):
            m = haversine_m(lat, lon, d["lat"], d["lng"])
            if m <= used:
                dead_near.append({**d, "straight_m": round(m),
                                  "note": "실시간 미제공"})
    dead_near.sort(key=lambda x: x["straight_m"])

    return {"lots": picked, "radius_used": used, "unlabeled": unl,
            "dead_feeds": dead_near,
            "exhausted": len(picked) < min_n,
            "message": None if picked else "주변에 공영주차장이 없습니다"}


if __name__ == "__main__":
    DESTS = {
        "안양역":   (37.401857, 126.922644),
        "인덕원역": (37.401494, 126.976680),
        "안양시청": (37.394259, 126.956861),
        "평촌역":   (37.394240, 126.963808),
        "범계역":   (37.389784, 126.950783),
        "석수역":   (37.435093, 126.902321),
    }
    L = _labeled_lots()
    print(f"라벨 있는 후보 풀 {len(L)}곳\n")
    print(f"{'목적지':<9}{'500m':>6}{'1km':>6}{'2km':>6}{'3km':>6}  "
          f"{'선택반경':>7}{'후보':>5}  최근접")
    for nm, (la, lo) in DESTS.items():
        cnt = {r: sum(1 for d in L if haversine_m(la, lo, d["lat"], d["lng"]) <= r)
               for r in (500, 1000, 2000, 3000)}
        res = find_candidates(la, lo, labeled=L)
        near = res["lots"][0] if res["lots"] else None
        print(f"{nm:<9}{cnt[500]:>6}{cnt[1000]:>6}{cnt[2000]:>6}{cnt[3000]:>6}  "
              f"{res['radius_used']:>6}m{len(res['lots']):>5}  "
              f"{near['name'][:12] if near else '-'} ({near['straight_m'] if near else '-'}m)"
              + ("  ⚠️소진" if res["exhausted"] else ""))
    print()
    r = find_candidates(*DESTS["석수역"], labeled=L)
    print(f"석수역: 반경 {r['radius_used']}m 로 확장, 후보 {len(r['lots'])}곳 "
          f"· 라벨 없는 곳 {len(r['unlabeled'])}곳")
    for d in r["lots"][:5]:
        print(f"   {d['name'][:16]:<18} {d['straight_m']:>5}m  {d['cell_cnt']:>4}면 "
              f"현재 {d['avail_now']}대")
