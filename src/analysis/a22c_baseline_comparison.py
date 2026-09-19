#!/usr/bin/env python3
"""A22c: persistence·lag_24h·lag_7d 세 기준선을 A22b와 같은 조건(label_valid 게이트,
같은 test 날짜·데이터 절단)에서 공통 행(교집합)으로 비교한다.

A20/A22 파일은 수정하지 않고 a22의 build_features_label_valid()/a20의
build_horizon_frame()을 그대로 재사용한다. lag_24h/lag_7d는 A20/A22가 쓰는
lag_5~lag_60(a16 feats())과 별개로, 같은 label_valid-NaN 처리된 occ 컬럼에
288/2016 grid-step(=24h/7d, 5분 격자) shift만 추가한 것이다 — 새 게이트 없음,
occ가 NaN이면 자동으로 빠진다.

lag_24h/lag_7d는 관측 시각(ts_kst) 기준이다(target_time 기준 아님) —
persistence의 occ_now와 같은 앵커라 세 기준선을 나란히 비교할 수 있다.

python src/analysis/a22c_baseline_comparison.py \
    --test-dates 2026-09-12,2026-09-13,2026-09-14,2026-09-15,2026-09-16,2026-09-17,2026-09-18 \
    --data-end "2026-09-19 00:00:00+09:00" --output-prefix a22c
"""
import argparse
import hashlib
import json
import sys
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.config import PARKING_DB, PARKING_ACCESS_RULES_CSV, TABLES
from src.analysis.a19_persistence_baseline import HORIZONS
from src.analysis.a20_matched_model_comparison import DAY_GROUPS, load_inputs, build_horizon_frame
from src.analysis.a22_baseline_api_alive import (
    build_features_label_valid, HOLIDAYS_KST, FIXED_TEST_DATES, FIXED_DATA_END,
)

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

LAG_STEPS = {"lag_24h": 288, "lag_7d": 2016}  # 5분 격자 기준: 24h=288, 7d=2016


def add_long_lags(base):
    base = base.sort_values(["parking_id", "ts_kst"]).copy()
    g = base.groupby("parking_id")["occ"]
    for name, steps in LAG_STEPS.items():
        base[name] = g.shift(steps)
    return base


def build_test_rows(base, rules, lots, test_dates):
    """A20 split_frame의 test 경계(ts_kst>=start & target_time<start+1일)를 날짜별로 합친다."""
    frames = []
    for h in HORIZONS:
        frame = build_horizon_frame(base, h, rules, lots)
        elig = frame.loc[frame["eligible"]]
        parts = []
        for start in test_dates:
            end = start + pd.Timedelta(days=1)
            parts.append(elig.loc[(elig["ts_kst"] >= start) & (elig["target_time"] < end)])
        frames.append(pd.concat(parts, ignore_index=True) if parts else elig.iloc[0:0])
    return pd.concat(frames, ignore_index=True)


def score(frame, col):
    err = (frame["actual_occ"] - frame[col]).abs()
    return float(err.mean()) if len(frame) else np.nan


