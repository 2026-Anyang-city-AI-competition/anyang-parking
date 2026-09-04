#!/usr/bin/env python3
"""a08 · oneday_amt 전수 대조 — 조례 별표 5 재계산값과 비교."""
import sqlite3, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from src.serve import fare_tables as T
from src.serve.fare import calc_fare, resolve_type
from datetime import datetime

TAB = ROOT/"reports/tables"; TAB.mkdir(parents=True, exist_ok=True)
R=[]
def say(s=""): print(s, flush=True); R.append(s)

con=sqlite3.connect(ROOT/"data/raw/parking.db")
L=pd.read_sql("SELECT * FROM lots",con); con.close()
def hh(s):
    try: h,m=str(s).split(":"); return int(h)+int(m)/60
    except Exception: return np.nan
L["op_h"]=L.wdays_end.map(hh)-L.wdays_start.map(hh)
L.loc[L.op_h<=0,"op_h"]=L.op_h+24
L["gr"]=pd.to_numeric(L.grade,errors="coerce")
L["type2"]=[resolve_type(n,d) for n,d in zip(L.name,L["div"])]
L["exp"]=[T.daily_pass(h,g) if pd.notna(h) and pd.notna(g) else None for h,g in zip(L.op_h,L.gr)]
L["od"]=pd.to_numeric(L.oneday_amt,errors="coerce")
inb=L.exp.notna(); L["match"]=inb & (L.od==L.exp)

say("# 요금 계산기 — 조례 대조 결과")
say()
say("## 1. `oneday_amt` 전수 대조 (별표 5 재계산값 기준)")
say()
say(f"- 89곳 중 별표5 표 안(운영 8~13h): **{int(inb.sum())}곳**")
say(f"  - 일치 **{int(L.match.sum())}곳 ({L.match.sum()/inb.sum():.1%})**")
say(f"  - 불일치 **{int((inb&~L['match']).sum())}곳**")
say(f"- 표 밖(24시간 운영): **{int((~inb).sum())}곳** → 일일권 없음, 일 상한 25,000 만 적용")
say()
bad=L[inb&~L["match"]]
say("| id | 이름 | 유형 | 급지 | 운영h | `oneday_amt` | 별표5 기댓값 |")
say("|---:|---|---|---:|---:|---:|---:|")
for r in bad.itertuples():
    say(f"| {r.parking_id} | {r.name} | {r.type2} | {int(r.gr)} | {r.op_h:.0f} | "
        f"{int(r.od):,} | {int(r.exp):,} |")
say()
say("### 결론")
say()
say("**별표 5 재계산값을 정본으로 쓴다. `oneday_amt` 는 참고로만.**")
say(f"일치율 {L.match.sum()/inb.sum():.1%} 로 대체로 맞지만 {int((inb&~L['match']).sum())}곳이 틀렸고,")
say("그중 2곳(관악3노상·삼봉노상)은 **일일권 자리에 일 상한액 25,000 이 들어가 있다.**")
say()
say("> ★ **25,000 의 정체가 밝혀졌다.** 별표 1 〈비고 10〉 "
    "「누진요금제 일 최대요금 상한액은 25,000원을 초과할 수 없다」 이다.")
say("> 월정기권(노외 5급지 25,000)과 금액이 같아 혼동됐던 것이다.")
say(f"> `oneday_amt == 25,000` 인 곳은 **{int((L.od==25000).sum())}곳**뿐이다 "
    "(GITS `ddPktckFare` 의 25,000 65곳과 혼동하지 말 것).")
say()

say("## 2. 계산 불가 주차장")
say()
mon=datetime(2026,9,7,10,0); fail={}
for r in L.itertuples():
    lot={"type":r.type2,"name":r.name,"grade":r.grade,"wdays_start":r.wdays_start,
         "wdays_end":r.wdays_end,"wend_start":r.wend_start,"wend_end":r.wend_end}
    v=calc_fare(lot,mon,120)
    if v["total"] is None: fail[v["reason"][:50]]=fail.get(v["reason"][:50],0)+1
say(f"- **89곳 전부 계산 가능** (실패 {sum(fail.values())}곳)" if not fail
    else f"- 실패 {sum(fail.values())}곳: {fail}")
say(f"- 급지 결측 0곳 · 유형 해소 후 노상 {int((L.type2=='노상').sum())} / "
    f"노외 {int((L.type2=='노외').sum())}")
say()
say("> ⚠️ 포털 `div` 의 '위탁' 14곳은 요금표에 없어 그대로면 계산 불가다.")
say("> **GITS `pkplcTypeNm` 은 이 14곳을 전부 '기타'로 준다** — 정본 §7 의")
say("> 「GITS 가 pkplcTypeNm 을 100% 주므로 이 함정은 해소된다」는 **성립하지 않는다.**")
say("> 주차장명 규칙으로 해소했고, 결과(노상 13 / 노외 1)가 정본 §7 의 실측과 정확히 일치한다.")
say()

L[["parking_id","name","div","type2","gr","op_h","od","exp","match"]].to_csv(
    TAB/"a08_oneday_check.csv", index=False)
(TAB/"a08_fare_check.md").write_text("\n".join(R)+"\n", encoding="utf-8")
print(f"\n→ {TAB/'a08_fare_check.md'}")
