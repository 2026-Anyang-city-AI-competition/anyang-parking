#!/usr/bin/env bash
# poll_parking.py 를 nohup 백그라운드로 실행한다.
# 이미 떠 있으면 중복 실행하지 않는다 (폴링이 겹치면 DB 중복 행 + API 부담).
#
#   bash scripts/run_poll.sh          # 시작
#   bash scripts/run_poll.sh status   # 상태
#   bash scripts/run_poll.sh stop     # 중지
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="src/collect/poll_parking.py"
LOG="$ROOT/logs/poll.log"
PIDFILE="$ROOT/logs/poll.pid"
PATTERN="[p]ython.*poll_parking\.py"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x "$ROOT/.venv/bin/python" ]; then PYTHON="$ROOT/.venv/bin/python"; else PYTHON="python3"; fi
fi

mkdir -p "$ROOT/logs"

# 살아 있는 폴러의 PID 를 출력한다 (없으면 빈 문자열).
running_pid() {
  if [ -f "$PIDFILE" ]; then
    local pid
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      echo "$pid"; return 0
    fi
  fi
  # PID 파일이 없거나 낡았으면 프로세스 목록에서 직접 찾는다
  pgrep -f "$PATTERN" 2>/dev/null | head -n 1 || true
}

case "${1:-start}" in
  start)
    PID="$(running_pid)"
    if [ -n "$PID" ]; then
      echo "이미 실행 중 (PID $PID). 중복 실행하지 않는다."
      echo "  로그: tail -f $LOG"
      exit 0
    fi

    if [ ! -s "$ROOT/$TARGET" ]; then
      echo "오류: $TARGET 가 비어 있다. 폴러 코드를 먼저 붙여넣을 것." >&2
      exit 1
    fi

    cd "$ROOT"
    nohup "$PYTHON" -u "$TARGET" >> "$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
      echo "폴링 시작 (PID $(cat "$PIDFILE"))"
      echo "  로그: tail -f $LOG"
    else
      echo "시작 직후 종료됐다. 로그 확인: $LOG" >&2
      tail -n 20 "$LOG" >&2 || true
      rm -f "$PIDFILE"
      exit 1
    fi
    ;;

  status)
    PID="$(running_pid)"
    if [ -n "$PID" ]; then
      echo "실행 중 (PID $PID)"
      [ -f "$LOG" ] && tail -n 5 "$LOG"
    else
      echo "실행 중이 아니다."
      exit 1
    fi
    ;;

  stop)
    PID="$(running_pid)"
    if [ -z "$PID" ]; then
      echo "실행 중이 아니다."
      rm -f "$PIDFILE"
      exit 0
    fi
    kill "$PID"
    for _ in $(seq 1 10); do
      kill -0 "$PID" 2>/dev/null || break
      sleep 1
    done
    kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "중지 (PID $PID)"
    ;;

  *)
    echo "사용법: bash scripts/run_poll.sh [start|status|stop]" >&2
    exit 2
    ;;
esac
