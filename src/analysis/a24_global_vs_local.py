#!/usr/bin/env python3
"""A24: 글로벌 G0 / 주차장별 L0 / 글로벌+잔차보정 H0를 같은 행에서 비교.

python src/analysis/a24_global_vs_local.py
python src/analysis/a24_global_vs_local.py --horizons 60,120

`performance_plan.md` §4 A24다. 세 모델은 같은 fold·같은 train/validation/test·같은
평가행을 쓴다.

- G0: 전 주차장을 함께 학습하는 글로벌 Δ-LightGBM (8-2의 M0와 같은 설정)
- L0: 주차장별 개별 Δ-LightGBM. 주차장 식별 피처는 상수라 빼고 학습한다.
- H0: G0 예측에 **validation에서 구한 주차장별 잔차 중앙값**을 더한 보정. test를 보지 않는다.

주의: 이 실험은 어느 모델을 서비스에 넣을지 결정하지 않는다. 계획대로 이후 test
날짜에서도 재현될 때만 반영하며, test 결과로 주차장별 라우터를 만들지 않는다.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.config import PARKING_DB, PARKING_ACCESS_RULES_CSV, PROCESSED, TABLES
from src.analysis.a20_matched_model_comparison import (
    FEATURES, PARAMS, build_horizon_frame, load_inputs, test_dates,
)
from src.analysis.a22_baseline_api_alive import HOLIDAYS_KST, build_features_label_valid
from src.analysis.a23_common import (
    BASELINES, HORIZONS, ROW_SETS, add_lag_baselines, apply_seasonal_naive, common_mask,
    fit_seasonal_naive, occ_lookup, parse_horizons, pred_column, truncate_obs,
)
from src.analysis.a23_horizon_curve import (
    CARRY, KEYS, MAE_TARGET_PP, by_day_scores, certified_max_horizon, certify,
    per_lot_scores, split_by_label_time, summarize, weekend_weeks,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

G0, L0, H0 = "g0", "l0", "h0"
MODELS = (G0, L0, H0)
# 주차장 하나만 보는 모델에서는 주차장 식별 피처가 상수라 뺀다.
LOT_IDENTITY = ["parking_id_cat", "pid_we"]
LOCAL_FEATURES = [f for f in FEATURES if f not in LOT_IDENTITY]
MIN_LOCAL_TRAIN_ROWS = 500   # 이보다 적으면 그 주차장의 개별 모델을 만들지 않는다.


def fit_delta(features, train, params):
    model = LGBMRegressor(objective="quantile", alpha=.5, **params)
    model.fit(train[features], train["delta_target"])
    return model


def restore(occ_now, delta):
    return np.clip(np.asarray(occ_now, dtype=float) + np.asarray(delta, dtype=float), 0, 100)


def predict_global(model, frame):
    if frame.empty:
        return np.array([], dtype=float)
    return restore(frame["occ_now"], model.predict(frame[FEATURES]))


def predict_local(train, test, params):
    """주차장별 모델. 학습 표본이 적은 주차장은 예측하지 않고 NaN으로 남긴다."""
    predictions = pd.Series(np.nan, index=test.index)
    trained = {}
    for pid, group in train.groupby("parking_id", observed=True):
        if len(group) < MIN_LOCAL_TRAIN_ROWS:
            continue
        trained[pid] = fit_delta(LOCAL_FEATURES, group, params)
    for pid, group in test.groupby("parking_id", observed=True):
        model = trained.get(pid)
        if model is None:
            continue
        predictions.loc[group.index] = restore(
            group["occ_now"], model.predict(group[LOCAL_FEATURES]))
    return predictions, sorted(trained)


def residual_corrections(model, validation):
    """validation에서 주차장별 잔차 중앙값을 구한다. test는 보지 않는다."""
    if validation.empty:
        return {}
    predicted = predict_global(model, validation)
    residual = validation["actual_occ"].to_numpy(dtype=float) - predicted
    frame = pd.DataFrame({"parking_id": validation["parking_id"].to_numpy(),
                          "residual": residual})
    return frame.groupby("parking_id")["residual"].median().to_dict()


def apply_corrections(global_pred, test, corrections):
    """보정값이 없는 주차장은 글로벌 예측을 그대로 둔다(0 보정)."""
    shift = test["parking_id"].map(corrections).fillna(0.0).to_numpy(dtype=float)
    return np.clip(np.asarray(global_pred, dtype=float) + shift, 0, 100), shift


def collect(base, rules, lots, dates, horizons, model_params=None, progress=True):
    """지평선 × fold마다 세 모델을 같은 분할로 만들고 같은 행에 예측을 남긴다."""
    params = {**PARAMS, **(model_params or {})}
    lookup = occ_lookup(base)
    seasonal = {start: fit_seasonal_naive(base, start) for start in dates}
    rows, audits = [], []
    for horizon in horizons:
        frame = add_lag_baselines(build_horizon_frame(base, horizon, rules, lots), lookup)
        for fold, start in enumerate(dates, 1):
            train, validation, test = split_by_label_time(frame, start)
            if train.empty:
                raise ValueError(f"{start.date()} H={horizon}: 학습 가능한 연속 관측이 없습니다.")
            scored = apply_seasonal_naive(seasonal[start], test) if len(test) else test.assign(
                **{pred_column("seasonal_naive"): np.nan})
            result = scored[KEYS + CARRY].copy()
            result["eligible"] = True
            result["fold"] = fold
            result["test_date"] = str(start.date())

            global_model = fit_delta(FEATURES, train, params)
            global_pred = predict_global(global_model, scored)
            local_pred, trained_lots = predict_local(train, scored, params)
            corrections = residual_corrections(global_model, validation)
            corrected, shift = apply_corrections(global_pred, scored, corrections)

            result[pred_column(G0)] = global_pred
            result[pred_column(L0)] = local_pred.to_numpy()
            result[pred_column(H0)] = corrected
            rows.append(result)
            test_lots = set(scored["parking_id"].unique())
            audits.append(dict(
                horizon=horizon, fold=fold, test_date=str(start.date()),
                train_n=len(train), validation_n=len(validation), test_n=len(test),
                local_models_trained=len(trained_lots),
                test_lots=len(test_lots),
                test_lots_without_local=len(test_lots - set(trained_lots)),
                corrected_lots=len(corrections),
                test_rows_without_correction=int((shift == 0).sum()) if len(scored) else 0,
                correction_abs_median=float(np.median(np.abs(shift))) if len(scored) else np.nan))
            if progress:
                print(f"H={horizon:>4}분 {fold}/{len(dates)} · {start.date()} · "
                      f"train={len(train):,} test={len(test):,} "
                      f"개별모델={len(trained_lots)}곳", flush=True)
    paired = pd.concat(rows, ignore_index=True)
    if paired.duplicated(KEYS).any():
        raise AssertionError("test 날짜 간 평가 행이 중복됩니다.")
    return paired, pd.DataFrame(audits)


def three_way(paired, horizons):
    """세 모델이 모두 예측을 낸 행에서만 비교한다. L0가 없는 행 수를 함께 남긴다."""
    rows = []
    for horizon in horizons:
        at_horizon = paired.loc[paired["horizon"].eq(horizon)]
        for label, baselines in ROW_SETS.items():
            common = at_horizon.loc[common_mask(at_horizon, baselines)]
            scored = common.dropna(subset=[pred_column(m) for m in MODELS])
            row = dict(horizon=horizon, row_set=label, n=len(scored),
                       n_dropped_without_local=len(common) - len(scored),
                       n_lots=int(scored["parking_id"].nunique()))
            for name in MODELS:
                error = np.abs(scored["actual_occ"] - scored[pred_column(name)])
                row[f"{name}_mae"] = float(error.mean()) if len(scored) else np.nan
            if len(scored):
                daily = {name: [] for name in (L0, H0)}
                for _, group in scored.groupby("test_date", observed=True):
                    base_mae = float(np.abs(group["actual_occ"]
                                            - group[pred_column(G0)]).mean())
                    for name in (L0, H0):
                        mae = float(np.abs(group["actual_occ"]
                                           - group[pred_column(name)]).mean())
                        daily[name].append(mae < base_mae)
                for name in (L0, H0):
                    row[f"{name}_better_days"] = int(sum(daily[name]))
                    row[f"{name}_better_everywhere"] = bool(daily[name] and all(daily[name]))
                row["test_days"] = len(daily[L0])
                row["best_model"] = min(MODELS, key=lambda n: row[f"{n}_mae"])
            else:
                for name in (L0, H0):
                    row[f"{name}_better_days"] = 0
                    row[f"{name}_better_everywhere"] = False
                row["test_days"] = 0
                row["best_model"] = None
            rows.append(row)
    return pd.DataFrame(rows)


def run(db_path=PARKING_DB, rules_path=PARKING_ACCESS_RULES_CSV, horizons=HORIZONS,
        table_dir=TABLES, prediction_dir=PROCESSED / "a24", test_days=7, min_train_days=7,
        model_params=None, data_end=None):
    print("=== A24 글로벌 G0 / 개별 L0 / 잔차보정 H0 ===", flush=True)
    obs, lots, rules, info = load_inputs(db_path, rules_path)
    # 폴링이 계속 쌓이므로 데이터 끝을 고정하지 않으면 다음 날 실행에서 test 창이 통째로 밀린다.
    obs, truncated_n = truncate_obs(obs, data_end)
    if truncated_n:
        print(f"데이터 절단: {truncated_n:,}행 제외 (끝={data_end})", flush=True)
    dates = test_dates(obs["ts_kst"].min(), obs["ts_kst"].max(), test_days, min_train_days)
    weekend_week_count = weekend_weeks(dates)
    print(f"대상 {len(rules)}곳 · {len(obs):,}행 · {info['start']} ~ {info['end']}", flush=True)
    print(f"test 날짜: {', '.join(str(d.date()) for d in dates)} "
          f"(주말 {weekend_week_count}회)", flush=True)

    base, anomalies, holiday_counts = build_features_label_valid(
        obs, lots, rules, holidays=HOLIDAYS_KST, progress=True)
    paired, audits = collect(base, rules, lots, dates, horizons, model_params)

    summary = summarize(paired, horizons, models=MODELS)
    versus = three_way(paired, horizons)
    per_lot = pd.concat([per_lot_scores(paired, horizons, model) for model in MODELS],
                        ignore_index=True)
    by_day = pd.concat([by_day_scores(paired, horizons, model) for model in MODELS],
                       ignore_index=True)
    certification = pd.concat([
        certify(summary, per_lot[per_lot["model"].eq(model)],
                by_day[by_day["model"].eq(model)], horizons, weekend_week_count, model)
        for model in MODELS], ignore_index=True)

    def ceiling(column):
        return {model: {label: certified_max_horizon(
            certification[certification["model"].eq(model)], label, horizons, column)
            for label in ROW_SETS} for model in MODELS}

    ceilings, provisional = ceiling("certified"), ceiling("provisional")

    table_dir, prediction_dir = Path(table_dir), Path(prediction_dir)
    table_dir.mkdir(parents=True, exist_ok=True)
    prediction_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in (("comparison", summary), ("three_way", versus), ("by_day", by_day),
                        ("per_lot", per_lot), ("splits", audits),
                        ("certification", certification)):
        frame.to_csv(table_dir / f"a24_{name}.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(prediction_dir / "predictions.csv.gz", index=False, compression="gzip")

    source_paths = [Path(__file__), Path(__file__).with_name("a23_horizon_curve.py"),
                    Path(__file__).with_name("a23_common.py")]
    manifest = {
        **info, "protocol": "a24_global_vs_local_v1",
        "status": "complete" if weekend_week_count >= 2 else "partial",
        "horizons": list(horizons), "models": list(MODELS), "baselines": list(BASELINES),
        "row_sets": {k: list(v) for k, v in ROW_SETS.items()},
        "test_dates": [str(d.date()) for d in dates],
        "data_end_kst": str(data_end) if data_end else None,
        "rows_truncated_after_data_end": int(truncated_n),
        "weekend_weeks_in_test": weekend_week_count,
        "global_features": FEATURES, "local_features": LOCAL_FEATURES,
        "local_min_train_rows": MIN_LOCAL_TRAIN_ROWS,
        "correction": "validation 하루의 주차장별 잔차 중앙값. 없으면 0 보정.",
        "certified_max_horizon": ceilings, "provisional_max_horizon": provisional,
        "mae_target_pp": MAE_TARGET_PP,
        "model": {"objective": "quantile", "alpha": .5, **PARAMS, **(model_params or {})},
        "prediction_clip": [0, 100], "matched_n": len(paired),
        "holidays_kst": sorted(str(d) for d in HOLIDAYS_KST),
        "holiday_grid_rows_affected": holiday_counts,
        "anomaly_candidates_n": int(len(anomalies)),
        "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
        "notes": [
            "세 모델은 같은 fold·같은 train/validation/test·같은 평가행을 쓴다.",
            "H0 보정은 validation 하루에서만 구한다. test 잔차를 쓰지 않는다.",
            "L0는 학습 표본이 부족한 주차장에서 예측하지 않는다. 그 행은 세 모델 비교에서 빠진다.",
            "이 결과로 서비스 모델을 교체하지 않는다. 이후 test 날짜에서 재현될 때만 반영한다.",
            "test 결과로 주차장별 라우터를 만들지 않는다.",
            "기존 U11 학습 산출물과 서비스 predictor.pkl을 갱신하지 않는다.",
        ],
    }
    (table_dir / "a24_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    for label in ROW_SETS:
        print(f"\n=== G0 / L0 / H0 · 행집합 {label} ===", flush=True)
        view = versus[versus["row_set"].eq(label)][
            ["horizon", "n", "n_dropped_without_local", "g0_mae", "l0_mae", "h0_mae",
             "l0_better_days", "h0_better_days", "test_days", "best_model"]]
        print(view.to_string(index=False, float_format=lambda x: f"{x:.3f}"), flush=True)
    for model in MODELS:
        print(f"{model} 연속 통과 최대 지평선: 인증 {ceilings[model]} · 탐색 {provisional[model]}",
              flush=True)
    print(f"\n상태: {manifest['status']}", flush=True)
    print(f"저장 완료: {table_dir}", flush=True)
    return versus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=PARKING_DB)
    parser.add_argument("--rules", type=Path, default=PARKING_ACCESS_RULES_CSV)
    parser.add_argument("--horizons", type=str, default="")
    parser.add_argument("--test-days", type=int, default=7)
    parser.add_argument("--min-train-days", type=int, default=7)
    parser.add_argument("--data-end", type=str, default="",
                        help="이 시각까지의 관측만 사용한다(예: '2026-09-18 23:40:22+09:00'). "
                             "다른 실험과 같은 test 창을 쓰려면 지정한다.")
    args = parser.parse_args()
    run(args.db, args.rules, parse_horizons(args.horizons),
        test_days=args.test_days, min_train_days=args.min_train_days,
        data_end=args.data_end or None)


if __name__ == "__main__":
    main()
