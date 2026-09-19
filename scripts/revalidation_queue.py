#!/usr/bin/env python3
"""조사 규칙 재검증 큐 — 만료·노후·미확인 행을 우선순위대로 뽑는다.

  .venv/bin/python scripts/revalidation_queue.py              # 큐 출력
  .venv/bin/python scripts/revalidation_queue.py --write      # CSV 저장
  .venv/bin/python scripts/revalidation_queue.py --days 60    # 노후 기준 변경

조사 데이터는 시간이 지나면 틀린다. 운영시간이 바뀌고, 임시 폐쇄가 풀리고,
`effective_to` 가 지나간다. 이 스크립트는 **무엇을 다시 확인해야 하는지만** 고른다 —
값을 고치지 않는다. 확정은 사람이 조사표로 한다.

우선순위:
  1. expired        `effective_to` 가 지났다 — 지금 서비스가 쓰면 안 되는 규칙이다
  2. expiring_soon  30일 안에 만료된다
  3. unknown        확정된 적이 없다 — 추천에 미확인 경고가 붙는 곳
  4. weak_evidence  확정인데 근거가 `user_experience` 하나뿐이다
  5. stale          마지막 확인이 오래됐다
"""
import argparse
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import ACCESS_RULES_CSV, TABLES

EXPIRING_WINDOW_DAYS = 30
STALE_DAYS = 90
WEAK_EVIDENCE = {"user_experience"}
PRIORITY = ("expired", "expiring_soon", "unknown", "weak_evidence", "stale")


def _date(value):
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def build(path=ACCESS_RULES_CSV, today=None, stale_days=STALE_DAYS):
    today = today or date.today()
    queue = []
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if not any(row.values()):
                continue
            status = (row.get("access_status") or "").strip()
            effective_to = _date(row.get("effective_to"))
            checked_at = _date(row.get("checked_at"))
            evidence = (row.get("evidence_method") or "").strip()

            reason = detail = None
            if effective_to and effective_to < today:
                reason = "expired"
                detail = f"{effective_to} 에 만료됨"
            elif effective_to and effective_to <= today + timedelta(days=EXPIRING_WINDOW_DAYS):
                reason = "expiring_soon"
                detail = f"{effective_to} 만료 예정"
            elif status == "unknown":
                reason = "unknown"
                detail = "확정된 적이 없음 — 추천에 미확인 경고가 붙는다"
            elif status.startswith("confirmed") and evidence in WEAK_EVIDENCE:
                reason = "weak_evidence"
                detail = f"근거가 {evidence} 하나뿐"
            elif checked_at and (today - checked_at).days >= stale_days:
                reason = "stale"
                detail = f"마지막 확인 {(today - checked_at).days}일 전"
            if reason is None:
                continue
            queue.append({
                "priority": PRIORITY.index(reason) + 1,
                "reason": reason, "detail": detail,
                "parking_id": row.get("parking_id"), "name": row.get("name"),
                "day_group": row.get("day_group"), "access_status": status,
                "checked_at": row.get("checked_at"), "effective_to": row.get("effective_to"),
                "evidence_method": evidence, "evidence_ref": row.get("evidence_ref"),
            })
    queue.sort(key=lambda item: (item["priority"], int(item["parking_id"] or 0),
                                 item["day_group"] or ""))
    return queue


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=STALE_DAYS, help="노후 기준 일수")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    queue = build(stale_days=args.days)
    counts = {reason: sum(1 for item in queue if item["reason"] == reason)
              for reason in PRIORITY}
    print(f"재검증 대상 {len(queue)}행 · 주차장 {len({item['parking_id'] for item in queue})}곳")
    for reason in PRIORITY:
        if counts[reason]:
            print(f"  {reason:<14} {counts[reason]:>4}행")

    expired = counts["expired"]
    if expired:
        print(f"\n⚠️ 만료된 규칙 {expired}행 — 서비스가 지난 규칙을 쓰고 있다. 먼저 처리한다.")

    print()
    for item in queue[:args.limit]:
        print(f"  [{item['reason']:<13}] {item['parking_id']:>4} {(item['name'] or '')[:14]:<16} "
              f"{item['day_group']:<15} {item['detail']}")
    if len(queue) > args.limit:
        print(f"  … 외 {len(queue) - args.limit}행")

    if args.write and queue:
        TABLES.mkdir(parents=True, exist_ok=True)
        out = TABLES / "revalidation_queue.csv"
        with out.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(queue[0]))
            writer.writeheader()
            writer.writerows(queue)
        print(f"\n큐: {out}")
    return 1 if expired else 0


if __name__ == "__main__":
    sys.exit(main())
