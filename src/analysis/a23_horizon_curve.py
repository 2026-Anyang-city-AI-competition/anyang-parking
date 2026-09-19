#!/usr/bin/env python3
"""A23: 지평선 15~1440분의 오차 곡선과 인증 지평선 산출.

python src/analysis/a23_horizon_curve.py
python src/analysis/a23_horizon_curve.py --horizons 15,60,120 --test-days 7

`performance_plan.md` §4 A23의 P0 실험이다. 모델 M0(현재 글로벌 Δ-LightGBM)을
지평선마다 직접 학습하고, 같은 평가행에서 기준선 4종과 비교한다. 재귀 예측으로
지평선을 이어 붙이지 않는다.

인증 조건(지평선 h):

1. 전체 MAE ≤ 10%p
2. 평일·주말 각각 MAE ≤ 10%p
3. 그 지평선의 가장 강한 기준선보다 MAE가 낮음
4. 평가 가능한 주차장의 80% 이상이 MAE ≤ 10%p
5. 최소 2회 주말을 포함한 복수 test 날짜에서 방향이 재현됨

하나라도 미달이면 그 지평선 이후를 자동 통과시키지 않는다(단조 prefix).
행 집합은 `core`(persistence·lag_24h·seasonal naive)와 `all`(+lag_7d) 두 벌을 모두 낸다.
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
    DAY_GROUPS, FEATURES, PARAMS, load_inputs, build_horizon_frame, test_dates,
)
from src.analysis.a22_baseline_api_alive import HOLIDAYS_KST, build_features_label_valid
from src.analysis.a23_common import (
    BASELINES, HORIZONS, ROW_SETS, add_lag_baselines, apply_seasonal_naive,
    baseline_scores, common_mask, fit_seasonal_naive, occ_lookup, parse_horizons,
    pred_column, score_predictions, strongest_baseline, truncate_obs,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

MAE_TARGET_PP = 10.0        # performance_plan.md의 점유율 MAE 합격선
LOT_PASS_RATE = 0.8         # 평가 가능한 주차장 중 합격 비율 하한
MIN_LOT_ROWS = 30           # 이보다 적은 주차장은 "평가 가능"으로 세지 않는다
MIN_WEEKEND_WEEKS = 2       # 방향 재현에 필요한 주말 수
ML = "ml"
METHODS = (ML, *BASELINES)
KEYS = ["parking_id", "ts_kst", "target_time", "horizon"]
CARRY = ["occ_now", "actual_occ", "weekday_group", "access_group",
         *(pred_column(b) for b in BASELINES)]


def split_by_label_time(frame, start):
    """정답 시각(target_time)으로 학습/validation/test를 나눈다.

    A20은 test를 `관측시각 >= start` + `정답시각 < start+1일`로 잡는다. 지평선이 하루에
    가까워지면 두 조건을 동시에 만족하는 행이 사라지고, 1440분에서는 정의상 0이 된다.
    A23은 지평선이 1440분까지 가므로 "그 날짜에 정답이 있는 행"을 test로 쓴다.
    학습은 A20과 같이 validation 시작 이전 정답만 보므로 미래 정답을 보지 않는다.
    """
    cal_start, end = start - pd.Timedelta(days=1), start + pd.Timedelta(days=1)
    valid = frame.loc[frame["eligible"]]
    label = valid["target_time"]
    train = valid.loc[label < cal_start].copy()
    validation = valid.loc[(label >= cal_start) & (label < start)].copy()
    test = valid.loc[(label >= start) & (label < end)].copy()
    if not train.empty:
        assert train["target_time"].max() < cal_start
    if not test.empty:
        assert test["target_time"].min() >= start and test["target_time"].max() < end
    return train, validation, test


def fold_predictions(model, test, fold, start):
    """같은 test 행에 AI 예측과 기준선 예측을 함께 남긴다."""
    result = test[KEYS + CARRY].copy()
    # 분할이 이미 eligible 행만 남긴다. 하류의 common_mask가 같은 뜻으로 읽도록 남겨 둔다.
    result["eligible"] = True
    result["fold"] = fold
    result["test_date"] = str(start.date())
    delta = model.predict(test[FEATURES]) if len(test) else np.array([], dtype=float)
    if not np.isfinite(delta).all():
        raise ValueError("모델이 유효하지 않은 값을 반환했습니다. 같은 평가 행을 유지할 수 없습니다.")
    result[pred_column(ML)] = np.clip(result["occ_now"].to_numpy() + delta, 0, 100)
    if result[["actual_occ", pred_column(ML)]].isna().to_numpy().any():
        raise ValueError("예측 또는 정답에 결측이 있습니다. 같은 평가 행을 유지할 수 없습니다.")
    return result


def collect(base, rules, lots, dates, horizons, model_params=None, progress=True):
    """지평선 × fold별로 M0을 학습하고 같은 행의 기준선 예측을 붙인다."""
    params = {**PARAMS, **(model_params or {})}
    lookup = occ_lookup(base)
    # seasonal naive는 fold 시작 이전 관측만 본다. fold마다 한 번씩만 집계한다.
    seasonal = {start: fit_seasonal_naive(base, start) for start in dates}
    rows, audits = [], []
    for horizon in horizons:
        frame = add_lag_baselines(build_horizon_frame(base, horizon, rules, lots), lookup)
        for fold, start in enumerate(dates, 1):
            train, validation, test = split_by_label_time(frame, start)
            if train.empty:
                raise ValueError(f"{start.date()} H={horizon}: 학습 가능한 연속 관측이 없습니다.")
            model = LGBMRegressor(objective="quantile", alpha=.5, **params)
            model.fit(train[FEATURES], train["delta_target"])
            scored = apply_seasonal_naive(seasonal[start], test) if len(test) else test.assign(
                **{pred_column("seasonal_naive"): np.nan})
            prediction = fold_predictions(model, scored, fold, start)
            rows.append(prediction)
            audits.append(dict(
                horizon=horizon, fold=fold, test_date=str(start.date()),
                train_n=len(train), validation_n=len(validation), test_n=len(test),
                train_target_max=str(train["target_time"].max()) if len(train) else None,
                test_start=str(start), test_end_exclusive=str(start + pd.Timedelta(days=1)),
                seasonal_table_rows=int(len(seasonal[start])),
                **{f"{name}_available_n": int(prediction[pred_column(name)].notna().sum())
                   for name in BASELINES}))
            if progress:
                print(f"H={horizon:>4}분 {fold}/{len(dates)} · {start.date()} · "
                      f"train={len(train):,} test={len(test):,}", flush=True)
    paired = pd.concat(rows, ignore_index=True)
    if paired.duplicated(KEYS).any():
        raise AssertionError("test 날짜 간 평가 행이 중복됩니다.")
    return paired, pd.DataFrame(audits)


def method_scores(frame, models=(ML,)):
    """같은 행에서 모델과 기준선의 MAE. 행이 없으면 NaN을 남긴다.

    models에 여러 모델을 주면 8-3의 M0/M1/M2를 같은 행에서 나란히 채점한다.
    """
    rows = [score_predictions(frame, name) for name in models]
    return pd.concat([pd.DataFrame(rows), baseline_scores(frame, BASELINES)], ignore_index=True)


def summarize(paired, horizons, models=(ML,)):
    """지평선 × 행집합 × 평일/주말의 방법별 MAE."""
    rows = []
    for horizon in horizons:
        at_horizon = paired.loc[paired["horizon"].eq(horizon)]
        for label, baselines in ROW_SETS.items():
            common = at_horizon.loc[common_mask(at_horizon, baselines)]
            for day in ("all", *DAY_GROUPS):
                subset = common if day == "all" else common.loc[common["weekday_group"].eq(day)]
                scored = method_scores(subset, models)
                scored = scored.loc[scored["baseline"].isin((*models, *baselines))]
                rows.append(scored.assign(horizon=horizon, row_set=label, weekday_group=day,
                                          n_lots=subset["parking_id"].nunique()))
    return pd.concat(rows, ignore_index=True).rename(columns={"baseline": "method"})[
        ["horizon", "row_set", "weekday_group", "method", "n", "n_missing", "n_lots",
         "mae", "rmse"]]


def per_lot_scores(paired, horizons, model=ML):
    rows = []
    for horizon in horizons:
        at_horizon = paired.loc[paired["horizon"].eq(horizon)]
        for label, baselines in ROW_SETS.items():
            common = at_horizon.loc[common_mask(at_horizon, baselines)]
            for pid, group in common.groupby("parking_id", observed=True):
                error = np.abs(group["actual_occ"] - group[pred_column(model)])
                rows.append(dict(horizon=horizon, row_set=label, model=model, parking_id=int(pid),
                                 n=len(group), ml_mae=float(error.mean()),
                                 evaluable=len(group) >= MIN_LOT_ROWS,
                                 passes=bool(len(group) >= MIN_LOT_ROWS
                                             and error.mean() <= MAE_TARGET_PP)))
    return pd.DataFrame(rows)


def by_day_scores(paired, horizons, model=ML):
    """test 날짜별 모델 vs 최강 기준선. 방향 재현 확인에 쓴다."""
    rows = []
    for horizon in horizons:
        at_horizon = paired.loc[paired["horizon"].eq(horizon)]
        for label, baselines in ROW_SETS.items():
            common = at_horizon.loc[common_mask(at_horizon, baselines)]
            for test_date, group in common.groupby("test_date", observed=True):
                scored = method_scores(group, (model,))
                scored = scored.loc[scored["baseline"].isin(baselines)]
                name, mae = strongest_baseline(scored)
                ml_mae = float(np.abs(group["actual_occ"] - group[pred_column(model)]).mean())
                stamp = pd.Timestamp(test_date)
                rows.append(dict(horizon=horizon, row_set=label, model=model, test_date=test_date,
                                 is_weekend=bool(stamp.weekday() >= 5), n=len(group),
                                 ml_mae=ml_mae, strongest_baseline=name, strongest_mae=mae,
                                 ml_better=bool(mae == mae and ml_mae < mae)))
    return pd.DataFrame(rows)


def weekend_weeks(dates):
    """test 날짜에 포함된 서로 다른 주말의 수(ISO 주 기준)."""
    return len({d.isocalendar()[:2] for d in dates if d.weekday() >= 5})


def certify(summary, per_lot, by_day, horizons, weekend_week_count, model=ML):
    """지평선별 인증 판정. 근거 수치를 모두 같은 행에 남긴다."""
    rows = []
    for label in ROW_SETS:
        for horizon in horizons:
            table = summary[(summary["horizon"].eq(horizon)) & (summary["row_set"].eq(label))]
            baseline_rows = table[table["method"].isin(BASELINES)
                                  & table["weekday_group"].eq("all")]
            best_name, best_mae = strongest_baseline(
                baseline_rows.rename(columns={"method": "baseline"}))

            def mae_of(day):
                row = table[table["method"].eq(model) & table["weekday_group"].eq(day)]
                return float(row["mae"].iloc[0]) if len(row) else np.nan

            def n_of(day):
                row = table[table["method"].eq(model) & table["weekday_group"].eq(day)]
                return int(row["n"].iloc[0]) if len(row) else 0

            lots = per_lot[(per_lot["horizon"].eq(horizon)) & (per_lot["row_set"].eq(label))
                           & per_lot["evaluable"]]
            lot_rate = float(lots["passes"].mean()) if len(lots) else np.nan
            days = by_day[(by_day["horizon"].eq(horizon)) & (by_day["row_set"].eq(label))]
            reproduced = bool(len(days) and days["ml_better"].all())

            overall, weekday, weekend = (mae_of(d) for d in ("all", *DAY_GROUPS))
            checks = {
                "overall_mae_ok": bool(overall <= MAE_TARGET_PP),
                "weekday_mae_ok": bool(weekday <= MAE_TARGET_PP),
                "weekend_mae_ok": bool(weekend <= MAE_TARGET_PP),
                "beats_strongest_baseline": bool(best_mae == best_mae and overall < best_mae),
                "lot_coverage_ok": bool(lot_rate == lot_rate and lot_rate >= LOT_PASS_RATE),
                "direction_reproduced": reproduced,
                "weekend_weeks_ok": bool(weekend_week_count >= MIN_WEEKEND_WEEKS),
            }
            # 주말 2회는 데이터 길이의 문제라 모델과 무관하다. performance_plan이 요구한
            # "현재 데이터로는 탐색 결과만" 내기 위해 그 조건만 뺀 판정도 함께 남긴다.
            provisional = all(v for k, v in checks.items() if k != "weekend_weeks_ok")
            rows.append(dict(
                row_set=label, model=model, horizon=horizon, n=n_of("all"),
                n_weekday=n_of("weekday"), n_weekend=n_of("weekend"),
                ml_mae=overall, ml_mae_weekday=weekday, ml_mae_weekend=weekend,
                strongest_baseline=best_name, strongest_mae=best_mae,
                lots_evaluable=int(len(lots)), lot_pass_rate=lot_rate,
                weekend_weeks=weekend_week_count,
                certified=all(checks.values()), provisional=provisional, **checks))
    return pd.DataFrame(rows)


def certified_max_horizon(certification, label, horizons, column="certified"):
    """미달 지평선 이후를 자동 통과시키지 않는다. 연속으로 통과한 마지막 지평선만 반환한다."""
    table = certification[certification["row_set"].eq(label)].set_index("horizon")
    best = None
    for horizon in sorted(horizons):
        if horizon not in table.index or not bool(table.loc[horizon, column]):
            break
        best = horizon
    return best


def run(db_path=PARKING_DB, rules_path=PARKING_ACCESS_RULES_CSV, horizons=HORIZONS,
        table_dir=TABLES, prediction_dir=PROCESSED / "a23", test_days=7, min_train_days=7,
        model_params=None, data_end=None):
    print("=== A23 지평선별 오차 곡선과 인증 판정 ===", flush=True)
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

    summary = summarize(paired, horizons)
    per_lot = per_lot_scores(paired, horizons)
    by_day = by_day_scores(paired, horizons)
    certification = certify(summary, per_lot, by_day, horizons, weekend_week_count)
    ceilings = {label: certified_max_horizon(certification, label, horizons) for label in ROW_SETS}
    provisional_ceilings = {label: certified_max_horizon(certification, label, horizons,
                                                         "provisional") for label in ROW_SETS}
    passed = any(v is not None for v in ceilings.values())

    table_dir, prediction_dir = Path(table_dir), Path(prediction_dir)
    table_dir.mkdir(parents=True, exist_ok=True)
    prediction_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in (("comparison", summary), ("by_day", by_day), ("per_lot", per_lot),
                        ("splits", audits), ("certification", certification),
                        ("anomalies", anomalies)):
        frame.to_csv(table_dir / f"a23_{name}.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(prediction_dir / "predictions.csv.gz", index=False, compression="gzip")

    source_paths = [Path(__file__), Path(__file__).with_name("a23_common.py"),
                    Path(__file__).with_name("a22_baseline_api_alive.py"),
                    Path(__file__).with_name("a20_matched_model_comparison.py")]
    manifest = {
        **info, "protocol": "a23_horizon_curve_v1",
        # 주말 2회 재현 조건을 채우지 못하면 결과가 좋아도 partial이다.
        "status": "complete" if passed and weekend_week_count >= MIN_WEEKEND_WEEKS else "partial",
        "horizons": list(horizons), "methods": list(METHODS),
        "row_sets": {k: list(v) for k, v in ROW_SETS.items()},
        "test_dates": [str(d.date()) for d in dates],
        "test_days": test_days, "min_train_days": min_train_days,
        "data_end_kst": str(data_end) if data_end else None,
        "rows_truncated_after_data_end": int(truncated_n),
        "weekend_weeks_in_test": weekend_week_count,
        "certified_max_horizon": ceilings,
        "provisional_max_horizon": provisional_ceilings,
        "criteria": {"mae_target_pp": MAE_TARGET_PP, "lot_pass_rate": LOT_PASS_RATE,
                     "min_lot_rows": MIN_LOT_ROWS, "min_weekend_weeks": MIN_WEEKEND_WEEKS},
        "model": {"objective": "quantile", "alpha": .5, **PARAMS, **(model_params or {})},
        "features": FEATURES, "prediction_clip": [0, 100], "matched_n": len(paired),
        "holidays_kst": sorted(str(d) for d in HOLIDAYS_KST),
        "holiday_grid_rows_affected": holiday_counts,
        "anomaly_candidates_n": int(len(anomalies)),
        "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
        "notes": [
            "지평선마다 직접 학습한 모델이며 재귀 예측으로 이어 붙이지 않았다.",
            "fold는 정답 시각(target_time) 기준으로 나눈다. A20의 관측시각 기준 test 창은 "
            "지평선이 하루에 가까워지면 행이 0이 되어 720·1440분을 평가할 수 없다.",
            "label_valid 게이트와 history/future 연속성 공식은 A22/A20과 동일하다.",
            "학습은 eligible 행 전체에서 하고, 채점만 기준선이 모두 정의된 행으로 제한한다.",
            "core 행 집합은 필수 기준선(persistence·lag_24h)만 요구한다. all 집합은 "
            "lag_7d와 seasonal naive까지 요구하며, 보유 기간이 짧아 주말 행이 거의 없다.",
            "720·1440분은 평가 가능 주차장이 줄어드는 지평선이다. 통과해도 표본 구성을 함께 본다.",
            "주말이 2회 미만인 test 구간에서는 방향 재현을 확인할 수 없어 status는 partial이다.",
            "provisional_max_horizon은 주말 2회 조건만 뺀 탐색 결과다. 인증 수치로 쓰지 않는다.",
            "기존 U11 학습 산출물과 서비스 predictor.pkl을 갱신하지 않는다.",
        ],
    }
    (table_dir / "a23_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    for label in ROW_SETS:
        print(f"\n=== 인증 판정 · 행집합 {label} ===", flush=True)
        view = certification[certification["row_set"].eq(label)][
            ["horizon", "n", "ml_mae", "ml_mae_weekday", "ml_mae_weekend",
             "strongest_baseline", "strongest_mae", "lot_pass_rate", "certified"]]
        print(view.to_string(index=False, float_format=lambda x: f"{x:.3f}"), flush=True)
        print(f"연속 통과 최대 지평선: 인증 {ceilings[label]} · "
              f"탐색(주말 2회 조건 제외) {provisional_ceilings[label]}", flush=True)
    print(f"\n상태: {manifest['status']} (주말 {weekend_week_count}회 / 요구 {MIN_WEEKEND_WEEKS}회)")
    print(f"저장 완료: {table_dir}", flush=True)
    return certification


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
