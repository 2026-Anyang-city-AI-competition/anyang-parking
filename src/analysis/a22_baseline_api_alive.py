#!/usr/bin/env python3
"""A22: label_valid(API 생존 시간) 게이트를 적용한 뒤 A20과 같은 조건에서 재평가.

python src/analysis/a22_baseline_api_alive.py

A20/A21 파일은 수정하지 않고 그 함수를 최대한 그대로 재사용한다. 핵심 차이는
observation_grid 직후 label_valid가 아닌 시각의 occ를 NaN으로 지우는 것뿐이다 —
하류의 모든 lag/roll/타깃(actual_occ)이 이 NaN을 shift/rolling으로 물려받으므로
A20의 history_complete/future_complete/eligible 게이트가 수정 없이 그대로
"label_valid 입력+타깃"을 강제하게 된다.

enterable(실제 입출차 가능 여부)은 이 스크립트에서 쓰지 않는다 — 그건 추천 필터링
개념이고 모델 성능 채점 기준이 아니다(계획 확정 사항).

test 날짜와 데이터 절단 시각은 A20과 정확히 같은 시간 범위에서 비교하기 위해
A20 manifest(reports/tables/a20_manifest.json)의 값으로 고정한다.
"""
import argparse
import hashlib
import json
import sys
import warnings
from datetime import date
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor  # noqa: F401  (a20.evaluate가 내부에서 사용)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.config import PARKING_DB, PARKING_ACCESS_RULES_CSV, PROCESSED, TABLES, RANDOM_STATE  # noqa: F401
from src.analysis.a19_persistence_baseline import accessible_at, HORIZONS
from src.analysis.a20_matched_model_comparison import (
    FEATURES, PARAMS, DAY_GROUPS, load_inputs, build_horizon_frame, evaluate,
)
from src.features.observation_grid import observation_grid

with warnings.catch_warnings():
    from src.analysis.a16_stratified_weekend import feats

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# A20과 정확히 같은 시간 범위에서 비교하기 위해 test 날짜·데이터 절단 시각을 고정한다.
# (data/raw/parking.db에 이후 데이터가 더 쌓여 있어도 이번 실행은 A20 manifest 종료
# 시각까지만 쓴다. 출처: reports/tables/a20_manifest.json test_dates/end.)
FIXED_TEST_DATES = pd.to_datetime(
    ["2026-09-12", "2026-09-13", "2026-09-14"]).tz_localize("Asia/Seoul")
FIXED_DATA_END = pd.Timestamp("2026-09-15 01:22:18+09:00")

# 라이브러리 대신 코드 상수. 데이터 기간(~10/9) 안의 공휴일만 적어둔다.
HOLIDAYS_KST = frozenset({
    date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26),  # 추석
    date(2026, 10, 3),   # 개천절
    date(2026, 10, 5),   # 개천절 대체공휴일
    date(2026, 10, 9),   # 한글날
})

ANOMALY_JUMP_THRESHOLD = 50.0  # occ %p, 5분 사이 이 이상 튀면 이상값 후보로 "기록만" 한다.
PASS_HORIZONS = (60, 120)


def _sunday_proxy(ts):
    """공휴일을 일요일 규칙으로 평가하기 위해 요일만 일요일(6)로 옮긴 프록시 시각.
    accessible_at은 ts.weekday()/hour/minute만 읽으므로 날짜를 옮겨도 시간 판정은 그대로다."""
    shift = (6 - ts.weekday()) % 7
    return ts + pd.Timedelta(days=int(shift)) if shift else ts


