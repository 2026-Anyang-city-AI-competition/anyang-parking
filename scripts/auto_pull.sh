#!/usr/bin/env bash
# VM DB 자동 회수 — cron 에서 3시간마다 호출한다.
#
#   crontab:  0 */3 * * * /Users/.../scripts/auto_pull.sh
#
# cron 은 대화형 셸 환경을 물려받지 않는다. PATH·HOME 을 직접 준다.
# 노트북이 자거나 꺼져 있으면 그 주기는 그냥 건너뛴다(cron 은 못 채운다).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

export HOME="${HOME:-$(eval echo ~$(id -un))}"
export PATH="/opt/homebrew/bin:/opt/local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

LOG="$ROOT/logs/auto_pull.log"
LOCK="$ROOT/logs/auto_pull.lock"
mkdir -p "$ROOT/logs"

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

# ── 중복 실행 방지 (macOS 엔 flock 이 없다. 디렉터리 mkdir 이 원자적) ──
if ! mkdir "$LOCK" 2>/dev/null; then
    if [ -f "$LOCK/pid" ] && kill -0 "$(cat "$LOCK/pid")" 2>/dev/null; then
        log "이미 실행 중 (PID $(cat "$LOCK/pid")) — 건너뜀"; exit 0
    fi
    log "낡은 잠금 제거"; rm -rf "$LOCK"; mkdir "$LOCK" 2>/dev/null || exit 1
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

# ── 로그가 무한정 자라지 않게 ──
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 2000000 ]; then
    tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
    log "로그 회전(2MB 초과)"
fi

if ! command -v gcloud >/dev/null 2>&1; then
    log "❌ gcloud 를 찾을 수 없다. PATH=$PATH"; exit 1
fi

log "회수 시작"
OUT="$(bash scripts/pull_vm.sh 2>&1)"
RC=$?
echo "$OUT" | sed 's/^/    /' >> "$LOG"

PY="python3"; [ -x "$ROOT/.venv/bin/python" ] && PY="$ROOT/.venv/bin/python"
AGE="$("$PY" - <<'PY' 2>/dev/null
import sqlite3
from datetime import datetime, timezone, timedelta
KST=timezone(timedelta(hours=9)); out=[]
for db,t in (("data/raw/parking.db","obs"),("data/raw/gits.db","gits_obs")):
    try:
        m=sqlite3.connect(db).execute(f"SELECT MAX(ts_kst) FROM {t}").fetchone()[0]
        h=(datetime.now(KST)-datetime.fromisoformat(m)).total_seconds()/3600
        out.append(f"{db.split('/')[-1]} {h:.1f}h")
    except Exception as e: out.append(f"{db.split('/')[-1]} ?({str(e)[:30]})")
print(" · ".join(out))
PY
)"
if [ $RC -eq 0 ]; then log "✅ 완료 · 최신성: $AGE"; else log "❌ 실패(rc=$RC) · 최신성: $AGE"; fi
exit $RC
