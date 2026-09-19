#!/usr/bin/env python3
"""사용자 제보 검수 — 관리자용 CLI.

  .venv/bin/python scripts/review_reports.py list
  .venv/bin/python scripts/review_reports.py accept <report_id> "확인 필요"
  .venv/bin/python scripts/review_reports.py reject <report_id> "중복"
  .venv/bin/python scripts/review_reports.py summary

★ 통과시켜도 `parking_access_rules.csv` 는 바뀌지 않는다. 확정은 조사표로만 한다.
  통과한 제보는 재검증 큐가 참고하는 「다시 볼 곳」 목록에 들어갈 뿐이다.
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.serve import reports


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else "list"
    if command == "list":
        queue = reports.pending()
        print(f"검수 대기 {len(queue)}건")
        for item in queue:
            when = datetime.fromtimestamp(item["created_at"]).strftime("%m-%d %H:%M")
            print(f"  {item['report_id'][:8]} {when} 주차장 {item['parking_id']:>4} "
                  f"[{item['label']}] {item['message'][:40]}")
        return 0
    if command == "summary":
        print(reports.summary())
        print("통과한 제보가 가리키는 주차장:", reports.accepted_lots())
        return 0
    if command in ("accept", "reject"):
        if len(sys.argv) < 3:
            print("report_id 가 필요하다", file=sys.stderr)
            return 1
        note = sys.argv[3] if len(sys.argv) > 3 else ""
        try:
            result = reports.review(sys.argv[2],
                                    "accepted" if command == "accept" else "rejected", note)
        except reports.InvalidReport as exc:
            print(exc, file=sys.stderr)
            return 1
        print(result)
        if command == "accept":
            print("→ CSV 는 바뀌지 않았다. 조사표로 확정한 뒤 재검증 큐에서 확인한다.")
        return 0
    print(__doc__, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
