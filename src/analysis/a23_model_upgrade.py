#!/usr/bin/env python3
"""A23 모델 보강: M1(lag_24h·lag_7d·time-of-week·공휴일)을 M0과 같은 행에서 비교.

python src/analysis/a23_model_upgrade.py
python src/analysis/a23_model_upgrade.py --horizons 60,120,360

`performance_plan.md` §4 A23의 "모델 M1 = M0 + lag_24h/lag_7d + time-of-week + 공휴일"이다.
M0(8-2에서 쓴 글로벌 Δ-LightGBM)과 M1을 fold·평가행·기준선까지 모두 공유한 채로만 비교한다.

행 일치 규칙:

- `eligible`(평가행) 정의는 M0의 피처로만 만든다. M1의 추가 피처가 비었다고 행을 빼면
  두 모델의 분모가 달라진다. LightGBM은 결측을 그대로 학습하므로 게이트를 늘리지 않는다.
- 추가 lag는 모두 `target_time` 기준 과거값이다. 지평선 h ≤ 1440분에서 `target_time - 24h`는
  항상 관측시각 이하이므로 예측 시점에 알 수 있다. 미래값을 쓰지 않는다.
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
    CARRY, KEYS, MAE_TARGET_PP, ML, by_day_scores, certified_max_horizon, certify,
    per_lot_scores, split_by_label_time, summarize, weekend_weeks,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

M0, M1 = ML, "m1"
WEEK_MINUTES = 7 * 24 * 60
# M1이 더 쓰는 피처. 모두 target_time 기준 과거 관측 또는 달력값이다.
M1_EXTRA = ["tgt_lag_24h", "tgt_lag_7d", "tgt_lag_24h_delta",
            "tow_sin", "tow_cos", "tgt_day_of_week", "tgt_is_holiday"]
M1_FEATURES = FEATURES + M1_EXTRA


def add_m1_features(frame, holidays=HOLIDAYS_KST):
    """기준선으로 이미 붙인 target 기준 lag를 M1 피처로 쓰고 time-of-week·공휴일을 더한다."""
    frame = frame.copy()
    target = frame["target_time"]
    frame["tgt_lag_24h"] = frame[pred_column("lag_24h")]
    frame["tgt_lag_7d"] = frame[pred_column("lag_7d")]
    # 타깃이 Δ이므로 하루 전 수준과 현재의 차이를 직접 준다.
    frame["tgt_lag_24h_delta"] = frame["tgt_lag_24h"] - frame["occ_now"]
    minute_of_week = (target.dt.weekday * 1440 + target.dt.hour * 60 + target.dt.minute)
    frame["tow_sin"] = np.sin(2 * np.pi * minute_of_week / WEEK_MINUTES)
    frame["tow_cos"] = np.cos(2 * np.pi * minute_of_week / WEEK_MINUTES)
    frame["tgt_day_of_week"] = target.dt.weekday
    frame["tgt_is_holiday"] = target.dt.date.map(lambda d: d in holidays).astype(int)
    return frame


def constant_extras(train):
    """학습 구간에서 값이 하나뿐인 추가 피처. 데이터 기간이 짧으면 공휴일이 여기 걸린다."""
    return sorted(c for c in M1_EXTRA if train[c].nunique(dropna=True) <= 1)


def fit_predict(features, train, test, params):
    model = LGBMRegressor(objective="quantile", alpha=.5, **params)
    model.fit(train[features], train["delta_target"])
    delta = model.predict(test[features]) if len(test) else np.array([], dtype=float)
    if not np.isfinite(delta).all():
        raise ValueError("모델이 유효하지 않은 값을 반환했습니다. 같은 평가 행을 유지할 수 없습니다.")
    return np.clip(test["occ_now"].to_numpy() + delta, 0, 100)


def collect(base, rules, lots, dates, horizons, model_params=None, progress=True):
    """지평선 × fold마다 M0과 M1을 같은 train/test로 학습하고 같은 행에 예측을 남긴다."""
    params = {**PARAMS, **(model_params or {})}
    lookup = occ_lookup(base)
    seasonal = {start: fit_seasonal_naive(base, start) for start in dates}
    rows, audits = [], []
    for horizon in horizons:
        frame = add_m1_features(
            add_lag_baselines(build_horizon_frame(base, horizon, rules, lots), lookup))
        for fold, start in enumerate(dates, 1):
            train, _, test = split_by_label_time(frame, start)
            if train.empty:
                raise ValueError(f"{start.date()} H={horizon}: 학습 가능한 연속 관측이 없습니다.")
            scored = apply_seasonal_naive(seasonal[start], test) if len(test) else test.assign(
                **{pred_column("seasonal_naive"): np.nan})
            result = scored[KEYS + CARRY].copy()
            result["eligible"] = True
            result["fold"] = fold
            result["test_date"] = str(start.date())
            for name, features in ((M0, FEATURES), (M1, M1_FEATURES)):
                result[pred_column(name)] = fit_predict(features, train, scored, params)
            rows.append(result)
            audits.append(dict(
                horizon=horizon, fold=fold, test_date=str(start.date()),
                train_n=len(train), test_n=len(test),
                constant_extras=",".join(constant_extras(train)),
                **{f"{c}_train_null_rate": float(train[c].isna().mean()) for c in M1_EXTRA}))
            if progress:
                print(f"H={horizon:>4}분 {fold}/{len(dates)} · {start.date()} · "
                      f"train={len(train):,} test={len(test):,}", flush=True)
    paired = pd.concat(rows, ignore_index=True)
    if paired.duplicated(KEYS).any():
        raise AssertionError("test 날짜 간 평가 행이 중복됩니다.")
    return paired, pd.DataFrame(audits)


def head_to_head(paired, horizons):
    """같은 행에서 M0 대비 M1의 MAE 변화. 날짜별 방향까지 함께 본다."""
    rows = []
    for horizon in horizons:
        at_horizon = paired.loc[paired["horizon"].eq(horizon)]
        for label, baselines in ROW_SETS.items():
            common = at_horizon.loc[common_mask(at_horizon, baselines)]
            if common.empty:
                rows.append(dict(horizon=horizon, row_set=label, n=0, m0_mae=np.nan,
                                 m1_mae=np.nan, gain_pp=np.nan, gain_pct=np.nan,
                                 m1_better_days=0, test_days=0, m1_better_everywhere=False))
                continue
            error = {name: np.abs(common["actual_occ"] - common[pred_column(name)])
                     for name in (M0, M1)}
            daily = []
            for _, group in common.groupby("test_date", observed=True):
                m0 = float(np.abs(group["actual_occ"] - group[pred_column(M0)]).mean())
                m1 = float(np.abs(group["actual_occ"] - group[pred_column(M1)]).mean())
                daily.append(m1 < m0)
            m0_mae, m1_mae = (float(error[name].mean()) for name in (M0, M1))
            gain = m0_mae - m1_mae
            rows.append(dict(horizon=horizon, row_set=label, n=len(common),
                             m0_mae=m0_mae, m1_mae=m1_mae, gain_pp=gain,
                             gain_pct=100 * gain / m0_mae if m0_mae > 0 else np.nan,
                             m1_better_days=int(sum(daily)), test_days=len(daily),
                             m1_better_everywhere=bool(daily and all(daily))))
    return pd.DataFrame(rows)


def run(db_path=PARKING_DB, rules_path=PARKING_ACCESS_RULES_CSV, horizons=HORIZONS,
        table_dir=TABLES, prediction_dir=PROCESSED / "a23m", test_days=7, min_train_days=7,
        model_params=None, data_end=None):
    print("=== A23 모델 보강: M0 vs M1 (같은 fold·같은 평가행) ===", flush=True)
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

    summary = summarize(paired, horizons, models=(M0, M1))
    versus = head_to_head(paired, horizons)
    per_lot = pd.concat([per_lot_scores(paired, horizons, model) for model in (M0, M1)],
                        ignore_index=True)
    by_day = pd.concat([by_day_scores(paired, horizons, model) for model in (M0, M1)],
                       ignore_index=True)
    certification = pd.concat([
        certify(summary, per_lot[per_lot["model"].eq(model)],
                by_day[by_day["model"].eq(model)], horizons, weekend_week_count, model)
        for model in (M0, M1)], ignore_index=True)
    def ceiling(column):
        return {model: {label: certified_max_horizon(
            certification[certification["model"].eq(model)], label, horizons, column)
            for label in ROW_SETS} for model in (M0, M1)}

    ceilings, provisional = ceiling("certified"), ceiling("provisional")
    # 상수 피처는 지평선마다 다르다. 1440분에서는 target_time-24h가 관측시각과 같아
    # tgt_lag_24h_delta가 0으로 고정된다. 지평선을 묶어 보고하면 오해를 부른다.
    constants = {int(horizon): sorted({c for row in group for c in row.split(",") if c})
                 for horizon, group in audits.groupby("horizon")["constant_extras"]}

    table_dir, prediction_dir = Path(table_dir), Path(prediction_dir)
    table_dir.mkdir(parents=True, exist_ok=True)
    prediction_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in (("comparison", summary), ("m0_vs_m1", versus), ("by_day", by_day),
                        ("per_lot", per_lot), ("splits", audits),
                        ("certification", certification)):
        frame.to_csv(table_dir / f"a23m_{name}.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(prediction_dir / "predictions.csv.gz", index=False, compression="gzip")

    source_paths = [Path(__file__), Path(__file__).with_name("a23_horizon_curve.py"),
                    Path(__file__).with_name("a23_common.py")]
    manifest = {
        **info, "protocol": "a23_model_upgrade_v1",
        "status": "complete" if weekend_week_count >= 2 else "partial",
        "horizons": list(horizons), "models": [M0, M1], "baselines": list(BASELINES),
        "row_sets": {k: list(v) for k, v in ROW_SETS.items()},
        "test_dates": [str(d.date()) for d in dates],
        "data_end_kst": str(data_end) if data_end else None,
        "rows_truncated_after_data_end": int(truncated_n),
        "weekend_weeks_in_test": weekend_week_count,
        "m0_features": FEATURES, "m1_extra_features": M1_EXTRA,
        "m1_extra_constant_in_train": constants,
        "certified_max_horizon": ceilings, "provisional_max_horizon": provisional,
        "mae_target_pp": MAE_TARGET_PP,
        "model": {"objective": "quantile", "alpha": .5, **PARAMS, **(model_params or {})},
        "prediction_clip": [0, 100], "matched_n": len(paired),
        "holidays_kst": sorted(str(d) for d in HOLIDAYS_KST),
        "holiday_grid_rows_affected": holiday_counts,
        "anomaly_candidates_n": int(len(anomalies)),
        "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
        "notes": [
            "M0과 M1은 같은 fold·같은 train/test·같은 평가행을 쓴다. 분모가 같다.",
            "eligible 게이트는 M0 피처로만 만든다. M1 추가 피처의 결측은 LightGBM이 그대로 학습한다.",
            "추가 lag는 target_time 기준 과거값이며 지평선 1440분까지 관측시각 이하다.",
            "m1_extra_constant_in_train에 적힌 피처는 그 지평선의 학습 구간에서 값이 하나뿐이라 "
            "기여할 수 없다. 1440분에서는 target_time-24h가 관측시각과 같아 "
            "tgt_lag_24h가 occ_now와 같고 tgt_lag_24h_delta는 0으로 고정된다.",
            "fold 분할은 8-2와 같이 정답 시각 기준이다.",
            "기존 U11 학습 산출물과 서비스 predictor.pkl을 갱신하지 않는다.",
        ],
    }
    (table_dir / "a23m_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    for label in ROW_SETS:
        print(f"\n=== M0 vs M1 · 행집합 {label} ===", flush=True)
        view = versus[versus["row_set"].eq(label)][
            ["horizon", "n", "m0_mae", "m1_mae", "gain_pp", "gain_pct",
             "m1_better_days", "test_days"]]
        print(view.to_string(index=False, float_format=lambda x: f"{x:.3f}"), flush=True)
    for horizon, columns in constants.items():
        if columns:
            print(f"\nH={horizon}분 학습 구간에서 상수인 추가 피처: {', '.join(columns)}",
                  flush=True)
    for model in (M0, M1):
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
