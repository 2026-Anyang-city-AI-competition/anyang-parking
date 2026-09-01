#!/usr/bin/env python3
"""
한국교통안전공단 주차정보 제공 API 조사·수집기

  fetch_kotsa.py probe                                  # 규모·응답형태
  fetch_kotsa.py sttus --yes --rows 50000 --out data/raw/kotsa_v2
  fetch_kotsa.py retry-failed --rows 50000 --out data/raw/kotsa_v2   # 실패분만 회수
  fetch_kotsa.py rt-locate    --rows 50000 --out data/raw/kotsa_v2
  fetch_kotsa.py realtime-quick                         # 실시간 갱신 여부(2회 호출)
  fetch_kotsa.py realtime-test                          # 위와 같되 전수 페이징
  fetch_kotsa.py opr / match

실측 전제 (재탐색 금지):
  - 지역·ID 필터 파라미터 없음. pageNo 방식이 유일.
  - numOfRows 상한은 문서의 1000이 아니라 최소 50,000.
  - 268바이트 XML 응답은 상한이 아니라 게이트웨이 타임아웃 → 재시도로 흡수.
  - items 키는 오퍼레이션 이름 그대로. body["items"] 아님.
  - prk_center_id 는 유니크하지 않다. PK 로 쓰지 말 것.

메모리: 페이지 결과를 누적하지 않고 JSONL 로 즉시 흘린다(상주 = 1페이지분).
"""
import argparse, json, math, os, sqlite3, sys, time
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[2]
RAW  = ROOT / "data/raw"; RAW.mkdir(parents=True, exist_ok=True)
BASE = "https://apis.data.go.kr/B553881/Parking"

DAILY_LIMIT  = 10000       # 승인 메일의 실제 일일 한도로 맞출 것
TIMEOUT      = 180         # 게이트웨이가 60초쯤에 XML 을 뱉는 일이 있어 넉넉히
RETRY        = 5
CKPT_EVERY   = 2           # 한 페이지가 5만 행이라 손실 비용이 크다
DEFAULT_ROWS = 50000
DEFAULT_OUT  = "data/raw/kotsa"

