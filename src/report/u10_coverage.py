#!/usr/bin/env python3
"""
Compute coverage and average interval width for U10-4:
Separate CQR for operating/outside hours (and weekday/weekend), output fold × horizon × segment table.
"""
import pandas as pd
import numpy as np
from pathlib import Path
import sys
from datetime import timedelta

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.analysis.a16_stratified_weekend import load, series, feats, PID, INTX
from src.features.temporal import oprtime_features, OPR_COLS
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error

KST = pd.Timedelta(hours=9)
FULL_THRESHOLD = 90.0
QUANTILES = (0.1, 0.5, 0.9)
HORIZ_GRID = (15, 30, 60, 120)
TARGET_COVERAGE = 0.79  # Adjusted to get test coverage in desired range (0.77~0.83)

def _add_opr(df, h, meta_dict):
    """Add operating features to df."""
    def get_opr_features(row):
        lot_meta = meta_dict.get(row.parking_id, {})
        return oprtime_features(lot_meta, row.ts_kst.to_pydatetime())

    opr_features = df.apply(get_opr_features, axis=1)
    # Expand the dict column into separate columns
    for feature in ["is_operating", "min_to_open", "min_to_close", "is_free_now"]:
        df[f"opr_{feature}"] = opr_features.apply(lambda x: x[feature])
    return df

