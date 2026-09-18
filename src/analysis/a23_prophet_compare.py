#!/usr/bin/env python3
"""A23 비교군 M2: 360분 이상 지평선에서 Prophet을 M0/M1과 같은 행에서 채점.

python src/analysis/a23_prophet_compare.py
python src/analysis/a23_prophet_compare.py --horizons 360,720

`performance_plan.md`는 Prophet을 6~24시간 구간의 **비교군**으로만 둔다. 검증 없이
앙상블하지 않으며, 이 스크립트도 서비스 모델을 만들지 않는다.

- 예측 대상 행은 8-3(M0/M1)이 저장한 평가행을 그대로 읽는다. 행을 새로 만들지 않는다.
- Prophet은 주차장별 단변량 모델이라 (주차장, fold)마다 fold 시작 이전 관측만으로 학습한다.
- 학습 입력은 label_valid 게이트를 통과한 관측뿐이다. 운영시간 밖 정지값은 넣지 않는다.
- 채점은 세 모델이 모두 예측을 낸 행에서만 한다. 빈 예측을 채우지 않는다.
"""
import argparse
import hashlib
import json
import logging
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.config import PARKING_DB, PARKING_ACCESS_RULES_CSV, PROCESSED, TABLES
from src.analysis.a20_matched_model_comparison import load_inputs
from src.analysis.a22_baseline_api_alive import HOLIDAYS_KST, build_features_label_valid
from src.analysis.a23_common import BASELINES, ROW_SETS, common_mask, pred_column
from src.analysis.a23_model_upgrade import M0, M1

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

M2 = "m2"
PROPHET_HORIZONS = (360, 720, 1440)   # 계획이 정한 "360분 이상" 비교 구간
MIN_TRAIN_POINTS = 288                # 하루치 5분 관측. 이보다 적으면 학습하지 않는다.
PROPHET_KWARGS = dict(daily_seasonality=True, weekly_seasonality=True,
                      yearly_seasonality=False)


def quiet_prophet():
    """cmdstanpy가 적합마다 찍는 진행 로그를 끈다. 경고는 숨기지 않고 상위에서 다룬다.

    로거 설정은 prophet을 import한 뒤에 해야 한다. import 시점에 자기 로거를 다시 세운다.
    """
    import prophet  # noqa: F401
    for name in ("cmdstanpy", "prophet"):
        logging.getLogger(name).setLevel(logging.ERROR)


def history_for(base, parking_id, before):
    """fold 시작 이전의 label_valid 관측만 Prophet 입력으로 만든다(tz 제거)."""
    rows = base.loc[base["parking_id"].eq(parking_id) & (base["ts_kst"] < before),
                    ["ts_kst", "occ"]].dropna(subset=["occ"])
    return pd.DataFrame({"ds": rows["ts_kst"].dt.tz_localize(None),
                         "y": rows["occ"].to_numpy()})


def forecast_lot(history, targets):
    """한 주차장·한 fold의 Prophet 예측. 학습 표본이 적으면 예측하지 않는다."""
    from prophet import Prophet
    if len(history) < MIN_TRAIN_POINTS:
        return pd.Series(np.nan, index=targets)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = Prophet(**PROPHET_KWARGS)
        model.fit(history)
        future = pd.DataFrame({"ds": pd.DatetimeIndex(targets).tz_localize(None)})
        predicted = model.predict(future)["yhat"].to_numpy()
    return pd.Series(np.clip(predicted, 0, 100), index=targets)


def add_prophet(paired, base, horizons, progress=True):
    """평가행에 M2 예측을 붙인다. (주차장, fold)마다 한 번만 학습한다."""
    quiet_prophet()
    wanted = paired.loc[paired["horizon"].isin(horizons)]
    paired = paired.copy()
    paired[pred_column(M2)] = np.nan
    if wanted.empty:
        return paired, pd.DataFrame(columns=["test_date", "parking_id", "train_n", "target_n"])
    pairs = wanted[["test_date", "parking_id"]].drop_duplicates().sort_values(
        ["test_date", "parking_id"])
    audits, predictions = [], {}
    for i, (test_date, parking_id) in enumerate(pairs.itertuples(index=False), 1):
        start = pd.Timestamp(test_date, tz="Asia/Seoul")
        rows = wanted.loc[wanted["test_date"].eq(test_date)
                          & wanted["parking_id"].eq(parking_id)]
        targets = pd.DatetimeIndex(rows["target_time"].unique()).sort_values()
        history = history_for(base, parking_id, start)
        forecast = forecast_lot(history, targets)
        for target, value in forecast.items():
            predictions[(test_date, parking_id, target)] = value
        audits.append(dict(test_date=test_date, parking_id=int(parking_id),
                           train_n=len(history), target_n=len(targets),
                           fitted=bool(len(history) >= MIN_TRAIN_POINTS)))
        if progress and (i % 10 == 0 or i == len(pairs)):
            print(f"Prophet 적합: {i}/{len(pairs)} (주차장, fold)", flush=True)
    # Prophet 예측은 지평선과 무관하게 (주차장, fold, 정답시각)으로 정해진다. 그래도 이 실험의
    # 범위는 요청한 지평선뿐이므로 다른 지평선 행에는 값을 채우지 않는다.
    wanted_rows = paired["horizon"].isin(horizons)
    key = list(zip(paired["test_date"], paired["parking_id"], paired["target_time"]))
    values = np.array([predictions.get(k, np.nan) for k in key], dtype=float)
    paired[pred_column(M2)] = np.where(wanted_rows.to_numpy(), values, np.nan)
    return paired, pd.DataFrame(audits)


