#!/usr/bin/env python3
"""A20: 같은 미래 test 행에서 LightGBM Δ 중앙값과 Persistence 비교.

python src/analysis/a20_matched_model_comparison.py
마지막 진행 중 날짜를 제외한 최근 3일을 하루씩 평가한다. 매 fold에서
과거 학습 / 직전 하루 validation / 다음 하루 test를 라벨 시각으로 분리한다.
점 예측 실험이며 확률·구간 보정 및 서비스 모델 교체를 수행하지 않는다.
"""
import argparse
import hashlib
import io
import json
import sqlite3
import sys
import warnings
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.config import PARKING_DB, PARKING_ACCESS_RULES_CSV, PROCESSED, TABLES, RANDOM_STATE
from scripts.fill_access_schedule import validate_rule_ids
from src.analysis.a19_persistence_baseline import accessible_at, HORIZONS
from src.features.observation_grid import observation_grid
from src.features.temporal import oprtime_features, OPR_COLS

# A16의 전역 warnings 설정은 가져오지 않고 기존 U11의 과거 피처만 재사용한다.
with warnings.catch_warnings():
    from src.analysis.a16_stratified_weekend import feats, INTX

FEATURES = INTX + ["opr_" + c for c in OPR_COLS]
PARAMS = dict(n_estimators=120, learning_rate=.08, num_leaves=31,
              min_child_samples=40, random_state=RANDOM_STATE, n_jobs=4, verbosity=-1)
ACCESS_GROUPS = ("accessible", "closed", "unknown")
DAY_GROUPS = ("weekday", "weekend")
KEYS = ["parking_id", "ts_kst", "target_time", "horizon"]


def frame_hash(frame):
    return hashlib.sha256(frame.to_csv(index=False).encode("utf-8")).hexdigest()


def load_inputs(db_path, rules_path):
    """동일한 읽기 트랜잭션에서 관측과 주차장 메타데이터를 고정한다."""
    rules_bytes = Path(rules_path).read_bytes()
    with sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        obs = pd.read_sql_query(
            "SELECT parking_id,ts_kst,cell_cnt,park_count FROM obs ORDER BY parking_id,ts_kst", db)
        lots = pd.read_sql_query("SELECT * FROM lots ORDER BY parking_id", db)
    if obs.empty:
        raise ValueError("DB에 관측이 없습니다.")
    if lots["parking_id"].duplicated().any():
        raise ValueError("lots.parking_id가 중복됩니다.")
    rules = validate_rule_ids(pd.read_csv(io.BytesIO(rules_bytes)), lots)
    if "predict_ok" not in rules:
        raise ValueError("분석용 CSV에 predict_ok가 없습니다. data/processed의 가로형 CSV를 사용하세요.")
    flags = pd.to_numeric(rules["predict_ok"], errors="coerce")
    if not flags.isin([0, 1]).all():
        raise ValueError("predict_ok는 0 또는 1이어야 합니다.")
    active = rules.loc[flags.eq(1)].copy()
    if active.empty:
        raise ValueError("predict_ok=1인 주차장이 없습니다.")
    info = {"raw_n": len(obs), "raw_hash": frame_hash(obs), "lots_hash": frame_hash(lots),
            "rules_hash": hashlib.sha256(rules_bytes).hexdigest(), "active_lots": len(active)}
    obs = obs.loc[obs["parking_id"].isin(active["parking_id"])].copy()
    if obs.empty:
        raise ValueError("예측 대상의 관측이 없습니다.")
    stamp = pd.to_datetime(obs["ts_kst"], format="mixed")
    if stamp.isna().any():
        raise ValueError("관측 시각이 비어 있습니다.")
    if stamp.dt.tz is None:
        stamp = stamp.dt.tz_localize("Asia/Seoul")
    else:
        stamp = stamp.dt.tz_convert("Asia/Seoul")
    obs["ts_kst"] = stamp
    if obs.duplicated(["parking_id", "ts_kst"]).any():
        raise ValueError("같은 주차장/시각의 관측이 중복됩니다.")
    info.update(active_raw_n=len(obs), start=str(stamp.min()), end=str(stamp.max()),
                absent_ids=sorted(set(active["parking_id"]) - set(obs["parking_id"])))
    return obs, lots, active, info


