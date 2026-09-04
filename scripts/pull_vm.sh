#!/usr/bin/env bash
# GCP VM에서 폴링 DB를 안전하게 회수해 로컬 DB와 병합한다.
set -euo pipefail

VM_USER=chabee2027
VM_NAME=anyang-poller
ZONE=us-east1-c
PROJECT=anyang-parking-507119
REMOTE=/home/$VM_USER/anyang-parking/data/raw/parking.db
REMOTE_GITS=/home/$VM_USER/anyang-parking/data/raw/gits.db

# venv 가 있으면 그걸 쓴다. 시스템 python3 엔 pandas 가 없다.
PY="python3"
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"

mkdir -p data/raw

# ⚠️ 돌아가는 SQLite 를 그냥 cp 하면 깨진다. 반드시 .backup 으로 스냅샷을 뜬다.
echo "[1/4] VM에서 안전 스냅샷 생성 (.backup) — parking.db + gits.db"
gcloud compute ssh "$VM_USER@$VM_NAME" --zone="$ZONE" --project="$PROJECT" \
  --command "sqlite3 $REMOTE \".backup '/tmp/parking_snapshot.db'\" && ls -lh /tmp/parking_snapshot.db;
             if [ -f $REMOTE_GITS ]; then sqlite3 $REMOTE_GITS \".backup '/tmp/gits_snapshot.db'\" && ls -lh /tmp/gits_snapshot.db; else echo 'gits.db 없음(건너뜀)'; fi"

echo "[2/4] 안양 89곳 복사"
gcloud compute scp "$VM_USER@$VM_NAME:/tmp/parking_snapshot.db" \
  data/raw/parking_vm.db --zone="$ZONE" --project="$PROJECT"

echo "[3/4] GITS 813곳 복사"
gcloud compute scp "$VM_USER@$VM_NAME:/tmp/gits_snapshot.db" \
  data/raw/gits_vm.db --zone="$ZONE" --project="$PROJECT" || echo "gits 스냅샷 없음 — 건너뜀"

echo "[4/4] 병합 + 결측 점검"
"$PY" src/utils/merge_db.py data/raw/parking.db data/raw/parking_vm.db
if [ -f data/raw/gits_vm.db ]; then
  "$PY" src/utils/merge_db.py --tables gits_lots,gits_obs data/raw/gits.db data/raw/gits_vm.db
fi
"$PY" scripts/check_gaps.py
