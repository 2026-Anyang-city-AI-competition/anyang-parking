#!/usr/bin/env python3
"""데이터가 쌓였을 때 무엇을 다시 돌려야 하는지 알려준다 (dev_todo 8-7).

  .venv/bin/python scripts/reevaluate_gate.py
  .venv/bin/python scripts/reevaluate_gate.py --test-days 7

A23·A24·U11 은 모두 **rolling-origin** 이라 폴링이 쌓이면 test 창이 저절로 밀린다.
그래서 "데이터가 늘었으니 좋아졌겠지"가 아니라, **지금 돌리면 무엇이 달라지는지**를
먼저 계산한다.

핵심 관문은 하나다 — `weekend_weeks >= 2`.
8-2 의 인증이 막힌 유일한 이유이고, 모델이 아니라 **데이터 길이**의 문제다.
다른 조건(MAE·기준선·주차장 합격률·방향 재현)은 15/30/60분에서 이미 통과해 있다.

★ 이 스크립트는 실험을 돌리지 않는다. 돌릴 가치가 있는지만 판정한다.
  판정이 바뀌지 않았는데 다시 돌리면 같은 `partial` 을 다시 얻을 뿐이다.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.analysis.a20_matched_model_comparison import test_dates
from src.analysis.a23_horizon_curve import MIN_WEEKEND_WEEKS, weekend_weeks
from src.config import PARKING_DB, TABLES

# 실험 이름 → (매니페스트, 재실행 명령)
EXPERIMENTS = {
    "A23 지평선 곡선": ("a23_manifest.json", "python -m src.analysis.a23_horizon_curve"),
    "A23 모델 보강(M1)": ("a23m_manifest.json", "python -m src.analysis.a23_model_upgrade"),
    "A23 Prophet 비교군": ("a23p_manifest.json", "python -m src.analysis.a23_prophet_compare"),
    "A24 글로벌 vs 개별": ("a24_manifest.json", "python -m src.analysis.a24_global_vs_local"),
}


def data_span(db_path=PARKING_DB):
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        first, last, rows = con.execute(
            "SELECT MIN(ts_kst), MAX(ts_kst), COUNT(*) FROM obs").fetchone()
    return pd.Timestamp(first), pd.Timestamp(last), int(rows)


def manifest_state(name):
    path = TABLES / name
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def certification_blockers():
    """현재 인증표에서 주말 조건 말고 걸려 있는 항목을 센다."""
    path = TABLES / "a23_certification.csv"
    if not path.exists():
        return None
    table = pd.read_csv(path)
    checks = [c for c in table.columns if c.endswith("_ok") or c == "direction_reproduced"]
    out = []
    for _, row in table[table.row_set.eq("core")].iterrows():
        failed = [c for c in checks if not bool(row[c])]
        out.append({"horizon": int(row.horizon), "certified": bool(row.certified),
                    "provisional": bool(row.provisional), "failed": failed})
    return out


def weekend_detail(dates):
    """test 창의 주말 날짜와, 그중 **온전한 주말**(토·일이 붙어 있는 쌍)의 수.

    ★ `weekend_weeks` 는 ISO 주 수를 센다. 토요일 하나와 다음 주 일요일 하나도 2주로
      잡힌다. 「방향 재현에 필요한 주말」의 뜻과는 다를 수 있어 둘 다 보여 준다.
      기준을 몰래 느슨하게 적용하지 않기 위한 장치다."""
    weekend = sorted(d for d in dates if d.weekday() >= 5)
    days = {d.date() for d in weekend}
    pairs = sum(1 for d in weekend
                if d.weekday() == 5 and (d + pd.Timedelta(days=1)).date() in days)
    return weekend, pairs


def gate_opens_at(first, last, test_days, min_train_days, horizon_days=60):
    """며칠 더 쌓이면 주말 조건이 열리는지. 열리지 않으면 None.

    7일 창도 일요일~토요일로 걸치면 서로 다른 ISO 주의 주말 이틀을 담는다.
    그래서 "2주 더"가 아니라 며칠 만에 열릴 수 있다 — 직접 세어 본다."""
    for offset in range(1, horizon_days + 1):
        future_last = last.normalize() + pd.Timedelta(days=offset)
        try:
            dates = test_dates(first, future_last, test_days, min_train_days)
        except ValueError:
            continue
        if weekend_weeks(dates) >= MIN_WEEKEND_WEEKS:
            return future_last
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(PARKING_DB))
    parser.add_argument("--test-days", type=int, default=7)
    parser.add_argument("--min-train-days", type=int, default=7)
    args = parser.parse_args()

    first, last, rows = data_span(args.db)
    print(f"데이터 {first} ~ {last} ({(last - first).days}일 · {rows:,}행)")

    try:
        dates = test_dates(first, last, args.test_days, args.min_train_days)
    except ValueError as exc:
        print(f"\n아직 평가할 수 없다: {exc}")
        return 1
    weekends = weekend_weeks(dates)
    weekend_days, full_pairs = weekend_detail(dates)
    labels = ", ".join(f"{d.date()}({'월화수목금토일'[d.weekday()]})" for d in weekend_days)
    print(f"지금 돌리면 test 날짜: {dates[0].date()} ~ {dates[-1].date()}")
    print(f"  주말 ISO주 {weekends}회 · 온전한 주말 {full_pairs}회 · 해당 날짜: {labels or '없음'}")

    print("\n── 인증 관문 ──")
    gate_open = weekends >= MIN_WEEKEND_WEEKS
    if gate_open:
        print(f"  ✅ 주말 {weekends}회 ≥ {MIN_WEEKEND_WEEKS} — 인증 조건을 이제 시도할 수 있다.")
        if full_pairs < MIN_WEEKEND_WEEKS:
            print(f"  ⚠️ 다만 온전한 주말은 {full_pairs}회뿐이다(토·일이 붙은 쌍). "
                  f"서로 다른 주의 토요일 하나·일요일 하나로 조건을 채운 것이라면 "
                  f"리포트에 그 사실을 함께 적는다.")
    else:
        need = MIN_WEEKEND_WEEKS - weekends
        print(f"  ⛔ 주말 {weekends}회 < {MIN_WEEKEND_WEEKS} — 주말이 {need}회 더 필요하다.")
        print(f"     지금 다시 돌려도 결과는 partial 그대로다.")
        opens = gate_opens_at(first, last, args.test_days, args.min_train_days)
        if opens is not None:
            days = (opens.date() - last.normalize().date()).days
            future = test_dates(first, opens, args.test_days, args.min_train_days)
            _, future_pairs = weekend_detail(future)
            print(f"     관문이 열리는 시점: 데이터가 {opens.date()} 까지 쌓이면 "
                  f"(앞으로 {days}일, 그때 온전한 주말 {future_pairs}회)")
            # 창이 하루씩 밀리며 ISO주 수가 2↔1로 오르내린다. 그날을 놓치면 다시 닫힌다.
            print(f"     ⚠️ 이 조건은 창이 밀리며 다시 닫힐 수 있다. 열린 날 바로 돌린다.")
        else:
            print("     60일 안에는 열리지 않는다. test 창 길이를 다시 본다.")

    blockers = certification_blockers()
    if blockers:
        print("\n── 주말 조건을 뺀 나머지 (core 행집합, 직전 실행 기준) ──")
        for item in blockers:
            other = [f for f in item["failed"] if f != "weekend_weeks_ok"]
            mark = "통과" if not other else f"미달: {', '.join(other)}"
            print(f"  {item['horizon']:>4}분  {mark}")

    print("\n── 실험별 데이터 최신성 ──")
    stale = []
    for label, (manifest_name, command) in EXPERIMENTS.items():
        state = manifest_state(manifest_name)
        if state is None:
            print(f"  {label:<20} 매니페스트 없음 — 아직 돌린 적이 없다")
            stale.append((label, command))
            continue
        recorded = pd.Timestamp(state.get("end")) if state.get("end") else None
        behind = (last - recorded) if recorded is not None else None
        status = state.get("status", "?")
        if behind is not None and behind > pd.Timedelta(hours=12):
            print(f"  {label:<20} {status:<16} 데이터가 {behind.days}일 {behind.seconds//3600}시간 뒤처짐")
            stale.append((label, command))
        else:
            print(f"  {label:<20} {status:<16} 최신")

    print("\n── 결론 ──")
    if not gate_open:
        print("  주말이 더 쌓이기 전에는 인증 상태가 바뀌지 않는다. 재실행을 미룬다.")
        print("  (탐색 수치만 갱신하고 싶다면 아래 명령을 직접 돌린다.)")
    elif stale:
        print("  주말 조건이 열렸고 데이터도 뒤처졌다. 아래 순서로 다시 돌린다.")
    else:
        print("  주말 조건이 열렸다. 마지막 실행이 최신이면 인증표만 다시 확인한다.")
    for label, command in stale:
        print(f"    {command}    # {label}")
    if stale:
        print("    ⚠️ 같은 test 창을 쓰려면 모두 같은 --data-end 로 돌린다.")
    return 0 if gate_open else 2


if __name__ == "__main__":
    sys.exit(main())
