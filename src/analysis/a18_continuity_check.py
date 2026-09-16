#!/usr/bin/env python3

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.config import PARKING_DB, PARKING_ACCESS_RULES_CSV
from src.features.observation_grid import observation_grid

DB = PARKING_DB
RULES = PARKING_ACCESS_RULES_CSV

HORIZONS = [15, 30, 60, 120]
FREQ_MIN = 5


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    obs = pd.read_sql(
        """
        SELECT parking_id, ts_kst, cell_cnt, park_count
        FROM obs
        """,
        con,
    )
    con.close()

    rules = pd.read_csv(RULES)

    obs["ts_kst"] = pd.to_datetime(obs["ts_kst"], format="mixed")
    obs = obs.sort_values(["parking_id", "ts_kst"])
    if obs.empty:
        raise ValueError("DB에 관측이 없습니다.")
    if rules["parking_id"].isna().any() or rules["parking_id"].duplicated().any():
        raise ValueError("출입 규칙의 parking_id가 비어 있거나 중복됩니다.")
    # 전체 DB의 공통 기간으로 진단해야 앞/뒤 수집 중단도 결측으로 잡힌다.
    analysis_index = pd.date_range(
        obs["ts_kst"].min().ceil("5min"),
        obs["ts_kst"].max().ceil("5min"),
        freq="5min", name="ts_kst",
    )

    active_ids = set(
        rules.loc[rules["predict_ok"] == 1, "parking_id"].astype(int)
    )
    if not active_ids:
        raise ValueError("predict_ok=1인 주차장이 없습니다.")
    obs = obs[obs["parking_id"].isin(active_ids)].copy()

    print("=== 기본 현황 ===")
    print("예측 대상 주차장:", obs["parking_id"].nunique())
    print("행수:", len(obs))
    print("기간:", obs["ts_kst"].min(), "~", obs["ts_kst"].max())
    print()

    # 원본 점유율
    obs["occ_raw"] = (
        obs["park_count"]
        / obs["cell_cnt"].replace(0, np.nan)
        * 100
    )

    # 이상치 규칙
    obs["bad_missing_count"] = obs["park_count"].isna()
    obs["bad_negative"] = obs["park_count"] < 0
    obs["bad_capacity"] = obs["park_count"] > obs["cell_cnt"]
    obs["bad_cell_cnt"] = obs["cell_cnt"].isna() | (obs["cell_cnt"] <= 0)

    obs["bad_row"] = (
        obs["bad_negative"]
        | obs["bad_capacity"]
        | obs["bad_cell_cnt"]
        | obs["bad_missing_count"]
    )

    # 학습/평가에 실제 사용할 점유율
    obs["occ"] = obs["occ_raw"].where(~obs["bad_row"])

    # 이상행 저장
    bad_rows = obs[obs["bad_row"]].copy()

    bad_save = ROOT / "reports/tables/a18_bad_rows.csv"
    bad_save.parent.mkdir(parents=True, exist_ok=True)

    bad_rows[
        [
            "parking_id",
            "ts_kst",
            "cell_cnt",
            "park_count",
            "occ_raw",
            "bad_negative",
            "bad_capacity",
            "bad_cell_cnt",
            "bad_missing_count",
        ]
    ].to_csv(
        bad_save,
        index=False,
        encoding="utf-8-sig",
    )

    results = []

    for pid in sorted(active_ids):
        # 대상이지만 관측이 한 건도 없는 주차장도 보고서에 남긴다.
        raw = obs.loc[obs["parking_id"].eq(pid)].sort_values("ts_kst").copy()

        # --------------------------------------------------
        # 1. 5분 grid 생성
        # --------------------------------------------------
        grid = observation_grid(raw, index=analysis_index).reset_index()

        grid["has_valid_obs"] = grid["occ"].notna()

        total_slots = len(grid)
        valid_slots = int(grid["has_valid_obs"].sum())

        missing_ratio = (
            1 - valid_slots / total_slots
            if total_slots > 0
            else np.nan
        )

        row = {
            "parking_id": pid,
            "n_slots_5m": total_slots,
            "n_valid_slots": valid_slots,
            "missing_ratio": missing_ratio,
            "has_observations": not raw.empty,
        }

        # --------------------------------------------------
        # 2. horizon별 "진짜 연속구간" 검사
        #
        # 예:
        # 60분 예측이면
        # 현재 포함 최근 13개 5분 슬롯이
        # 전부 존재해야 usable
        #
        # t-60, t-55, ..., t
        # --------------------------------------------------
        for h in HORIZONS:
            slots = h // FREQ_MIN + 1

            continuous = (
                grid["has_valid_obs"]
                .rolling(
                    window=slots,
                    min_periods=slots,
                )
                .sum()
                .eq(slots)
            )

            row[f"continuous_{h}m_count"] = int(
                continuous.sum()
            )
            row[f"continuous_{h}m_ratio"] = float(
                continuous.mean()
            )

        # --------------------------------------------------
        # 3. 원자료 이상치 개수
        # --------------------------------------------------
        row["negative_count"] = int(
            raw["bad_negative"].sum()
        )

        row["over_capacity_count"] = int(
            raw["bad_capacity"].sum()
        )

        row["bad_cell_cnt_count"] = int(
            raw["bad_cell_cnt"].sum()
        )

        row["bad_total_count"] = int(
            raw["bad_row"].sum()
        )
        row["missing_park_count"] = int(raw["bad_missing_count"].sum())

        row["zero_occ_count"] = int(
            ((raw["occ_raw"] == 0) & ~raw["bad_row"]).sum()
        )

        row["full_occ_count"] = int(
            ((raw["occ_raw"] == 100) & ~raw["bad_row"]).sum()
        )

        # --------------------------------------------------
        # 4. 진짜 급변 탐지
        #
        # 긴 공백을 건너뛴 점프는 제외.
        # 실제 시간차가 4~6분일 때만
        # 30%p 이상 변화를 급변으로 처리.
        # --------------------------------------------------
        raw_valid = raw[~raw["bad_row"]].copy()

        raw_valid["dt_min"] = (
            raw_valid["ts_kst"]
            .diff()
            .dt.total_seconds()
            / 60
        )

        raw_valid["occ_diff"] = (
            raw_valid["occ_raw"]
            .diff()
            .abs()
        )

        true_jump = (
            raw_valid["dt_min"].between(4, 6)
            & (raw_valid["occ_diff"] >= 30)
        )

        row["true_jump_30pp_count"] = int(
            true_jump.sum()
        )

        # --------------------------------------------------
        # 5. usable 비율
        #
        # 전체 raw 중 이상값 제외 비율
        # --------------------------------------------------
        row["raw_count"] = len(raw)

        row["usable_raw_count"] = int(
            (~raw["bad_row"]).sum()
        )

        row["usable_ratio"] = (
            row["usable_raw_count"] / row["raw_count"]
            if row["raw_count"] > 0
            else np.nan
        )

        results.append(row)

    out = pd.DataFrame(results)

    names = rules[
        ["parking_id", "name"]
    ].copy()

    out = out.merge(
        names,
        on="parking_id",
        how="left",
    )

    cols = ["parking_id", "name"] + [
        c for c in out.columns
        if c not in ("parking_id", "name")
    ]

    out = out[cols]

    out = out.sort_values(
        ["usable_ratio", "continuous_120m_ratio"],
        ascending=[True, True],
    )

    save = ROOT / "reports/tables/a18_continuity_check.csv"

    out.to_csv(
        save,
        index=False,
        encoding="utf-8-sig",
    )

    print("=== 연속성 요약 ===")

    show_cols = [
        "parking_id",
        "name",
        "missing_ratio",
        "continuous_15m_ratio",
        "continuous_30m_ratio",
        "continuous_60m_ratio",
        "continuous_120m_ratio",
        "over_capacity_count",
        "true_jump_30pp_count",
        "usable_ratio",
    ]

    print(
        out[show_cols]
        .to_string(index=False)
    )

    print()
    print("=== 전체 합계 ===")

    print(
        out[
            [
                "negative_count",
                "over_capacity_count",
                "bad_cell_cnt_count",
                "bad_total_count",
                "true_jump_30pp_count",
            ]
        ].sum()
    )

    print()
    print(
        "평균 usable_ratio:",
        round(out["usable_ratio"].mean(), 5)
    )

    print(
        "평균 continuous_60m_ratio:",
        round(out["continuous_60m_ratio"].mean(), 5)
    )

    print(
        "평균 continuous_120m_ratio:",
        round(out["continuous_120m_ratio"].mean(), 5)
    )

    print()
    print("저장:", save)
    print("이상행 저장:", bad_save)


if __name__ == "__main__":
    main()