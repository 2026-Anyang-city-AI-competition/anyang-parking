#!/usr/bin/env python3
"""`prediction_availability.csv` 를 parking.db 진단으로 만든다.

  .venv/bin/python scripts/build_prediction_availability.py            # 미리보기
  .venv/bin/python scripts/build_prediction_availability.py --write    # CSV 적재

★ 이 파일은 **조사 결과가 아니라 DB 진단 결과**다(dev_todo §1.4).
  `parking_access_rules.csv`(출입 가능시간)와 목적이 다르다 — 예측을 내보내도 되는
  시간대만 고른다. 예측 불가와 출입 불가는 서로 다른 상태다.

★ 게이트는 fail-closed 다. 행이 없으면 그 주차장의 예측은 **전부 차단**된다.
  그래서 「검증했으니 연다」만 쓰고, 애매하면 `evaluation_pending` 으로 닫아 둔다.

판정 순서 (주차장 × 요일그룹 × 시(hour) 버킷):
  1. 전 기간 점유율 변동폭 < 1%p          → dead_feed      (요일그룹 전체)
  2. 버킷 표본이 얇다                      → evaluation_pending
  3. 버킷 안에서 값이 전혀 안 움직인다      → frozen
  4. 정원 초과·급변이 섞여 있다             → anomaly
  5. 그 외                                 → available
열린 시(hour)들을 이어 붙여 `prediction_windows` 로 만든다.
"""
import argparse
import csv
import sqlite3
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import PARKING_DB, PREDICTION_AVAILABILITY_CSV
from src.serve.access_check import is_korean_holiday
from src.serve.access_rules import day_group_for
from src.serve.candidates import DEAD_RANGE
from src.serve.prediction_gate import FIELDS

DIAGNOSIS_CSV = ROOT / "reports/tables/prediction_availability_diagnosis.csv"
EVIDENCE = "reports/tables/prediction_availability_diagnosis.csv"

MIN_DAYS = 2        # 서로 다른 날짜가 이보다 적으면 그 시간대는 평가하지 않는다
MIN_OBS = 12        # 5분 폴링 기준 1시간치
FROZEN_RANGE = DEAD_RANGE
OVER_CAP_RATIO = 0.01
JUMP_RATIO = 0.02
JUMP_SIZE = 0.5     # 5분 사이 점유율이 50%p 넘게 튀면 급변으로 본다


def _load(db_path):
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        df = pd.read_sql("SELECT ts_kst, parking_id, cell_cnt, park_count FROM obs", con)
    df["ts"] = pd.to_datetime(df.ts_kst, format="ISO8601")
    df = df[df.cell_cnt > 0].copy()
    df["occ"] = df.park_count / df.cell_cnt
    df["over_cap"] = df.park_count > df.cell_cnt
    df["hour"] = df.ts.dt.hour
    df["day"] = df.ts.dt.date
    # 공휴일 달력을 쓴다. 일요일과 법정공휴일을 같은 그룹으로 묶는다.
    groups = {d: day_group_for(d, is_holiday=is_korean_holiday(d)) for d in df.day.unique()}
    df["day_group"] = df.day.map(groups)
    df = df.sort_values(["parking_id", "ts"])
    df["jump"] = (df.groupby("parking_id").occ.diff().abs() > JUMP_SIZE) & \
                 (df.groupby("parking_id").ts.diff() <= timedelta(minutes=10))
    return df


def _classify(bucket):
    if bucket["days"] < MIN_DAYS or bucket["n"] < MIN_OBS:
        return "evaluation_pending", "insufficient_history"
    if bucket["over_cap_ratio"] > OVER_CAP_RATIO:
        return "anomaly", "over_capacity"
    if bucket["jump_ratio"] > JUMP_RATIO:
        return "anomaly", "step_change"
    if bucket["range"] < FROZEN_RANGE:
        return "frozen", "no_movement"
    return "available", "verified"


def _windows(hours):
    """열린 시(hour)들을 `HH:MM-HH:MM` 구간으로 잇는다."""
    out, start = [], None
    for hour in range(25):
        if hour in hours and start is None:
            start = hour
        elif hour not in hours and start is not None:
            out.append(f"{start:02d}:00-{hour:02d}:00" if hour < 24 else f"{start:02d}:00-24:00")
            start = None
    return "|".join(out)


