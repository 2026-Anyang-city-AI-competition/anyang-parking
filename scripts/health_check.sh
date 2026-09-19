#!/usr/bin/env bash
# /health 를 모니터링에 연결하는 훅. cron·uptime 감시·systemd timer 어디서든 쓴다.
#
#   bash scripts/health_check.sh                       # 로컬
#   API_URL=https://예시/api/v1/health bash scripts/health_check.sh
#
# 종료코드: 0=ok · 1=degraded · 2=응답 없음
# ⚠️ 응답 본문을 그대로 외부로 넘기지 않는다. 사람이 읽을 요약만 찍는다.
set -uo pipefail

URL="${API_URL:-http://127.0.0.1:8000/api/v1/health}"
TIMEOUT="${TIMEOUT:-10}"

body="$(curl -fsS --max-time "$TIMEOUT" "$URL" 2>/dev/null)" || {
  echo "CRITICAL: /health 에 응답이 없다 ($URL)"; exit 2; }

read -r status model fresh rules gate err <<<"$(printf '%s' "$body" | python3 -c '
import json,sys
d=json.load(sys.stdin)
c=d.get("checks",{})
m=d.get("metrics",{}).get("external",{})
worst=max((v.get("error_rate",0) for v in m.values()), default=0)
print(d.get("status"), c.get("model"), c.get("observations_fresh"),
      c.get("access_rules"), c.get("prediction_gate"), worst)
')"

echo "status=$status model=$model fresh=$fresh rules=$rules gate=$gate 외부실패율=$err"
[ "$status" = "ok" ] && exit 0
echo "WARNING: degraded — 위 항목 중 false 인 것을 확인한다"
exit 1
