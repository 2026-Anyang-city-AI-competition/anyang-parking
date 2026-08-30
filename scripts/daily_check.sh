#!/usr/bin/env bash
# 매일 확인용: 폴러가 살아 있는지 + DB 가 실제로 자라고 있는지.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash "$ROOT/scripts/run_poll.sh" status || true
DB="$ROOT/data/raw/parking.db"
if [ -f "$DB" ]; then
  echo "DB 크기: $(du -h "$DB" | cut -f1)"
  echo "최종 수정: $(date -r "$DB" '+%Y-%m-%d %H:%M:%S')"
else
  echo "경고: $DB 가 아직 없다."
fi
