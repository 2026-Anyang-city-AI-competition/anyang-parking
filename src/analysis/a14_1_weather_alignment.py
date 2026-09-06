from pathlib import Path
import sqlite3

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]

PARKING_DB = ROOT / "data/raw/parking.db"
WEATHER_CSV = ROOT / "data/raw/weather.csv"

OUT_CSV = (
    ROOT
    / "reports/tables/a14_1_weather_alignment.csv"
)


def load_parking():
    con = sqlite3.connect(PARKING_DB)

    df = pd.read_sql_query(
        """
        SELECT
            ts_kst,
            parking_id,
            cell_cnt,
            park_count
        FROM obs
        """,
        con,
    )

    con.close()

    df["ts_kst"] = pd.to_datetime(
        df["ts_kst"],
        format="mixed",
        errors="coerce",
    )

    df = df[
        df["ts_kst"].notna()
        & df["cell_cnt"].notna()
        & df["park_count"].notna()
        & (df["cell_cnt"] > 0)
    ].copy()

    df["occ"] = (
        df["park_count"]
        / df["cell_cnt"]
        * 100
    ).clip(0, 100)

    return df


def load_weather():
    df = pd.read_csv(
        WEATHER_CSV
    )

    df["ts_kst"] = pd.to_datetime(
        df["ts_kst"],
        format="mixed",
        errors="coerce",
    )

    return df.sort_values(
        "ts_kst"
    )


def main():

    print("=" * 72)
    print("A14-1 Weather Alignment Check")
    print("=" * 72)

    parking = load_parking()
    weather = load_weather()

    print()
    print("[parking]")
    print(
        f"{parking['ts_kst'].min()} "
        f"~ {parking['ts_kst'].max()}"
    )
    print(
        f"rows : {len(parking):,}"
    )

    print()
    print("[weather]")
    print(
        f"{weather['ts_kst'].min()} "
        f"~ {weather['ts_kst'].max()}"
    )
    print(
        f"rows : {len(weather):,}"
    )

    # -----------------------------------------------------
    # 미래 날씨 사용 금지
    #
    # 각 parking 시각 t에서
    # t보다 같거나 이전의 최신 시간별 날씨만 사용
    # -----------------------------------------------------
    parking = parking.sort_values(
        "ts_kst"
    )

    merged = pd.merge_asof(
        parking,
        weather,
        on="ts_kst",
        direction="backward",
        tolerance=pd.Timedelta("60min"),
    )

    weather_cols = [
        "temperature",
        "humidity",
        "precipitation",
        "wind_speed",
        "is_rain",
    ]

    valid = (
        merged["temperature"]
        .notna()
    )

    print()
    print("[weather merge coverage]")
    print(
        f"전체 parking rows : "
        f"{len(merged):,}"
    )

    print(
        f"weather 유효 rows : "
        f"{valid.sum():,}"
    )

    print(
        f"coverage : "
        f"{valid.mean() * 100:.2f}%"
    )

    print()
    print("[결측치]")
    print(
        merged[
            weather_cols
        ]
        .isna()
        .sum()
        .to_string()
    )

    print()
    print("[merge된 기상값 분포]")
    print(
        merged[
            [
                "temperature",
                "humidity",
                "precipitation",
                "wind_speed",
            ]
        ]
        .describe()
        .to_string()
    )

    # -----------------------------------------------------
    # 비 / 비 안 옴 시 현재 점유율 단순 비교
    # 인과효과가 아니라 diagnostic
    # -----------------------------------------------------
    valid_df = merged[
        valid
    ].copy()

    rain_summary = (
        valid_df
        .groupby("is_rain")["occ"]
        .agg(
            [
                "count",
                "mean",
                "median",
                "std",
            ]
        )
        .reset_index()
    )

    print()
    print("[현재 점유율: 비 여부 단순 비교]")
    print(
        rain_summary
        .to_string(index=False)
    )

    OUT_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    merged.to_csv(
        OUT_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print(
        f"저장 완료: {OUT_CSV}"
    )


if __name__ == "__main__":
    main()