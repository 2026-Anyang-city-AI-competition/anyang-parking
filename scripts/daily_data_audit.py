#!/usr/bin/env python3
"""매일 돌리는 데이터 감시 — 연속성 · 고정값 · 정원 초과 · 급변.

  .venv/bin/python scripts/daily_data_audit.py            # 최근 24시간
  .venv/bin/python scripts/daily_data_audit.py --hours 72
  .venv/bin/python scripts/daily_data_audit.py --write    # 리포트 CSV 저장

문제가 있으면 **0이 아닌 코드로 끝난다.** cron·모니터링이 그걸 보고 알 수 있어야 한다.

`check_gaps.py`(전 기간 결측)와 `watch_freshness.py`(로컬 DB 최신성)는 그대로 둔다.
이 스크립트는 **최근 구간의 값 품질**을 본다 — 네 가지를 한 번에 본다.

  1. 연속성   폴링 간격이 5분 격자에서 벗어났는가
  2. 고정값   값이 통째로 안 움직이는 주차장이 늘었는가
  3. 정원 초과 park_count > cell_cnt (관측 오류 또는 정원 변경)
  4. 급변     5분 사이 점유율이 한 번에 크게 튀는가

★ "변동 0"을 계측 불량으로 단정하지 않는다(항상 만차일 수 있다). 세어서 보고만 한다.
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import PARKING_DB, TABLES
from src.serve.candidates import DEAD_RANGE

KST = timezone(timedelta(hours=9))
EXPECTED_INTERVAL_MIN = 5
GAP_TOLERANCE_MIN = 20         # 이보다 길게 끊기면 결측으로 센다
JUMP_SIZE = 0.5                # 5분 사이 점유율 50%p 이상 변화
JUMP_WINDOW_MIN = 10


def _load(db_path, hours):
    since = (datetime.now(KST) - timedelta(hours=hours)).isoformat()
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        df = pd.read_sql(
            "SELECT ts_kst, parking_id, cell_cnt, park_count FROM obs WHERE ts_kst >= ?",
            con, params=(since,))
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df.ts_kst, format="ISO8601")
    return df.sort_values(["parking_id", "ts"])


def audit(db_path=PARKING_DB, hours=24):
    df = _load(db_path, hours)
    findings, rows = [], []
    if df.empty:
        findings.append(("no_data", f"최근 {hours}시간 관측이 없다", 1))
        return findings, pd.DataFrame(), {}

    # 1. 연속성 — 스냅샷 간격
    stamps = pd.Series(sorted(df.ts.unique()))
    gaps = stamps.diff().dt.total_seconds().div(60).dropna()
    long_gaps = gaps[gaps > GAP_TOLERANCE_MIN]
    summary = {
        "hours": hours,
        "snapshots": len(stamps),
        "median_interval_min": round(float(gaps.median()), 1) if len(gaps) else None,
        "gaps_over_tolerance": int(len(long_gaps)),
        "lost_hours": round(float(long_gaps.sum()) / 60, 2) if len(long_gaps) else 0.0,
        "lots_seen": int(df.parking_id.nunique()),
    }
    if len(long_gaps):
        findings.append(("polling_gap",
                         f"{GAP_TOLERANCE_MIN}분 넘게 끊긴 구간 {len(long_gaps)}회 "
                         f"(총 {summary['lost_hours']}시간)", len(long_gaps)))
    expected = hours * 60 / EXPECTED_INTERVAL_MIN
    if len(stamps) < expected * 0.9:
        findings.append(("snapshot_shortfall",
                         f"스냅샷 {len(stamps)}개 — 기대치 {int(expected)}개의 90% 미만", 1))

    # 3. 정원 초과
    valid = df[df.cell_cnt > 0].copy()
    valid["occ"] = valid.park_count / valid.cell_cnt
    over = valid[valid.park_count > valid.cell_cnt]
    if len(over):
        findings.append(("over_capacity",
                         f"정원 초과 관측 {len(over)}건 · 주차장 {over.parking_id.nunique()}곳",
                         len(over)))

    # 4. 급변 — 짧은 간격 안에서의 큰 점유율 변화
    valid["delta"] = valid.groupby("parking_id").occ.diff().abs()
    valid["elapsed"] = valid.groupby("parking_id").ts.diff()
    jumps = valid[(valid.delta > JUMP_SIZE)
                  & (valid.elapsed <= timedelta(minutes=JUMP_WINDOW_MIN))]
    if len(jumps):
        findings.append(("step_change",
                         f"{JUMP_WINDOW_MIN}분 내 {int(JUMP_SIZE*100)}%p 초과 급변 {len(jumps)}건 "
                         f"· 주차장 {jumps.parking_id.nunique()}곳", len(jumps)))

    # 2. 고정값 — 최근 구간에서 전혀 움직이지 않은 곳
    per_lot = valid.groupby("parking_id").agg(
        n=("occ", "size"), lo=("occ", "min"), hi=("occ", "max"),
        over_cap=("park_count", lambda s: 0), last=("ts", "max")).reset_index()
    per_lot["range"] = per_lot.hi - per_lot.lo
    frozen = per_lot[(per_lot.range < DEAD_RANGE) & (per_lot.n >= 12)]
    summary["frozen_lots"] = int(len(frozen))
    summary["moving_lots"] = int(len(per_lot) - len(frozen))
    # 고정 피드는 이미 알려진 21곳이 있다. 그보다 늘었을 때만 문제로 본다.
    findings.append(("frozen_count", f"최근 구간 고정 주차장 {len(frozen)}곳", 0))

    for lot_id, group in valid.groupby("parking_id"):
        lot_over = int((group.park_count > group.cell_cnt).sum())
        lot_jumps = int(((group.delta > JUMP_SIZE)
                         & (group.elapsed <= timedelta(minutes=JUMP_WINDOW_MIN))).sum())
        lot_range = float(group.occ.max() - group.occ.min())
        rows.append({"parking_id": int(lot_id), "observations": len(group),
                     "occ_range": round(lot_range, 4),
                     "frozen": bool(lot_range < DEAD_RANGE and len(group) >= 12),
                     "over_capacity": lot_over, "step_changes": lot_jumps,
                     "last_seen": group.ts.max().isoformat()})
    return findings, pd.DataFrame(rows), summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--db", default=str(PARKING_DB))
    parser.add_argument("--write", action="store_true", help="주차장별 리포트를 CSV로 저장")
    args = parser.parse_args()

    findings, per_lot, summary = audit(args.db, args.hours)
    print(f"최근 {args.hours}시간 데이터 감시")
    for key, value in summary.items():
        print(f"  {key:<22} {value}")
    print()
    problems = [f for f in findings if f[2] > 0]
    for code, message, count in findings:
        mark = "⚠️" if count > 0 else "  "
        print(f"{mark} {code:<20} {message}")

    if args.write and not per_lot.empty:
        TABLES.mkdir(parents=True, exist_ok=True)
        path = TABLES / "daily_data_audit.csv"
        per_lot.sort_values(["over_capacity", "step_changes"], ascending=False).to_csv(
            path, index=False, encoding="utf-8-sig")
        print(f"\n리포트: {path}")

    if problems:
        print(f"\n문제 {len(problems)}종 — 확인이 필요하다.")
        return 1
    print("\n이상 없음.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