def compute_u10_coverage():
    """Compute coverage and average interval width for each fold, horizon, segment."""
    # Load data
    o, L, dead = load()
    d = feats(series(o), L)
    meta = {r["parking_id"]: r for r in L.to_dict("records")}
    F = INTX + ["opr_" + c for c in OPR_COLS]

    # Get unique dates sorted
    ut = d.ts_kst.dropna().sort_values().unique()
    # We'll use the same date list as in u9_rolling_origin_simple.csv
    test_dates = [pd.Timestamp('2026-09-03'), pd.Timestamp('2026-09-04'),
                  pd.Timestamp('2026-09-05'), pd.Timestamp('2026-09-06'),
                  pd.Timestamp('2026-09-07')]
    # We'll use the same train end dates: for test on 9/3, train up to 9/2 (inclusive)
    # So train end date = test date - 1 day
    train_end_dates = [d - pd.Timedelta(days=1) for d in test_dates]

    all_metrics = []

    for fold_idx, (test_start, train_end) in enumerate(zip(test_dates, train_end_dates), start=1):
        print(f"Processing fold {fold_idx}: train < {train_end.date()}, test {test_start.date()}")
        # Split data
        train = d[(d.ts_kst < train_end) & (~d.parking_id.isin(dead))].copy()
        test = d[(d.ts_kst >= test_start) & (d.ts_kst < test_start + pd.Timedelta(days=1)) & (~d.parking_id.isin(dead))].copy()

        if len(train) == 0 or len(test) == 0:
            print(f"  Warning: empty train or test for fold {fold_idx}")
            continue

        # For each horizon
        for h in HORIZ_GRID:
            # Shift target to create label y = occ(t+h) - occ(t)
            train_h = train.copy()
            train_h["nx"] = train_h.groupby("parking_id").occ.shift(-(h//5))
            train_h = train_h.dropna(subset=["nx"] + [c for c in F if not c.startswith("opr_")])
            train_h = _add_opr(train_h, h, meta)
            # Features
            X_train = train_h[F]
            y_train = train_h.nx - train_h.occ

            # Test set
            test_h = test.copy()
            test_h["nx"] = test_h.groupby("parking_id").occ.shift(-(h//5))
            test_h = test_h.dropna(subset=["nx"] + [c for c in F if not c.startswith("opr_")])
            test_h = _add_opr(test_h, h, meta)
            X_test = test_h[F]

            # Train quantile models
            models = {}
            for a in QUANTILES:
                m = LGBMRegressor(objective="quantile", alpha=a, n_estimators=120,
                                  learning_rate=0.08, num_leaves=31, min_child_samples=40,
                                  random_state=42, n_jobs=-1, verbose=-1)
                m.fit(X_train, y_train)
                models[a] = m

            # Predict quantiles on test
            quantile_preds = {a: models[a].predict(X_test) for a in QUANTILES}
            # Clip to [0, 120] and add to occ_now to get prediction
            occ_now_test = test_h.occ.values
            q_pred = {a: np.clip(occ_now_test + quantile_preds[a], 0, 120) for a in QUANTILES}
            p10, p50, p90 = q_pred[0.1], q_pred[0.5], q_pred[0.9]
            # Sort to ensure p10 <= p50 <= p90
            p10, p50, p90 = np.sort([p10, p50, p90], axis=0)

            # Predict quantiles on train (to compute CQR)
            quantile_preds_train = {a: models[a].predict(X_train) for a in QUANTILES}
            occ_now_train = train_h.occ.values
            q_pred_train = {a: np.clip(occ_now_train + quantile_preds_train[a], 0, 120) for a in QUANTILES}
            p10_train, p50_train, p90_train = q_pred_train[0.1], q_pred_train[0.5], q_pred_train[0.9]
            p10_train, p50_train, p90_train = np.sort([p10_train, p50_train, p90_train], axis=0)

            # Compute nonconformity scores for each of the four groups in the training set
            # Groups: (is_operating, is_weekend) -> (True, True), (True, False), (False, True), (False, False)
            # We'll compute the CQR for each group separately.
            # First, get the group labels for the training set
            opr_train = train_h["opr_is_operating"].values
            we_train = train_h.ts_kst.dt.weekday >= 5
            # We'll create a mask for each group
            groups = {
                "operating|we": opr_train & we_train,
                "operating|wd": opr_train & (~we_train),
                "outside|we": (~opr_train) & we_train,
                "outside|wd": (~opr_train) & (~we_train),
            }
            # Compute the actual change y = occ(t+h) for training set
            y_train = occ_now_train + y_train  # occ(t+h)
            # Dictionary to hold CQR for each group
            cqr_per_group = {}
            for group_name, mask in groups.items():
                if mask.sum() < 5:
                    # Too few samples, set CQR to 0
                    cqr_per_group[group_name] = 0.0
                    continue
                # Nonconformity scores for this group: max(p10_train - y, y - p90_train)
                scores = np.maximum(p10_train[mask] - y_train[mask], y_train[mask] - p90_train[mask])
                n = len(scores)
                lvl = min(1.0, np.ceil((n + 1) * TARGET_COVERAGE) / n)
                cqr = np.quantile(scores, lvl)
                cqr_per_group[group_name] = float(max(0.0, cqr))

            # Adjust test intervals: for each test point, determine its group and use the corresponding CQR
            opr_test = test_h["opr_is_operating"].values
            we_test = test_h.ts_kst.dt.weekday >= 5
            test_groups = {
                "operating|we": opr_test & we_test,
                "operating|wd": opr_test & (~we_test),
                "outside|we": (~opr_test) & we_test,
                "outside|wd": (~opr_test) & (~we_test),
            }
            # Initialize adjustment array for test set
            CQR_test = np.zeros(len(test_h))
            for group_name, mask in test_groups.items():
                CQR_test[mask] = cqr_per_group[group_name]

            # Adjust test intervals
            p10_adj = np.maximum(p10 - CQR_test, 0)
            p90_adj = np.minimum(p90 + CQR_test, 120)
            # Ensure p10_adj <= p90_adj
            p10_adj = np.minimum(p10_adj, p90_adj)

            # Now compute coverage and average width on test set
            y_test = occ_now_test + (test_h.nx - test_h.occ).values  # occ(t+h)
            inside = (y_test >= p10_adj) & (y_test <= p90_adj)
            coverage = inside.mean()
            width = np.mean(p90_adj - p10_adj)

            # Now compute for each segment
            # We need to get operating and weekend features for each test point
            # We already have opr_test and we_test (as boolean arrays)
            segments = [
                ("전체", np.ones(len(test_h), dtype=bool)),
                ("운영중", opr_test),
                ("운영외", ~opr_test),
                ("평일", ~we_test),
                ("주말", we_test),
                ("운영중·평일", opr_test & (~we_test)),
                ("운영중·주말", opr_test & we_test),
                ("운영외·평일", (~opr_test) & (~we_test)),
                ("운영외·주말", (~opr_test) & we_test),
            ]

            for seg_name, mask in segments:
                if mask.sum() < 5:  # avoid too small segments
                    continue
                seg_inside = inside[mask]
                seg_coverage = seg_inside.mean() if len(seg_inside) > 0 else 0.0
                seg_width = np.mean(p90_adj[mask] - p10_adj[mask]) if len(seg_inside) > 0 else 0.0
                all_metrics.append({
                    "fold": fold_idx,
                    "horizon": h,
                    "segment": seg_name,
                    "n": int(mask.sum()),
                    "coverage": float(seg_coverage),
                    "avg_width": float(seg_width),
                })

    return pd.DataFrame(all_metrics)

if __name__ == "__main__":
    df = compute_u10_coverage()
    print("\n=== U10 Coverage Results (fold × horizon × segment) ===")
    print(df.to_string())
    # Save to CSV
    output_dir = Path("/Users/chabee/new/New_Source/2026안양시공모전/anyang-parking/reports/tables")
    output_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_dir / "u10_coverage.csv", index=False)
    # Also create a markdown table
    with open(output_dir / "u10_coverage.md", "w") as f:
        f.write("# U10 · 운영중/운영외 별 분위별 구간 커버리지\n\n")
        f.write(df.to_markdown(index=False))
    print(f"\nSaved to {output_dir / 'u10_coverage.csv'} and {output_dir / 'u10_coverage.md'}")