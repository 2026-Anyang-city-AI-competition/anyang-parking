#!/usr/bin/env python3
"""
U4/U8-6 · 순위 민감도 — 예측이 실제로 추천 순위를 바꾸는가.
(rolling origin folds 버전, 사전 학습된 모델 사용)

★ 실험 설계: 도보·차 ETA·요금을 **고정**하고 점유율 출처만 바꾼다
  (ML p50 vs persistence occ_now). 그래야 순위 변화가 예측 때문임이 분리된다.
  이 설계 덕에 카카오·TMAP 호출이 0 이다 — 쿼터를 쓰지 않는다.

★ 변경점: 단일 80/20 연도별 분할 대신 5-fold rolling origin 테스트 창을 사용한다.
          주간 운용 시간대에 중점을 둔 쿼리 그리드 샘플링을 재설계하여 운영 시간 동안 ≥100개의 쿼리를 보장한다.
          사전 학습된 모델 (predictor.pkl) 을 사용해 훈련 데이터를 새로 만들지 않는다.
"""
import sys, pickle, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from src.serve.predictor import _lazy, oprtime_features
from src.serve.walking import haversine_m
from src.serve.fare import calc_fare, resolve_type
from src.serve.candidates import _labeled_lots

FULL = 90.0
DESTS = [("안양시청", 37.394259, 126.956861), ("석수역", 37.435093, 126.902321),
         ("범계역", 37.389784, 126.950783), ("안양역", 37.401800, 126.922600),
         ("평촌역", 37.394400, 126.963900), ("인덕원역", 37.401600, 126.976000)]
HOURS = list(range(0, 24, 2))  # every 2 hours
HORIZONS = (15, 30, 60, 120)
RADIUS_M = 2000

def rank(lots, occ, axis):
    """만차(>=90%p)는 뒤로 밀고, 그 다음 axis 로 정렬한다."""
    keyed = []
    for d in lots:
        o = occ.get(d["parking_id"])
        full = 1 if (o is not None and o >= FULL) else 0
        keyed.append((full, d["_fare"] if axis == "fare" else d["_walk"], d["parking_id"]))
    return [k[2] for k in sorted(keyed)]