KEY = os.environ.get("KOTSA_KEY", "")
if not KEY:
    for line in (ROOT/".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("KOTSA_KEY="):
            KEY = line.split("=", 1)[1].strip()
if not KEY:
    sys.exit("KOTSA_KEY 없음. .env 에 디코딩 인증키를 넣어라.")

CALLS = 0
def call(op, page=1, rows=100, retry=RETRY):
    """디코딩 키를 params로 넘긴다(requests가 한 번만 인코딩)."""
    global CALLS
    for a in range(retry):
        try:
            r = requests.get(f"{BASE}/{op}",
                params={"serviceKey": KEY, "numOfRows": rows,
                        "pageNo": page, "format": 2},
                timeout=TIMEOUT)
            CALLS += 1
            if r.text.lstrip().startswith("<"):   # 게이트웨이 타임아웃 등은 XML
                raise RuntimeError(f"XML응답 {len(r.content)}B: {r.text[:160]}")
            j = r.json()
            if str(j.get("resultCode")) not in ("0", "00"):
                raise RuntimeError(f"{j.get('resultCode')} {j.get('resultMsg')}")
            return j
        except Exception:
            if a == retry - 1: raise
            time.sleep(3 * (a + 1))

def items(j, op):
    v = j.get(op)
    if v is None: return []
    return v if isinstance(v, list) else [v]

# ── 지역 판별 ─────────────────────────────────────────
def sido_of(o):
    return (o.get("prk_plce_adres_sido") or "") + (o.get("prk_plce_adres") or "")[:6]

def is_gyeonggi(o):
    return "경기" in sido_of(o)

def is_anyang(o):
    # 주소 문자열 매칭은 "안양판교로" 같은 도로명을 오탐한다(의왕·성남 각 1건).
    return (o.get("prk_plce_adres_sigungu") or "").startswith("안양")

# ── 출력 경로 · 체크포인트 ────────────────────────────
def outp(out, suffix):
    p = Path(out)
    if not p.is_absolute(): p = ROOT / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p.parent / f"{p.name}_{suffix}"

def ckpt_path(out, tag):
    p = Path(out)
    if not p.is_absolute(): p = ROOT / p
    return p.parent / f".ckpt_{p.name}_{tag}.json"

def ckpt_load(out, tag, rows):
    f = ckpt_path(out, tag)
    if not f.exists(): return None
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception as e:
        print(f"체크포인트 손상, 무시: {e}"); return None
    if d.get("rows") != rows:
        print(f"체크포인트 rows={d.get('rows')} != 현재 {rows} → 무시"); return None
    print(f"체크포인트 발견: p{d.get('last_page')} 까지 완료 → 이어서 진행", flush=True)
    return d

def ckpt_save(out, tag, d):
    json.dump(d, open(ckpt_path(out, tag), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

def ckpt_clear(out, tag):
    f = ckpt_path(out, tag)
    if f.exists(): f.unlink()

def guard_overwrite(paths, resuming):
    """재개가 아닌데 산출물이 이미 있으면 멈춘다(다른 배치의 결과 보호)."""
    if resuming: return
    exist = [p for p in paths if p.exists()]
    if exist:
        sys.exit("이미 존재하는 산출물이 있다. --out 으로 다른 prefix 를 주거나 지워라:\n  "
                 + "\n  ".join(str(p) for p in exist))

# ── idmap: 한 줄에 한 항목인 JSON 오브젝트 ─────────────
def iter_idmap(path):
    """{PREFIX}_idmap.json 을 통째로 로드하지 않고 한 줄씩 읽는다."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line in ("{", "}"): continue
            if line.startswith(","): line = line[1:]
            try:
                yield json.loads("{" + line + "}")
            except json.JSONDecodeError:
                continue

# ── 1. probe ──────────────────────────────────────────
def probe(rows=DEFAULT_ROWS, out=DEFAULT_OUT, yes=False):
    print("=== 엔드포인트별 규모 ===")
    for op in ("PrkSttusInfo", "PrkOprInfo", "PrkRealtimeInfo"):
        try:
            j = call(op, 1, 10)
            lst = items(j, op)
            total = int(j.get("totalCount") or 0)
            print(f"  {op:<16} totalCount={total:>9,}  "
                  f"(rows={rows} 기준 {math.ceil(total/rows)}페이지)")
            if lst: print(f"    샘플 키: {list(lst[0].keys())}")
        except Exception as e:
            print(f"  {op:<16} 실패: {e}")
    print(f"\n소모한 호출: {CALLS}회")

# ── 2. 시설정보 전수 → 경기/안양 스트리밍 저장 ─────────
def sttus(rows=DEFAULT_ROWS, yes=False, out=DEFAULT_OUT):
    gg_f   = outp(out, "gg.jsonl")
    any_f  = outp(out, "anyang.jsonl")
    idm_f  = outp(out, "idmap.json")
    meta_f = outp(out, "pages.json")

    ck = ckpt_load(out, "sttus", rows)
    guard_overwrite([gg_f, any_f, idm_f, meta_f], resuming=bool(ck))

    if ck:
        pages      = ck["total_pages"]
        start_page = ck["last_page"] + 1
        gg_pages, anyang_pages, failed = ck["gg_pages"], ck["anyang_pages"], ck["failed"]
        n_gg, n_any, n_all, dup = ck["n_gg"], ck["n_any"], ck["n_all"], ck["dup"]
        # 체크포인트 이후에 쓰인 부분은 잘라내 중복 append 를 막는다
        for f, size in ((gg_f, ck["gg_bytes"]), (any_f, ck["any_bytes"]),
                        (idm_f, ck["idm_bytes"])):
            if f.exists():
                with open(f, "r+b") as fh: fh.truncate(size)
        seen = {e_id for d in iter_idmap(idm_f) for e_id in d}
        print(f"  재개 준비: idmap {len(seen):,}개 ID 복원", flush=True)
        first = ck["first"]
    else:
        j = call("PrkSttusInfo", 1, rows)
        total = int(j.get("totalCount") or 0)
        pages = math.ceil(total / rows)
        print(f"총 {total:,}건 / rows={rows:,} / {pages}페이지", flush=True)
        if not yes:
            input("계속하려면 Enter: ")
        start_page = 1
        gg_pages, anyang_pages, failed = [], [], []
        n_gg = n_any = n_all = dup = 0
        seen, first = set(), True
        idm_f.write_text("{\n", encoding="utf-8")

    gg_fh  = open(gg_f,  "a", encoding="utf-8")
    any_fh = open(any_f, "a", encoding="utf-8")
    idm_fh = open(idm_f, "a", encoding="utf-8")

    def save_ckpt(p):
        gg_fh.flush(); any_fh.flush(); idm_fh.flush()
        ckpt_save(out, "sttus", {
            "last_page": p, "rows": rows, "total_pages": pages,
            "gg_pages": gg_pages, "anyang_pages": anyang_pages, "failed": failed,
            "n_gg": n_gg, "n_any": n_any, "n_all": n_all, "dup": dup,
            "first": first,
            "gg_bytes": gg_f.stat().st_size, "any_bytes": any_f.stat().st_size,
            "idm_bytes": idm_f.stat().st_size})

    t0 = time.time()
    try:
        for p in range(start_page, pages + 1):
            try:
                lst = items(call("PrkSttusInfo", p, rows), "PrkSttusInfo")
            except Exception as e:
                failed.append(p)
                print(f"  p{p} 실패({len(failed)}건째) 건너뜀: {str(e)[:110]}", flush=True)
                if p % CKPT_EVERY == 0: save_ckpt(p)
                continue
            if not lst: break

            n_all += len(lst)
            hit_gg = hit_any = 0
            for o in lst:
                cid = o.get("prk_center_id")
                if cid:
                    if cid in seen:
                        dup += 1
                    else:
                        seen.add(cid)
                        entry = json.dumps(
                            {cid: {"sido": o.get("prk_plce_adres_sido") or "",
                                   "sigungu": o.get("prk_plce_adres_sigungu") or ""}},
                            ensure_ascii=False)[1:-1]        # 바깥 중괄호 제거
                        idm_fh.write(("" if first else ",") + entry + "\n")
                        first = False
                if is_gyeonggi(o):
                    o["_page"] = p
                    o["_anyang"] = is_anyang(o)
                    gg_fh.write(json.dumps(o, ensure_ascii=False) + "\n")
                    hit_gg += 1
                    if o["_anyang"]:
                        any_fh.write(json.dumps(o, ensure_ascii=False) + "\n")
                        hit_any += 1
            n_gg += hit_gg; n_any += hit_any
            if hit_gg: gg_pages.append(p)
            if hit_any: anyang_pages.append(p)

            gg_fh.flush(); any_fh.flush(); idm_fh.flush()
            lst = None                                   # 즉시 회수
            el = time.time() - t0
            print(f"  p{p}/{pages} 전체{n_all:,} 경기{n_gg:,} 안양{n_any:,} "
                  f"중복ID{dup:,} {el:.0f}s", flush=True)
            if p % CKPT_EVERY == 0: save_ckpt(p)
    finally:
        gg_fh.close(); any_fh.close(); idm_fh.close()

    with open(idm_f, "a", encoding="utf-8") as f:
        f.write("}\n")

    json.dump({"gg_pages": gg_pages, "anyang_pages": anyang_pages,
               "rows": rows, "total_pages": pages, "sttus_failed": failed,
               "n_all": n_all, "n_gg": n_gg, "n_anyang": n_any, "dup_ids": dup},
              open(meta_f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ckpt_clear(out, "sttus")

    print(f"\n★ 전체 {n_all:,}행 / 경기 {n_gg:,}행 / 안양 {n_any:,}행")
    print(f"  중복 prk_center_id: {dup:,}건 (첫 값 유지)")
    print(f"  {gg_f.name} / {any_f.name} / {idm_f.name} / {meta_f.name}")
    if failed:
        print(f"\n⚠️ 실패 {len(failed)}페이지: {failed}  → 결과는 하한이다.")
    else:
        print("\n실패 페이지 없음")
    print(f"호출 {CALLS}회 / {time.time()-t0:.0f}초")

# ── 2-b. 실패 페이지만 재수집해 append ─────────────────
def retry_failed(rows=DEFAULT_ROWS, out=DEFAULT_OUT, yes=False):
    gg_f, any_f = outp(out, "gg.jsonl"), outp(out, "anyang.jsonl")
    idm_f, meta_f = outp(out, "idmap.json"), outp(out, "pages.json")
    if not meta_f.exists(): sys.exit(f"{meta_f} 없음. sttus 를 먼저 돌릴 것.")
    meta = json.load(open(meta_f, encoding="utf-8"))
    failed = list(meta.get("sttus_failed") or [])
    if not failed:
        print("실패 페이지 없음. 할 일 없음."); return
    if meta.get("rows") != rows:
        sys.exit(f"meta 의 rows={meta.get('rows')} 와 현재 {rows} 가 다르다. 페이지 경계가 어긋난다.")
    print(f"재수집 대상: {failed}", flush=True)

    seen = {cid for d in iter_idmap(idm_f) for cid in d}
    print(f"기존 idmap {len(seen):,}개 ID 복원", flush=True)

    with open(idm_f, "r+b") as fh:            # 닫는 '}' 를 걷어낸다
        fh.seek(0, 2); size = fh.tell()
        fh.seek(max(0, size - 4)); tail = fh.read()
        cut = len(tail) - tail.rfind(b"}")
        fh.truncate(size - cut)

    gg_fh, any_fh = open(gg_f, "a", encoding="utf-8"), open(any_f, "a", encoding="utf-8")
    idm_fh = open(idm_f, "a", encoding="utf-8")
    still, n_gg, n_any, n_all, dup = [], 0, 0, 0, 0
    try:
        for p in failed:
            try:
                lst = items(call("PrkSttusInfo", p, rows), "PrkSttusInfo")
            except Exception as e:
                still.append(p); print(f"  p{p} 또 실패: {str(e)[:110]}", flush=True); continue
            n_all += len(lst); hg = ha = 0
            for o in lst:
                cid = o.get("prk_center_id")
                if cid:
                    if cid in seen: dup += 1
                    else:
                        seen.add(cid)
                        e = json.dumps({cid: {"sido": o.get("prk_plce_adres_sido") or "",
                                              "sigungu": o.get("prk_plce_adres_sigungu") or ""}},
                                       ensure_ascii=False)[1:-1]
                        idm_fh.write("," + e + "\n")
                if is_gyeonggi(o):
                    o["_page"] = p; o["_anyang"] = is_anyang(o)
                    gg_fh.write(json.dumps(o, ensure_ascii=False) + "\n"); hg += 1
                    if o["_anyang"]:
                        any_fh.write(json.dumps(o, ensure_ascii=False) + "\n"); ha += 1
            n_gg += hg; n_any += ha
            gg_fh.flush(); any_fh.flush(); idm_fh.flush()
            if hg: meta.setdefault("gg_pages", []).append(p)
            if ha: meta.setdefault("anyang_pages", []).append(p)
            print(f"  p{p} 회수: 전체{len(lst):,} 경기{hg:,} 안양{ha:,}", flush=True)
            lst = None
    finally:
        gg_fh.close(); any_fh.close(); idm_fh.close()
        with open(idm_f, "a", encoding="utf-8") as f: f.write("}\n")

    meta["gg_pages"] = sorted(set(meta.get("gg_pages", [])))
    meta["anyang_pages"] = sorted(set(meta.get("anyang_pages", [])))
    meta["sttus_failed"] = still
    for k, v in (("n_all", n_all), ("n_gg", n_gg), ("n_anyang", n_any), ("dup_ids", dup)):
        meta[k] = meta.get(k, 0) + v
    json.dump(meta, open(meta_f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n★ 회수 +{n_all:,}행 (경기 +{n_gg:,} / 안양 +{n_any:,} / 중복ID +{dup:,})")
    print(f"  누계: 전체 {meta['n_all']:,} 경기 {meta['n_gg']:,} 안양 {meta['n_anyang']:,}")
    print(f"  남은 실패: {still if still else '없음'}")
    print(f"호출 {CALLS}회")

# ── 3. 실시간에서 경기·안양 위치 찾기 ──────────────────
def rt_locate(rows=DEFAULT_ROWS, out=DEFAULT_OUT, yes=False):
    idm_f  = outp(out, "idmap.json")
    meta_f = outp(out, "pages.json")
    if not idm_f.exists():
        sys.exit(f"{idm_f} 없음. sttus 를 먼저 완료할 것.")

    ids_gg, ids_any = set(), set()
    for d in iter_idmap(idm_f):
        for cid, v in d.items():
            if "경기" in (v.get("sido") or ""):
                ids_gg.add(cid)
                if "안양" in (v.get("sigungu") or ""): ids_any.add(cid)
    print(f"기준: 경기 {len(ids_gg):,}개 ID (그중 안양 {len(ids_any):,}개)", flush=True)
    if not ids_gg:
        sys.exit("idmap 에 경기 ID 가 없다. sttus 결과를 확인할 것.")

    ck = ckpt_load(out, "rtlocate", rows)
    if ck:
        pages, start_page = ck["total_pages"], ck["last_page"] + 1
        gg_pages, any_pages, failed = ck["gg_pages"], ck["any_pages"], ck["failed"]
        seen_gg, seen_any = set(ck["seen_gg"]), set(ck["seen_any"])
        n_rows = ck["n_rows"]
    else:
        j = call("PrkRealtimeInfo", 1, rows)
        total = int(j.get("totalCount") or 0)
        pages = math.ceil(total / rows)
        print(f"실시간 총 {total:,}행 / {pages}페이지", flush=True)
        start_page = 1
        gg_pages, any_pages, failed = [], [], []
        seen_gg, seen_any, n_rows = set(), set(), 0

    def save_ckpt(p):
        ckpt_save(out, "rtlocate", {
            "last_page": p, "rows": rows, "total_pages": pages,
            "gg_pages": gg_pages, "any_pages": any_pages, "failed": failed,
            "seen_gg": sorted(seen_gg), "seen_any": sorted(seen_any),
            "n_rows": n_rows})

    t0 = time.time()
    for p in range(start_page, pages + 1):
        try:
            lst = items(call("PrkRealtimeInfo", p, rows), "PrkRealtimeInfo")
        except Exception as e:
            failed.append(p)
            print(f"  p{p} 실패({len(failed)}건째) 건너뜀: {str(e)[:110]}", flush=True)
            if p % CKPT_EVERY == 0: save_ckpt(p)
            continue
        if not lst: break
        n_rows += len(lst)
        pg_gg = pg_any = 0
        for o in lst:
            cid = o.get("prk_center_id")
            if cid in ids_gg:
                seen_gg.add(cid); pg_gg += 1
                if cid in ids_any:
                    seen_any.add(cid); pg_any += 1
        if pg_gg:
            gg_pages.append(p)
            if pg_any: any_pages.append(p)
            print(f"  p{p}: 경기 {pg_gg}행 (안양 {pg_any}행)", flush=True)
        lst = None
        if p % CKPT_EVERY == 0: save_ckpt(p)

    meta = json.load(open(meta_f, encoding="utf-8")) if meta_f.exists() else {}
    meta.update({"rt_gg_pages": gg_pages, "rt_anyang_pages": any_pages,
                 "rt_total_pages": pages, "rt_rows_scanned": n_rows,
                 "rt_gg_ids": len(seen_gg), "rt_anyang_ids": len(seen_any),
                 "rt_failed": failed})
    json.dump(meta, open(meta_f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ckpt_clear(out, "rtlocate")

    print(f"\n★ 실시간 {n_rows:,}행 / {pages}페이지 스캔")
    print(f"  경기 ID {len(seen_gg):,} / {len(ids_gg):,}개가 실시간에 존재"
          f"  → {len(gg_pages)}페이지 {gg_pages}")
    print(f"  안양 ID {len(seen_any):,} / {len(ids_any):,}개가 실시간에 존재"
          f"  → {len(any_pages)}페이지 {any_pages}")
    print(f"\n→ 하루 가능 스냅샷 (일일 한도 {DAILY_LIMIT:,}회 기준)")
    for label, ps in (("경기 전체", gg_pages), ("안양만", any_pages)):
        if ps:
            n = DAILY_LIMIT // len(ps)
            itv = 1440 / n if n else 0
            flag = "  ← 5분 폴링 불가" if itv > 5 else ""
            print(f"  {label:<8} {len(ps)}페이지 → {n:,}회/일 = 최소 {itv:.1f}분 간격{flag}")
        else:
            print(f"  {label:<8} 해당 없음 (실시간 연계 안 됨)")
    if failed:
        print(f"\n⚠️ 실패 {len(failed)}페이지: {failed}  → 결과는 하한이다.")
    print(f"호출 {CALLS}회 / {time.time()-t0:.0f}초")

# ── 안양 레코드 로더 (jsonl 우선, 구 json 폴백) ────────
def load_anyang(out):
    f = outp(out, "anyang.jsonl")
    if f.exists():
        return [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
    old = RAW/"kotsa_anyang.json"
    if old.exists():
        return json.load(open(old, encoding="utf-8"))
    sys.exit("안양 목록 없음. sttus 를 먼저 실행할 것.")

# ── 4. 빠른 실시간 판별 (2회 호출) ────────────────────
def realtime_quick(wait=600, rows=DEFAULT_ROWS, out=DEFAULT_OUT, yes=False):
    def snap():
        lst = items(call("PrkRealtimeInfo", 1, rows), "PrkRealtimeInfo")
        return {o["prk_center_id"]: (o.get("pkfc_ParkingLots_total"),
                                     o.get("pkfc_Available_ParkingLots_total"))
                for o in lst}
    a = snap(); print(f"1차 {len(a)}곳 {time.strftime('%H:%M:%S')}")
    dummy = sum(1 for v in a.values() if v[0] == v[1])
    print(f"  잔여==총면수 인 곳: {dummy}/{len(a)} ({dummy/max(len(a),1):.0%})")
    print(f"{wait//60}분 대기...")
    time.sleep(wait)
    b = snap(); print(f"2차 {len(b)}곳 {time.strftime('%H:%M:%S')}")
    diff = [k for k in a if k in b and a[k] != b[k]]
    print(f"\n★ {wait//60}분간 변동: {len(diff)}곳 / {len(a)}곳")
    for k in diff[:15]: print(f"  {k}: {a[k]} → {b[k]}")
    print("\n→ 진짜 실시간" if diff else "\n→ 변동 없음. 일 1회 스냅샷일 가능성")

# ── 5. 안양분 운영정보 ────────────────────────────────
def opr(rows=DEFAULT_ROWS, out=DEFAULT_OUT, yes=False):
    ids = {o["prk_center_id"] for o in load_anyang(out)}
    res, p = [], 1
    while True:
        try:
            lst = items(call("PrkOprInfo", p, rows), "PrkOprInfo")
        except Exception as e:
            print(f"  p{p} 실패, 중단: {str(e)[:110]}"); break
        if not lst: break
        res += [o for o in lst if o.get("prk_center_id") in ids]
        print(f"  p{p} 누적 {len(res)}", flush=True)
        p += 1
    f = outp(out, "anyang_opr.json")
    json.dump(res, open(f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"안양 운영정보 {len(res)}건 → {f.name}")
    if res: print(json.dumps(res[0], ensure_ascii=False, indent=1)[:900])

# ── 6. 포털 89곳과 좌표 매칭 ──────────────────────────
def match(rows=DEFAULT_ROWS, out=DEFAULT_OUT, yes=False, max_m=120):
    kt = load_anyang(out)
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

CMDS = {"probe": probe, "sttus": sttus, "retry-failed": retry_failed,
        "rt-locate": rt_locate,
        "realtime-quick": realtime_quick, "opr": opr, "match": match}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="probe", choices=list(CMDS))
    ap.add_argument("-y", "--yes", action="store_true",
                    help="확인 프롬프트를 건너뛴다 (nohup·백그라운드용)")
    ap.add_argument("--rows", type=int, default=DEFAULT_ROWS,
                    help=f"numOfRows (기본 {DEFAULT_ROWS})")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help=f"출력 파일 prefix (기본 {DEFAULT_OUT})")
    a = ap.parse_args()
    CMDS[a.cmd](rows=a.rows, out=a.out, yes=a.yes)
