#!/usr/bin/env python3
"""목적지 검색 — 카카오 로컬 키워드·주소.

  from src.serve.places import search_places
  search_places("안양시청") -> {"places": [...], "source": "kakao", "stale": False}

★★ 카카오 좌표는 `x=경도, y=위도` 다. 위경도 순으로 읽으면 조용히 엉뚱한 곳이 나온다.
   이 모듈은 경계에서 한 번만 뒤집고, 바깥으로는 `lat`/`lng` 이름으로만 내보낸다.
★  키는 서버에만 있다. 응답 어디에도 키·원천 본문·내부 경로를 넣지 않는다.
★  카카오가 죽어도 검색은 「조용히 틀린 결과」 대신 「명확한 실패」를 돌려준다.
   TTL 이 지난 캐시가 있으면 `stale=True` 로 알리고 내보낸다.
"""
import json
import sqlite3
import threading
import time
from pathlib import Path

import requests

from src.config import INTERIM
from src.serve import metrics
from src.serve.candidates import haversine_m
from src.serve.routing import _key

KEYWORD = "https://dapi.kakao.com/v2/local/search/keyword.json"
ADDRESS = "https://dapi.kakao.com/v2/local/search/address.json"
COORD2ADDRESS = "https://dapi.kakao.com/v2/local/geo/coord2address.json"
TIMEOUT, RETRY = 5, 2

# 안양시청. 결과 정렬의 기준점이며 카카오에 보내는 중심 좌표다.
ANYANG = (37.394259, 126.956861)
# 안양시를 넉넉히 감싸는 사각형. 경계 밖이라고 버리지 않고 뒤로 민다.
BBOX = (37.34, 37.46, 126.87, 127.01)
RADIUS_M = 12000
CACHE_TTL_SEC = 300          # 짧은 TTL. 장소는 자주 안 바뀌지만 오래 들고 있지 않는다.
STALE_MAX_SEC = 86400        # 카카오 장애 때 이 이내의 캐시까지만 대신 내보낸다.
MAX_RESULTS = 10
MIN_INTERVAL_SEC = 0.2       # 프로세스당 초당 5회 상한
MAX_QUERY_LEN = 50

CACHE_DB = INTERIM / "places_cache.sqlite"
_lock = threading.RLock()
_last_call = 0.0


class SearchUnavailable(Exception):
    """카카오도 캐시도 답을 주지 못했다. 빈 목록으로 위장하지 않는다."""


class ReverseGeocodeUnavailable(Exception):
    """좌표는 있지만 주소로 변환하지 못했다."""