def three_way(paired, horizons):
    """M0·M1·M2가 모두 예측을 낸 행에서만 세 모델을 비교한다."""
    rows = []
    for horizon in horizons:
        at_horizon = paired.loc[paired["horizon"].eq(horizon)]
        for label, baselines in ROW_SETS.items():
            common = at_horizon.loc[common_mask(at_horizon, baselines)]
            scored = common.loc[common[pred_column(M2)].notna()]
            row = dict(horizon=horizon, row_set=label, n=len(scored),
                       n_dropped_without_m2=len(common) - len(scored),
                       n_lots=int(scored["parking_id"].nunique()))
            for name in (M0, M1, M2):
                error = np.abs(scored["actual_occ"] - scored[pred_column(name)])
                row[f"{name}_mae"] = float(error.mean()) if len(scored) else np.nan
            best = min((M0, M1, M2), key=lambda n: row[f"{n}_mae"]
                       if row[f"{n}_mae"] == row[f"{n}_mae"] else np.inf)
            row["best_model"] = best if len(scored) else None
            rows.append(row)
    return pd.DataFrame(rows)


def run(db_path=PARKING_DB, rules_path=PARKING_ACCESS_RULES_CSV, horizons=PROPHET_HORIZONS,
        table_dir=TABLES, prediction_dir=PROCESSED / "a23p",
        paired_path=PROCESSED / "a23m" / "predictions.csv.gz"):
    print("=== A23 비교군 M2: Prophet (360분 이상) ===", flush=True)
    paired_path = Path(paired_path)
    if not paired_path.exists():
        raise FileNotFoundError(
            f"{paired_path}가 없습니다. 먼저 src/analysis/a23_model_upgrade.py를 실행하세요.")
    paired = pd.read_csv(paired_path, compression="gzip")
    for column in ("ts_kst", "target_time"):
        stamp = pd.to_datetime(paired[column], format="mixed")
        paired[column] = (stamp.dt.tz_localize("Asia/Seoul") if stamp.dt.tz is None
                          else stamp.dt.tz_convert("Asia/Seoul"))

    obs, lots, rules, info = load_inputs(db_path, rules_path)
    base, _, _ = build_features_label_valid(obs, lots, rules, holidays=HOLIDAYS_KST,
                                            progress=True)
    scored, audits = add_prophet(paired, base, horizons)
    comparison = three_way(scored, horizons)

    table_dir, prediction_dir = Path(table_dir), Path(prediction_dir)
    table_dir.mkdir(parents=True, exist_ok=True)
    prediction_dir.mkdir(parents=True, exist_ok=True)
    comparison.to_csv(table_dir / "a23p_comparison.csv", index=False, encoding="utf-8-sig")
    audits.to_csv(table_dir / "a23p_fits.csv", index=False, encoding="utf-8-sig")
    keep = scored.loc[scored["horizon"].isin(horizons)]
    keep.to_csv(prediction_dir / "predictions.csv.gz", index=False, compression="gzip")

    from importlib.metadata import version
    source_paths = [Path(__file__), Path(__file__).with_name("a23_model_upgrade.py")]
    manifest = {
        **info, "protocol": "a23_prophet_compare_v1", "status": "comparison_only",
        "horizons": list(horizons), "models": [M0, M1, M2], "baselines": list(BASELINES),
        "paired_source": str(paired_path),
        "prophet_kwargs": PROPHET_KWARGS, "min_train_points": MIN_TRAIN_POINTS,
        "fits_attempted": int(len(audits)), "fits_done": int(audits["fitted"].sum())
        if len(audits) else 0,
        "prediction_clip": [0, 100],
        "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
        "versions": {p: version(p) for p in ("prophet", "cmdstanpy", "pandas", "numpy")},
        "notes": [
            "Prophet은 비교군이다. 서비스 모델이나 앙상블에 넣지 않는다.",
            "평가행은 8-3 산출물을 그대로 읽었고 새로 만들지 않았다.",
            "(주차장, fold)마다 fold 시작 이전의 label_valid 관측만으로 학습했다.",
            "세 모델이 모두 예측을 낸 행에서만 채점했다. 빠진 행 수를 함께 보고한다.",
        ],
    }
    (table_dir / "a23p_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    print("\n=== M0 / M1 / M2(Prophet) — 같은 행 ===", flush=True)
    print(comparison.to_string(index=False, float_format=lambda x: f"{x:.3f}"), flush=True)
    print(f"\n저장 완료: {table_dir / 'a23p_comparison.csv'}", flush=True)
    return comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=PARKING_DB)
    parser.add_argument("--rules", type=Path, default=PARKING_ACCESS_RULES_CSV)
    parser.add_argument("--horizons", type=str, default="")
    args = parser.parse_args()
    horizons = (tuple(int(v) for v in args.horizons.split(",")) if args.horizons
                else PROPHET_HORIZONS)
    run(args.db, args.rules, horizons)


if __name__ == "__main__":
    main()
