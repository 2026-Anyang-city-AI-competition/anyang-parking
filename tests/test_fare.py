#!/usr/bin/env python3
"""
요금 계산기 단위 테스트 — 조례 별표 1·2·5 기준. 손으로 검산한 값이다.

  python3 tests/test_fare.py
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.serve.fare import calc_fare, operating_minutes, resolve_type
from src.serve import fare_tables as T

FAIL = []
def check(name, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<44} {got!r}" + ("" if ok else f"  (기대 {want!r})"))
    if not ok: FAIL.append(name)

MON = datetime(2026, 9, 7, 10, 0)          # 월요일 10:00 (일요일 무료 조항 회피)
G1 = {"type": "노외", "grade": 1, "wdays_start": "09:00", "wdays_end": "22:00"}  # 13h, 일일권 16,000
G3 = {"type": "노외", "grade": 3, "wdays_start": "10:00", "wdays_end": "18:00"}  # 8h,  일일권 5,000

print("=== 노외 1급지 (13시간 운영 · 일일권 16,000) ===")
for m, want in ((14, 0), (30, 600), (40, 900), (60, 1500),
                (90, 3300), (120, 5100), (180, 10500), (300, 24900)):
    check(f"{m}분 (후불 누진)", calc_fare(G1, MON, m)["total"], want)

print("\n=== 노외 3급지 (8시간 운영 · 일일권 5,000) ===")
for m, want in ((30, 200), (60, 500), (120, 2300), (180, 4700), (240, 7100)):
    check(f"{m}분 (후불 누진)", calc_fare(G3, MON, m)["total"], want)

print("\n=== 운영시간 처리 ===")
# 23:00 입차 3시간 → 운영 09:00~22:00 밖 → 전액 0원
night = datetime(2026, 9, 7, 23, 0)
check("운영시간 외 전체(23시 입차 3h)", calc_fare(G1, night, 180)["total"], 0)
# 21:00 입차 3시간 → 21~22시 60분만 과금 → 600 + 300×3 = 1,500
late = datetime(2026, 9, 7, 21, 0)
r = calc_fare(G1, late, 180)
check("걸침(21시 입차 3h) 과금 분", r["billable_min"], 60)
check("걸침(21시 입차 3h) 요금", r["total"], 1500)
# 일요일 무료 (비고 4)
sun = datetime(2026, 9, 6, 14, 0)
check("일요일 무료", calc_fare(G1, sun, 120)["total"], 0)
check("일요일 무료 끄면 과금", calc_fare(G1, sun, 120, sunday_free=False)["total"], 5100)

print("\n=== 감면 (별표 2) ===")
check("경형자동차 50% (120분 5,100→2,550→절사)",
      calc_fare(G1, MON, 120, discount="경형자동차")["total"], 2500)
check("15분 미만은 감면 이전에 0원",
      calc_fare(G1, MON, 14, discount="경형자동차")["total"], 0)
check("장애인_중 최초 2시간 면제(120분)",
      calc_fare(G1, MON, 120, discount="장애인_중")["total"], 0)
check("전통시장 최초 90분 면제(120분 → 30분분)",
      calc_fare(G1, MON, 120, discount="전통시장")["total"], 600)

print("\n=== ★ 일일권은 선불 상품 (비고 8, v11) ===")
r = calc_fare(G1, MON, 300)
check("300분 후불 누진", r["total"], 24900)
check("300분 선불 일일권", r["total_prepaid"], 16000)
check("선불 권장", r["recommend_prepaid"], True)
check("절약액", r["prepaid_saving"], 8900)
r = calc_fare(G1, MON, 120)
check("120분은 후불이 유리", r["recommend_prepaid"], False)
check("자동 상한을 켜면 v10 동작", calc_fare(G1, MON, 300, apply_daily_pass_cap=True)["total"], 16000)

print("\n=== 운영시간 표기 해석 ===")
closed = {"type": "노외", "grade": 1, "wdays_start": "00:00", "wdays_end": "00:00"}
check("00:00~00:00 은 미운영(24h 아님)", calc_fare(closed, MON, 300)["total"], 0)
open24 = {"type": "노외", "grade": 1, "wdays_start": "00:00", "wdays_end": "24:00"}
check("00:00~24:00 은 진짜 24시간", calc_fare(open24, MON, 300)["billable_min"], 300)
nohours = {"type": "노외", "grade": 1}
check("운영시간 값 자체가 없으면 24h", calc_fare(nohours, MON, 300)["billable_min"], 300)

print("\n=== 상한 (비고 10 · 일 최대 25,000) ===")
G24 = {"type": "노외", "grade": 1, "wdays_start": "00:00", "wdays_end": "24:00"}  # 24h → 별표5 밖
r = calc_fare(G24, MON, 600)
check("24시간 운영은 일일권 표 밖", r["daily_pass"], None)
check("그래도 25,000 상한은 걸린다", r["total"], 25000)

print("\n=== 유형 해소 (포털 '위탁' 함정) ===")
check("범계역노상 → 노상", resolve_type("범계역노상", "위탁"), "노상")
check("냉천놀이터지하 → 노외", resolve_type("냉천놀이터지하", "위탁"), "노외")
check("예술공원고가밑 → 노상", resolve_type("예술공원고가밑", "위탁"), "노상")
check("표준데이터 유형이 최우선", resolve_type("아무개노상", "위탁", std_type="노외"), "노외")

print("\n=== 부설·결측은 예외 대신 reason ===")
r = calc_fare({"type": "부설", "grade": 1}, MON, 60)
check("부설은 total None + reason", (r["total"], bool(r["reason"])), (None, True))
r = calc_fare({"type": "노외", "grade": None, "name": "x"}, MON, 60)
check("급지 결측도 total None + reason", (r["total"], bool(r["reason"])), (None, True))

print("\n=== 일일권 교차점 ===")
rr = calc_fare(G1, MON, 300)
r = rr
n = r["daily_pass_better_after_min"]
before, _ = calc_fare(G1, MON, n - 1), None
print(f"  1급지 13시간: 일일권 {r['daily_pass']:,}원 · 교차점 {n}분 "
      f"({n//60}시간 {n%60}분)")
check("교차점 직전은 일일권보다 싸다",
      calc_fare(G1, MON, n - 1)["total"] <= r["daily_pass"], True)
check("교차점 이후는 일일권보다 비싸다",
      calc_fare(G1, MON, n + 10)["total"] > r["daily_pass"], True)

print()
if FAIL:
    print(f"❌ 실패 {len(FAIL)}건: {FAIL}"); sys.exit(1)
print("✅ 전부 통과")
