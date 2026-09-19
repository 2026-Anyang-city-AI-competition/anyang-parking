#!/usr/bin/env bash
# 매일 확인용 — dev_todo 8-6 의 다섯 가지를 한 번에 본다.
#
#   1. 최신성      watch_freshness.py   (로컬 DB 가 낡았는가)
#   2. 연속성·결측  daily_data_audit.py  (폴링 간격이 5분 격자를 지켰는가)
#   3. 고정값      daily_data_audit.py  (값이 안 움직이는 곳이 늘었는가)
#   4. 정원 초과    daily_data_audit.py
#   5. 급변        daily_data_audit.py
#
# ★ 하나라도 걸리면 0이 아닌 코드로 끝난다. cron 이 그걸 보고 알 수 있어야 한다.
#   `set -e` 를 쓰지 않는 이유가 이것이다 — 앞 검사에서 멈추면 뒤를 못 본다.
set -uo pipefail
status=0
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bash "$ROOT/scripts/run_poll.sh" status || true
DB="$ROOT/data/raw/parking.db"
if [ -f "$DB" ]; then
  echo "DB 크기: $(du -h "$DB" | cut -f1)"
  echo "최종 수정: $(date -r "$DB" '+%Y-%m-%d %H:%M:%S')"
else
  echo "경고: $DB 가 아직 없다."
fi

# 값 품질까지 본다 — 연속성·고정값·정원 초과·급변. 문제가 있으면 0이 아닌 코드로 끝난다.
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then PYTHON="$ROOT/.venv/bin/python"; else PYTHON="python3"; fi
fi
echo "── 1. 로컬 DB 최신성 ──"
"$PYTHON" "$ROOT/scripts/watch_freshness.py" || status=1

echo
echo "── 2~5. 값 품질 (연속성·고정값·정원 초과·급변) ──"
"$PYTHON" "$ROOT/scripts/daily_data_audit.py" --write || status=1

echo
if [ "$status" -ne 0 ]; then
  echo "⚠️ 확인이 필요한 항목이 있다."
else
  echo "이상 없음."
fi
exit "$status"
