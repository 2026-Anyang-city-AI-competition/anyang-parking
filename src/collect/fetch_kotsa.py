#!/usr/bin/env python3
"""
한국교통안전공단 주차정보 제공 API 조사·수집기
  python3 src/collect/fetch_kotsa.py probe          # 규모·응답형태·numOfRows 상한
  python3 src/collect/fetch_kotsa.py sttus [-y]     # 시설정보 전수 → 경기도 필터(안양 표시)
  python3 src/collect/fetch_kotsa.py realtime-test  # ★ 실시간 갱신 여부 (10분 2회)
  python3 src/collect/fetch_kotsa.py realtime-quick # ★ 위와 같되 1페이지만, 총 2회 호출
  python3 src/collect/fetch_kotsa.py rt-locate      # 실시간에서 경기·안양이 몇 페이지에 있나
  python3 src/collect/fetch_kotsa.py opr            # 안양분 운영정보
  python3 src/collect/fetch_kotsa.py match          # 포털 89곳과 좌표 매칭
"""
import argparse, json, math, os, sqlite3, sys, time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[2]
RAW  = ROOT / "data/raw"; RAW.mkdir(parents=True, exist_ok=True)
BASE = "https://apis.data.go.kr/B553881/Parking"
DAILY_LIMIT = 10000        # 승인 메일의 실제 일일 한도로 맞출 것
KEY  = os.environ.get("KOTSA_KEY", "")
if not KEY:
    for line in (ROOT/".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("KOTSA_KEY="):
            KEY = line.split("=", 1)[1].strip()
if not KEY:
    sys.exit("KOTSA_KEY 없음. .env 에 디코딩 인증키를 넣어라.")

CALLS = 0
def call(op, page=1, rows=100, retry=5):
    """디코딩 키를 params로 넘긴다(requests가 한 번만 인코딩)."""
    global CALLS
    for a in range(retry):
        try:
            r = requests.get(f"{BASE}/{op}",
                params={"serviceKey": KEY, "numOfRows": rows,
                        "pageNo": page, "format": 2},
                timeout=120)
            CALLS += 1
            if r.text.lstrip().startswith("<"):      # 에러는 XML로 온다
                raise RuntimeError(r.text[:300])
            j = r.json()
            if str(j.get("resultCode")) not in ("0", "00"):
                raise RuntimeError(f"{j.get('resultCode')} {j.get('resultMsg')}")
            return j
        except Exception as e:
            if a == retry - 1: raise
            time.sleep(3 * (a + 1))

def items(j, op):
    v = j.get(op)
    if v is None: return []
    return v if isinstance(v, list) else [v]

def ckpt_path(tag):
    return RAW / f".ckpt_{tag}.json"

def ckpt_load(tag, rows):
    """이전 실행이 남긴 체크포인트를 읽는다. rows가 다르면 무시(페이징이 어긋남)."""
    f = ckpt_path(tag)
    if not f.exists(): return None
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception as e:
        print(f"체크포인트 손상, 무시: {e}"); return None
    if d.get("rows") != rows:
        print(f"체크포인트의 rows={d.get('rows')} 가 현재 {rows} 와 달라 무시한다.")
        return None
    print(f"체크포인트 발견: p{d.get('last_page')} 까지 완료 → 이어서 진행")
    return d

def ckpt_save(tag, d):
    json.dump(d, open(ckpt_path(tag), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

def ckpt_clear(tag):
    f = ckpt_path(tag)
    if f.exists(): f.unlink()

def sido_of(o):
    return (o.get("prk_plce_adres_sido") or "") + (o.get("prk_plce_adres") or "")[:6]

def is_gyeonggi(o):
    return "경기" in sido_of(o)

def is_anyang(o):
    s = (o.get("prk_plce_adres_sigungu") or "") + (o.get("prk_plce_adres") or "")
    return "안양" in s

# ── 1. probe ──────────────────────────────────────────
def probe():
    print("=== numOfRows 상한 실측 ===")
    best = 10
    for rows in (10, 100, 500, 1000):
        try:
            j = call("PrkSttusInfo", 1, rows)
            got = len(items(j, "PrkSttusInfo"))
            print(f"  요청 {rows:>4} → 실제 {got:>4}건 / totalCount={j.get('totalCount')}")
            if got >= min(rows, 1): best = max(best, got)
        except Exception as e:
            print(f"  요청 {rows:>4} → 실패: {e}")
    print(f"\n권장 numOfRows = {best}")

    print("\n=== 엔드포인트별 규모 ===")
    for op in ("PrkSttusInfo", "PrkOprInfo", "PrkRealtimeInfo"):
        try:
            j = call(op, 1, 10)
            lst = items(j, op)
            total = int(j.get("totalCount") or 0)
            pages = math.ceil(total / best) if best else 0
            print(f"  {op:<16} totalCount={total:>7,}  (numOfRows={best} 기준 {pages}페이지)")
            if lst: print(f"    샘플 키: {list(lst[0].keys())}")
        except Exception as e:
            print(f"  {op:<16} 실패: {e}")
    print(f"\n소모한 호출: {CALLS}회")

# ── 2. 시설정보 전수 → 경기도 필터(안양 표시) ──────────
def sttus(rows=1000, yes=False):
    ck = ckpt_load("sttus", rows)
    if ck:
        gg, gg_pages = ck["gg"], ck["gg_pages"]
        anyang_pages, failed = ck["anyang_pages"], ck["failed"]
        pages, start_page = ck["total_pages"], ck["last_page"] + 1
    else:
        j = call("PrkSttusInfo", 1, rows)
        total = int(j.get("totalCount") or 0)
        pages = math.ceil(total / rows)
        print(f"총 {total:,}건 / {pages}페이지 / 예상 {pages*3//60}분", flush=True)
        if not yes:
            input("계속하려면 Enter: ")
        gg, gg_pages, anyang_pages, failed = [], [], [], []
        start_page = 1

    def save_ckpt(p):
        ckpt_save("sttus", {"last_page": p, "rows": rows, "total_pages": pages,
                            "gg": gg, "gg_pages": gg_pages,
                            "anyang_pages": anyang_pages, "failed": failed})

    for p in range(start_page, pages + 1):
        try:
            lst = items(call("PrkSttusInfo", p, rows), "PrkSttusInfo")
        except Exception as e:
            failed.append(p)
            print(f"  p{p} 실패({len(failed)}건째), 건너뜀: {str(e)[:120]}", flush=True)
            if p % 50 == 0: save_ckpt(p)
            continue
        if not lst: break
        hits = [o for o in lst if is_gyeonggi(o)]
        for o in hits:
            o["_page"] = p
            o["_anyang"] = is_anyang(o)
        if hits:
            gg_pages.append(p)
            if any(o["_anyang"] for o in hits): anyang_pages.append(p)
        gg += hits
        if p % 50 == 0 or hits:
            n_any = sum(1 for o in gg if o["_anyang"])
            print(f"  p{p}/{pages}  경기누적 {len(gg)}  안양 {n_any}", flush=True)
        if p % 50 == 0: save_ckpt(p)
        time.sleep(0.15)

    anyang = [o for o in gg if o["_anyang"]]
    json.dump(gg,     open(RAW/"kotsa_gg.json","w"),     ensure_ascii=False, indent=1)
    json.dump(anyang, open(RAW/"kotsa_anyang.json","w"), ensure_ascii=False, indent=1)
    json.dump({"gg_pages": gg_pages, "anyang_pages": anyang_pages,
               "rows": rows, "total_pages": pages, "sttus_failed": failed},
              open(RAW/"kotsa_pages.json","w"), indent=1)
    ckpt_clear("sttus")

    def rng(ps): return f"{min(ps)}~{max(ps)} ({len(ps)}개 페이지)" if ps else "-"
    print(f"\n★ 경기도 {len(gg)}곳 / 페이지 {rng(gg_pages)}")
    print(f"★ 안양시 {len(anyang)}곳 / 페이지 {rng(anyang_pages)}")
    from collections import Counter
    print("\n시군구별 상위 20:")
    for name, n in Counter(o.get('prk_plce_adres_sigungu','') or '(빈값)'
                           for o in gg).most_common(20):
        print(f"  {name:<16} {n:>5}곳")
    if failed:
        print(f"\n⚠️ 실패 {len(failed)}페이지: {failed}")
        print("   → 같은 명령을 다시 돌리면 실패분만 재시도하지는 않는다. 위 목록을 보고 판단할 것.")
    else:
        print("\n실패 페이지 없음")
    print(f"\n호출 {CALLS}회")

# ── 3. ★ 실시간이 진짜 실시간인가 ──────────────────────
def realtime_test(wait=600, rows=1000):
    f = RAW/"kotsa_anyang.json"
    ids = {o["prk_center_id"] for o in json.load(open(f))} if f.exists() else None
    if ids: print(f"안양 {len(ids)}곳 기준으로 비교")
    else:   print("안양 목록 없음 → 전국 전체로 비교 (sttus 먼저 돌리는 걸 권장)")

    def snap():
        m, p = {}, 1
        while True:
            lst = items(call("PrkRealtimeInfo", p, rows), "PrkRealtimeInfo")
            if not lst: break
            for o in lst:
                cid = o.get("prk_center_id")
                if ids is None or cid in ids:
                    m[cid] = (o.get("pkfc_ParkingLots_total"),
                              o.get("pkfc_Available_ParkingLots_total"))
            p += 1; time.sleep(0.2)
        return m

    a = snap(); print(f"1차 스냅샷: {len(a)}곳  {time.strftime('%H:%M:%S')}")
    if not a: return print("대상 0곳. 안양이 실시간 연계에 없다는 뜻일 수 있음.")
    print(f"{wait//60}분 대기...")
    time.sleep(wait)
    b = snap(); print(f"2차 스냅샷: {len(b)}곳  {time.strftime('%H:%M:%S')}")

    diff = [k for k in a if k in b and a[k] != b[k]]
    print(f"\n★ {wait//60}분간 변동: {len(diff)}곳 / {len(a)}곳")
    for k in diff[:15]: print(f"  {k}: {a[k]} → {b[k]}")
    print("\n→ 변동 있음: 진짜 실시간. 라벨로 쓸 수 있다."
          if diff else
          "\n→ 변동 없음: 일 1회 스냅샷일 가능성. 피처로만 쓴다.")
    print(f"소모한 호출: {CALLS}회")

# ── 3-b. 빠른 실시간 판별 (2회 호출로 끝) ─────────────
def realtime_quick(wait=600, rows=1000):
    def snap():
        lst = items(call("PrkRealtimeInfo", 1, rows), "PrkRealtimeInfo")
        return {o["prk_center_id"]: (o.get("pkfc_ParkingLots_total"),
                                     o.get("pkfc_Available_ParkingLots_total"))
                for o in lst}
    a = snap(); print(f"1차 {len(a)}곳 {time.strftime('%H:%M:%S')}")
    # 더미값(잔여=총면수) 비율도 같이 본다
    dummy = sum(1 for v in a.values() if v[0] == v[1])
    print(f"  잔여==총면수 인 곳: {dummy}/{len(a)} ({dummy/max(len(a),1):.0%})")
    print(f"{wait//60}분 대기...")
    time.sleep(wait)
    b = snap(); print(f"2차 {len(b)}곳 {time.strftime('%H:%M:%S')}")
    diff = [k for k in a if k in b and a[k] != b[k]]
    print(f"\n★ {wait//60}분간 변동: {len(diff)}곳 / {len(a)}곳")
    for k in diff[:15]: print(f"  {k}: {a[k]} → {b[k]}")
    print("\n→ 진짜 실시간" if diff else "\n→ 변동 없음. 일 1회 스냅샷일 가능성")

# ── 3-c. 실시간 엔드포인트에서 경기·안양 위치 찾기 ─────
def rt_locate(rows=1000):
    gg = json.load(open(RAW/"kotsa_gg.json"))
    ids_gg  = {o["prk_center_id"] for o in gg}
    ids_any = {o["prk_center_id"] for o in gg if o.get("_anyang")}
    print(f"기준: 경기 {len(ids_gg)}곳 (그중 안양 {len(ids_any)}곳)")

    ck = ckpt_load("rtlocate", rows)
    if ck:
        gg_pages, any_pages = ck["gg_pages"], ck["any_pages"]
        found_gg, found_any = ck["found_gg"], ck["found_any"]
        failed, pages, start_page = ck["failed"], ck["total_pages"], ck["last_page"] + 1
    else:
        j = call("PrkRealtimeInfo", 1, rows)
        pages = math.ceil(int(j.get("totalCount") or 0) / rows)
        gg_pages, any_pages, failed = [], [], []
        found_gg = found_any = 0
        start_page = 1

    def save_ckpt(p):
        ckpt_save("rtlocate", {"last_page": p, "rows": rows, "total_pages": pages,
                               "gg_pages": gg_pages, "any_pages": any_pages,
                               "found_gg": found_gg, "found_any": found_any,
                               "failed": failed})

    for p in range(start_page, pages + 1):
        try:
            lst = items(call("PrkRealtimeInfo", p, rows), "PrkRealtimeInfo")
        except Exception as e:
            failed.append(p)
            print(f"  p{p} 실패({len(failed)}건째), 건너뜀: {str(e)[:120]}", flush=True)
            if p % 50 == 0: save_ckpt(p)
            continue
        if not lst: break
        cids = [o.get("prk_center_id") for o in lst]
        n_gg  = sum(1 for c in cids if c in ids_gg)
        n_any = sum(1 for c in cids if c in ids_any)
        if n_gg:
            gg_pages.append(p); found_gg += n_gg
            if n_any: any_pages.append(p); found_any += n_any
            print(f"  p{p}: 경기 {n_gg}곳 (안양 {n_any}곳)", flush=True)
        if p % 50 == 0:
            print(f"  p{p}/{pages} 진행중", flush=True); save_ckpt(p)
        time.sleep(0.15)

    meta = json.load(open(RAW/"kotsa_pages.json"))
    meta.update({"rt_gg_pages": gg_pages, "rt_anyang_pages": any_pages,
                 "rt_total_pages": pages,
                 "rt_gg_found": found_gg, "rt_anyang_found": found_any,
                 "rt_failed": failed})
    json.dump(meta, open(RAW/"kotsa_pages.json","w"), indent=1)
    ckpt_clear("rtlocate")

    print(f"\n★ 실시간 전체 {pages}페이지 중")
    print(f"  경기 {found_gg}곳 / {len(gg_pages)}페이지  {gg_pages}")
    print(f"  안양 {found_any}곳 / {len(any_pages)}페이지  {any_pages}")
    print(f"\n→ 하루 가능 스냅샷 (일일 한도 {DAILY_LIMIT:,}회 기준)")
    for label, ps in (("경기 전체", gg_pages), ("안양만", any_pages)):
        if ps:
            n = DAILY_LIMIT // len(ps)
            itv = 1440 / n if n else 0
            flag = "  ← 5분 폴링 불가" if itv > 5 else ""
            print(f"  {label:<8} {len(ps)}페이지 호출 → {n:,}회/일 = 최소 {itv:.1f}분 간격{flag}")
        else:
            print(f"  {label:<8} 해당 없음 (실시간 연계 안 됨)")
    if failed:
        print(f"\n⚠️ 실패 {len(failed)}페이지: {failed}")
        print("   → 이 페이지에 경기·안양이 있었을 수 있다. 위 결과는 하한이다.")
    else:
        print("\n실패 페이지 없음")
    print(f"\n호출 {CALLS}회")

# ── 4. 안양분 운영정보 ────────────────────────────────
def opr(rows=1000):
    ids = {o["prk_center_id"] for o in json.load(open(RAW/"kotsa_anyang.json"))}
    out, p = [], 1
    while True:
        lst = items(call("PrkOprInfo", p, rows), "PrkOprInfo")
        if not lst: break
        out += [o for o in lst if o.get("prk_center_id") in ids]
        p += 1; time.sleep(0.2)
    json.dump(out, open(RAW/"kotsa_anyang_opr.json","w"), ensure_ascii=False, indent=1)
    print(f"안양 운영정보 {len(out)}건 저장")
    if out: print(json.dumps(out[0], ensure_ascii=False, indent=1)[:900])

# ── 5. 포털 89곳과 좌표 매칭 ──────────────────────────
def match(max_m=120):
    kt = json.load(open(RAW/"kotsa_anyang.json"))
    con = sqlite3.connect(RAW/"parking.db")
    por = con.execute("SELECT parking_id,name,cell_cnt,lat,lng FROM lots").fetchall()
    def dist(a, b, c, d):
        return math.hypot((a-c)*111000, (b-d)*88000)
    hit = 0
    print(f"{'공단':<28} {'포털':<20} {'거리m':>7} {'면수':>9}")
    for o in kt:
        try: la, lo = float(o["prk_plce_entrc_la"]), float(o["prk_plce_entrc_lo"])
        except (TypeError, ValueError, KeyError): continue
        cand = [(dist(la, lo, p[3], p[4]), p) for p in por if p[3] and p[4]]
        if not cand: continue
        d, p = min(cand, key=lambda x: x[0])
        if d <= max_m:
            hit += 1
            print(f"{o.get('prk_plce_nm','')[:26]:<28} {p[1][:18]:<20} {d:>7.0f} "
                  f"{o.get('prk_cmprt_co','?')}/{p[2]}")
    print(f"\n매칭 {hit} / 공단 안양 {len(kt)}곳 / 포털 89곳")

CMDS = {"probe": probe, "sttus": sttus, "realtime-test": realtime_test,
        "realtime-quick": realtime_quick, "rt-locate": rt_locate,
        "opr": opr, "match": match}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="probe", choices=list(CMDS))
    ap.add_argument("-y", "--yes", action="store_true",
                    help="확인 프롬프트를 건너뛴다 (nohup·백그라운드용)")
    args = ap.parse_args()
    if args.cmd == "sttus":
        sttus(yes=args.yes)
    else:
        CMDS[args.cmd]()
