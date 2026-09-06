from pathlib import Path
import sqlite3
import pandas as pd
import numpy as np


ROOT = Path(__file__).resolve().parents[2]
GITS_DB = ROOT / "data/raw/gits.db"

OUT_CSV = (
    ROOT
    / "reports/tables/a13_2_gits_freshness.csv"
)


def load_gits_obs():
    con = sqlite3.connect(GITS_DB)

    df = pd.read_sql_query(
        """
        SELECT
            ts_kst,
            lae_id,
            pkplc_id,
            cell_cnt,
            avail_cnt,
            ocrn_dt
        FROM gits_obs
        """,
        con,
    )

    con.close()

    # 수집 시각
    df["ts_kst"] = pd.to_datetime(
        df["ts_kst"],
        format="mixed",
        errors="coerce",
    )

    # GITS 원본 발생 시각
    df["ocrn_dt_parsed"] = pd.to_datetime(
        df["ocrn_dt"],
        format="mixed",
        errors="coerce",
    )
    # ocrn_dt가 timezone 없는 값이면 KST로 지정
    df["ocrn_dt_parsed"] = df["ocrn_dt_parsed"].apply(
    lambda x: (
        x.tz_localize("Asia/Seoul")
        if pd.notna(x) and x.tzinfo is None
        else x
    )
)
    return df


def main():
    print("=" * 70)
    print("A13-2 GITS Freshness Check")
    print("=" * 70)

    df = load_gits_obs()

    print()
    print(f"전체 관측치 : {len(df):,}")
    print(
        f"주차장 수   : "
        f"{df[['lae_id', 'pkplc_id']].drop_duplicates().shape[0]:,}"
    )

    print(
        f"수집 기간   : "
        f"{df['ts_kst'].min()} "
        f"~ {df['ts_kst'].max()}"
    )

    # -----------------------------------------------------
    # 1. ocrn_dt 파싱 상태
    # -----------------------------------------------------
    valid_ocrn = df["ocrn_dt_parsed"].notna()

    print()
    print("[ocrn_dt 상태]")

    print(
        f"파싱 성공 : "
        f"{valid_ocrn.sum():,} / {len(df):,} "
        f"({valid_ocrn.mean() * 100:.2f}%)"
    )

    # -----------------------------------------------------
    # 2. 수집 시각 - 원본 발생 시각
    # -----------------------------------------------------
    valid = df[
        df["ts_kst"].notna()
        & df["ocrn_dt_parsed"].notna()
    ].copy()

    if len(valid) > 0:
        try:
            valid["source_age_min"] = (
                valid["ts_kst"]
                - valid["ocrn_dt_parsed"]
            ).dt.total_seconds() / 60

            print()
            print("[수집 시점 기준 원본 데이터 나이]")

            print(
                valid["source_age_min"]
                .describe(
                    percentiles=[
                        0.50,
                        0.75,
                        0.90,
                        0.95,
                        0.99,
                    ]
                )
            )

            for threshold in [5, 10, 15, 30, 60]:
                ratio = (
                    valid["source_age_min"]
                    <= threshold
                ).mean() * 100

                print(
                    f"{threshold:>2}분 이내 : "
                    f"{ratio:.2f}%"
                )

            future_ratio = (
                valid["source_age_min"] < -1
            ).mean() * 100

            print(
                f"발생시각이 수집시각보다 "
                f"1분 이상 미래인 비율 : "
                f"{future_ratio:.2f}%"
            )

        except TypeError as e:
            print()
            print(
                "ocrn_dt와 ts_kst의 timezone 형식이 "
                "달라 age 계산 실패:"
            )
            print(e)

    # -----------------------------------------------------
    # 3. 우리가 실제 수집한 업데이트 간격
    # -----------------------------------------------------
    df = df.sort_values(
        ["lae_id", "pkplc_id", "ts_kst"]
    ).copy()

    df["poll_gap_min"] = (
        df.groupby(
            ["lae_id", "pkplc_id"]
        )["ts_kst"]
        .diff()
        .dt.total_seconds()
        / 60
    )

    gaps = df["poll_gap_min"].dropna()

    print()
    print("[DB 관측 간격]")

    if len(gaps) > 0:
        print(
            gaps.describe(
                percentiles=[
                    0.50,
                    0.75,
                    0.90,
                    0.95,
                    0.99,
                ]
            )
        )

        for threshold in [5, 10, 15, 30]:
            ratio = (
                gaps <= threshold
            ).mean() * 100

            print(
                f"관측 간격 {threshold:>2}분 이내 : "
                f"{ratio:.2f}%"
            )

    # -----------------------------------------------------
    # 4. 주차장별 관측 커버리지
    # -----------------------------------------------------
    lot_stats = (
        df.groupby(
            ["lae_id", "pkplc_id"],
            as_index=False,
        )
        .agg(
            n_obs=("ts_kst", "count"),
            first_ts=("ts_kst", "min"),
            last_ts=("ts_kst", "max"),
            median_gap_min=(
                "poll_gap_min",
                "median",
            ),
            p90_gap_min=(
                "poll_gap_min",
                lambda x: (
                    np.nanpercentile(x, 90)
                    if x.notna().any()
                    else np.nan
                ),
            ),
        )
    )

    print()
    print("[주차장별 관측 수]")

    print(
        lot_stats["n_obs"].describe(
            percentiles=[
                0.10,
                0.25,
                0.50,
                0.75,
                0.90,
            ]
        )
    )

    print()
    print("[관측 수가 적은 주차장 20개]")

    print(
        lot_stats.sort_values(
            "n_obs"
        )
        .head(20)
        .to_string(index=False)
    )

    # -----------------------------------------------------
    # 저장
    # -----------------------------------------------------
    OUT_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    lot_stats.to_csv(
        OUT_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print(f"저장 완료: {OUT_CSV}")


if __name__ == "__main__":
    main()