def test_dates(first, last, test_days=3, min_train_days=7):
    if test_days < 1 or min_train_days < 1:
        raise ValueError("test_days와 min_train_days는 1 이상이어야 합니다.")
    # 마지막 관측이 속한 날짜는 아직 수집 중인 것으로 취급한다.
    end = last.normalize()
    dates = pd.date_range(end - pd.Timedelta(days=test_days), periods=test_days, freq="D")
    if dates[0] - pd.Timedelta(days=1) - first < pd.Timedelta(days=min_train_days):
        raise ValueError("학습 기간이 부족합니다. 최소 학습 기간 + validation 1일 + test 기간의 관측이 필요합니다.")
    return dates


def build_features(obs, lots, progress=False):
    parts = []
    groups = obs.groupby("parking_id", sort=True)
    for i, (pid, raw) in enumerate(groups, 1):
        grid = observation_grid(raw)
        # 모델이 사용하는 최대 lag는 60분이다. 부분 이력 평균으로 평가 표본을 늘리지 않는다.
        grid["history_complete"] = grid["occ"].notna().rolling(13, min_periods=13).sum().eq(13)
        grid["parking_id"] = pid
        parts.append(grid.reset_index())
        if progress and (i % 10 == 0 or i == len(groups)):
            print(f"관측 격자: {i}/{len(groups)}곳", flush=True)
    return feats(pd.concat(parts, ignore_index=True), lots).reset_index(drop=True)


def build_horizon_frame(base, horizon, rules, lots):
    """A19의 미래 연속성, U11의 과거 피처, 실제 출입 분류를 별도로 만든다."""
    if horizon not in HORIZONS:
        raise ValueError(f"지원하지 않는 horizon: {horizon}")
    d = base.copy()
    steps = horizon // 5
    g = d.groupby("parking_id", sort=False)["occ"]
    d["horizon"] = horizon
    d["target_time"] = d["ts_kst"] + pd.Timedelta(minutes=horizon)
    d["actual_occ"] = g.shift(-steps)
    d["future_complete"] = g.transform(
        lambda s: s.notna().iloc[::-1].rolling(steps + 1, min_periods=steps + 1)
        .sum().eq(steps + 1).iloc[::-1])
    d["weekday_group"] = np.where(d["target_time"].dt.weekday >= 5, "weekend", "weekday")
    rule_map = rules.set_index("parking_id").to_dict("index")
    meta = lots.set_index("parking_id").to_dict("index")
    cache, operations, access = {}, [], []
    for pid, target in zip(d["parking_id"], d["target_time"]):
        # 두 기존 함수는 요일·시분만 사용한다. 공휴일 예외는 별도 검증 대상이다.
        key = (pid, target.weekday(), target.hour, target.minute)
        if key not in cache:
            state = accessible_at(rule_map[pid], target)
            cache[key] = (oprtime_features(meta[pid], target),
                          "unknown" if state is None else "accessible" if state else "closed")
        op, state = cache[key]
        operations.append(op)
        access.append(state)
    d = pd.concat([d, pd.DataFrame(operations, index=d.index).add_prefix("opr_")], axis=1)
    d["access_group"] = access
    d["eligible"] = d["future_complete"] & d["history_complete"] & d[FEATURES].notna().all(axis=1)
    d["delta_target"] = d["actual_occ"] - d["occ_now"]
    return d


def split_frame(frame, start):
    cal_start, end = start - pd.Timedelta(days=1), start + pd.Timedelta(days=1)
    valid = frame.loc[frame["eligible"]]
    train = valid.loc[valid["target_time"] < cal_start].copy()
    validation = valid.loc[(valid["ts_kst"] >= cal_start) & (valid["target_time"] < start)].copy()
    test = valid.loc[(valid["ts_kst"] >= start) & (valid["target_time"] < end)].copy()
    if not train.empty:
        assert train["target_time"].max() < cal_start
    if not validation.empty:
        assert validation["ts_kst"].min() >= cal_start and validation["target_time"].max() < start
    if not test.empty:
        assert test["ts_kst"].min() >= start and test["target_time"].max() < end
    return train, validation, test


def predict(model, frame, fold):
    result = frame[KEYS + ["occ_now", "actual_occ", "weekday_group", "access_group"]].copy()
    result["fold"] = fold
    result["persistence_pred"] = result["occ_now"]
    delta = model.predict(frame[FEATURES]) if len(frame) else np.array([], dtype=float)
    if not np.isfinite(delta).all():
        raise ValueError("모델이 유효하지 않은 값을 반환했습니다. 같은 평가 행을 유지할 수 없습니다.")
    result["ml_pred"] = np.clip(result["occ_now"].to_numpy() + delta, 0, 100)
    if not np.isfinite(result[["actual_occ", "persistence_pred", "ml_pred"]].to_numpy()).all():
        raise ValueError("예측 또는 정답에 유효하지 않은 값이 있습니다. 같은 평가 행을 유지할 수 없습니다.")
    return result


