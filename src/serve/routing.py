#!/usr/bin/env python3
"""
카카오 길찾기 · 로컬 — 목적지에서 각 주차장까지의 소요시간과 대체 주차장.

★★ 좌표는 전부 `경도(x), 위도(y)` 순이다. 위경도 순으로 넣으면 조용히 엉뚱한 곳이 나온다.
★  카카오가 죽어도 서비스는 살아야 한다 → 모든 호출에 직선거리 근사 폴백이 붙는다.

실측 확인된 사양 (2026-09-04):
  POST https://apis-navi.kakaomobility.com/v1/destinations/directions
       body {"origin":{"x","y"}, "destinations":[{"x","y","key"}], "radius"}
       resp {"trans_id", "routes":[{"result_code","result_msg","key",
                                    "summary":{"distance"(m),"duration"(초)}}]}
       목적지 최대 30개 · 쿼터 관련 응답 헤더 없음
  GET  https://apis-navi.kakaomobility.com/v1/future/directions
       departure_time=YYYYMMDDHHMM (미래여야 함) · origin/destination="x,y"
       resp routes[0].summary{distance, duration, fare{taxi,toll}}
  GET  https://dapi.kakao.com/v2/local/search/category.json?category_group_code=PK6
       x,y,radius(최대 20000),size(≤15),page(≤3) → 한 질의 최대 45건(pageable_count)
       documents[]{place_name,x,y,distance,category_name,road_address_name,place_url,id}

  python3 src/serve/routing.py            # 자체 점검(폴백 포함)
"""
import json, math, os, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
KST  = timezone(timedelta(hours=9))
NAVI = "https://apis-navi.kakaomobility.com"
LOCAL = "https://dapi.kakao.com/v2/local/search/category.json"
TIMEOUT, MAX_DEST = 15, 30

# 폴백 상수 — 직선거리 × 우회계수 ÷ 도심 평균속도
#   ★ 출발지 1곳·경로 25개로 맞춘 값이라 근거가 얇다. **더 맞추지 않는다(재보정 금지).**
#     대신 폴백 결과에 estimated=True 를 달아 호출자가 신뢰도를 알게 한다.
#     (참고: 원래 가정 1.3/20km/h 는 소요시간을 37% 과소추정했다)
DETOUR, URBAN_KMH = 1.6, 15.5

def _key():
    k = os.environ.get("KAKAO_REST_KEY", "")
    if not k and (ROOT/".env").exists():
        for l in (ROOT/".env").read_text(encoding="utf-8").splitlines():
            if l.startswith("KAKAO_REST_KEY="): k = l.split("=", 1)[1].strip()
    return k

def haversine_m(lat1, lon1, lat2, lon2):
    """직선거리(m). 후보 추림(반경 1km 등)에 쓰는 건 **정상 로직**이지 폴백이 아니다."""
    p = math.pi/180
    a = (math.sin((lat2-lat1)*p/2)**2 +
         math.cos(lat1*p)*math.cos(lat2*p)*math.sin((lon2-lon1)*p/2)**2)
    return 6371000 * 2 * math.asin(math.sqrt(a))

def fallback_eta(lat1, lon1, lat2, lon2):
    """직선거리 근사. 카카오가 죽어도 서비스가 돌게 하는 최후 수단.

    ★ 상수는 출발지 1곳·경로 25개로 맞춘 것이라 근거가 얇다. 더 맞추지 않는다.
      대신 `estimated: True` 를 달아 호출자가 "추정치"임을 알게 한다.
      이 플래그가 붙은 항목은 **만차확률 기반 순위 강등을 적용하지 않는다.**"""
    d = haversine_m(lat1, lon1, lat2, lon2) * DETOUR
    return {"distance": int(round(d)), "duration": int(round(d / (URBAN_KMH*1000/3600))),
            "source": "fallback", "estimated": True}

def multi_eta(origin, dests, radius=10000, key=None):
    """origin=(lat,lon) · dests={id:(lat,lon)} → {id:{distance,duration,source}}
    실패하면 그 항목만 폴백으로 채운다. 예외를 밖으로 던지지 않는다."""
    if key is None: key = _key()      # key="" 는 '키 없음' 강제(폴백 시험용)
    out, items = {}, list(dests.items())
    for i in range(0, len(items), MAX_DEST):          # 30개씩 끊는다
        chunk = items[i:i+MAX_DEST]
        got = {}
        if key:
            body = {"origin": {"x": origin[1], "y": origin[0]},        # ★ x=경도
                    "destinations": [{"x": lon, "y": lat, "key": str(k)}
                                     for k, (lat, lon) in chunk],
                    "radius": radius}
            try:
                r = requests.post(f"{NAVI}/v1/destinations/directions",
                                  headers={"Authorization": f"KakaoAK {key}",
                                           "Content-Type": "application/json"},
                                  data=json.dumps(body), timeout=TIMEOUT)
                if r.status_code == 200:
                    for rt in (r.json().get("routes") or []):
                        if rt.get("result_code") == 0 and rt.get("summary"):
                            got[rt["key"]] = {"distance": rt["summary"]["distance"],
                                              "duration": rt["summary"]["duration"],
                                              "source": "kakao", "estimated": False}
                else:
                    print(f"[routing] HTTP {r.status_code} {r.text[:120]} → 폴백")
            except Exception as e:
                print(f"[routing] {type(e).__name__}: {str(e)[:100]} → 폴백")
        for k, (lat, lon) in chunk:
            out[k] = got.get(str(k)) or fallback_eta(origin[0], origin[1], lat, lon)
    return out

