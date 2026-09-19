#!/usr/bin/env bash
# parking.db 백업·복구·잠금 점검.
#
#   bash scripts/backup_db.sh backup     # 스냅샷 생성 + 무결성 검사
#   bash scripts/backup_db.sh verify     # 최신 백업 검사
#   bash scripts/backup_db.sh restore <파일>
#   bash scripts/backup_db.sh locks      # 읽기/쓰기 잠금 상태
#
# ★★ **돌아가는 DB 를 cp 로 복사하면 깨진다.** 반드시 `sqlite3 .backup` 을 쓴다.
#    폴러가 5분마다 쓰는 중이라 중간 상태가 그대로 복사될 수 있다.
# ★  복구는 되돌릴 수 없으므로 현재 파일을 먼저 옆으로 치운다.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="${PARKING_DB:-$ROOT/data/raw/parking.db}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/data/backup}"
KEEP="${KEEP:-7}"

command -v sqlite3 >/dev/null || { echo "sqlite3 가 필요하다" >&2; exit 1; }

integrity() {
  local target="$1"
  local result
  result="$(sqlite3 "$target" "PRAGMA integrity_check;" 2>&1 | head -1)"
  [ "$result" = "ok" ] || { echo "무결성 실패: $result" >&2; return 1; }
  echo "  무결성 ok · $(sqlite3 "$target" 'SELECT COUNT(*) FROM obs;') obs행"
}

case "${1:-backup}" in
  backup)
    [ -f "$DB" ] || { echo "DB 가 없다: $DB" >&2; exit 1; }
    mkdir -p "$BACKUP_DIR"
    out="$BACKUP_DIR/parking-$(date '+%Y%m%dT%H%M%S').db"
    # .backup 은 쓰기 중에도 일관된 스냅샷을 만든다. cp 는 그러지 못한다.
    sqlite3 "$DB" ".backup '$out'"
    echo "백업: $out ($(du -h "$out" | cut -f1))"
    integrity "$out"
    # 오래된 것부터 지운다. 파일명이 시간순이라 정렬이 곧 순서다.
    ls -1t "$BACKUP_DIR"/parking-*.db 2>/dev/null | tail -n "+$((KEEP+1))" | while read -r old; do
      echo "  삭제: $(basename "$old")"; rm -f "$old"
    done
    ;;
  verify)
    latest="$(ls -1t "$BACKUP_DIR"/parking-*.db 2>/dev/null | head -1 || true)"
    [ -n "$latest" ] || { echo "백업이 없다" >&2; exit 1; }
    echo "최신 백업: $latest"
    integrity "$latest"
    ;;
  restore)
    src="${2:?복구할 백업 파일을 지정해야 한다}"
    [ -f "$src" ] || { echo "파일이 없다: $src" >&2; exit 1; }
    integrity "$src"
    echo "⚠️ 복구 전에 폴러를 멈춰야 한다: bash scripts/run_poll.sh stop"
    read -r -p "현재 DB 를 대체한다. 계속하려면 yes: " answer
    [ "$answer" = "yes" ] || { echo "취소"; exit 1; }
    if [ -f "$DB" ]; then
      aside="$DB.replaced-$(date '+%Y%m%dT%H%M%S')"
      mv "$DB" "$aside"
      echo "현재 파일을 옆으로: $aside"
    fi
    sqlite3 "$src" ".backup '$DB'"
    integrity "$DB"
    echo "복구 완료. 폴러를 다시 켠다: bash scripts/run_poll.sh"
    ;;
  locks)
    echo "journal_mode : $(sqlite3 "$DB" 'PRAGMA journal_mode;')"
    echo "busy_timeout : $(sqlite3 "$DB" 'PRAGMA busy_timeout;')"
    # 읽기가 쓰기를 막지 않으려면 WAL 이어야 한다.
    [ "$(sqlite3 "$DB" 'PRAGMA journal_mode;')" = "wal" ] \
      || echo "  ⚠️ WAL 이 아니다 — 폴링 중 읽기가 막힐 수 있다"
    echo "쓰는 프로세스:"; pgrep -fl "poll_parking.py" || echo "  없음"
    echo "읽는 프로세스:"; pgrep -fl "uvicorn src.serve.api" || echo "  없음"
    ;;
  *) echo "사용법: $0 {backup|verify|restore <파일>|locks}" >&2; exit 1 ;;
esac
