#!/usr/bin/env python3
"""
표준데이터 좌표 결측 보정.

좌표는 `compet_n_500` / `compet_cells_500`(반경 500m 경쟁 주차장 수·면수) 계산에만 쓴다.
반경이 500m 라 좌표 오차가 크면 두 피처가 무의미해진다. 그래서 `validate` 가 본 작업만큼 중요하다.

  python3 src/collect/geocode.py            # 본 작업 (data/raw/std_parking.csv 필요)
  python3 src/collect/geocode.py validate   # 정확도 측정만 (도시공사 89곳 정답 대조)
  python3 src/collect/geocode.py --force    # 캐시 무시하고 API 재호출

채우는 순서 — API 없이 채울 수 있는 것부터:
  1) 공단 시설정보 7,456곳과 주소/이름 매칭  ← 추정점이 아니라 등록된 주차장 좌표. 가장 정확
  2) VWorld (road → 실패 시 parcel)
  3) Kakao (VWorld 실패분) + VWorld 성공분 교차검증

실패한 좌표는 **결측으로 두고 수기 확인 목록에 올린다.** 평균·행정동 중심으로 채우지 않는다.
"""
import argparse, json, math, os, re, sqlite3, sys, time
from pathlib import Path

import pandas as pd
import requests

ROOT    = Path(__file__).resolve().parents[2]
RAW     = ROOT / "data/raw"
INTERIM = ROOT / "data/interim"; INTERIM.mkdir(parents=True, exist_ok=True)
TAB     = ROOT / "reports/tables"; TAB.mkdir(parents=True, exist_ok=True)

STD_CSV   = RAW / "std_parking.csv"
KOTSA     = RAW / "kotsa_v2_anyang.jsonl"
DB        = RAW / "parking.db"
CACHE     = INTERIM / "geocode_cache.csv"
REPORT_MD = TAB / "geocode_report.md"

# 안양시 경계 — 동명 지명 때문에 다른 시로 날아가는 게 흔하다
LAT_MIN, LAT_MAX = 37.35, 37.47
LON_MIN, LON_MAX = 126.87, 127.01

SLEEP   = 0.2
RETRY   = 3
TIMEOUT = 15
CROSSCHECK_M = 100          # VWorld↔Kakao 이 이상 벌어지면 needs_review
_WARNED = set()             # 같은 원인의 실패를 한 번만 알린다

COLS = ["parking_id","name","address","lat","lon","source",
        "dist_vworld_kakao_m","needs_review"]

REPORT = []
def say(s=""):
    print(s); REPORT.append(s)

# ── 인증키 ────────────────────────────────────────────────
def env(name):
    v = os.environ.get(name, "")
    if not v and (ROOT/".env").exists():
        for line in (ROOT/".env").read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                v = line.split("=", 1)[1].strip()
    return v

VWORLD_KEY = env("VWORLD_KEY")
KAKAO_KEY  = env("KAKAO_REST_KEY")

# ── 정규화·거리 ───────────────────────────────────────────
_PAREN = re.compile(r"\([^)]*\)")
_JUNK  = re.compile(r"(번지|일원|일대)")

def norm_addr(s):
    if not isinstance(s, str): return ""
    s = _PAREN.sub("", s)
    s = _JUNK.sub("", s)
    s = s.replace("경기도", "경기").replace("특별시", "").replace("광역시", "")
    return re.sub(r"\s+", "", s).strip()

_SUFFIX = re.compile(r"(주차장|공영|노상|노외|제?\d+면?)$")

def norm_name(s):
    """접미어를 더 없어질 때까지 뗀다.
    한 번만 떼면 '안양3동노상주차장'→'안양3동노상' vs '안양3동노상'→'안양3동' 으로
    엇갈려 2단계 완전일치가 실패하고 3단계에서 후보가 폭발한다."""
    if not isinstance(s, str): return ""
    s = _PAREN.sub("", s)
    s = re.sub(r"[\s\-_·]", "", s).strip()
    while True:
        t = _SUFFIX.sub("", s)
        if t == s or not t: break
        s = t
    return s

def dong_of(addr):
    m = re.search(r"([가-힣]+[0-9]*동)\b", addr or "")
    return m.group(1) if m else ""