def scores(frame):
    n = len(frame)
    p = (frame["actual_occ"] - frame["persistence_pred"]).to_numpy()
    m = (frame["actual_occ"] - frame["ml_pred"]).to_numpy()
    p_mae, m_mae = (float(np.abs(x).mean()) if n else np.nan for x in (p, m))
    gain = p_mae - m_mae
    status = ("unavailable" if not n else "tie" if abs(gain) < 1e-12 else "better" if gain > 0 else "worse")
    return dict(n=n, n_lots=frame["parking_id"].nunique(),
                n_days=frame["target_time"].dt.normalize().nunique(),
                persistence_mae=p_mae, ml_mae=m_mae,
                persistence_rmse=float(np.sqrt(np.mean(p*p))) if n else np.nan,
                ml_rmse=float(np.sqrt(np.mean(m*m))) if n else np.nan,
                gain_pp=gain, gain_pct=100*gain/p_mae if p_mae > 0 else np.nan, status=status)


def summarize(pred, horizons=HORIZONS):
    """빈 집단도 unavailable로 남긴다. 동일한 행에서 두 방법의 분모를 만든다."""
    rows = []
    for h in horizons:
        p = pred.loc[pred["horizon"].eq(h)]
        for access in ("all", *ACCESS_GROUPS):
            a = p if access == "all" else p.loc[p["access_group"].eq(access)]
            for day in ("all", *DAY_GROUPS):
                d = a if day == "all" else a.loc[a["weekday_group"].eq(day)]
                rows.append(dict(horizon=h, access_group=access, weekday_group=day, **scores(d)))
    return pd.DataFrame(rows)


def evaluate(base, rules, lots, dates, model_params=None):
    params = {**PARAMS, **(model_params or {})}
    predictions, daily, audits = [], [], []
    for h in HORIZONS:
        print(f"H={h}분: 평가 행 생성", flush=True)
        frame = build_horizon_frame(base, h, rules, lots)
        for fold, start in enumerate(dates, 1):
            train, validation, test = split_frame(frame, start)
            if train.empty:
                raise ValueError(f"{start.date()} H={h}: 학습 가능한 연속 관측이 없습니다.")
            print(f"H={h}분 {fold}/{len(dates)} · {start.date()} · "
                  f"train={len(train):,} validation={len(validation):,} test={len(test):,}", flush=True)
            # Validation/test로 하이퍼파라미터, 모델 또는 clipping을 선택하지 않는다.
            model = LGBMRegressor(objective="quantile", alpha=.5, **params)
            model.fit(train[FEATURES], train["delta_target"])
            pred = predict(model, test, fold)
            val = predict(model, validation, fold)
            predictions.append(pred)
            daily.append(summarize(pred, [h]).assign(fold=fold, test_date=str(start.date())))
            possible = frame.loc[(frame["ts_kst"] >= start)
                                 & (frame["target_time"] < start + pd.Timedelta(days=1))
                                 & frame["future_complete"]]
            val_scores = scores(val)
            audits.append(dict(fold=fold, horizon=h, test_date=str(start.date()),
                train_n=len(train), validation_n=len(validation), test_n=len(test),
                a19_eligible_n=len(possible), excluded_history_or_features_n=len(possible)-len(test),
                matched_ratio=len(test)/len(possible) if len(possible) else np.nan,
                train_target_max=str(train["target_time"].max()),
                validation_start=str(start-pd.Timedelta(days=1)),
                validation_target_max=str(validation["target_time"].max()),
                test_start=str(start), test_end_exclusive=str(start+pd.Timedelta(days=1)),
                test_target_max=str(test["target_time"].max()),
                validation_persistence_mae=val_scores["persistence_mae"], validation_ml_mae=val_scores["ml_mae"]))
            service = scores(pred.loc[pred["access_group"].eq("accessible")])
            print(f"  출입 가능 n={service['n']:,} · 현재값 MAE={service['persistence_mae']:.3f} · "
                  f"AI MAE={service['ml_mae']:.3f} · {service['status']}", flush=True)
    paired = pd.concat(predictions, ignore_index=True)
    if paired.empty:
        raise ValueError("test 기간에 두 방법을 비교할 수 있는 행이 없습니다.")
    if paired.duplicated(KEYS).any():
        raise AssertionError("test 날짜 간 평가 행이 중복됩니다.")
    names = rules.set_index("parking_id")["name"]
    paired["name"] = paired["parking_id"].map(names)
    per_lot = pd.DataFrame([
        dict(parking_id=pid, name=names.loc[pid], horizon=h, access_group=access, **scores(g))
        for (pid, h, access), g in paired.groupby(["parking_id", "horizon", "access_group"], observed=True)])
    return paired, summarize(paired), pd.concat(daily, ignore_index=True), pd.DataFrame(audits), per_lot


