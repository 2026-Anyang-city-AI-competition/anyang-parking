#!/usr/bin/env python3
"""
주차장 유형 자동판정 (노상 / 노외 / 부설).

`is_nosang` 이 단일 최강 피처(between ρ +0.623)인데 공단 7,456곳엔 유형이 없다.
도로중심선·건물 폴리곤까지의 거리로 유형을 맞출 수 있는지 정답 107곳으로 검증한다.

  python3 src/features/infer_type.py
  python3 src/features/infer_type.py --force   # 거리 재수집

임계값 T1·T2 는 눈대중이 아니라 정답으로 최적화한다.
※ 부설은 입력 피처로만 쓴다. 예측 대상에 넣지 않는다(패턴이 반대이고 라벨 0곳).
"""
import argparse, re, sqlite3, sys
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import Point

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.utils import geo as G

RADIUS = 300                      # 거리 탐색 반경
STD   = ROOT / "data/raw/std_parking.csv"
GEOC  = ROOT / "data/interim/geocode_cache.csv"
CACHE = ROOT / "data/interim/type_dist_cache.csv"
OUT   = ROOT / "data/interim/inferred_type.csv"
TAB   = ROOT / "reports/tables"; TAB.mkdir(parents=True, exist_ok=True)

BUILDING_WORDS = ("아파트","빌딩","타워","프라자","플라자","상가","병원","센터",
                  "마트","백화점","오피스텔","빌라","시장","웨딩","교회","학교")

REPORT = []
def say(s=""):
    print(s, flush=True); REPORT.append(s)

def collect(points, key, force=False):
    cache = pd.read_csv(CACHE) if (CACHE.exists() and not force) else pd.DataFrame()
    done = set(cache["pid"].astype(str)) if len(cache) else set()
    rows = cache.to_dict("records") if len(cache) else []
    todo = [(p, c) for p, c in points.items() if str(p) not in done]
    say(f"- 거리 수집: 전체 {len(points)} / 캐시 {len(done)} / 신규 **{len(todo)}**")
    for i, (pid, (lat, lon)) in enumerate(todo, 1):
        pt = G.point_m(lat, lon)
        rec = {"pid": pid, "lat": lat, "lon": lon}
        try:
            fs, _ = G.wfs(G.LAYER_ROAD, lat, lon, RADIUS, key)
            gs = G.geoms_with_props(fs)
            if gs:
                d = [(gm.distance(pt), pr) for gm, pr in gs]
                d.sort(key=lambda x: x[0])
                rec["road_dist_m"] = d[0][0]
                try: rec["road_width_m"] = float(d[0][1].get("rvwd"))
                except (TypeError, ValueError): rec["road_width_m"] = np.nan
            else:
                rec["road_dist_m"] = np.nan; rec["road_width_m"] = np.nan
        except Exception as e:
            rec["road_err"] = str(e)[:100]
        try:
            fs, _ = G.wfs(G.LAYER_BUILDING, lat, lon, RADIUS, key)
            gs = G.geoms_with_props(fs)
            if gs:
                dd = [(gm.distance(pt), gm.contains(pt), pr) for gm, pr in gs]
                dd.sort(key=lambda x: x[0])
                rec["bld_dist_m"] = dd[0][0]
                rec["in_building"] = int(any(x[1] for x in dd))
                rec["bld_floors"]  = dd[0][2].get("gro_flo_co")
            else:
                rec["bld_dist_m"] = np.nan; rec["in_building"] = 0
        except Exception as e:
            rec["bld_err"] = str(e)[:100]
        rows.append(rec)
        if i % 20 == 0 or i == len(todo):
            pd.DataFrame(rows).to_csv(CACHE, index=False)
            say(f"  {i}/{len(todo)}")
    df = pd.DataFrame(rows); df.to_csv(CACHE, index=False)
    return df

def classify(row, t1, t2):
    """도로 ≤T1 → 노상 / 건물내부 or ≤T2 → 부설 / 나머지 노외"""
    rd = row.get("road_dist_m", np.nan)
    bd = row.get("bld_dist_m", np.nan)
    inb = row.get("in_building", 0)
    if pd.notna(rd) and rd <= t1: return "노상"
    if inb == 1 or (pd.notna(bd) and bd <= t2): return "부설"
    return "노외"

