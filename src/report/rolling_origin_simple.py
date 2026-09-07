#!/usr/bin/env python3
"""
Simple rolling origin evaluation for U9-2.
"""
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import timedelta

# Add project root to sys.path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.analysis.a16_stratified_weekend import load, series, feats, PID, INTX
from src.features.temporal import oprtime_features, OPR_COLS
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error

KST = pd.Timedelta(hours=9)
FULL_THRESHOLD = 90.0
QUANTILES = (0.1, 0.5, 0.9)
HORIZ_GRID = (15, 30, 60, 120)

def _add_opr(df, h, meta_dict):
    """Add operating features for target time (ts + h)."""
    from src.features.temporal import oprtime_features as _oprtime_features
    opr_list = []
    for _, row in df.iterrows():
        opr = _oprtime_features(meta_dict.get(row["parking_id"], {}),
                               (pd.Timestamp(row["ts_kst"]) + timedelta(minutes=h)).to_pydatetime(),
                               sunday_free=True)
        opr_list.append(opr)
    opr_df = pd.DataFrame(opr_list, index=df.index)
    opr_df.columns = [f"opr_{c}" for c in opr_df.columns]
    return pd.concat([df, opr_df], axis=1)

def rolling_origin_evaluation():
    """Perform rolling origin evaluation and return metrics DataFrame."""
    o, L, dead = load()
    d = feats(series(o), L)
    meta_dict = {r["parking_id"]: r for r in L.to_dict("records")}

    # Get unique dates sorted
    ut = d.ts_kst.dropna().dt.date.sort_values().unique()
    print(f"Unique dates: {ut}")
    print(f"Number of unique dates: {len(ut)}")

    # Define folds as per U9-2: expanding window, test on next day
    folds = []
    for i in range(3, len(ut)):  # i is the test index (0-based)
        train_dates = ut[:i]  # dates before test date
        test_date = ut[i]
        folds.append({
            'fold': i-2,  # starting from 0
            'train_start': train_dates[0],
            'train_end': train_dates[-1],
            'test_date': test_date,
            'train_dates': train_dates,
            'test_date': test_date
        })

    all_metrics = []

    for fold_info in folds:
        print(f"\nProcessing fold {fold_info['fold']}: "
              f"train {fold_info['train_start']} to {fold_info['train_end']}, "
              f"test {fold_info['test_date']}")

        # Create masks for train and test based on the date of ts_kst
        train_mask = d.ts_kst.dt.date.isin(fold_info['train_dates'])
        test_mask = d.ts_kst.dt.date == fold_info['test_date']

        for h in HORIZ_GRID:
            # Create target: nx = occupancy at ts_kst + h minutes
            shift_steps = h // 5
            d_copy = d.copy()
            d_copy["nx"] = d_copy.groupby("parking_id")["occ"].shift(-shift_steps)
            d_copy["y"] = d_copy["nx"] - d_copy["occ"]

            # We will now purge rows where the label time (ts_kst + h) is not in the same set as the row's ts_kst.
            # We'll create a column for the label time's date.
            d_copy["ts_kst_plus_h"] = d_copy["ts_kst"] + pd.Timedelta(minutes=h)
            d_copy["label_date"] = d_copy["ts_kst_plus_h"].dt.date

            # For each row, we keep it only if:
            #   (row is in train and label_date is in train_dates) OR
            #   (row is in test and label_date is == test_date)
            # We'll create a mask for this condition.
            label_in_train = d_copy["label_date"].isin(fold_info['train_dates'])
            label_in_test = d_copy["label_date"] == fold_info['test_date']
            valid_label = (train_mask & label_in_train) | (test_mask & label_in_test)

            # Apply the mask
            d_copy = d_copy[valid_label].copy()

            # Now split again into train and test based on the original ts_kst date (which is now guaranteed to have label in same set)
            train_df = d_copy[train_mask].copy()
            test_df = d_copy[test_mask].copy()

            # Drop rows where nx is NaN (due to shift)
            train_df = train_df.dropna(subset=["nx"])
            test_df = test_df.dropna(subset=["nx"])

            if len(train_df) == 0 or len(test_df) == 0:
                print(f"  Warning: No data for horizon {h} in fold {fold_info['fold']} (train:{len(train_df)}, test:{len(test_df)})")
                continue

            # Add operating features for target time (ts + h)
            train_df = _add_opr(train_df, h, meta_dict)
            test_df = _add_opr(test_df, h, meta_dict)

            # Feature columns
            F = INTX + [f"opr_{c}" for c in OPR_COLS]

            # Quantile models for p10, p50, p90
            models = {}
            for a in QUANTILES:
                m = LGBMRegressor(objective="quantile", alpha=a,
                                  n_estimators=120, learning_rate=0.08,
                                  num_leaves=31, min_child_samples=40,
                                  random_state=42, n_jobs=-1, verbose=-1)
                m.fit(train_df[F], train_df["y"])
                models[(h, a)] = m

            # Predict on test set
            q_preds = {a: np.clip(test_df.occ_now.values + models[(h, a)].predict(test_df[F]), 0, 120)
                       for a in QUANTILES}
            p10_pred = q_preds[0.1]
            p50_pred = q_preds[0.5]
            p90_pred = q_preds[0.9]

            # For simplicity, we'll use p50 as the ML prediction (we can change this to router selection later)
            ml_pred = p50_pred
            pers_pred = test_df.occ_now.values

            # Compute MAE
            ml_mae = mean_absolute_error(test_df.nx.values, ml_pred)
            pers_mae = mean_absolute_error(test_df.nx.values, pers_pred)

            # Now, compute metrics per segment
            # We need to get operating and weekday info from the test set
            test_opr = []
            for _, row in test_df.iterrows():
                opr = oprtime_features(meta_dict.get(row["parking_id"], {}),
                                      (pd.Timestamp(row["ts_kst"]) + timedelta(minutes=h)).to_pydatetime(),
                                      sunday_free=True)
                test_opr.append(opr)
            test_opr_df = pd.DataFrame(test_opr, index=test_df.index)
            test_opr_df.columns = [f"opr_{c}" for c in test_opr_df.columns]

            # Determine operating and weekend
            test_opr_is_operating = test_opr_df["opr_is_operating"].eq(1).values
            test_is_weekend = test_df.ts_kst.dt.weekday.ge(5).values  # 5=Sat, 6=Sun

            segments = [
                ("전체", np.ones(len(test_df), dtype=bool)),
                ("운영중", test_opr_is_operating),
                ("운영외", ~test_opr_is_operating),
                ("평일", ~test_is_weekend),
                ("주말", test_is_weekend),
                ("운영중·평일", test_opr_is_operating & ~test_is_weekend),
                ("운영중·주말", test_opr_is_operating & test_is_weekend),
                ("운영외·평일", (~test_opr_is_operating) & (~test_is_weekend)),
                ("운영외·주말", (~test_opr_is_operating) & test_is_weekend),
            ]

            for seg_name, mask in segments:
                if mask.sum() > 0:
                    seg_ml_mae = mean_absolute_error(test_df.nx.values[mask], ml_pred[mask])
                    seg_pers_mae = mean_absolute_error(test_df.nx.values[mask], pers_pred[mask])
                    all_metrics.append({
                        "fold": fold_info['fold'],
                        "horizon": h,
                        "segment": seg_name,
                        "n": int(mask.sum()),
                        "ml_mae": float(seg_ml_mae),
                        "persistence_mae": float(seg_pers_mae),
                    })

    return pd.DataFrame(all_metrics)

if __name__ == "__main__":
    df = rolling_origin_evaluation()
    print("\n=== Results ===")
    print(df.to_string())
    # Save to CSV for inspection
    output_dir = Path("/Users/chabee/new/New_Source/2026안양시공모전/anyang-parking/reports/tables")
    output_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_dir / "u9_rolling_origin_simple.csv", index=False)
    # Also create a markdown table
    with open(output_dir / "u9_rolling_origin_simple.md", "w") as f:
        f.write("# U9-2 · 롤링 오리진 분할 평가 (간단 버전)\n\n")
        f.write(df.to_markdown(index=False))