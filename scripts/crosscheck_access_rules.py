#!/usr/bin/env python3
"""서비스본과 분석본 출입규칙을 대조한다.

  .venv/bin/python scripts/crosscheck_access_rules.py

  data/raw/parking_access_rules.csv        주차장 × 요일그룹 267행. 서비스가 읽는 정본.
  data/processed/parking_access_rules.csv  주차장 1행 89행. A20/A22 분석용. enterable_status 보유.

★ 두 파일을 **합치지 않는다.** dev_todo §1.2 는 출처가 충돌하면 확정값을 만들지 말고
  `unknown` 으로 두라고 한다. 이 스크립트는 판정을 바꾸지 않고 세 가지만 보고한다.

  1. 충돌   — 서비스본은 확정인데 분석본과 다르다. 사람이 재확인해야 한다.
  2. 보강 후보 — 서비스본이 `unknown` 인데 분석본에는 확정값이 있다. 조사표로 채울 대상.
  3. 일치   — 두 출처가 같다. 그대로 둔다.
"""
import csv
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SERVICE = ROOT / "data/raw/parking_access_rules.csv"
ANALYSIS = ROOT / "data/processed/parking_access_rules.csv"

# 분석본 enterable_status → 서비스본에서 기대되는 출입 형태
ALWAYS, FEE_ONLY, UNKNOWN = "always", "fee_hours_only", "unknown"


def _rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _is_24h(row):
    return row["entry_windows"] == "00:00-24:00" and row["exit_windows"] == "00:00-24:00"


def main():
    service, analysis = _rows(SERVICE), {int(r["parking_id"]): r for r in _rows(ANALYSIS)}
    conflicts, fillable, agreed, unmatched = [], [], 0, []

    for row in service:
        pid = int(row["parking_id"])
        peer = analysis.get(pid)
        if peer is None:
            unmatched.append(pid)
            continue
        expected = peer["enterable_status"]
        confirmed = row["access_status"].startswith("confirmed")

        if not confirmed:
            if expected != UNKNOWN:
                fillable.append((pid, row["name"], row["day_group"], expected,
                                 peer["enterable_note"]))
            continue
        if expected == ALWAYS and not _is_24h(row):
            conflicts.append((pid, row["name"], row["day_group"],
                              f"분석본 always / 서비스본 {row['entry_windows']}·{row['exit_windows']}"))
        elif expected == FEE_ONLY and _is_24h(row):
            conflicts.append((pid, row["name"], row["day_group"],
                              "분석본 fee_hours_only / 서비스본 24시간 출입"))
        else:
            agreed += 1

    print(f"서비스본 {len(service)}행 · 분석본 {len(analysis)}곳")
    print(f"  일치            {agreed:>4}행")
    print(f"  충돌            {len(conflicts):>4}행  ← 사람이 재확인")
    print(f"  보강 후보        {len(fillable):>4}행  ← 서비스본 unknown, 분석본은 확정")
    if unmatched:
        print(f"  분석본에 없는 ID {len(unmatched):>4}행: {sorted(set(unmatched))}")

    if conflicts:
        print("\n[충돌] 자동으로 고치지 않는다. 조사표로 확정해야 한다.")
        for pid, name, group, detail in conflicts[:30]:
            print(f"  {pid:>4} {name[:14]:<16} {group:<15} {detail}")
        if len(conflicts) > 30:
            print(f"  … 외 {len(conflicts)-30}행")

    if fillable:
        print("\n[보강 후보] 분석본 근거가 있으니 조사표에서 우선 확인할 주차장이다.")
        lots = {}
        for pid, name, group, expected, note in fillable:
            lots.setdefault((pid, name), []).append((group, expected, note))
        for (pid, name), items in sorted(lots.items())[:30]:
            groups = ",".join(g for g, _, _ in items)
            print(f"  {pid:>4} {name[:14]:<16} {items[0][1]:<15} [{groups}] {items[0][2][:30]}")
        print(f"  주차장 {len(lots)}곳")

    both_unknown = sorted({(int(r["parking_id"]), r["name"]) for r in service
                           if not r["access_status"].startswith("confirmed")
                           and analysis.get(int(r["parking_id"]), {}).get("enterable_status") == UNKNOWN})
    if both_unknown:
        print(f"\n[양쪽 미확인 {len(both_unknown)}곳] 조사표로 채워야 할 정확한 대상이다.")
        for pid, name in both_unknown:
            print(f"  {pid:>4} {name}")

    print("\n분석본 enterable_status 분포:",
          dict(Counter(r["enterable_status"] for r in analysis.values())))
    return 1 if conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