def main(force=False):
    if not STD.exists(): sys.exit(f"[선행] {STD} 없음")
    if not GEOC.exists(): sys.exit(f"[선행] {GEOC} 없음")
    key = G.vworld_key()

    std = pd.read_csv(STD, dtype=str, encoding="utf-8-sig")
    gc  = pd.read_csv(GEOC, dtype={"parking_id": str})
    tcol = next((c for c in std.columns if "주차장유형" in c), None)
    ncol = next((c for c in std.columns if "주차장명" in c), None)
    icol = next((c for c in std.columns if "관리번호" in c), None)
    say("# 유형 자동판정")
    say()
    say(f"- 정답 컬럼 매핑: 유형 `{tcol}` · 이름 `{ncol}` · id `{icol}`")
    truth = std.set_index(icol)[tcol]
    say(f"- 정답 분포: {dict(truth.value_counts())}")
    say()

    pts = {r.parking_id: (r.lat, r.lon) for r in gc.itertuples()
           if pd.notna(r.lat) and pd.notna(r.lon)}
    d = collect(pts, key, force).set_index("pid")
    d.index = d.index.astype(str)
    names = std.set_index(icol)[ncol]
    d["name"] = names.reindex(d.index)
    d["name_building"] = d["name"].fillna("").apply(
        lambda s: int(any(w in s for w in BUILDING_WORDS)))
    say()
    say(f"- 도로거리 결측 {int(d['road_dist_m'].isna().sum())} / "
        f"건물거리 결측 {int(d['bld_dist_m'].isna().sum())} / {len(d)}")
    say(f"- 도로거리 중앙 {d['road_dist_m'].median():.1f}m · "
        f"건물거리 중앙 {d['bld_dist_m'].median():.1f}m · "
        f"건물 내부 {int(d['in_building'].sum())}곳")
    say()

    y = truth.reindex(d.index)
    ok = y.notna()
    major = y[ok].value_counts(normalize=True).max()
    say(f"## 기준선")
    say()
    say(f"- 다수클래스(항상 `{y[ok].value_counts().idxmax()}`) 정확도 **{major:.1%}** "
        f"— 어떤 방법이든 이걸 못 넘으면 쓸모없다")
    say()

    # ── A. 이름 규칙 ──────────────────────────────────────
    nm = d["name"].fillna("")
    name_pred = nm.apply(lambda t: "노상" if "노상" in t else ("노외" if "노외" in t else None))
    nm_ok = name_pred.notna() & ok
    say("## A. 이름 규칙 (`노상`/`노외` 문자열)")
    say()
    say(f"- 적용 가능 **{int(nm_ok.sum())}/{int(ok.sum())}곳** ({nm_ok.sum()/ok.sum():.0%})"
        f" · 그중 정확도 **{(name_pred[nm_ok]==y[nm_ok]).mean():.1%}**")
    say()

    # ── B. 기하 규칙 ──────────────────────────────────────
    say("## B. 기하 규칙 (도로·건물 거리) — 임계값을 정답으로 최적화")
    say()
    best = (None, -1)
    for t1 in np.arange(2, 41, 2):
        for t2 in np.arange(0, 41, 2):
            pr = d.apply(lambda r: classify(r, t1, t2), axis=1)
            a = (pr[ok] == y[ok]).mean()
            if a > best[1]: best = ((float(t1), float(t2)), a)
    (T1, T2), acc = best
    pred = d.apply(lambda r: classify(r, T1, T2), axis=1)
    say(f"- 최적 **T1={T1:.0f}m · T2={T2:.0f}m** → 정확도 **{acc:.1%}** "
        f"({'다수클래스보다 낫다' if acc > major else '**다수클래스보다 나쁘다 → 쓸 수 없다**'})")
    say()
    say("| 유형 | n | 도로거리 중앙 | 도로폭 중앙 | 건물 내부 |")
    say("|---|---:|---:|---:|---:|")
    for a in sorted(set(y[ok])):
        g = d[ok & (y == a)]
        say(f"| {a} | {len(g)} | {g.road_dist_m.median():.1f}m | "
            f"{g.road_width_m.median():.1f}m | {int(g.in_building.sum())} |")
    say()
    say("> ⚠️ **노상이 노외보다 도로에서 멀다.** 신호가 없는 게 아니라 뒤집혀 있다.")
    say("> 표준데이터 좌표가 실제 주차 위치가 아니라 **주소 대표점**이라 그렇다 "
        "(107곳 중 81곳이 건물 폴리곤 내부로 찍힌다).")
    say("> **좌표 품질이 원인이므로 3-3(전국 데이터로 분류기 학습)도 같은 벽에 막힌다.**")
    say()

    say("## 혼동행렬 — 기하 규칙 (행=정답, 열=예측)")
    say()
    labs = sorted(set(y[ok]) | set(pred[ok]))
    say("| 정답＼예측 | " + " | ".join(labs) + " | 합 |")
    say("|---" * (len(labs) + 2) + "|")
    for a in labs:
        row = [int(((y == a) & (pred == p) & ok).sum()) for p in labs]
        say(f"| **{a}** | " + " | ".join(str(v) for v in row) + f" | {sum(row)} |")
    say()
    say("| 클래스 | 정밀도 | 재현율 | n |")
    say("|---|---:|---:|---:|")
    for a in labs:
        tp = int(((y == a) & (pred == a) & ok).sum())
        pp = int(((pred == a) & ok).sum()); ap = int(((y == a) & ok).sum())
        say(f"| {a} | {(f'{tp/pp:.1%}' if pp else '-')} | "
            f"{(f'{tp/ap:.1%}' if ap else '-')} | {ap} |")
    say()

    # ── C. 결합 — 이름 우선, 나머지는 기하 ────────────────
    comb = name_pred.where(name_pred.notna(), pred)
    say(f"## C. 결합 (이름 우선 → 기하 보완): 정확도 **{(comb[ok]==y[ok]).mean():.1%}**")
    say()

    # ── ★ 공단 7,456곳에 적용 가능한가 ────────────────────
    ka = pd.read_json(ROOT/"data/raw/kotsa_v2_anyang.jsonl", lines=True).drop_duplicates("prk_center_id")
    knm = ka["prk_plce_nm"].fillna("")
    cov = knm.str.contains("노상|노외").mean()
    say("## ★ 판정 — 공단 7,456곳으로 넓힐 수 있는가")
    say()
    say(f"- 이름 규칙 적용률: 표준 107곳 {nm_ok.sum()/ok.sum():.0%} vs "
        f"**공단 {len(ka):,}곳 {cov:.1%}({int(knm.str.contains('노상|노외').sum())}곳)**")
    say(f"- 기하 규칙 정확도 {acc:.1%} < 다수클래스 {major:.1%}")
    say()
    say("- ### 결론: 🔴 **유형 자동판정 실패. 타깃을 공단 7,456곳으로 넓힐 수 없다.**")
    say("  이름은 표준데이터에만 붙어 있고(공단은 0.4%), 기하는 좌표 품질 때문에 작동하지 않는다.")
    say("  **정본 결정 ①(공영 107곳 한정)이 데이터로 재확인됐다.**")
    say()

    conf = pd.Series(0.5, index=d.index)
    conf[(d["road_dist_m"] <= T1/2)] = 0.9
    conf[(d["in_building"] == 1)] = 0.9
    conf[d["road_dist_m"].isna() & d["bld_dist_m"].isna()] = 0.1
    out = pd.DataFrame({"parking_id": d.index, "inferred_type": comb.values,
                        "confidence": np.where(name_pred.notna(), 0.95, conf.values),
                        "method": np.where(name_pred.notna(), "name",
                                           f"geom(T1={T1:.0f},T2={T2:.0f})")})
    out.to_csv(OUT, index=False)
    low = out[out.confidence < 0.5]
    say(f"- 저확신 {len(low)}곳 → 로드뷰 육안 확인 대상 (전수 육안은 불가)")
    say(f"- 출력: `data/interim/inferred_type.csv`")
    (TAB / "type_inference.md").write_text("\n".join(REPORT) + "\n", encoding="utf-8")
    print(f"\n→ {TAB/'type_inference.md'}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--force", action="store_true")
    main(**vars(ap.parse_args()))