def run(db_path=PARKING_DB, rules_path=PARKING_ACCESS_RULES_CSV, table_dir=TABLES,
        prediction_dir=PROCESSED / "a20", test_days=3, min_train_days=7, model_params=None):
    print("=== A20 같은 test 행의 AI / Persistence 비교 ===", flush=True)
    obs, lots, rules, info = load_inputs(db_path, rules_path)
    dates = test_dates(obs["ts_kst"].min(), obs["ts_kst"].max(), test_days, min_train_days)
    print(f"대상 {len(rules)}곳 · {len(obs):,}행 · {info['start']} ~ {info['end']}", flush=True)
    print(f"test 날짜(KST): {', '.join(str(d.date()) for d in dates)}", flush=True)
    base = build_features(obs, lots, progress=True)
    paired, summary, daily, splits, per_lot = evaluate(base, rules, lots, dates, model_params)
    table_dir, prediction_dir = Path(table_dir), Path(prediction_dir)
    table_dir.mkdir(parents=True, exist_ok=True)
    prediction_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in (("comparison", summary), ("by_day", daily), ("splits", splits), ("per_lot", per_lot)):
        frame.to_csv(table_dir / f"a20_{name}.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(prediction_dir / "predictions.csv.gz", index=False, compression="gzip")
    source_paths = [Path(__file__), Path(__file__).with_name("a16_stratified_weekend.py"),
                    Path(__file__).with_name("a19_persistence_baseline.py"),
                    Path(__file__).parents[1] / "features/observation_grid.py",
                    Path(__file__).parents[1] / "features/temporal.py"]
    manifest = {**info, "protocol": "a20_point_comparison_v1", "status": "complete",
        "test_dates": [str(d.date()) for d in dates], "test_days": test_days, "min_train_days": min_train_days,
        "model": {"objective": "quantile", "alpha": .5, **PARAMS, **(model_params or {})},
        "features": FEATURES, "prediction_clip": [0, 100], "matched_n": len(paired),
        "available_segments": int(summary["n"].gt(0).sum()), "unavailable_segments": int(summary["n"].eq(0).sum()),
        "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
        "versions": {p: version(p) for p in ("pandas", "numpy", "lightgbm", "scikit-learn")},
        "notes": ["complete는 실행 완료이며 AI 성능 합격이 아니다.",
                  "predict_ok 목록을 고정한 후향 평가이며 출입 가능과 센서 경직을 구분한다.",
                  "A19와 같은 미래 연속성에 더해 과거 60분 이력이 완전한 행에서 두 방법을 함께 평가한다.",
                  "요일 기반 출입 규칙을 쓰며 공휴일·출차 제한은 별도 검증 대상이다.",
                  "validation은 진단만 하며 이 점 예측 실험에서 보정이나 모델 선택에 사용하지 않는다.",
                  "기존 U11 학습 산출물과 서비스 predictor.pkl을 갱신하지 않는다."]}
    (table_dir / "a20_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print("\n=== 출입 가능 시간 · 같은 test 행의 비교 ===", flush=True)
    print(summary.loc[summary["access_group"].eq("accessible"),
        ["horizon", "weekday_group", "n", "persistence_mae", "ml_mae", "gain_pp", "gain_pct", "status"]]
        .to_string(index=False, float_format=lambda x: f"{x:.3f}"), flush=True)
    print("gain_pp/gain_pct가 양수면 AI의 MAE가 더 낮습니다. unavailable은 평가 표본 없음입니다.", flush=True)
    print(f"저장 완료: {table_dir / 'a20_comparison.csv'}", flush=True)
    print(f"분할/날짜별/주차장별 표와 manifest: {table_dir}", flush=True)
    print(f"같은 행의 예측값: {prediction_dir / 'predictions.csv.gz'}", flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=PARKING_DB)
    parser.add_argument("--rules", type=Path, default=PARKING_ACCESS_RULES_CSV)
    parser.add_argument("--test-days", type=int, default=3)
    parser.add_argument("--min-train-days", type=int, default=7)
    args = parser.parse_args()
    run(args.db, args.rules, test_days=args.test_days, min_train_days=args.min_train_days)


if __name__ == "__main__":
    main()