def future_eta(origin, dest, minutes_ahead=30, key=None):
    """미래 출발시각 기준 소요시간. departure_time 은 YYYYMMDDHHMM 이고 미래여야 한다."""
    if key is None: key = _key()
    if key:
        dt = (datetime.now(KST) + timedelta(minutes=max(1, minutes_ahead))).strftime("%Y%m%d%H%M")
        try:
            r = requests.get(f"{NAVI}/v1/future/directions",
                             headers={"Authorization": f"KakaoAK {key}"},
                             params={"origin": f"{origin[1]},{origin[0]}",   # ★ 경도,위도
                                     "destination": f"{dest[1]},{dest[0]}",
                                     "departure_time": dt, "summary": "true"},
                             timeout=TIMEOUT)
            if r.status_code == 200:
                rt = (r.json().get("routes") or [{}])[0]
                if rt.get("result_code") == 0:
                    s = rt["summary"]
                    return {"distance": s["distance"], "duration": s["duration"],
                            "fare": s.get("fare"), "departure_time": dt,
                            "source": "kakao", "estimated": False}
            print(f"[future] HTTP {r.status_code} → 폴백")
        except Exception as e:
            print(f"[future] {type(e).__name__}: {str(e)[:100]} → 폴백")
    return {**fallback_eta(origin[0], origin[1], dest[0], dest[1]), "fare": None}

def alt_parkings(lat, lon, radius=1000, key=None, pages=3):
    """대체 주차장(카카오 PK6). 일반인이 못 대는 아파트·사무실 부설은 애초에 안 나온다.
    ⚠️ 한 질의당 최대 45건(15×3). total_count 가 더 크면 반경을 줄여 격자로 훑을 것."""
    if key is None: key = _key()
    if not key: return [], {"error": "no key"}
    out, meta = [], {}
    for pg in range(1, pages+1):
        try:
            r = requests.get(LOCAL, headers={"Authorization": f"KakaoAK {key}"},
                             params={"category_group_code": "PK6", "x": lon, "y": lat,
                                     "radius": radius, "size": 15, "page": pg},
                             timeout=TIMEOUT)
            if r.status_code != 200:
                meta["error"] = f"HTTP {r.status_code} {r.text[:100]}"; break
            j = r.json(); meta = j.get("meta") or {}
            out += j.get("documents") or []
            if meta.get("is_end"): break
            time.sleep(0.2)
        except Exception as e:
            meta["error"] = f"{type(e).__name__}: {str(e)[:100]}"; break
    for d in out:
        d["is_public"] = "공영" in (d.get("category_name") or "")
    return out, meta

if __name__ == "__main__":
    import sqlite3
    con = sqlite3.connect(ROOT/"data/raw/parking.db")
    rows = con.execute("SELECT parking_id,name,lat,lng FROM lots WHERE lat IS NOT NULL "
                       "ORDER BY parking_id LIMIT 12").fetchall(); con.close()
    ORIG = (37.4018, 126.9226)                       # 안양역 부근 (위도, 경도)
    dests = {pid: (lat, lng) for pid, nm, lat, lng in rows}
    nm = {pid: n for pid, n, _, _ in rows}

    print("=== multi_eta (카카오) ===")
    res = multi_eta(ORIG, dests)
    for k, v in list(res.items())[:6]:
        print(f"  {nm[k][:14]:<16} {v['distance']:>6}m {v['duration']:>5}초 ({v['source']})")

    print("\n=== 폴백 강제 (키를 비워서) ===")
    fb = multi_eta(ORIG, dests, key="")
    for k, v in list(fb.items())[:3]:
        print(f"  {nm[k][:14]:<16} {v['distance']:>6}m {v['duration']:>5}초 "
              f"({v['source']}, estimated={v['estimated']})")

    print("\n=== ★ estimated 플래그 검증 ===")
    bad_k = [k for k, v in res.items() if v["source"] == "kakao" and v["estimated"] is not False]
    bad_f = [k for k, v in fb.items() if v["source"] == "fallback" and v["estimated"] is not True]
    miss  = [k for k, v in list(res.items()) + list(fb.items()) if "estimated" not in v]
    print(f"  카카오 경로 estimated=False  : {'PASS' if not bad_k else f'FAIL {bad_k}'}"
          f"  (n={sum(1 for v in res.values() if v['source']=='kakao')})")
    print(f"  폴백  경로 estimated=True   : {'PASS' if not bad_f else f'FAIL {bad_f}'}"
          f"  (n={sum(1 for v in fb.values() if v['source']=='fallback')})")
    print(f"  플래그 누락 없음             : {'PASS' if not miss else f'FAIL {miss}'}")
    fe_k = future_eta(ORIG, list(dests.values())[0])
    fe_f = future_eta(ORIG, list(dests.values())[0], key="")
    print(f"  future_eta 카카오/폴백       : "
          f"{'PASS' if fe_k.get('estimated') is False and fe_f.get('estimated') is True else 'FAIL'}"
          f"  ({fe_k.get('source')}/{fe_k.get('estimated')} · {fe_f.get('source')}/{fe_f.get('estimated')})")

    print("\n=== future_eta (30분 뒤 출발) ===")
    print(" ", future_eta(ORIG, dests[16] if 16 in dests else list(dests.values())[0]))

    print("\n=== alt_parkings (안양시청 1km) ===")
    docs, meta = alt_parkings(37.3943, 126.9568, 1000)
    pub = sum(1 for d in docs if d["is_public"])
    print(f"  {len(docs)}곳 수집 · total_count {meta.get('total_count')} · "
          f"공영 {pub} / 그 외 {len(docs)-pub}")
