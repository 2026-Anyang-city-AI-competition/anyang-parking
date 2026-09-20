#!/usr/bin/env bash
# 배포 직후의 최소 생존 검사. 데이터가 잠시 stale인 것은 롤백 사유가 아니지만,
# API 무응답·모델 로드 실패·백그라운드 갱신 중단·프런트 누락은 롤백한다.
set -euo pipefail

API_URL="${API_URL:-http://127.0.0.1:8000/api/v1/health}"
WEB_URL="${WEB_URL:-http://127.0.0.1/}"
# 1GB VM은 OS 캐시가 차가우면 50MB 모델 역직렬화와 초기 데이터 적재에 1분 이상
# 걸릴 수 있다. 30초만 기다리면 정상 릴리스를 실패로 오판하고 다시 모델을 읽게 된다.
ATTEMPTS="${ATTEMPTS:-60}"
WAIT_SEC="${WAIT_SEC:-2}"
last_body=""
last_web="unreachable"

for ((attempt=1; attempt<=ATTEMPTS; attempt++)); do
  body="$(curl -fsS --max-time 8 "$API_URL" 2>/dev/null || true)"
  last_body="$body"
  if [ -n "$body" ] && printf '%s' "$body" | python3 -c '
import json, sys
data = json.load(sys.stdin)
checks = data.get("checks") or {}
background = data.get("background_refresh") or {}
if checks.get("model") is not True:
    raise SystemExit(1)
if checks.get("model_bundle_current") is not True:
    raise SystemExit(1)
if background.get("running") is not True or background.get("last_error") is not None:
    raise SystemExit(1)
' 2>/dev/null; then
    if curl -fsS --max-time 8 "$WEB_URL" 2>/dev/null | grep -q '<div id="root"></div>'; then
      echo "배포 스모크 체크 통과 (시도 $attempt/$ATTEMPTS)"
      exit 0
    else
      last_web="root-missing"
    fi
  fi
  sleep "$WAIT_SEC"
done

echo "배포 스모크 체크 실패: API·모델·백그라운드 갱신·프런트 중 하나가 준비되지 않음" >&2
if [ -n "$last_body" ]; then
  printf '%s' "$last_body" | python3 -c '
import json, sys
data = json.load(sys.stdin)
checks = data.get("checks") or {}
background = data.get("background_refresh") or {}
print("health 요약:", json.dumps({
    "status": data.get("status"),
    "model": checks.get("model"),
    "model_bundle_current": checks.get("model_bundle_current"),
    "background_running": background.get("running"),
    "background_last_error": background.get("last_error"),
}, ensure_ascii=False, sort_keys=True))
' >&2 || echo "health 응답을 해석할 수 없음" >&2
else
  echo "health 응답 없음" >&2
fi
echo "frontend 상태: $last_web" >&2
exit 1