def summarize(common):
    rows = []
    baselines = [("persistence", "occ_now"), ("lag_24h", "lag_24h"), ("lag_7d", "lag_7d")]
    for h in HORIZONS:
        for day in ("all", *DAY_GROUPS):
            d = common.loc[common["horizon"].eq(h)]
            d = d if day == "all" else d.loc[d["weekday_group"].eq(day)]
            row = dict(horizon=h, weekday_group=day, n=len(d))
            for label, col in baselines:
                row[f"{label}_mae"] = score(d, col)
            rows.append(row)
    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=PARKING_DB)
    parser.add_argument("--rules", type=Path, default=PARKING_ACCESS_RULES_CSV)
    parser.add_argument("--test-dates", default=None,
                         help="쉼표 구분 YYYY-MM-DD. 기본값은 A20/A22 기본값(09-12~09-14).")
    parser.add_argument("--data-end", default=None, help="기본값은 A22 기본값(2026-09-15 01:22:18+09:00).")
    parser.add_argument("--output-prefix", default="a22c")
    args = parser.parse_args()

    test_dates = (FIXED_TEST_DATES if args.test_dates is None else
                  pd.to_datetime([d.strip() for d in args.test_dates.split(",")]).tz_localize("Asia/Seoul"))
    data_end = (FIXED_DATA_END if args.data_end is None else
                (lambda t: t.tz_localize("Asia/Seoul") if t.tz is None else t.tz_convert("Asia/Seoul"))(
                    pd.Timestamp(args.data_end)))

    print("=== A22C persistence/lag_24h/lag_7d 공통 행 비교 ===", flush=True)
    obs, lots, rules, info = load_inputs(args.db, args.rules)
    before_n = len(obs)
    obs = obs.loc[obs["ts_kst"] <= data_end].copy()
    print(f"대상 {len(rules)}곳 · {len(obs):,}행(절단 {before_n - len(obs):,}행 제외, 절단 시각={data_end})",
          flush=True)
    print(f"test 날짜: {', '.join(str(d.date()) for d in test_dates)}", flush=True)

    base, _anom, _hol = build_features_label_valid(obs, lots, rules, holidays=HOLIDAYS_KST, progress=True)
    base = add_long_lags(base)

    test_rows = build_test_rows(base, rules, lots, test_dates)
    eligible_n = len(test_rows)
    common = test_rows.dropna(subset=["occ_now", "actual_occ", "lag_24h", "lag_7d"]).copy()
    dropped_for_lag = eligible_n - len(common)

    summary = summarize(common)

    table_dir = Path(TABLES)
    table_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(table_dir / f"{args.output_prefix}_comparison.csv", index=False, encoding="utf-8-sig")
    common.drop(columns=[c for c in common.columns if c not in
                ["parking_id", "ts_kst", "target_time", "horizon", "weekday_group",
                 "actual_occ", "occ_now", "lag_24h", "lag_7d"]]).to_csv(
        table_dir / f"{args.output_prefix}_rows.csv.gz", index=False, compression="gzip")

    source_paths = [Path(__file__),
                    Path(__file__).with_name("a22_baseline_api_alive.py"),
                    Path(__file__).with_name("a20_matched_model_comparison.py"),
                    Path(__file__).with_name("a19_persistence_baseline.py")]
    manifest = {
        **info, "protocol": "a22c_baseline_comparison_v1",
        "test_dates": [str(d.date()) for d in test_dates], "data_end_kst": str(data_end),
        "eligible_n_before_lag_intersection": int(eligible_n),
        "common_n_after_lag_intersection": int(len(common)),
        "dropped_for_missing_lag_24h_or_7d": int(dropped_for_lag),
        "lag_anchor": "ts_kst(관측 시각) 기준, target_time 기준 아님 — persistence의 occ_now와 동일 앵커",
        "baselines": {"persistence": "occ_now", "lag_24h": "occ(ts_kst-24h)", "lag_7d": "occ(ts_kst-7d)"},
        "note": "세 기준선을 공통 행(교집합)에서만 비교함 — CLAUDE.local.md 'A20/A22 확정 규칙' 준수. "
                "AI 모델은 이 스크립트의 비교 대상이 아니다(A23에서 다룸).",
        "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
        "versions": {p: version(p) for p in ("pandas", "numpy")},
    }
    (table_dir / f"{args.output_prefix}_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    print(f"\neligible(label_valid, test 날짜) {eligible_n:,}행 -> lag_24h/lag_7d 공통 교집합 "
          f"{len(common):,}행 ({dropped_for_lag:,}행 제외)", flush=True)
    print("\n=== persistence / lag_24h / lag_7d — horizon x 평일/주말/전체 (공통 행) ===", flush=True)
    print(summary.to_string(index=False, float_format=lambda x: f"{x:.3f}"), flush=True)
    print(f"\n저장: {table_dir / (args.output_prefix + '_comparison.csv')}", flush=True)


if __name__ == "__main__":
    main()
