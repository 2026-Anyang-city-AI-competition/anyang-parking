#!/usr/bin/env python3
"""폴링 결측 구간 점검.  python3 scripts/check_gaps.py [DB경로]"""
import sqlite3, sys
import pandas as pd

db = sys.argv[1] if len(sys.argv) > 1 else "data/raw/parking.db"
t = pd.to_datetime(pd.read_sql(
    "SELECT DISTINCT ts_kst FROM obs ORDER BY ts_kst", sqlite3.connect(db)).ts_kst)
if len(t) < 2:
    sys.exit("관측이 2개 미만이다.")

span = (t.max() - t.min()).total_seconds() / 3600
gap = t.diff().dt.total_seconds().div(60)
big = gap[gap > 20]

print(f"수집 구간 : {t.min()} ~ {t.max()}  ({span:.1f}시간)")
print(f"스냅샷    : {len(t):,}개 / 간격 중앙값 {gap.median():.1f}분")
print(f"20분 이상 끊긴 구간: {len(big)}회, 총 손실 {big.sum()/60:.1f}시간")
if big.sum()/60 > 24:
    print("⚠️ 손실이 24시간을 넘었다. 요일 주기 추정이 흔들린다.")
for i in big.index[:15]:
    print(f"  {t[i-1]} → {t[i]}  ({gap[i]:.0f}분)")
