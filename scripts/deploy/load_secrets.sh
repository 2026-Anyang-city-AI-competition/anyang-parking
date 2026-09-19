#!/usr/bin/env bash
# Secret Manager → EnvironmentFile. systemd 가 읽을 /etc/anyang-parking/api.env 를 만든다.
#
#   sudo bash scripts/deploy/load_secrets.sh
#
# ★ 키를 코드·유닛파일·이미지에 넣지 않는다. 여기서만 내려받고 파일 권한은 0600 이다.
# ★ 값을 echo 하지 않는다. 실패해도 값이 로그에 남으면 안 된다.
set -euo pipefail

PROJECT="${GCP_PROJECT:-anyang-parking-507119}"
TARGET_DIR=/etc/anyang-parking
TARGET="$TARGET_DIR/api.env"

# 이름 = Secret Manager 의 시크릿 이름, 값 = 환경변수 이름
SECRETS=(
  "kakao-rest-key:KAKAO_REST_KEY"
  "kakao-client-secret:KAKAO_CLIENT_SECRET"
  "session-pepper:SESSION_PEPPER"
)

command -v gcloud >/dev/null || { echo "gcloud 가 필요하다" >&2; exit 1; }
mkdir -p "$TARGET_DIR"
TMP="$(mktemp)"
chmod 600 "$TMP"
trap 'rm -f "$TMP"' EXIT

{
  echo "# scripts/deploy/load_secrets.sh 가 생성. 직접 고치지 말 것."
  echo "APP_ENV=production"
  echo "CORS_ORIGINS=${CORS_ORIGINS:?운영 도메인을 CORS_ORIGINS 로 지정해야 한다}"
  echo "KAKAO_REDIRECT_URI=${KAKAO_REDIRECT_URI:?콜백 주소를 지정해야 한다}"
} > "$TMP"

for entry in "${SECRETS[@]}"; do
  secret="${entry%%:*}"; var="${entry##*:}"
  if value="$(gcloud secrets versions access latest --secret="$secret" --project="$PROJECT" 2>/dev/null)"; then
    printf '%s=%s\n' "$var" "$value" >> "$TMP"
    echo "  가져옴: $var"          # 이름만 찍는다. 값은 찍지 않는다.
  else
    echo "  없음:   $var (선택 항목이면 무시해도 된다)" >&2
  fi
done

install -m 600 "$TMP" "$TARGET"
echo "작성: $TARGET (0600)"
echo "적용: sudo systemctl restart anyang-api"