def build_features_label_valid(obs, lots, rules, holidays=HOLIDAYS_KST, progress=False):
    """A20의 build_features와 같은 산출물을 만들되, label_valid가 아닌 시각의 occ를
    feats() 호출 전에 NaN으로 지운다. 이상값은 제거하지 않고 후보만 별도로 모은다."""
    rule_map = rules.set_index("parking_id").to_dict("index")
    holiday_hits = {h: 0 for h in holidays}
    parts, anomalies = [], []
    groups = obs.groupby("parking_id", sort=True)
    for i, (pid, raw) in enumerate(groups, 1):
        grid = observation_grid(raw)
        grid["occ_raw"] = grid["occ"]
        rule = rule_map.get(pid, {})
        cache = {}
        valid = np.empty(len(grid), dtype=bool)
        for j, ts in enumerate(grid.index):
            d = ts.date()
            if d in holidays:
                holiday_hits[d] += 1
                query_ts = _sunday_proxy(ts)
            else:
                query_ts = ts
            cache_key = (query_ts.weekday(), query_ts.hour, query_ts.minute)
            if cache_key not in cache:
                cache[cache_key] = accessible_at(rule, query_ts) is True
            valid[j] = cache[cache_key]
        grid["label_valid"] = valid

        jump = grid["occ_raw"].diff().abs()
        candidate = jump.gt(ANOMALY_JUMP_THRESHOLD)
        for ts in grid.index[candidate.fillna(False)]:
            anomalies.append(dict(parking_id=pid, ts_kst=str(ts),
                                   local_hm=f"{ts.hour:02d}:{ts.minute:02d}",
                                   jump=float(jump.loc[ts])))

        grid.loc[~grid["label_valid"], "occ"] = np.nan
        grid["history_complete"] = (
            grid["occ"].notna().rolling(13, min_periods=13).sum().eq(13))
        recently_invalid = (
            (~grid["label_valid"]).astype(int).rolling(13, min_periods=1).max().astype(bool))
        opened_recently = grid["label_valid"] & recently_invalid

        grid["parking_id"] = pid
        grid["opened_recently"] = opened_recently.to_numpy()
        parts.append(grid.reset_index())
        if progress and (i % 10 == 0 or i == len(groups)):
            print(f"관측 격자: {i}/{len(groups)}곳", flush=True)
    base = feats(pd.concat(parts, ignore_index=True), lots).reset_index(drop=True)
    anomalies_df = pd.DataFrame(anomalies)
    holiday_counts = {str(d): n for d, n in sorted(holiday_hits.items())}
    return base, anomalies_df, holiday_counts


def diagnostics_per_horizon(base, rules, lots):
    """opr_is_operating 상수 lot과 '오픈 직후' 이력부족 제외 비율. 기록만 하고 게이트에 쓰지 않는다."""
    opr_rows, open_rows = [], []
    names = rules.set_index("parking_id")["name"]
    for h in HORIZONS:
        frame = build_horizon_frame(base, h, rules, lots)
        elig = frame.loc[frame["eligible"]]
        if len(elig):
            nun = elig.groupby("parking_id")["opr_is_operating"].nunique()
            for pid in nun.loc[nun.le(1)].index:
                val = elig.loc[elig["parking_id"].eq(pid), "opr_is_operating"].iloc[0]
                opr_rows.append(dict(horizon=h, parking_id=int(pid),
                                      name=names.get(pid), opr_is_operating_constant=float(val)))
        opened = frame.loc[frame["opened_recently"]]
        rate = float(1 - opened["eligible"].mean()) if len(opened) else np.nan
        open_rows.append(dict(horizon=h, opened_recently_n=int(len(opened)),
                               excluded_rate=rate))
    return pd.DataFrame(opr_rows), pd.DataFrame(open_rows)


def load_a20_predictions(path):
    path = Path(path)
    if not path.exists():
        return None
    df = pd.read_csv(path, compression="gzip")
    for col in ("ts_kst", "target_time"):
        stamp = pd.to_datetime(df[col], format="mixed")
        df[col] = (stamp.dt.tz_localize("Asia/Seoul") if stamp.dt.tz is None
                   else stamp.dt.tz_convert("Asia/Seoul"))
    return df