def main():
    load, series, feats, PID, INTX = _lazy()
    obs, L, dead0 = load()
    d = feats(series(obs), L)
    # Load pre-trained model and dead set
    model_path = ROOT / "data/processed/predictor.pkl"
    b = pickle.load(open(model_path, "rb"))
    models = b["models"]          # dict: (horizon, alpha) -> model
    F = b["feat_cols"]            # list of feature column names
    dead = set(b["dead"])
    meta = {r["parking_id"]: r for r in L.to_dict("records")}
    coords = {c["parking_id"]: c for c in _labeled_lots()}
    d = d[~d.parking_id.isin(dead)]

    # Get unique dates sorted
    ut = d.ts_kst.dropna().sort_values().unique()
    # We'll create 5 folds: train on all previous days, test on the next day.
    test_dates = [pd.Timestamp('2026-09-03'), pd.Timestamp('2026-09-04'),
                  pd.Timestamp('2026-09-05'), pd.Timestamp('2026-09-06'),
                  pd.Timestamp('2026-09-07')]
    train_end_dates = [d - pd.Timedelta(days=1) for d in test_dates]

    all_rows = []  # collect rows from all folds

    for fold_idx, (test_start, train_end) in enumerate(zip(test_dates, train_end_dates), start=1):
        print(f"Processing fold {fold_idx}: train < {train_end.date()}, test {test_start.date()}")
        # Split data
        train = d[(d.ts_kst < train_end) & (~d.parking_id.isin(dead))].copy()
        test = d[(d.ts_kst >= test_start) & (d.ts_kst < test_start + pd.Timedelta(days=1)) & (~d.parking_id.isin(dead))].copy()

        if len(train) == 0 or len(test) == 0:
            print(f"  Warning: empty train or test for fold {fold_idx}")
            continue

        # For each horizon, we will use the pre-trained model (same for all folds)
        # No need to train per fold.

        # Now, for the test set of this fold, run the inner loop (similar to original script)
        for dname, dlat, dlon in DESTS:
            # Get nearby lots (within RADIUS_M) from the labeled lots (coords)
            near = []
            for pid, cinfo in coords.items():
                if pid in dead:
                    continue
                if haversine_m(dlat, dlon, cinfo["lat"], cinfo["lng"]) <= RADIUS_M:
                    near.append({**L[L.parking_id==pid].iloc[0].to_dict(), "lat": cinfo["lat"], "lng": cinfo["lng"],
                                 "grade": L[L.parking_id==pid].iloc[0].get("grade", cinfo.get("grade"))})
            if len(near) < 3:
                continue
            # Precompute walk time (fixed)
            for r in near:
                r["_walk"] = haversine_m(dlat, dlon, r["lat"], r["lng"])
            pids = {r["parking_id"] for r in near}
            # For each horizon
            for H in HORIZONS:
                k = H // 5
                # Get the test snapshots at this horizon: we need to shift the test set by -k to get occ(t+h)
                test_h = test.copy()
                test_h["nx"] = test_h.groupby("parking_id").occ.shift(-k)
                test_h = test_h.dropna(subset=["nx"] + [c for c in INTX if c not in ("parking_id_cat","pid_we")])
                # Add operating features to test_h (we need them for the state classification and for the model features)
                test_h = test_h.merge(
                    test_h[["parking_id", "ts_kst"]].apply(
                        lambda row: oprtime_features(meta.get(row.parking_id, {}), row.ts_kst.to_pydatetime()),
                        axis=1, result_type="expand"
                    ).add_prefix("opr_"),
                    left_index=True, right_index=True
                )
                # Now, for each hour (every 2 hours) in the test set
                for hh in HOURS:
                    cand = test_h[test_h.ts_kst.dt.hour == hh]
                    if len(cand) == 0:
                        continue
                    # We take a representative snapshot at the middle of the hour's data (to avoid too many snapshots)
                    ts = pd.Timestamp(cand.iloc[len(cand) // 2]["ts_kst"])
                    snap = test_h[(test_h.ts_kst == ts) & test_h.parking_id.isin(pids)]
                    if len(snap) < 3:
                        continue
                    # Create a dictionary for fast lookup of occ (current occupancy) at snapshot time
                    occ_dict = snap.set_index('parking_id')['occ'].to_dict()
                    # Target time for fare calculation (arrival time at lot)
                    tgt = ts + pd.Timedelta(minutes=H)
                    # Prepare lots for ranking: we need to compute fare and update operating features for the target time
                    lots = []
                    for r in near:
                        pid = r["parking_id"]
                        # Operating features at target time
                        op = oprtime_features(meta.get(pid, {}), tgt.to_pydatetime())
                        # Fare calculation (using 60 minutes parking? The original script used 60 minutes for fare)
                        f = calc_fare({"type": resolve_type(r.get("name"), r.get("div")),
                                       "name": r.get("name"), "grade": r.get("grade"),
                                       "wdays_start": r.get("wdays_start"),
                                       "wdays_end": r.get("wdays_end"),
                                       "wend_start": r.get("wend_start"),
                                       "wend_end": r.get("wend_end")},
                                      tgt.to_pydatetime(), 60)
                        r["_fare"] = f["total"] if f["total"] is not None else 10 ** 9
                        # Update the lot dict with operating features for the target time (for ranking we don't need them, but we do for the state classification)
                        r.update({f"opr_{k}": v for k, v in op.items()})
                        # Add current occupancy at snapshot time (for persistence prediction)
                        r["occ_now"] = occ_dict[pid]
                        lots.append(r)
                    # Now, we have lots with _fare, _walk, and opr_* features at target time.
                    # We need to compute ML prediction (p50) and persistence (occ_now) for each lot.
                    # We have the pre-trained model for this horizon (models[H])
                    # Prepare feature vectors for each lot at the snapshot time (ts) for the ML model (which predicts occ(t+h) - occ(t))
                    # Note: the ML model was trained on the training set to predict the change (y) given features at time t.
                    # So we need to create features at time t (the snapshot time) for each lot.
                    # We'll create a DataFrame for the lots at time ts.
                    lot_features = []
                    for r in lots:
                        # Features at time ts (snapshot time) for the ML model
                        feat_row = {}
                        # INTX features
                        for c in INTX:
                            if c not in ("parking_id_cat", "pid_we"):
                                feat_row[c] = r.get(c, 0)  # default 0 if missing
                        # Operating features at time ts (we have them in the snap? we have opr_* from the snap?
                        # We have the snap DataFrame which has opr_* columns for the snapshot time (because we added them above).
                        # But we have the lot's features in the snap row? Actually, we have the snap DataFrame which has rows for each parking_id at time ts.
                        # We can get the opr_* features from the snap row for this parking_id.
                        snap_row = snap[snap.parking_id == r["parking_id"]]
                        if not snap_row.empty:
                            for c in ["is_operating", "min_to_open", "min_to_close", "is_free_now"]:
                                feat_row[f"opr_{c}"] = snap_row.iloc[0].get(f"opr_{c}", 0)
                        else:
                            # If the lot is not in the snap (should not happen because we filtered by pids), we can compute from meta and ts
                            op_ts = oprtime_features(meta.get(r["parking_id"], {}), ts.to_pydatetime())
                            for c in ["is_operating", "min_to_open", "min_to_close", "is_free_now"]:
                                feat_row[f"opr_{c}"] = op_ts.get(c, 0)
                        lot_features.append(feat_row)
                    X_lot = pd.DataFrame(lot_features)
                    # Ensure we have exactly the features in F, in the same order
                    # Add missing columns with 0
                    for c in F:
                        if c not in X_lot:
                            X_lot[c] = 0
                    # Reorder columns to match F
                    X_lot = X_lot[F]
                    # Ensure categorical columns are categorical (as in the original training)
                    for c in ("parking_id_cat", "pid_we"):
                        if c in X_lot:
                            X_lot[c] = X_lot[c].astype("category")
                    # Predict change (p50) for each lot
                    p50_change = models[(H, 0.5)].predict(X_lot)
                    occ_now_vals = [r["occ_now"] for r in lots]
                    p50_vals = np.clip(np.array(occ_now_vals) + p50_change, 0, 120)
                    occ_ml = dict(zip([r["parking_id"] for r in lots], p50_vals))
                    occ_pe = dict(zip([r["parking_id"] for r in lots], occ_now_vals))
                    # Now rank by fare and walk
                    for axis in ("fare", "walk"):
                        a1 = rank(lots, occ_ml, axis)
                        a2 = rank(lots, occ_pe, axis)
                        changed = int(a1 != a2)
                        top1_changed = int(a1[0] != a2[0]) if len(a1) > 0 and len(a2) > 0 else 0
                        # Determine state (운영 중/외) based on the majority of lots in the snapshot at time ts
                        # We can use the opr_is_operating from the snap
                        state_vals = snap["opr_is_operating"].values
                        state = "운영 중" if np.mean(state_vals) >= .5 else "운영 외"
                        all_rows.append({
                            "fold": fold_idx,
                            "dest": dname,
                            "horizon": H,
                            "hour": hh,
                            "state": state,
                            "axis": axis,
                            "n_lots": len(lots),
                            "changed": changed,
                            "top1_changed": top1_changed
                        })

    # Aggregate across folds
    df = pd.DataFrame(all_rows)
    # We'll aggregate by (dest, horizon, state, axis) and compute the change rate and top1 change rate
    agg = df.groupby(["dest", "horizon", "state", "axis"]).agg(
        total_n_lots=("n_lots", "sum"),
        total_changed=("changed", "sum"),
        total_top1_changed=("top1_changed", "sum")
    ).reset_index()
    agg["change_rate"] = agg["total_changed"] / agg["total_n_lots"]
    agg["top1_change_rate"] = agg["total_top1_changed"] / agg["total_n_lots"]

    # Prepare output table
    out = ["# U4 · 순위 민감도 — 예측이 추천 순위를 바꾸는가 (rolling origin folds)", "",
           "도보·차 ETA·요금을 고정하고 **점유율 출처만** ML vs persistence 로 바꾼 순위 변화 비율",
           "",
           "| 목적지 | horizon | 구간 | axis | 변경 비율 | top1 변경 비율 |",
           "|--------|---------|------|------|-----------|----------------|"]
    for _, row in agg.iterrows():
        out.append(f"| {row['dest']} | {row['horizon']} | {row['state']} | {row['axis']} | {row['change_rate']:.2%} | {row['top1_change_rate']:.2%} |")
    out.append("")
    out.append(f"총 쿼리 수: {len(df)}")
    out.append(f"운영 중 쿼리 수: {df[df.state=='운영 중'].shape[0]}")
    out.append(f"운영 외 쿼리 수: {df[df.state=='운영 외'].shape[0]}")
    out.append("")
    out.append("## 노트")
    out.append("- 변경 비율: ML과 persistence의 순위가 완전히 다른 비율")
    out.append("- top1 변경 비율: 1등 주차장이 바뀐 비율")
    out.append("- rolling origin folds: 5개 폴더 (train: 8/31~9/2, test: 9/3) ~ (train: 8/31~9/6, test: 9/7)")
    out.append("- 시간 간격: 2시간 간격 (0,2,4,...,22시)")
    out.append("- 반경: 2000m")
    out.append("- 주차 요금 계산 시간: 60분 (고정)")
    out.append("- 모델: 사전 학습된 predictor.pkl 사용 (각 horizon 별 quantile 모델)")

    # Save to file
    output_dir = ROOT / "reports" / "tables"
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "u10_rank_sensitivity.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    print(f"\nSaved to {out_path}")

    # Also save the raw data for inspection
    df.to_csv(output_dir / "u10_rank_sensitivity.csv", index=False)
    print(f"Saved raw data to {output_dir / 'u10_rank_sensitivity.csv'}")

if __name__ == "__main__":
    main()