def diagnose(db_path=PARKING_DB):
    df = _load(db_path)
    total_range = df.groupby("parking_id").occ.agg(lambda s: s.max() - s.min())
    dead = set(total_range[total_range < DEAD_RANGE].index)

    grouped = df.groupby(["parking_id", "day_group", "hour"])
    buckets = grouped.agg(n=("occ", "size"), days=("day", "nunique"),
                          lo=("occ", "min"), hi=("occ", "max"),
                          over_cap=("over_cap", "sum"), jumps=("jump", "sum")).reset_index()
    buckets["range"] = buckets.hi - buckets.lo
    buckets["over_cap_ratio"] = buckets.over_cap / buckets.n
    buckets["jump_ratio"] = buckets.jumps / buckets.n
    verdicts = buckets.apply(lambda b: _classify(b), axis=1)
    buckets["status"] = [v[0] for v in verdicts]
    buckets["reason"] = [v[1] for v in verdicts]
    buckets.loc[buckets.parking_id.isin(dead), ["status", "reason"]] = ["dead_feed", "no_movement_at_all"]

    today = date.today().isoformat()
    rows = []
    for (pid, group), part in buckets.groupby(["parking_id", "day_group"]):
        if pid in dead:
            rows.append({"parking_id": pid, "day_group": group, "prediction_windows": "",
                         "status": "dead_feed", "reason": "dead_feed:전 기간 점유율 변동 없음",
                         "evidence_report": EVIDENCE, "evaluated_at": today})
            continue
        open_hours = set(part.loc[part.status == "available", "hour"])
        blocked = Counter(f"{r.status}:{r.reason}" for r in part.itertuples()
                          if r.status != "available")
        # 관측이 아예 없던 시(hour)도 열어 주지 않는다 — 버킷 자체가 없으면 닫힌 채로 둔다.
        note = ";".join(f"{k}={v}h" for k, v in sorted(blocked.items())) or "all_hours_verified"
        if open_hours:
            rows.append({"parking_id": pid, "day_group": group,
                         "prediction_windows": _windows(open_hours),
                         "status": "available", "reason": note,
                         "evidence_report": EVIDENCE, "evaluated_at": today})
        else:
            worst = blocked.most_common(1)[0][0] if blocked else "evaluation_pending:no_data"
            status = worst.split(":")[0]
            rows.append({"parking_id": pid, "day_group": group, "prediction_windows": "",
                         "status": status if status != "anomaly" else "anomaly",
                         "reason": note, "evidence_report": EVIDENCE, "evaluated_at": today})
    rows.sort(key=lambda r: (r["parking_id"], r["day_group"]))
    return rows, buckets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="CSV를 실제로 덮어쓴다")
    parser.add_argument("--db", default=str(PARKING_DB))
    args = parser.parse_args()

    rows, buckets = diagnose(args.db)
    counts = Counter(r["status"] for r in rows)
    lots = {r["parking_id"] for r in rows}
    print(f"주차장 {len(lots)}곳 · 행 {len(rows)}개")
    for status, n in counts.most_common():
        print(f"  {status:<20} {n:>4}행")
    open_lots = {r["parking_id"] for r in rows if r["status"] == "available"}
    print(f"예측을 여는 주차장 {len(open_lots)}곳 · 완전 차단 {len(lots - open_lots)}곳")
    partial = [r for r in rows if r["status"] == "available" and "=" in r["reason"]]
    print(f"일부 시간대만 차단된 행 {len(partial)}개")

    if not args.write:
        print("\n--write 를 붙이면 적재한다. 예시 3행:")
        for row in rows[:3]:
            print(" ", row)
        return

    DIAGNOSIS_CSV.parent.mkdir(parents=True, exist_ok=True)
    buckets.to_csv(DIAGNOSIS_CSV, index=False, encoding="utf-8-sig")
    path = Path(PREDICTION_AVAILABILITY_CSV)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n적재: {path}")
    print(f"근거: {DIAGNOSIS_CSV}")


if __name__ == "__main__":
    main()