def hav(lat1, lon1, lat2, lon2):
    if None in (lat1, lon1, lat2, lon2) or any(pd.isna(v) for v in (lat1,lon1,lat2,lon2)):
        return float("nan")
    p = math.pi/180
    a = (math.sin((lat2-lat1)*p/2)**2 +
         math.cos(lat1*p)*math.cos(lat2*p)*math.sin((lon2-lon1)*p/2)**2)
    return 6371000 * 2 * math.asin(math.sqrt(a))

def in_bounds(lat, lon):
    return (lat is not None and lon is not None and not pd.isna(lat) and not pd.isna(lon)
            and LAT_MIN < lat < LAT_MAX and LON_MIN < lon < LON_MAX)

# ── 컬럼 자동 탐지 (추측 금지 — 실제 컬럼에서 찾아 출력) ────
def pick_col(df, label, keywords, required=True):
    hits = [c for c in df.columns if any(k in str(c) for k in keywords)]
    if not hits:
        if required:
            sys.exit(f"[컬럼 탐지 실패] {label}: {keywords} 중 어느 것도 없음\n"
                     f"  실제 컬럼: {list(df.columns)}")
        return None
    if len(hits) > 1:
        print(f"  ⚠️ {label}: 후보 {hits} → 첫 번째 '{hits[0]}' 사용")
    else:
        print(f"  {label:<10} → '{hits[0]}'")
    return hits[0]

# ── 지오코더 ──────────────────────────────────────────────
def _get(url, **kw):
    for a in range(RETRY):
        try:
            return requests.get(url, timeout=TIMEOUT, **kw)
        except Exception:
            if a == RETRY - 1: raise
            time.sleep(SLEEP * (a + 1) * 3)

def vworld(addr, typ="road"):
    """반환 (lat, lon). response.result.point.x 가 경도, .y 가 위도다."""
    if not VWORLD_KEY or not addr: return None
    try:
        r = _get("http://api.vworld.kr/req/address", params={
            "service": "address", "request": "getcoord", "version": "2.0",
            "crs": "epsg:4326",              # 생략 금지 — 기본이 4326이 아닐 수 있다
            "type": typ, "address": addr, "key": VWORLD_KEY})
        j = r.json()
        st = j.get("response", {}).get("status")
        if st != "OK":
            if st == "ERROR":
                warn_once("vworld_err",
                          f"VWorld ERROR: {j.get('response',{}).get('error')}")
            return None
        p = j["response"]["result"]["point"]
        return float(p["y"]), float(p["x"])          # (위도, 경도)
    except Exception:
        return None
    finally:
        time.sleep(SLEEP)

def warn_once(tag, msg):
    """인증 실패와 '주소 못 찾음' 은 둘 다 None 이라 구분이 안 된다. 원인을 한 번은 알린다."""
    if tag in _WARNED: return
    _WARNED.add(tag)
    print(f"  ⚠️ {msg}", flush=True)

def kakao(addr):
    """반환 (lat, lon). documents[0].x 가 경도, .y 가 위도다."""
    if not KAKAO_KEY or not addr: return None
    try:
        r = _get("https://dapi.kakao.com/v2/local/search/address.json",
                 params={"query": addr},
                 headers={"Authorization": f"KakaoAK {KAKAO_KEY}"})
        if r.status_code != 200:
            body = (r.text or "")[:200]
            warn_once("kakao_http", f"Kakao HTTP {r.status_code}: {body}")
            if r.status_code in (401, 403):
                warn_once("kakao_fix",
                          "→ 키는 유효하나 앱에 지도 서비스가 꺼져 있다. "
                          "developers.kakao.com → 내 애플리케이션 → 제품 설정 → "
                          "카카오맵 활성화 후 다시 실행할 것.")
            return None
        docs = (r.json() or {}).get("documents") or []
        if not docs:
            warn_once("kakao_empty", f"Kakao 결과 0건 (예: {addr})")
            return None
        return float(docs[0]["y"]), float(docs[0]["x"])   # (위도, 경도)
    except Exception as e:
        warn_once("kakao_exc", f"Kakao 예외: {str(e)[:150]}")
        return None
    finally:
        time.sleep(SLEEP)

