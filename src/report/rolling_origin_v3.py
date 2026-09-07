#!/usr/bin/env python3
"""
Rolling origin evaluation for U9-2, version 3 with proper purging by checking label time.
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
from lightgbm import LGBMRegressor, LGBMClassifier
from sklearn.metrics import mean_absolute_error
from sklearn.linear_model import LogisticRegression

KST = pd.Timedelta(hours=9)
FULL_THRESHOLD = 90.0
QUANTILES = (0.1, 0.5, 0.9)
HORIZ_GRID = (15, 30, 60, 120)
MARGIN = 0.05  # For U9-3 conservative tie-break

def _logodds(p, eps=1e-6):
    q = np.clip(np.asarray(p, dtype=float), eps, 1 - eps)
    return np.log(q / (1 - q))

def _add_opr(df, h, meta):
    """Add operating features for target time (ts + h)."""
    from src.features.temporal import oprtime_features as _oprtime_features
    opr_list = []
    for _, row in df.iterrows():
        opr = _oprtime_features(meta.get(row["parking_id"], {}),
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
            train_df = _add_opr(train_df, h, {r["parking_id"]: r for r in L.to_dict("records")})
            test_df = _add_opr(test_df, h, {r["parking_id"]: r for r in L.to_dict("records")})

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

            # Router selection: split train data into base and val (last 20% of train dates for validation).
            train_dates_in_fold = sorted(train_df.ts_kst.dt.date.unique())
            if len(train_dates_in_fold) >= 2:
                split_idx = max(1, int(len(train_dates_in_fold) * 0.8))
                base_dates = train_dates_in_fold[:split_idx]
                val_dates = train_dates_in_fold[split_idx:]
                base_mask = train_df.ts_kst.dt.date.isin(base_dates)
                val_mask = train_df.ts_kst.dt.date.isin(val_dates)
                base_df = train_df[base_mask]
                val_df = train_df[val_mask]
            else:
                # Not enough dates for split, use all for base and none for val
                base_df = train_df
                val_df = train_df.iloc[0:0]  # empty

            if len(base_df) > 0:
                sel = LGBMRegressor(objective="quantile", alpha=0.5,
                                    n_estimators=400, learning_rate=0.05,
                                    num_leaves=63, min_child_samples=40,
                                    random_state=42, n_jobs=-1, verbose=-1)
                sel.fit(base_df[F], base_df["y"])
            else:
                sel = None

            # Make predictions on validation set (if exists) for router selection
            if sel is not None and len(val_df) > 0:
                val_pred = np.clip(val_df.occ_now.values + sel.predict(val_df[F]), 0, 120)
                val_mae = mean_absolute_error(val_df.nx.values, val_pred)
                val_pers_mae = mean_absolute_error(val_df.nx.values, val_df.occ_now.values)
            else:
                val_mae = val_pers_mae = None

            # Make predictions on test set
            # First, get predictions from each quantile model
            q_preds = {a: np.clip(test_df.occ_now.values + models[(h, a)].predict(test_df[F]), 0, 120)
                       for a in QUANTILES}
            p10_pred = q_preds[0.1]
            p50_pred = q_preds[0.5]
            p90_pred = q_preds[0.9]

            # For router selection: if we have validation MAE, compare ML vs persistence
            # Otherwise, default to persistence?
            if val_mae is not None and val_pers_mae is not None:
                # Apply conservative tie-break from U9-3: ML only if it beats persistence by MARGIN
                if val_mae < val_pers_mae * (1 - MARGIN):
                    chosen = "ml"
                else:
                    chosen = "persistence"
            else:
                chosen = "persistence"  # default

            if chosen == "ml":
                test_pred = p50_pred
            else:
                test_pred = test_df.occ_now.values

            test_mae = mean_absolute_error(test_df.nx.values, test_pred)
            test_pers_mae = mean_absolute_error(test_df.nx.values, test_df.occ_now.values)

            # Now, compute metrics per segment
            # We need to get operating and weekday info from the test set
            test_opr = []
            for _, row in test_df.iterrows():
                opr = oprtime_features(meta.get(row["parking_id"], {}),
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
                    if chosen == "ml":
                        seg_mae = mean_absolute_error(test_df.nx.values[mask], test_pred[mask])
                    else:
                        seg_mae = mean_absolute_error(test_df.nx.values[mask], test_df.occ_now.values[mask])
                    seg_pers_mae = mean_absolute_error(test_df.nx.values[mask], test_df.occ_now.values[mask])
                    all_metrics.append({
                        "fold": fold_info['fold'],
                        "horizon": h,
                        "segment": seg_name,
                        "n": int(mask.sum()),
                        "ml_mae": float(seg_mae) if chosen == "ml" else None,
                        "persistence_mae": float(seg_pers_mae),
                        "selected": chosen,
                        "val_ml_mae": float(val_mae) if val_mae is not None else None,
                        "val_persistence_mae": float(val_pers_mae) if val_pers_mae is not None else None,
                    })

    return pd.DataFrame(all_metrics)

if __name__ == "__main__":
    df = rolling_origin_evaluation()
    print("\n=== Results ===")
    print(df.to_string())
    # Save to CSV for inspection
    output_dir = Path("/Users/chabee/new/New_Source/2026안양시공모전/anyang-parking/reports/tables")
    output_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_dir / "u9_rolling_origin.csv", index=False)
    # Also create a markdown table
    with open(output_dir / "u9_rolling_origin.md", "w") as f:
        f.write("# U9-2 · 롤링 오리진 분할 평가\n\n")
        f.write(df.to_markdown(index=False))