def rescore_with_a20(paired_a22, a20_pred):
    """(b) A20 재채점: A20 예측(persistence_pred/ml_pred)을 A22 평가 행에만 매칭해서
    persistence·AI MAE를 다시 계산한다. A22 자체 모델은 쓰지 않는다."""
    key = ["parking_id", "ts_kst", "horizon"]
    base_cols = paired_a22[key + ["actual_occ", "weekday_group"]].copy()
    if a20_pred is not None:
        merged = base_cols.merge(
            a20_pred[key + ["persistence_pred", "ml_pred"]], on=key, how="left")
    else:
        merged = base_cols.assign(persistence_pred=np.nan, ml_pred=np.nan)
    rows = []
    for h in HORIZONS:
        for day in DAY_GROUPS:
            u = merged[(merged["horizon"].eq(h)) & (merged["weekday_group"].eq(day))]
            d = u.dropna(subset=["persistence_pred", "ml_pred"])
            n = len(d)
            p_mae = float((d["actual_occ"] - d["persistence_pred"]).abs().mean()) if n else np.nan
            m_mae = float((d["actual_occ"] - d["ml_pred"]).abs().mean()) if n else np.nan
            gain_pct = 100 * (p_mae - m_mae) / p_mae if n and p_mae > 0 else np.nan
            rows.append(dict(horizon=h, weekday_group=day, n=n, unmatched_n=len(u) - n,
                              persistence_mae=p_mae, ml_mae=m_mae, gain_pct=gain_pct))
    return pd.DataFrame(rows)


def dropped_row_diagnostic(a20_pred, paired_a22):
    """(a)->(b) 차이의 원인 진단: A20엔 있었지만 label_valid 게이트로 A22에서 빠진 행이
    A20 기준으로 얼마나 오차가 컸는지(=persistence가 그 행에서 얼마나 크게 틀렸는지)."""
    if a20_pred is None:
        return pd.DataFrame()
    key = ["parking_id", "ts_kst", "horizon"]
    kept_keys = set(map(tuple, paired_a22[key].to_numpy()))
    a20 = a20_pred.copy()
    a20["kept_in_a22"] = list(map(tuple, a20[key].to_numpy()))
    a20["kept_in_a22"] = a20["kept_in_a22"].isin(kept_keys)
    a20["abs_err_persist"] = (a20["actual_occ"] - a20["persistence_pred"]).abs()
    rows = []
    for h in HORIZONS:
        for day in DAY_GROUPS:
            sub = a20[(a20["horizon"].eq(h)) & (a20["weekday_group"].eq(day))]
            kept, dropped = sub[sub["kept_in_a22"]], sub[~sub["kept_in_a22"]]
            rows.append(dict(
                horizon=h, weekday_group=day, n_a20_total=len(sub),
                n_kept_in_a22=len(kept), n_dropped_by_label_valid=len(dropped),
                persistence_mae_kept=float(kept["abs_err_persist"].mean()) if len(kept) else np.nan,
                persistence_mae_dropped=float(dropped["abs_err_persist"].mean()) if len(dropped) else np.nan,
            ))
    return pd.DataFrame(rows)


def build_three_line_table(a20_comparison_path, summary_a22, b_table):
    a20_all = pd.read_csv(a20_comparison_path)
    a20_all = a20_all[a20_all["access_group"].eq("all")]
    c_all = summary_a22[summary_a22["access_group"].eq("all")]

    def pick(df, col, default=np.nan):
        return df[col].iloc[0] if len(df) else default

    rows = []
    for h in HORIZONS:
        for day in DAY_GROUPS:
            a = a20_all[(a20_all["horizon"].eq(h)) & (a20_all["weekday_group"].eq(day))]
            b = b_table[(b_table["horizon"].eq(h)) & (b_table["weekday_group"].eq(day))]
            c = c_all[(c_all["horizon"].eq(h)) & (c_all["weekday_group"].eq(day))]
            for line, src in (("a_A20", a), ("b_A20_rescored", b), ("c_A22", c)):
                rows.append(dict(horizon=h, weekday_group=day, line=line,
                                  n=int(pick(src, "n", 0)),
                                  persistence_mae=pick(src, "persistence_mae"),
                                  ml_mae=pick(src, "ml_mae"),
                                  gain_pct=pick(src, "gain_pct")))
    return pd.DataFrame(rows)


def pass_fail(summary_a22):
    c_all = summary_a22[summary_a22["access_group"].eq("all")]
    checks = []
    passed = True
    for h in PASS_HORIZONS:
        for day in DAY_GROUPS:
            row = c_all[(c_all["horizon"].eq(h)) & (c_all["weekday_group"].eq(day))]
            ok = bool(len(row) and row["ml_mae"].iloc[0] < row["persistence_mae"].iloc[0])
            passed &= ok
            checks.append(dict(horizon=h, weekday_group=day, pass_=ok,
                                persistence_mae=pick_scalar(row, "persistence_mae"),
                                ml_mae=pick_scalar(row, "ml_mae")))
    return passed, checks


