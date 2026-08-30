#!/usr/bin/env bash
# GCP VM에서 폴링 DB를 안전하게 회수해 로컬 DB와 병합한다.
set -euo pipefail

VM_USER=chabee2027
VM_NAME=anyang-poller
ZONE=us-east1-c
PROJECT=anyang-parking-507119
REMOTE=/home/$VM_USER/anyang-parking/data/raw/parking.db

mkdir -p data/raw

echo "[1/3] VM에서 안전 스냅샷 생성 (.backup)"
gcloud compute ssh "$VM_USER@$VM_NAME" --zone="$ZONE" --project="$PROJECT" \
  --command "sqlite3 $REMOTE \".backup '/tmp/parking_snapshot.db'\" && ls -lh /tmp/parking_snapshot.db"

echo "[2/3] 로컬로 복사"
gcloud compute scp "$VM_USER@$VM_NAME:/tmp/parking_snapshot.db" \
  data/raw/parking_vm.db --zone="$ZONE" --project="$PROJECT"

echo "[3/3] 병합 + 결측 점검"
python3 src/utils/merge_db.py data/raw/parking.db data/raw/parking_vm.db
python3 scripts/check_gaps.py
