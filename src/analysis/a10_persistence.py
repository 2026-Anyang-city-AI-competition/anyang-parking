from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]

# 네 프로젝트에서는 parking.db라는 이름으로 두는 것을 권장
DB_PATH = ROOT / "data/raw/parking.db"

OUT_CSV = ROOT / "reports/tables/a10_persistence.csv"
OUT_MD = ROOT / "reports/tables/a10_persistence.md"

HORIZONS = [15, 30, 60, 120]

# 실제 관측시각이 정확히 5분 단위가 아닐 수 있으므로
# 목표시각 ±2분 안에서 가장 가까운 관측치를 사용
TOLERANCE = pd.Timedelta(minutes=2)


def load_data():
    con = sqlite3.connect(DB_PATH)

    df = pd.read_sql_query(
        """
        SELECT
            ts_kst,
            parking_id,
            cell_cnt,
            park_count
        FROM obs
        ORDER BY parking_id, ts_kst
        """,
        con,
    )

    con.close()

    df["ts_kst"] = pd.to_datetime(df["ts_kst"])

    # 비정상 데이터 제거
    df = df[
        df["cell_cnt"].notna()
        & df["park_count"].notna()
        & (df["cell_cnt"] > 0)
        & (df["park_count"] >= 0)
    ].copy()

    # 점유율 0~1
    df["occ"] = df["park_count"] / df["cell_cnt"]

    # 혹시 센서값이 총 면수를 초과하면 1로 제한
    df["occ"] = df["occ"].clip(0, 1)

    return df


def evaluate_horizon(df, horizon):
    left = df[
        ["parking_id", "ts_kst", "occ"]
    ].copy()

    left = left.rename(
        columns={
            "ts_kst": "current_time",
            "occ": "current_occ",
        }
    )

    left["target_time"] = (
        left["current_time"]
        + pd.Timedelta(minutes=horizon)
    )

    right = df[
        ["parking_id", "ts_kst", "occ"]
    ].copy()

    right = right.rename(
        columns={
            "ts_kst": "actual_time",
            "occ": "actual_occ",
        }
    )

    # merge_asof를 위해 정렬
    left = left.sort_values(
        ["target_time", "parking_id"]
    )

    right = right.sort_values(
        ["actual_time", "parking_id"]
    )

    matched = pd.merge_asof(
        left,
        right,
        left_on="target_time",
        right_on="actual_time",
        by="parking_id",
        direction="nearest",
        tolerance=TOLERANCE,
    )

    matched = matched.dropna(
        subset=["actual_occ"]
    ).copy()

    # Persistence:
    # 미래도 현재 점유율과 같다고 예측
    matched["pred_occ"] = matched["current_occ"]

    matched["abs_error"] = (
        matched["pred_occ"]
        - matched["actual_occ"]
    ).abs()

    mae = matched["abs_error"].mean() * 100

    median_ae = matched["abs_error"].median() * 100

    p90_ae = (
        matched["abs_error"].quantile(0.90) * 100
    )

    return {
        "horizon_min": horizon,
        "n_pairs": len(matched),
        "n_lots": matched["parking_id"].nunique(),
        "mae_pct_point": mae,
        "median_ae_pct_point": median_ae,
        "p90_ae_pct_point": p90_ae,
    }


def main():
    print("=" * 60)
    print("A10 Persistence Baseline")
    print("=" * 60)

    df = load_data()

    print()
    print(f"관측치 수 : {len(df):,}")
    print(f"주차장 수 : {df['parking_id'].nunique()}")
    print(
        f"수집 기간 : "
        f"{df['ts_kst'].min()} ~ "
        f"{df['ts_kst'].max()}"
    )

    results = []

    print()
    print("[Persistence 결과]")

    for horizon in HORIZONS:
        result = evaluate_horizon(df, horizon)
        results.append(result)

        print(
            f"{horizon:>3}분 후 | "
            f"MAE {result['mae_pct_point']:.2f}%p | "
            f"Median {result['median_ae_pct_point']:.2f}%p | "
            f"P90 {result['p90_ae_pct_point']:.2f}%p | "
            f"N {result['n_pairs']:,} | "
            f"Lots {result['n_lots']}"
        )

    result_df = pd.DataFrame(results)

    OUT_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    result_df.to_csv(
        OUT_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    with open(
        OUT_MD,
        "w",
        encoding="utf-8",
    ) as f:
        f.write("# A10 Persistence Baseline\n\n")
        f.write(
            result_df.to_markdown(
                index=False,
                floatfmt=".2f",
            )
        )
        f.write("\n")

    print()
    print(f"저장: {OUT_CSV}")
    print(f"저장: {OUT_MD}")


if __name__ == "__main__":
    main()