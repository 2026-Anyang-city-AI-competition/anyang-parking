#!/usr/bin/env bash
# CI가 올린 검증 완료 번들을 운영 VM에 적용한다. 실패하면 직전 코드와 웹을 되돌린다.
set -Eeuo pipefail

ARCHIVE="${1:?release.tgz 경로가 필요합니다}"
REVISION="${2:?Git revision이 필요합니다}"
APP_DIR="${APP_DIR:-/home/chabee2027/anyang-parking}"
WEB_ROOT="${WEB_ROOT:-/var/www/anyang-parking}"
DEPLOY_ROOT="${DEPLOY_ROOT:-/home/chabee2027/anyang-deployments}"
OWNER="${DEPLOY_OWNER:-chabee2027}"
KEEP_BACKUPS="${KEEP_BACKUPS:-5}"

if [[ ! "$REVISION" =~ ^[0-9a-f]{7,64}$ ]]; then
  echo "revision 형식이 잘못됐습니다" >&2
  exit 2
fi
if [ ! -f "$ARCHIVE" ]; then
  echo "배포 번들이 없습니다: $ARCHIVE" >&2
  exit 2
fi

mkdir -p "$DEPLOY_ROOT/backups" "$DEPLOY_ROOT/staging"
STAGE="$(mktemp -d "$DEPLOY_ROOT/staging/${REVISION}.XXXXXX")"
BACKUP="$DEPLOY_ROOT/backups/$(date -u +%Y%m%dT%H%M%SZ)-${REVISION:0:12}"
ROLLED_BACK=0
APPLY_STARTED=0

cleanup() {
  case "$STAGE" in
    "$DEPLOY_ROOT"/staging/*) rm -rf -- "$STAGE" ;;
  esac
}

rollback() {
  code=$?
  trap - ERR
  if [ "$APPLY_STARTED" -eq 1 ] && [ "$ROLLED_BACK" -eq 0 ] && [ -d "$BACKUP/app" ]; then
    ROLLED_BACK=1
    echo "배포 실패 — 직전 릴리스로 롤백합니다" >&2
    set +e
    rsync -a --delete "$BACKUP/app/src/" "$APP_DIR/src/"
    rsync -a --delete "$BACKUP/app/scripts/" "$APP_DIR/scripts/"
    rsync -a --delete "$BACKUP/app/web-dist/" "$APP_DIR/web/dist/"
    rsync -a --delete "$BACKUP/web/" "$WEB_ROOT/"
    if [ -f "$BACKUP/requirements-api.txt" ]; then
      install -m 0644 "$BACKUP/requirements-api.txt" "$APP_DIR/requirements-api.txt"
    fi
    if [ -f "$BACKUP/deploy-revision" ]; then
      install -m 0644 "$BACKUP/deploy-revision" "$APP_DIR/.deploy-revision"
    else
      rm -f -- "$APP_DIR/.deploy-revision"
    fi
    chown -R "$OWNER:$OWNER" "$APP_DIR/src" "$APP_DIR/scripts" "$APP_DIR/web/dist"
    systemctl reset-failed anyang-api
    systemctl restart anyang-api
    nginx -t && systemctl reload nginx
    set -e
  fi
  cleanup
  exit "$code"
}
trap rollback ERR
trap cleanup EXIT

# 절대경로와 상위 경로가 든 아카이브는 풀지 않는다.
python3 - "$ARCHIVE" <<'PY'
import sys, tarfile
with tarfile.open(sys.argv[1], "r:gz") as archive:
    for member in archive.getmembers():
        name = member.name
        if name.startswith("/") or ".." in name.split("/"):
            raise SystemExit(f"unsafe archive member: {name}")
PY
tar -xzf "$ARCHIVE" -C "$STAGE"

for required in src/serve/api.py scripts/deploy/deploy_release.sh \
                scripts/deploy/deploy_smoke_check.sh web/dist/index.html requirements-api.txt; do
  if [ ! -e "$STAGE/$required" ]; then
    echo "배포 번들 필수 파일 누락: $required" >&2
    exit 2
  fi
done

mkdir -p "$BACKUP/app/web-dist" "$BACKUP/web"
cp -a "$APP_DIR/src" "$BACKUP/app/src"
cp -a "$APP_DIR/scripts" "$BACKUP/app/scripts"
if [ -d "$APP_DIR/web/dist" ]; then
  rsync -a "$APP_DIR/web/dist/" "$BACKUP/app/web-dist/"
fi
cp -a "$WEB_ROOT/." "$BACKUP/web/"
cp -a "$APP_DIR/requirements-api.txt" "$BACKUP/requirements-api.txt"
if [ -f "$APP_DIR/.deploy-revision" ]; then
  cp -a "$APP_DIR/.deploy-revision" "$BACKUP/deploy-revision"
fi

APPLY_STARTED=1
systemctl stop anyang-api
rsync -a --delete "$STAGE/src/" "$APP_DIR/src/"
rsync -a --delete "$STAGE/scripts/" "$APP_DIR/scripts/"
mkdir -p "$APP_DIR/web/dist"
rsync -a --delete "$STAGE/web/dist/" "$APP_DIR/web/dist/"
install -m 0644 "$STAGE/requirements-api.txt" "$APP_DIR/requirements-api.txt"
printf '%s\n' "$REVISION" > "$APP_DIR/.deploy-revision"
chown -R "$OWNER:$OWNER" "$APP_DIR/src" "$APP_DIR/scripts" "$APP_DIR/web/dist" \
  "$APP_DIR/requirements-api.txt" "$APP_DIR/.deploy-revision"

"$APP_DIR/.venv/bin/python" -m pip install --disable-pip-version-check -q \
  -r "$APP_DIR/requirements-api.txt"
"$APP_DIR/.venv/bin/python" -m compileall -q "$APP_DIR/src"

mkdir -p "$WEB_ROOT"
rsync -a --delete --delay-updates "$STAGE/web/dist/" "$WEB_ROOT/"
chown -R www-data:www-data "$WEB_ROOT"
nginx -t
systemctl reset-failed anyang-api
systemctl start anyang-api
systemctl reload nginx

API_URL=http://127.0.0.1:8000/api/v1/health \
WEB_URL=http://127.0.0.1/ \
  bash "$APP_DIR/scripts/deploy/deploy_smoke_check.sh"

# 성공한 뒤에만 오래된 백업을 명시적으로 지운다.
mapfile -t backups < <(find "$DEPLOY_ROOT/backups" -mindepth 1 -maxdepth 1 -type d | sort)
if [ "${#backups[@]}" -gt "$KEEP_BACKUPS" ]; then
  remove_count=$((${#backups[@]} - KEEP_BACKUPS))
  for ((i=0; i<remove_count; i++)); do
    old="${backups[$i]}"
    case "$old" in
      "$DEPLOY_ROOT"/backups/*) rm -rf -- "$old" ;;
    esac
  done
fi

trap - ERR
echo "배포 완료: $REVISION"
