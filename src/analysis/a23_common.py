#!/usr/bin/env python3
"""A23 공통 평가 기반: 지평선 확장(15~1440분)과 기준선 4종.

python src/analysis/a23_common.py            # 지평선별 평가 가능 행 진단만 수행
python src/analysis/a23_common.py --horizons 15,60,1440

이 모듈은 모델을 학습하지 않는다. `performance_plan.md`의 A23이 요구하는
"지평선별 동일 평가행 + 가장 강한 기준선"을 만들기 위한 재료만 만든다.

- 평가행: A22의 label_valid 게이트와 A20의 history/future 연속성 공식을 그대로 재사용한다.
- 기준선: persistence, lag_24h, lag_7d, 요일×시각 seasonal naive
- seasonal naive만 fold 의존(학습 구간에서만 집계)이므로 fit/apply로 분리한다.
- 인증 비교는 네 기준선이 모두 정의된 `common` 행에서만 한다. 각 기준선의 단독
  제공률은 커버리지 표에 따로 남기고, 없는 값을 채워 넣지 않는다.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.config import PARKING_DB, PARKING_ACCESS_RULES_CSV, TABLES
from src.analysis.a20_matched_model_comparison import (
    DAY_GROUPS, load_inputs, build_horizon_frame,
)
from src.analysis.a22_baseline_api_alive import (
    HOLIDAYS_KST, build_features_label_valid,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# performance_plan.md A23의 지평선. A20/A22의 (15,30,60,120)을 포함한 확장이다.
HORIZONS = (15, 30, 60, 120, 180, 240, 360, 720, 1440)

# 기준선 이름 → 예측 컬럼. persistence는 A20과 같은 정의(occ_now)를 쓴다.
BASELINES = ("persistence", "lag_24h", "lag_7d", "seasonal_naive")
# 보유 기간이 18일이라 lag_7d와 seasonal naive는 제공률이 낮다. 특히 주말은 같은 요일이
# 2~3회뿐이라 두 기준선이 거의 비어 인증 행에서 주말이 통째로 사라진다. performance_plan.md는
# persistence와 lag_24h만 필수로, lag_7d는 "가능한 경우"로 둔다. 그래서 행 집합을 두 벌로
# 만들고(core=필수 기준선, all=+lag_7d+seasonal naive) 어느 쪽 수치인지 항상 함께 보고한다.
BASELINES_CORE = ("persistence", "lag_24h")
ROW_SETS = {"core": BASELINES_CORE, "all": BASELINES}
LAG_OFFSETS = {"lag_24h": pd.Timedelta(days=1), "lag_7d": pd.Timedelta(days=7)}
SEASONAL_KEYS = ["parking_id", "target_weekday", "target_minute_of_day"]


def pred_column(name):
    return f"pred_{name}"


def occ_lookup(base):
    """label_valid 게이트를 통과한 occ만 (주차장, 시각)으로 조회한다.

    게이트에서 NaN이 된 시각은 조회해도 NaN이다. 기준선이 운영시간 밖 정지값을
    정답처럼 쓰지 못하게 하려는 것이며, A22의 학습·정답 게이트와 같은 기준이다.
    """
    series = base.set_index(["parking_id", "ts_kst"])["occ"]
    if series.index.has_duplicates:
        raise ValueError("같은 주차장/시각이 중복됩니다. 관측 격자를 확인하세요.")
    return series


def add_lag_baselines(frame, lookup):
    """target_time에서 하루/일주일 전의 실측값을 기준선 예측으로 붙인다.

    5분 격자 위의 정확한 시각만 쓰고 근처 값으로 대체하지 않는다.
    """
    frame = frame.copy()
    frame[pred_column("persistence")] = frame["occ_now"]
    for name, offset in LAG_OFFSETS.items():
        index = pd.MultiIndex.from_arrays(
            [frame["parking_id"], frame["target_time"] - offset])
        frame[pred_column(name)] = lookup.reindex(index).to_numpy()
    return frame


def fit_seasonal_naive(base, before):
    """`before` 이전 관측만으로 주차장×요일×시각 중앙값 표를 만든다.

    test·validation 구간을 보지 않는다. 표본이 없으면 행 자체를 만들지 않으며,
    조회 실패는 NaN으로 남겨 해당 기준선을 그 행에서 제공하지 않는다.
    """
    history = base.loc[base["ts_kst"] < before, ["parking_id", "ts_kst", "occ"]].dropna(subset=["occ"])
    if history.empty:
        return pd.DataFrame(columns=SEASONAL_KEYS + ["seasonal_occ", "seasonal_n"])
    stamp = history["ts_kst"]
    history = history.assign(
        target_weekday=stamp.dt.weekday,
        target_minute_of_day=stamp.dt.hour * 60 + stamp.dt.minute)
    table = (history.groupby(SEASONAL_KEYS, observed=True)["occ"]
             .agg(seasonal_occ="median", seasonal_n="size").reset_index())
    return table


def apply_seasonal_naive(table, frame, min_samples=2):
    """seasonal naive 예측을 target_time 기준으로 붙인다. 표본 부족은 NaN이다."""
    frame = frame.copy()
    column = pred_column("seasonal_naive")
    if table.empty:
        frame[column] = np.nan
        return frame
    keyed = frame.assign(
        target_weekday=frame["target_time"].dt.weekday,
        target_minute_of_day=frame["target_time"].dt.hour * 60 + frame["target_time"].dt.minute)
    merged = keyed.merge(table, on=SEASONAL_KEYS, how="left")
    value = merged["seasonal_occ"].where(merged["seasonal_n"].ge(min_samples))
    frame[column] = value.to_numpy()
    return frame


def common_mask(frame, baselines=BASELINES):
    """주어진 기준선이 모두 정의된 평가행. 인증 비교는 이 행에서만 한다."""
    mask = frame["eligible"].to_numpy(dtype=bool, copy=True)
    for name in baselines:
        mask &= frame[pred_column(name)].notna().to_numpy()
    return pd.Series(mask, index=frame.index)


def baseline_scores(frame, baselines=BASELINES):
    """같은 행에서 기준선별 MAE. 비어 있으면 NaN을 남기고 0으로 만들지 않는다."""
    rows = []
    actual = frame["actual_occ"].to_numpy(dtype=float)
    for name in baselines:
        error = np.abs(actual - frame[pred_column(name)].to_numpy(dtype=float))
        rows.append(dict(baseline=name, n=len(frame),
                         mae=float(error.mean()) if len(frame) else np.nan,
                         rmse=float(np.sqrt((error ** 2).mean())) if len(frame) else np.nan))
    return pd.DataFrame(rows)


def strongest_baseline(scores):
    """MAE가 가장 낮은 기준선. A23의 인증 조건에서 'AI가 이겨야 할 대상'이다."""
    usable = scores.dropna(subset=["mae"])
    if usable.empty:
        return None, np.nan
    best = usable.loc[usable["mae"].idxmin()]
    return str(best["baseline"]), float(best["mae"])


def coverage_row(frame, horizon):
    """지평선별 평가 가능 행 진단. 어떤 기준선 때문에 행이 줄었는지 남긴다."""
    eligible = frame.loc[frame["eligible"]]
    row = dict(horizon=horizon, grid_n=len(frame),
               eligible_n=len(eligible),
               eligible_lots=int(eligible["parking_id"].nunique()),
               eligible_days=int(eligible["target_time"].dt.normalize().nunique()))
    for name in BASELINES:
        available = int(eligible[pred_column(name)].notna().sum())
        row[f"{name}_n"] = available
        row[f"{name}_rate"] = available / len(eligible) if len(eligible) else np.nan
    for label, baselines in ROW_SETS.items():
        common = frame.loc[common_mask(frame, baselines)]
        row[f"common_{label}_n"] = len(common)
        row[f"common_{label}_lots"] = int(common["parking_id"].nunique())
        row[f"common_{label}_days"] = int(common["target_time"].dt.normalize().nunique())
        for day in DAY_GROUPS:
            row[f"common_{label}_n_{day}"] = int((common["weekday_group"] == day).sum())
    return row


def prepare_horizon(base, horizon, rules, lots, lookup, seasonal_before=None):
    """한 지평선의 평가행 + 기준선 예측. seasonal_before가 없으면 그 기준선은 NaN이다."""
    frame = build_horizon_frame(base, horizon, rules, lots)
    frame = add_lag_baselines(frame, lookup)
    table = (fit_seasonal_naive(base, seasonal_before)
             if seasonal_before is not None else pd.DataFrame())
    return apply_seasonal_naive(table, frame)


def run_coverage(db_path=PARKING_DB, rules_path=PARKING_ACCESS_RULES_CSV,
                 horizons=HORIZONS, table_dir=TABLES, holdout_days=3):
    """모델 없이 지평선별 평가 가능 행만 진단한다. A23 본실험 전 사전 점검용이다."""
    print("=== A23 사전 점검: 지평선별 평가 가능 행과 기준선 제공률 ===", flush=True)
    obs, lots, rules, info = load_inputs(db_path, rules_path)
    print(f"대상 {len(rules)}곳 · {len(obs):,}행 · {info['start']} ~ {info['end']}", flush=True)

    base, anomalies, holiday_counts = build_features_label_valid(
        obs, lots, rules, holidays=HOLIDAYS_KST, progress=True)
    lookup = occ_lookup(base)
    # 사전 점검에서도 test 구간을 보지 않도록 seasonal naive는 홀드아웃 이전만 집계한다.
    seasonal_before = base["ts_kst"].max().normalize() - pd.Timedelta(days=holdout_days)

    rows = []
    for horizon in horizons:
        frame = prepare_horizon(base, horizon, rules, lots, lookup, seasonal_before)
        rows.append(coverage_row(frame, horizon))
        latest = rows[-1]
        print(f"H={horizon:>4}분 · 평가행 {latest['eligible_n']:,}({latest['eligible_lots']}곳) · "
              f"core 공통 {latest['common_core_n']:,}"
              f"(평일 {latest['common_core_n_weekday']:,}/주말 {latest['common_core_n_weekend']:,}) · "
              f"lag_7d 포함 {latest['common_all_n']:,}"
              f"(평일 {latest['common_all_n_weekday']:,}/주말 {latest['common_all_n_weekend']:,})",
              flush=True)

    coverage = pd.DataFrame(rows)
    table_dir = Path(table_dir)
    table_dir.mkdir(parents=True, exist_ok=True)
    coverage.to_csv(table_dir / "a23_coverage.csv", index=False, encoding="utf-8-sig")
    manifest = {
        **info, "protocol": "a23_coverage_v1", "status": "diagnostic",
        "horizons": list(horizons), "baselines": list(BASELINES),
        "row_sets": {k: list(v) for k, v in ROW_SETS.items()},
        "seasonal_naive_history_end_kst": str(seasonal_before),
        "seasonal_naive_min_samples": 2,
        "holidays_kst": sorted(str(d) for d in HOLIDAYS_KST),
        "holiday_grid_rows_affected": holiday_counts,
        "anomaly_candidates_n": int(len(anomalies)),
        "notes": [
            "이 실행은 모델을 학습하지 않는다. 평가행과 기준선 제공률만 진단한다.",
            "label_valid 게이트와 history/future 연속성 공식은 A22/A20과 동일하다.",
            "lag_24h/lag_7d는 target_time에서 정확히 24시간/7일 전의 격자값만 쓴다.",
            "seasonal naive는 홀드아웃 이전 관측만으로 집계하며 표본 2개 미만은 제공하지 않는다.",
            "core 행 집합은 performance_plan이 필수로 둔 persistence와 lag_24h만 요구한다. "
            "보유 18일로는 lag_7d와 seasonal naive 제공률이 낮고, 특히 주말은 같은 요일 표본이 "
            "1회뿐이라 all 집합에서 주말 행이 거의 사라진다. 두 집합을 항상 함께 보고한다.",
            "공통 행이 0인 지평선은 현재 데이터로 인증할 수 없다는 뜻이며 통과로 처리하지 않는다.",
        ],
    }
    (table_dir / "a23_coverage_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"\n저장 완료: {table_dir / 'a23_coverage.csv'}", flush=True)
    return coverage


def parse_horizons(text):
    if not text:
        return HORIZONS
    values = tuple(int(v) for v in text.split(","))
    for v in values:
        if v <= 0 or v % 5:
            raise ValueError(f"지원하지 않는 horizon: {v}")
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=PARKING_DB)
    parser.add_argument("--rules", type=Path, default=PARKING_ACCESS_RULES_CSV)
    parser.add_argument("--horizons", type=str, default="")
    parser.add_argument("--holdout-days", type=int, default=3)
    args = parser.parse_args()
    run_coverage(args.db, args.rules, parse_horizons(args.horizons),
                 holdout_days=args.holdout_days)


if __name__ == "__main__":
    main()