def geocode_both(addr):
    """(lat, lon, source, dist_m). VWorld 우선, Kakao 로 교차검증."""
    v = vworld(addr, "road"); src = "vworld_road"
    if v is None:
        v = vworld(addr, "parcel"); src = "vworld_parcel"
    k = kakao(addr)
    if v is None and k is None:
        return None, None, "", float("nan")
    if v is None:
        return k[0], k[1], "kakao", float("nan")
    d = hav(v[0], v[1], k[0], k[1]) if k else float("nan")
    return v[0], v[1], src, d

# ── 1. 공단 매칭 ──────────────────────────────────────────
def kotsa_index():
    rows = []
    with open(KOTSA, encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            try:
                lat, lon = float(o["prk_plce_entrc_la"]), float(o["prk_plce_entrc_lo"])
            except (TypeError, ValueError, KeyError):
                continue
            if not in_bounds(lat, lon): continue
            ad = o.get("prk_plce_adres") or ""
            rows.append({"lat": lat, "lon": lon,
                         "na": norm_addr(ad), "nn": norm_name(o.get("prk_plce_nm")),
                         "dong": dong_of(ad)})
    return pd.DataFrame(rows)

def match_kotsa(need, ki):
    """엄격한 것부터. 단계마다 매칭 건수 출력. 다중 매칭은 보류."""
    filled, ambiguous = {}, {}
    stage_counts = {}
    by_addr, by_name = {}, {}
    for r in ki.itertuples():
        if r.na: by_addr.setdefault(r.na, []).append(r)
        if r.nn: by_name.setdefault(r.nn, []).append(r)

    def take(stage, key_fn, table):
        n = 0
        for pid, rec in need.items():
            if pid in filled or pid in ambiguous: continue
            k = key_fn(rec)
            if not k: continue
            cand = table.get(k)
            if not cand: continue
            if len(cand) > 1:
                ambiguous[pid] = f"{stage}: 후보 {len(cand)}건"
                continue
            filled[pid] = (cand[0].lat, cand[0].lon); n += 1
        stage_counts[stage] = n
        return n

    take("1 주소 완전일치", lambda r: r["na"], by_addr)
    take("2 이름 완전일치", lambda r: r["nn"], by_name)

    n = 0
    for pid, rec in need.items():
        if pid in filled or pid in ambiguous: continue
        nn, dg = rec["nn"], rec["dong"]
        if not nn or len(nn) < 3 or not dg: continue
        def substantial(a, b):
            # 짧은 쪽이 긴 쪽의 60% 이상이어야 "포함"으로 인정
            if not a or not b: return False
            sh, lo = (a, b) if len(a) <= len(b) else (b, a)
            return sh in lo and len(sh) >= 0.6 * len(lo)
        cand = [r for r in ki.itertuples()
                if r.dong == dg and substantial(nn, r.nn)]
        if not cand: continue
        if len(cand) > 1:
            ambiguous[pid] = f"3 이름포함+동: 후보 {len(cand)}건"; continue
        filled[pid] = (cand[0].lat, cand[0].lon); n += 1
    stage_counts["3 이름포함+같은동"] = n
    return filled, ambiguous, stage_counts

# ── 4. ★ 정확도 검증 — 도시공사 89곳은 정답이 있다 ─────────
def validate():
    if not DB.exists(): sys.exit(f"{DB} 없음")
    con = sqlite3.connect(DB)
    lots = pd.read_sql("SELECT parking_id,name,addr,lat,lng,div FROM lots", con); con.close()
    lots = lots.dropna(subset=["lat","lng","addr"])
    say("## 정확도 검증 — 도시공사 89곳 (좌표 결측 0 = 정답)")
    say()
    if not (VWORLD_KEY or KAKAO_KEY):
        say("- ❌ VWORLD_KEY / KAKAO_REST_KEY 가 둘 다 없어 측정 불가.")
        say()
        return None
    rows = []
    for r in lots.itertuples():
        v = vworld(r.addr, "road") or vworld(r.addr, "parcel")
        k = kakao(r.addr)
        rows.append({"parking_id": r.parking_id, "name": r.name,
                     "is_nosang": int(r.div == "노상"),
                     "err_vworld": hav(r.lat, r.lng, *v) if v else float("nan"),
                     "err_kakao":  hav(r.lat, r.lng, *k) if k else float("nan"),
                     # 두 지오코더가 서로 얼마나 다른가 — 독립성 점검용
                     "dist_vk_m":  hav(*v, *k) if (v and k) else float("nan")})
    v = pd.DataFrame(rows)
    v.to_csv(TAB / "geocode_validation.csv", index=False)

    say("| 지오코더 | n | 중앙값(m) | 90분위(m) | 최대(m) |")
    say("|---|---:|---:|---:|---:|")
    for c, lb in (("err_vworld","VWorld"), ("err_kakao","Kakao")):
        s = v[c].dropna()
        if len(s):
            say(f"| {lb} | {len(s)} | **{s.median():.0f}** | {s.quantile(.9):.0f} | {s.max():.0f} |")
    say()
    say("**노상 vs 노외** — 노상은 도로 구간이라 주소가 부정확할 것으로 예상했다.")
    say()
    say("| 유형 | n | VWorld 중앙값 | Kakao 중앙값 |")
    say("|---|---:|---:|---:|")
    for f, lb in ((1,"노상"), (0,"노상 아님")):
        g = v[v.is_nosang == f]
        if len(g):
            say(f"| {lb} | {len(g)} | {g.err_vworld.median():.0f}m | {g.err_kakao.median():.0f}m |")
    say()
    say("**두 지오코더의 상호 일치도** — 서로 독립인지 점검한다.")
    say()
    say("| 구간 | n | VW↔Kakao 중앙값 | 1m 미만 |")
    say("|---|---:|---:|---:|")
    for f, lb in ((1,"노상"), (0,"노상 아님")):
        g = v[(v.is_nosang == f)]["dist_vk_m"].dropna()
        if len(g): say(f"| {lb} | {len(g)} | {g.median():.1f}m | {int((g<1).sum())}곳 |")
    vk = v["dist_vk_m"].dropna()
    if len(vk) and vk.median() < 5:
        say()
        say("> ⚠️ 두 지오코더가 서로 거의 같다. **둘 다 같은 도로명주소 DB 를 쓰기 때문**이며,")
        say("> 서로에 대한 독립 검증이 되지 못한다. 도시공사 좌표도 같은 DB 산출물이면")
        say("> 이 비교 전체가 순환 참조다. **보수 기준은 노외 구간의 오차를 쓴다.**")
    say()
    mv, mk = v.err_vworld.median(), v.err_kakao.median()
    if pd.notna(mv) and pd.notna(mk):
        say(f"- 더 정확한 쪽: **{'VWorld' if mv <= mk else 'Kakao'}** ({min(mv,mk):.0f}m vs {max(mv,mk):.0f}m)")
    # 노상은 도시공사 좌표 자체가 지오코더 산출물이라 오차가 0 에 붙는다(순환).
    # 판정은 순환이 아닌 노외 구간으로 한다.
    ext = v[v.is_nosang == 0]
    cand = [x for x in (ext.err_vworld.median(), ext.err_kakao.median()) if pd.notna(x)]
    best = min(cand) if cand else float("nan")
    if pd.notna(best):
        say(f"- 전체 중앙값은 {min([x for x in (mv,mk) if pd.notna(x)], default=float('nan')):.0f}m 지만 "
            f"노상이 순환이라 부풀려진 값이다.")
        say(f"- ### 결론: **노외 기준 오차 중앙값 {best:.0f}m** — "
            + ("**100m 를 넘는다. `compet_*` 의 반경 500m 를 재검토해야 한다** "
               "(오차가 반경의 20%를 넘으면 경쟁 집계가 흔들린다)."
               if best > 100 else
               f"100m 이하이고 반경 500m 의 {best/500:.0%} 수준이라 경쟁 집계에 쓸 수 있다."))
    say()
    return v

# ── 본 작업 ───────────────────────────────────────────────
def main(force=False):
    if not STD_CSV.exists():
        sys.exit(f"[선행 조건 미충족] {STD_CSV} 가 없다.\n"
                 f"  경기데이터드림 「주차장 정보 현황(제공표준)」 안양 107행을 먼저 받아야 한다.\n"
                 f"  좌표 검증만 하려면: python3 {Path(__file__).name} validate")

    std = pd.read_csv(STD_CSV, dtype=str, encoding="utf-8-sig")
    print("std_parking.csv 컬럼:", list(std.columns))
    print("컬럼 매핑:")
    c_id   = pick_col(std, "관리번호", ["관리번호","주차장관리"], required=False)
    c_name = pick_col(std, "주차장명", ["주차장명","명칭"])
    c_road = pick_col(std, "도로명주소", ["도로명주소"], required=False)
    c_jibun= pick_col(std, "지번주소", ["지번주소","소재지지번"], required=False)
    c_lat  = pick_col(std, "위도", ["위도","latitude"], required=False)
    c_lon  = pick_col(std, "경도", ["경도","longitude"], required=False)
    if not (c_road or c_jibun):
        sys.exit("주소 컬럼(도로명/지번)이 하나도 없다. 지오코딩 불가.")

    std["k_id"]   = std[c_id] if c_id else std.index.astype(str)
    std["k_name"] = std[c_name].fillna("")
    std["k_addr"] = (std[c_road].fillna("") if c_road else "")
    if c_jibun is not None:
        std["k_addr"] = std["k_addr"].where(std["k_addr"].str.strip() != "", std[c_jibun].fillna(""))
    std["k_lat"] = pd.to_numeric(std[c_lat], errors="coerce") if c_lat else pd.NA
    std["k_lon"] = pd.to_numeric(std[c_lon], errors="coerce") if c_lon else pd.NA

    say("# 지오코딩 리포트")
    say()
    say(f"- 입력 {len(std)}행 / 주소 있는 행 {int((std["k_addr"].str.strip()!='').sum())}")
    have = std.apply(lambda r: in_bounds(r["k_lat"], r["k_lon"]), axis=1)
    say(f"- 원본 좌표 유효 **{int(have.sum())}행** / 결측·범위밖 **{int((~have).sum())}행** ← 채울 대상")
    say()

    cache = pd.read_csv(CACHE, dtype={"parking_id": str}) if (CACHE.exists() and not force) \
            else pd.DataFrame(columns=COLS)
    cached = {r.parking_id: r for r in cache.itertuples()} if len(cache) else {}
    if cached: say(f"- 캐시 {len(cached)}건 재사용 (API 재호출 안 함). 무시하려면 `--force`")

    out = {}
    for r in std[have].itertuples():
        out[r.k_id] = dict(
            parking_id=r.k_id, name=r.k_name, address=r.k_addr,
            lat=r.k_lat, lon=r.k_lon, source="std",
            dist_vworld_kakao_m=float("nan"), needs_review=False)

    need = {}
    for r in std[~have].itertuples():
        pid = r.k_id; ad = r.k_addr
        need[pid] = dict(name=r.k_name, address=ad,
                         na=norm_addr(ad), nn=norm_name(r.k_name), dong=dong_of(ad))

    # 1) 공단 매칭
    say("## 1. 공단 시설정보 매칭 (API 호출 없음)")
    say()
    if KOTSA.exists():
        ki = kotsa_index()
        say(f"- 공단 안양 좌표 유효 {len(ki):,}곳")
        filled, ambiguous, counts = match_kotsa(need, ki)
        say()
        say("| 단계 | 매칭 |")
        say("|---|---:|")
        for k, v_ in counts.items(): say(f"| {k} | {v_}건 |")
        say(f"| **소계** | **{len(filled)}건** |")
        if ambiguous:
            say(f"| 다중 매칭으로 보류 | {len(ambiguous)}건 |")
        say()
        for pid, (la, lo) in filled.items():
            rec = need.pop(pid)
            out[pid] = dict(parking_id=pid, name=rec["name"], address=rec["address"],
                            lat=la, lon=lo, source="kotsa",
                            dist_vworld_kakao_m=float("nan"), needs_review=False)
    else:
        ambiguous = {}
        say(f"- ⚠️ {KOTSA.name} 없음 → 건너뜀")
        say()

    # 2·3) VWorld → Kakao
    say("## 2·3. VWorld → Kakao")
    say()
    if not (VWORLD_KEY or KAKAO_KEY):
        say("- ❌ `VWORLD_KEY` / `KAKAO_REST_KEY` 가 `.env` 에 없다. API 단계 전체 건너뜀.")
        say(f"- 남은 {len(need)}건은 좌표 결측으로 남는다(평균값 등으로 채우지 않는다).")
        say()
        stats = {}
    else:
        stats = {"vworld_road":0, "vworld_parcel":0, "kakao":0, "fail":0, "crosscheck_far":0}
        for pid, rec in list(need.items()):
            if pid in cached and pd.notna(getattr(cached[pid], "lat", None)):
                c = cached[pid]
                out[pid] = dict(parking_id=pid, name=rec["name"], address=rec["address"],
                                lat=c.lat, lon=c.lon, source=c.source,
                                dist_vworld_kakao_m=c.dist_vworld_kakao_m,
                                needs_review=bool(c.needs_review))
                need.pop(pid); continue
            lat, lon, src, d = geocode_both(rec["address"])
            if lat is None:
                stats["fail"] += 1; continue
            review = False
            if not in_bounds(lat, lon):
                say(f"  - 경계 밖이라 폐기: {rec['name']} / {rec['address']} → ({lat:.5f},{lon:.5f})")
                lat = lon = None; src = ""; review = True
            elif pd.notna(d) and d >= CROSSCHECK_M:
                review = True; stats["crosscheck_far"] += 1
            if src: stats[src] = stats.get(src, 0) + 1
            out[pid] = dict(parking_id=pid, name=rec["name"], address=rec["address"],
                            lat=lat, lon=lon, source=src,
                            dist_vworld_kakao_m=d, needs_review=review)
            need.pop(pid)
        say("| 결과 | 건수 |")
        say("|---|---:|")
        for k, v_ in stats.items(): say(f"| {k} | {v_} |")
        say()

    # 남은 실패분
    for pid, rec in need.items():
        out[pid] = dict(parking_id=pid, name=rec["name"], address=rec["address"],
                        lat=None, lon=None, source="",
                        dist_vworld_kakao_m=float("nan"), needs_review=True)

    df = pd.DataFrame(list(out.values()), columns=COLS)
    df.to_csv(CACHE, index=False)

    say("## 최종")
    say()
    say("| source | 건수 |")
    say("|---|---:|")
    for k, v_ in df["source"].replace("", "(실패)").value_counts().items():
        say(f"| {k} | {v_} |")
    ok = df["lat"].notna().sum()
    say(f"| **좌표 확보** | **{ok} / {len(df)}** |")
    say()
    say("> `source` 는 `coord_is_geocoded` 피처로도 쓴다 — 모델이 좌표 신뢰도를 알게 하려고.")
    say(f"> `kotsa`/`std` 는 등록 좌표, `vworld_*`/`kakao` 는 추정 좌표다.")
    say()

    man = df[(df["lat"].isna()) | (df["needs_review"])]
    say(f"## ★ 수기 확인 필요 — {len(man)}건")
    say()
    if len(man):
        say("| id | 주차장명 | 주소 | source | VW↔Kakao(m) | 사유 |")
        say("|---|---|---|---|---:|---|")
        for r in man.itertuples():
            why = ("좌표 없음" if pd.isna(r.lat) else
                   f"교차검증 {r.dist_vworld_kakao_m:.0f}m 이상 차이")
            if r.parking_id in ambiguous: why = ambiguous[r.parking_id]
            dd = "-" if pd.isna(r.dist_vworld_kakao_m) else f"{r.dist_vworld_kakao_m:.0f}"
            say(f"| {r.parking_id} | {r.name} | {r.address} | {r.source or '-'} | {dd} | {why} |")
    else:
        say("없음 ✅")
    say()
    validate()
    REPORT_MD.write_text("\n".join(REPORT) + "\n", encoding="utf-8")
    print(f"\n→ {CACHE}\n→ {REPORT_MD}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", nargs="?", default="run", choices=["run","validate"])
    ap.add_argument("--force", action="store_true", help="캐시 무시하고 API 재호출")
    a = ap.parse_args()
    if a.cmd == "validate":
        say("# 지오코더 정확도 검증")
        say()
        validate()
        REPORT_MD.write_text("\n".join(REPORT) + "\n", encoding="utf-8")
        print(f"\n→ {REPORT_MD}")
    else:
        main(force=a.force)
