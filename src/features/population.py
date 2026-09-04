#!/usr/bin/env python3
"""
모집단 확정 — 안양 주차장에서 **부설 vs 비부설** 이진 분류.

목표가 「부설만 제외하고 전부 예측 대상」으로 바뀌었다. 3분류가 아니라 이진이다.
공단 7,458곳(고유 prk_center_id)을 분류하고, 표준데이터 107곳(공영·부설 0)을
좌표+면수로 매칭해 앵커로 쓴다.

  python3 src/features/population.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
KOTSA = ROOT / "data/raw/kotsa_v2_anyang.jsonl"
STD   = ROOT / "data/raw/std_parking.csv"
OPR   = ROOT / "data/raw/kotsa_v2_anyang_opr.json"
OUT   = ROOT / "data/interim/population.csv"
TAB   = ROOT / "reports/tables"; TAB.mkdir(parents=True, exist_ok=True)

# 이름 규칙은 실제 이름 빈도(끝 2글자 상위 25)에서 뽑았다. 임의 목록이 아니다.
BUILD = ("주택","빌라","아파트","맨션","타운","캐슬","힐스","팰리스","하우스","오피스텔",
         "주상복합","근생","근린생활","상가","프라자","플라자","빌딩","타워","빌","파크",
         "교회","성당","사찰","학교","대학","유치원","어린이집","병원","의원","한의원",
         "공장","산업","정밀","물류","창고","호텔","모텔","연수원","기숙사","사옥","지사",
         "본부","㈜","(주)","주식회사","건축허가")
PUBLIC = ("노상","노외","공영","공원","체육","문화","도서관","시장","환승","주민센터",
          "행정복지","청사","시청","구청","보건소","광장","고가","둔치","하천","박물관",
          "경로당","차고지")

REPORT = []
def say(s=""):
    print(s, flush=True); REPORT.append(s)

def hav(a, b, c, e):
    p = np.pi/180
    return 6371000*2*np.arcsin(np.sqrt(
        np.sin((c-a)*p/2)**2 + np.cos(a*p)*np.cos(c*p)*np.sin((e-b)*p/2)**2))

def main():
    if not KOTSA.exists(): sys.exit(f"[선행] {KOTSA} 없음")
    d = pd.read_json(KOTSA, lines=True).drop_duplicates("prk_center_id").reset_index(drop=True)
    d["nm"]    = d["prk_plce_nm"].fillna("").str.strip()
    d["cells"] = pd.to_numeric(d["prk_cmprt_co"], errors="coerce")
    d["la"]    = pd.to_numeric(d["prk_plce_entrc_la"], errors="coerce")
    d["lo"]    = pd.to_numeric(d["prk_plce_entrc_lo"], errors="coerce")

    say("# 모집단 확정 — 부설 vs 비부설")
    say()
    say(f"- 공단 안양 고유 **{len(d):,}곳** · 이름 있음 {(d.nm!='').sum():,} "
        f"({(d.nm!='').mean():.0%}) · 면수 중앙 **{d.cells.median():.0f}면**")
    say(f"- 면수 분위 {[int(d.cells.quantile(q)) for q in (.25,.5,.75,.9,.99)]} "
        f"→ 소형 부설이 지배적인 분포다")
    say()

    # 앵커: 표준데이터 107곳(부설 0%)을 좌표+면수로 매칭
    matched = set(); pairs = 0
    if STD.exists():
        s = pd.read_csv(STD, dtype=str, encoding="utf-8-sig")
        s["lat"]   = pd.to_numeric(s["위도"], errors="coerce")
        s["lon"]   = pd.to_numeric(s["경도"], errors="coerce")
        s["cells"] = pd.to_numeric(s["주차구획수"], errors="coerce")
        kk = d.dropna(subset=["la","lo"])
        for r in s.itertuples():
            if pd.isna(r.lat) or pd.isna(r.cells) or r.cells <= 0: continue
            dist = hav(r.lat, r.lon, kk.la.values, kk.lo.values)
            near = np.where(dist <= 60)[0]
            cand = [(dist[i], kk.index[i]) for i in near
                    if pd.notna(kk.iloc[i].cells) and 0.8 <= kk.iloc[i].cells/r.cells <= 1.25]
            if cand: matched.add(min(cand)[1]); pairs += 1
        say(f"- 앵커: 표준 107곳 중 **{pairs}곳**이 좌표 60m + 면수 ±25% 로 매칭 "
            f"→ 공단측 {len(matched)}행")
        say(f"  (좌표만 120m 로 보면 107곳 전부 매칭되지만, 옆 건물 부설이 잡혀 신뢰할 수 없다)")
    say()

    # 요금 신호 — PrkOprInfo. 부설은 basic_info 껍데기만 있고 값이 비어 있다.
    #  ★ 레코드 존재가 아니라 "필드 채움" 으로 판단해야 한다(7,458곳 전부 레코드는 있다).
    fee_ids = set()
    if OPR.exists():
        import json as _j
        o = pd.DataFrame(_j.load(open(OPR, encoding="utf-8"))).drop_duplicates("prk_center_id")
        def _num(r, k1, k2):
            v = r.get(k1) or {}
            try: return float(v.get(k2))
            except (TypeError, ValueError): return np.nan
        for k1, k2 in (("basic_info","parking_chrge_bs_chrge"),
                       ("fxamt_info","parking_chrge_one_day_chrge"),
                       ("fxamt_info","parking_chrge_mon_unit_chrge")):
            col = o.apply(lambda r: _num(r, k1, k2), axis=1)
            fee_ids |= set(o.loc[col.notna(), "prk_center_id"])
        say(f"- 요금 신호: 운영정보 {len(o):,}곳 중 요금 필드가 **실제로 채워진 곳 {len(fee_ids)}곳** "
            f"({len(fee_ids)/len(o):.1%})")
        say(f"  운영시간도 같은 {len(fee_ids)}곳만 값이 있고 나머지는 빈 문자열, "
            f"`opertn_bs_free_time` 은 전 곳 `0` 상수 → 모집단 피처로 못 쓴다")
        say()
    d["fee_sig"] = d["prk_center_id"].isin(fee_ids)
    d["b_sig"]     = d.nm.apply(lambda t: any(w in t for w in BUILD))
    d["p_sig"]     = d.nm.apply(lambda t: any(w in t for w in PUBLIC))
    d["std_match"] = d.index.isin(matched)

    def klass(r):
        if r.std_match or r.fee_sig or (r.p_sig and not r.b_sig): return "비부설"
        if r.b_sig: return "부설"
        if r.nm == "" and pd.notna(r.cells) and r.cells <= 10: return "부설"
        return "애매"
    d["klass"] = d.apply(klass, axis=1)

    say("## 분류 결과")
    say()
    say("| 구분 | 곳 | 근거 | 면수 중앙 |")
    say("|---|---:|---|---:|")
    for k, why in (("비부설","표준데이터 매칭 또는 공공 지명(노상·공원·시장·환승 등)"),
                   ("부설","건물 지명(주택·빌라·아파트·빌딩·교회·학교·공장 등) 또는 무명 ≤10면"),
                   ("애매","둘 다 아님")):
        g = d[d.klass == k]
        say(f"| **{k}** | **{len(g):,}** | {why} | {g.cells.median():.0f} |")
    say()
    amb = d[d.klass == "애매"]
    say(f"- 애매 {len(amb):,}곳 중 이름 없음 **{int((amb.nm=='').sum()):,}곳** · "
        f"면수 분위 {[int(amb.cells.quantile(q)) for q in (.25,.5,.75,.9)]}")
    say(f"- 애매 예시(이름 있음): {', '.join(amb[amb.nm!=''].nm.head(12))}")
    say()
    say("## ★ 보고 숫자")
    say()
    n_np = int((d.klass=='비부설').sum()); n_amb = len(amb)
    say(f"- **비부설 추정 {n_np}곳** (확실) · 애매 {n_amb:,}곳을 더하면 상한 {n_np+n_amb:,}곳")
    say(f"- 부설 {int((d.klass=='부설').sum()):,}곳 ({(d.klass=='부설').mean():.0%}) 은 예측 대상에서 제외")
    say()
    say("> ⚠️ **공단 안양 데이터는 사실상 건축물 부설주차장 대장이다.** "
        "면수 중앙 5면, 53%가 무명이다. 비부설을 신뢰성 있게 골라낼 신호가 이름뿐이라 "
        "애매 구간이 크게 남는다.")
    say(f"> **요금 카드는 써봤고 거의 소득이 없었다.** 정밀도는 높지만"
        f"(요금 있음 → 95% 가 비부설) 재현율이 낮아 애매 구간을 못 가른다.")
    say("> 다음 후보: 좌표 밀집도(부설은 건물마다 하나씩 촘촘하다) · 표준데이터 좌표 근접성 · "
        "면수 분포. 다만 애매 1,190곳의 면수 중앙이 14면이라 소형 부설과 겹친다.")
    say()

    d["confidence"] = d.klass.map({"비부설":0.9, "부설":0.9, "애매":0.3})
    d[["prk_center_id","nm","cells","la","lo","klass","confidence",
       "b_sig","p_sig","std_match"]].rename(columns={"nm":"name"}).to_csv(OUT, index=False)
    amb[["prk_center_id","nm","cells","la","lo"]].rename(columns={"nm":"name"}) \
        .to_csv(TAB/"population_ambiguous.csv", index=False)
    say(f"- 출력: `data/interim/population.csv` · 애매 목록 `reports/tables/population_ambiguous.csv`")
    (TAB/"population.md").write_text("\n".join(REPORT)+"\n", encoding="utf-8")
    print(f"\n→ {TAB/'population.md'}")

if __name__ == "__main__":
    main()