def _cache():
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(CACHE_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS place_query(
        q TEXT PRIMARY KEY, payload TEXT, ts REAL) WITHOUT ROWID""")
    con.commit()
    return con


def _read_cache(query):
    try:
        with _cache() as con:
            row = con.execute("SELECT payload, ts FROM place_query WHERE q = ?",
                              (query,)).fetchone()
    except sqlite3.Error:
        return None, None
    if not row:
        return None, None
    try:
        return json.loads(row[0]), time.time() - row[1]
    except (TypeError, ValueError):
        return None, None


def _write_cache(query, places):
    try:
        with _cache() as con:
            con.execute("INSERT OR REPLACE INTO place_query VALUES (?,?,?)",
                        (query, json.dumps(places, ensure_ascii=False), time.time()))
    except sqlite3.Error:
        pass                                  # 캐시 실패로 검색을 죽이지 않는다


def _throttle():
    global _last_call
    with _lock:
        wait = MIN_INTERVAL_SEC - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _call(url, params, key):
    _throttle()
    started = time.monotonic()
    name = ("kakao_local_keyword" if url == KEYWORD else
            "kakao_local_reverse" if url == COORD2ADDRESS else
            "kakao_local_address")
    for attempt in range(RETRY):
        try:
            response = requests.get(url, headers={"Authorization": f"KakaoAK {key}"},
                                    params=params, timeout=TIMEOUT)
            if response.status_code == 200:
                metrics.record_external(name, True, (time.monotonic() - started) * 1000)
                return response.json().get("documents") or []
            if response.status_code in (401, 403):
                break                         # 키 문제는 재시도해도 같다
        except requests.RequestException:
            pass
        if attempt < RETRY - 1:
            time.sleep(0.3)
    metrics.record_external(name, False, (time.monotonic() - started) * 1000)
    return None


def reverse_geocode(lat, lng, key=None):
    """지도에서 고른 WGS84 좌표를 서비스 `Place`로 바꿔 준다.

    좌표 선택은 주소 변환이 실패해도 유효하지만, 사용자가 다른 곳을 선택하는
    실수를 막기 위해 API는 실패를 명시적으로 알린다. 브라우저에 REST 키를 노출하지 않는다.
    """
    lat, lng = float(lat), float(lng)
    if not (36.0 <= lat <= 39.0 and 125.0 <= lng <= 129.0):
        raise ValueError("지원 좌표 범위를 벗어났습니다")
    key = _key() if key is None else key
    documents = _call(COORD2ADDRESS, {"x": lng, "y": lat}, key) if key else None
    if documents is None:
        raise ReverseGeocodeUnavailable("좌표를 주소로 변환할 수 없습니다")

    doc = documents[0] if documents else {}
    road = (doc.get("road_address") or {}).get("address_name")
    address = (doc.get("address") or {}).get("address_name")
    label = road or address or f"{lat:.5f}, {lng:.5f}"
    return {
        "name": label,
        "road_address": road or None,
        "address": address or None,
        "lat": lat,
        "lng": lng,
        "in_anyang": _in_anyang(lat, lng),
        "distance_from_anyang_m": round(haversine_m(ANYANG[0], ANYANG[1], lat, lng)),
        "category": "지도 선택",
    }


def _in_anyang(lat, lng):
    lo_lat, hi_lat, lo_lng, hi_lng = BBOX
    return lo_lat <= lat <= hi_lat and lo_lng <= lng <= hi_lng


def _place(doc):
    """카카오 문서 하나를 우리 스키마로. 여기가 x/y 를 뒤집는 **유일한** 지점이다."""
    try:
        lng, lat = float(doc["x"]), float(doc["y"])
    except (KeyError, TypeError, ValueError):
        return None
    name = doc.get("place_name") or doc.get("address_name") or ""
    road = doc.get("road_address_name") or (doc.get("road_address") or {}).get("address_name")
    return {
        "name": name,
        "road_address": road or None,
        "address": doc.get("address_name") or (doc.get("address") or {}).get("address_name"),
        "lat": lat, "lng": lng,
        "in_anyang": _in_anyang(lat, lng),
        "distance_from_anyang_m": round(haversine_m(ANYANG[0], ANYANG[1], lat, lng)),
        "category": doc.get("category_group_name") or None,
    }


def _rank(places):
    """안양시 안을 먼저, 그다음 시청에서 가까운 순. 밖이라고 버리지는 않는다."""
    seen, unique = set(), []
    for place in places:
        key = (round(place["lat"], 6), round(place["lng"], 6), place["name"])
        if key not in seen:
            seen.add(key)
            unique.append(place)
    unique.sort(key=lambda p: (not p["in_anyang"], p["distance_from_anyang_m"]))
    return unique[:MAX_RESULTS]


def search_places(query, key=None, use_cache=True):
    """{"places": [...], "source": "kakao"|"cache", "stale": bool} 를 돌려준다.

    카카오와 캐시가 모두 실패하면 `SearchUnavailable` 을 올린다. 빈 목록은
    「검색은 됐는데 결과가 없다」는 뜻으로만 쓴다 — 두 상황을 섞지 않는다."""
    query = (query or "").strip()
    if not query:
        raise ValueError("검색어가 비어 있습니다")
    query = query[:MAX_QUERY_LEN]

    cached, age = _read_cache(query) if use_cache else (None, None)
    if cached is not None and age is not None and age < CACHE_TTL_SEC:
        return {"places": cached, "source": "cache", "stale": False,
                "cache_age_sec": round(age)}

    # key=None 은 「환경에서 찾아라」, key="" 는 「키가 없다」로 구분한다.
    key = _key() if key is None else key
    documents = None
    if key:
        common = {"query": query, "x": ANYANG[1], "y": ANYANG[0],
                  "radius": RADIUS_M, "size": 15}
        # 키워드가 먼저다. 주소 검색은 "관평로 149" 같은 입력을 받기 위한 보완이다.
        by_keyword = _call(KEYWORD, common, key)
        by_address = _call(ADDRESS, {"query": query, "size": 10}, key)
        if by_keyword is not None or by_address is not None:
            documents = (by_keyword or []) + (by_address or [])

    if documents is None:
        # 카카오 실패. 오래된 캐시라도 있으면 stale 로 알리고 내보낸다.
        if cached is not None and age is not None and age < STALE_MAX_SEC:
            return {"places": cached, "source": "cache", "stale": True,
                    "cache_age_sec": round(age)}
        raise SearchUnavailable("장소 검색을 지금 사용할 수 없습니다")

    places = _rank([p for p in (_place(d) for d in documents) if p is not None])
    _write_cache(query, places)
    return {"places": places, "source": "kakao", "stale": False, "cache_age_sec": 0}


if __name__ == "__main__":
    import sys
    result = search_places(sys.argv[1] if len(sys.argv) > 1 else "안양시청")
    print(f"{result['source']} · stale={result['stale']} · {len(result['places'])}건")
    for place in result["places"]:
        print(f"  {place['name'][:22]:<24} {place['lat']:.5f},{place['lng']:.5f} "
              f"{'안양' if place['in_anyang'] else '외부':<4} {place['road_address'] or '-'}")
