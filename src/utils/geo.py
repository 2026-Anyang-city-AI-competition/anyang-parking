#!/usr/bin/env python3
"""
공간 유틸 — VWorld WFS 조회 · 좌표 변환 · 반경 집계.

⚠️ 레이어 ID·필드명은 추측하지 않는다. 아래 값은 GetCapabilities 와
   실제 GetFeature 응답으로 확인한 것이다(2026-09-03).

   lt_c_uq111        도시지역        uname("제2종일반주거지역") · MultiPolygon
   lp_pa_cbnd_bubun  연속지적도      pnu · jiga(공시지가 원/㎡) · addr · MultiPolygon
   lt_l_n3a0020000   도로중심선      rvwd(도로폭 m) · rdln · MultiLineString
   lt_c_spbd         도로명주소건물   buld_nm · gro_flo_co(지상층수) · MultiPolygon

   BBOX 순서는 minx,miny,maxx,maxy = lon,lat 순 (실측 확인)
   면적·거리는 반드시 EPSG:5186 으로 변환한 뒤 계산한다. 경위도로 재면 틀린다.
"""
import json, os, time
from pathlib import Path

import requests
from pyproj import Transformer
from shapely.geometry import shape, Point
from shapely.ops import transform as shp_transform
from shapely.validation import make_valid

WFS = "http://api.vworld.kr/req/wfs"
TIMEOUT, RETRY, SLEEP = 60, 3, 0.15

LAYER_ZONING   = "lt_c_uq111"
LAYER_CADASTRE = "lp_pa_cbnd_bubun"
LAYER_ROAD     = "lt_l_n3a0020000"
LAYER_BUILDING = "lt_c_spbd"

# EPSG:5186 = 중부원점 TM. 미터 단위라 면적·거리 계산에 쓴다.
_TO_M   = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)
_FROM_M = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)

def to_m(geom):
    return shp_transform(lambda x, y, z=None: _TO_M.transform(x, y), geom)

def point_m(lat, lon):
    x, y = _TO_M.transform(lon, lat)
    return Point(x, y)

def vworld_key(root=None):
    k = os.environ.get("VWORLD_KEY", "")
    if not k:
        root = Path(root or Path(__file__).resolve().parents[2])
        for line in (root / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("VWORLD_KEY="):
                k = line.split("=", 1)[1].strip()
    return k

def bbox_around(lat, lon, r_m):
    """반경을 감싸는 경위도 bbox. 위도 1도≈111,320m, 경도는 cos(위도) 배."""
    import math
    dlat = r_m / 111320.0
    dlon = r_m / (111320.0 * math.cos(math.radians(lat)))
    return f"{lon-dlon},{lat-dlat},{lon+dlon},{lat+dlat}"

def wfs(layer, lat, lon, r_m, key, maxfeatures=1000):
    """반환 (features, truncated). 실패 시 ([], False) 가 아니라 예외를 올린다."""
    params = {"SERVICE": "WFS", "REQUEST": "GetFeature", "VERSION": "1.1.0",
              "TYPENAME": layer, "BBOX": bbox_around(lat, lon, r_m),
              "SRSNAME": "EPSG:4326", "OUTPUT": "application/json",
              "KEY": key, "MAXFEATURES": str(maxfeatures)}
    last = None
    for a in range(RETRY):
        try:
            r = requests.get(WFS, params=params, timeout=TIMEOUT)
            time.sleep(SLEEP)
            t = r.text.strip()
            if not t.startswith("{"):
                last = RuntimeError(f"{layer}: 비JSON 응답 {t[:150]}")
            else:
                j = r.json()
                f = j.get("features") or []
                return f, len(f) >= maxfeatures
        except Exception as e:
            last = e
        time.sleep(SLEEP * (a + 1) * 4)
    raise last

def geoms_with_props(features):
    """(shapely geom in EPSG:5186, properties) 목록. 깨진 폴리곤은 보정한다."""
    out = []
    for f in features:
        g = f.get("geometry")
        if not g: continue
        try:
            gm = to_m(shape(g))
            if not gm.is_valid: gm = make_valid(gm)
            out.append((gm, f.get("properties") or {}))
        except Exception:
            continue
    return out

def circle_m(lat, lon, r_m):
    return point_m(lat, lon).buffer(r_m)