def pick_scalar(df, col):
    return float(df[col].iloc[0]) if len(df) else None


def run(db_path=PARKING_DB, rules_path=PARKING_ACCESS_RULES_CSV, table_dir=TABLES,
        prediction_dir=PROCESSED / "a22", a20_table_dir=TABLES,
        a20_prediction_path=PROCESSED / "a20" / "predictions.csv.gz", model_params=None):
    print("=== A22 label_valid 게이트 재평가 (A20 대비) ===", flush=True)
    obs, lots, rules, info = load_inputs(db_path, rules_path)
    before_n = len(obs)
    obs = obs.loc[obs["ts_kst"] <= FIXED_DATA_END].copy()
    truncated_n = before_n - len(obs)
    print(f"대상 {len(rules)}곳 · {len(obs):,}행(데이터 절단으로 {truncated_n:,}행 제외, "
          f"절단 시각(A20과 동일)={FIXED_DATA_END})", flush=True)
    print(f"test 날짜(A20과 동일, 고정): "
          f"{', '.join(str(d.date()) for d in FIXED_TEST_DATES)}", flush=True)

    base, anomalies, holiday_counts = build_features_label_valid(obs, lots, rules, progress=True)
    opr_const, open_ratio = diagnostics_per_horizon(base, rules, lots)
    paired, summary, daily, audits, per_lot = evaluate(
        base, rules, lots, FIXED_TEST_DATES, model_params)

    a20_pred = load_a20_predictions(a20_prediction_path)
    b_table = rescore_with_a20(paired, a20_pred)
    three_line = build_three_line_table(a20_table_dir / "a20_comparison.csv", summary, b_table)
    dropped_diag = dropped_row_diagnostic(a20_pred, paired)
    passed, pass_checks = pass_fail(summary)

    table_dir, prediction_dir = Path(table_dir), Path(prediction_dir)
    table_dir.mkdir(parents=True, exist_ok=True)
    prediction_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in (("comparison", summary), ("by_day", daily), ("splits", audits),
                        ("per_lot", per_lot), ("three_line", three_line),
                        ("opr_constant_lots", opr_const), ("open_transition", open_ratio),
                        ("anomalies", anomalies), ("a20_dropped_diagnostic", dropped_diag)):
        frame.to_csv(table_dir / f"a22_{name}.csv", index=False, encoding="utf-8-sig")
    paired.to_csv(prediction_dir / "predictions.csv.gz", index=False, compression="gzip")

    unmatched_total = int(b_table["unmatched_n"].sum())
    source_paths = [Path(__file__),
                    Path(__file__).with_name("a20_matched_model_comparison.py"),
                    Path(__file__).with_name("a19_persistence_baseline.py"),
                    Path(__file__).with_name("a16_stratified_weekend.py"),
                    Path(__file__).parents[1] / "features/observation_grid.py",
                    Path(__file__).parents[1] / "features/temporal.py"]
    manifest = {
        **info, "protocol": "a22_label_valid_v1", "status": "complete" if passed else "partial",
        "fixed_test_dates": [str(d.date()) for d in FIXED_TEST_DATES],
        "fixed_data_end_kst": str(FIXED_DATA_END),
        "data_rows_truncated_after_fixed_end": int(truncated_n),
        "holidays_kst": sorted(str(d) for d in HOLIDAYS_KST),
        "holiday_grid_rows_affected": holiday_counts,
        "anomaly_jump_threshold_pp": ANOMALY_JUMP_THRESHOLD,
        "anomaly_candidates_n": int(len(anomalies)),
        "anomaly_note": "제거하지 않고 기록만 함(a22_anomalies.csv)",
        "opr_is_operating_constant_lots_n": int(len(opr_const)),
        "opr_is_operating_note": "이번 실행에서 FEATURES에서 제거하지 않음(a22_opr_constant_lots.csv에 목록)",
        "open_transition_exclusion": open_ratio.to_dict("records"),
        "a20_predictions_path": str(a20_prediction_path),
        "a20_predictions_found": a20_pred is not None,
        "a22_test_rows_unmatched_to_a20_predictions": unmatched_total,
        "enterable_used_for_scoring": False,
        "pass_criteria": "A22 평가 행(label_valid)에서 60·120분 AI MAE가 persistence보다 평일·주말 모두 낮을 것 (enterable/출입가능 조건 미적용)",
        "pass_checks": pass_checks, "pass": passed,
        "model": {"objective": "quantile", "alpha": .5, **PARAMS, **(model_params or {})},
        "features": FEATURES, "prediction_clip": [0, 100], "matched_n": len(paired),
        "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
        "versions": {p: version(p) for p in ("pandas", "numpy", "lightgbm", "scikit-learn")},
        "notes": [
            "label_valid = accessible_at(access_rule/*_access_status, ts) is True. "
            "enterable_status는 이 스크립트에서 쓰지 않는다(추천 필터링 개념).",
            "occ는 observation_grid 직후, feats() 호출 전에 label_valid가 아닌 시각만 NaN 처리. "
            "history_complete/future_complete/eligible 공식은 A20과 동일(수정 없음).",
            "test 날짜·데이터 절단 시각은 A20 manifest 값으로 고정(재계산하지 않음).",
            "이상값은 제거하지 않고 a22_anomalies.csv에 후보만 기록.",
            "합격 기준은 label_valid 행 기준이며 enterable(출입 가능) 조건은 포함하지 않는다.",
            "open_transition_exclusion의 excluded_rate는 정의상 항상 1.0이다(opened_recently 자체가 "
            "history_complete를 반드시 깨는 조건이라 동어반복) — opened_recently_n(오픈 직후 손실 행 수)만 의미 있다.",
            "(a)->(b) persistence MAE 변화 방향은 horizon/요일에 따라 다르다(a22_a20_dropped_diagnostic.csv 참고). "
            "평일 30/60/120분은 감소하는데, label_valid로 빠지는 행이 운영시간 밖 관측을 입력으로 쓴 행(주로 개장 "
            "전 이른 아침 시간대)이라 persistence 오차가 원래 훨씬 컸기 때문이다(재개장 전 정지값으로 재개장 후 "
            "실제값을 맞히려다 크게 틀림). 주말은 반대로 모든 horizon에서 증가하는데, 빠지는 행이 주말 내내 닫혀 "
            "있는 노상 주차장의 정지값(거의 0에 가까운 persistence 오차)이라 쉬운 행이 빠지기 때문이다.",
            "기존 U11 학습 산출물과 서비스 predictor.pkl을 갱신하지 않는다.",
        ],
    }
    (table_dir / "a22_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    print("\n=== (a)A20 / (b)A20 재채점 / (c)A22 — horizon x 평일/주말 ===", flush=True)
    print(three_line.to_string(index=False, float_format=lambda x: f"{x:.3f}"), flush=True)
    print(f"\nA22 test 행 중 A20 예측 미매칭: {unmatched_total:,}행", flush=True)
    print(f"이상값 후보(기록만, 미제거): {len(anomalies):,}건 → {table_dir / 'a22_anomalies.csv'}",
          flush=True)
    print(f"opr_is_operating이 상수인 (horizon,lot): {len(opr_const)}건 → "
          f"{table_dir / 'a22_opr_constant_lots.csv'}", flush=True)
    print(f"\n합격 기준: {manifest['pass_criteria']}")
    print(f"결과: {'PASS' if passed else 'FAIL/PARTIAL'}", flush=True)
    for c in pass_checks:
        print(f"  h={c['horizon']:>3} {c['weekday_group']:<7} "
              f"persistence={c['persistence_mae']} ai={c['ml_mae']} pass={c['pass_']}", flush=True)
    print(f"\n저장 완료: {table_dir}", flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=PARKING_DB)
    parser.add_argument("--rules", type=Path, default=PARKING_ACCESS_RULES_CSV)
    args = parser.parse_args()
    run(args.db, args.rules)


if __name__ == "__main__":
    main()
