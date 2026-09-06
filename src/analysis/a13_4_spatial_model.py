from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd

from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error


ROOT = Path(__file__).resolve().parents[2]

PARKING_DB = ROOT / "data/raw/parking.db"

SPATIAL_CSV = (
    ROOT
    / "reports/tables/a13_3_compet_occ_500.csv"
)

OUT_CSV = (
    ROOT
    / "reports/tables/a13_4_spatial_model.csv"
)

FREQ = "5min"

HORIZONS = [15, 30, 60, 120]

# overlap 구간 내부 시간순 70% train / 30% test
TRAIN_RATIO = 0.70


# =========================================================
# 평가
# =========================================================
def metrics(y_true, y_pred):
    err = np.abs(
        np.asarray(y_true)
        - np.asarray(y_pred)
    )

    return {
        "MAE": np.mean(err),
        "MedianAE": np.median(err),
        "P90AE": np.percentile(err, 90),
    }


# =========================================================
# parking.db 로드
# =========================================================
def load_parking():
    con = sqlite3.connect(PARKING_DB)

    obs = pd.read_sql_query(
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

    obs["ts_kst"] = pd.to_datetime(
        obs["ts_kst"],
        format="mixed",
        errors="coerce",
    )

    obs = obs[
        obs["ts_kst"].notna()
        & obs["cell_cnt"].notna()
        & obs["park_count"].notna()
        & (obs["cell_cnt"] > 0)
    ].copy()

    obs["occ"] = (
        obs["park_count"]
        / obs["cell_cnt"]
        * 100
    )

    obs["occ"] = obs["occ"].clip(
        0,
        100,
    )

    return obs


# =========================================================
# A13-3 공간 feature 로드
# =========================================================
def load_spatial():
    df = pd.read_csv(
        SPATIAL_CSV
    )

    df["ts_kst"] = pd.to_datetime(
        df["ts_kst"],
        format="mixed",
        errors="coerce",
    )

    return df[
        [
            "ts_kst",
            "parking_id",
            "compet_occ_500",
            "compet_n_500",
        ]
    ].copy()


# =========================================================
# 5분 시계열 생성
# =========================================================
def build_base_series(obs):

    all_parts = []

    for parking_id, g in obs.groupby(
        "parking_id"
    ):

        g = g.sort_values(
            "ts_kst"
        ).copy()

        g = (
            g.set_index("ts_kst")
            [["occ"]]
            .resample(FREQ)
            .mean()
        )

        # 한 슬롯 정도의 짧은 누락만 보간
        g["occ"] = (
            g["occ"]
            .interpolate(
                method="linear",
                limit=1,
                limit_area="inside",
            )
        )

        g["parking_id"] = parking_id

        g = g.reset_index()

        all_parts.append(g)

    return pd.concat(
        all_parts,
        ignore_index=True,
    )


# =========================================================
# A13 spatial도 동일하게 5분 bin
# =========================================================
def build_spatial_series(spatial):

    parts = []

    for parking_id, g in spatial.groupby(
        "parking_id"
    ):

        g = g.sort_values(
            "ts_kst"
        ).copy()

        g = (
            g.set_index("ts_kst")
            .resample(FREQ)
            .agg(
                compet_occ_500=(
                    "compet_occ_500",
                    "mean",
                ),
                compet_n_500=(
                    "compet_n_500",
                    "max",
                ),
            )
        )

        g["parking_id"] = parking_id

        g = g.reset_index()

        parts.append(g)

    return pd.concat(
        parts,
        ignore_index=True,
    )


# =========================================================
# A12식 시계열 feature
# =========================================================
def add_features(df):

    df = df.sort_values(
        ["parking_id", "ts_kst"]
    ).copy()

    grp = df.groupby(
        "parking_id"
    )["occ"]

    # 현재
    df["occ_now"] = df["occ"]

    # lag
    lag_minutes = [
        5,
        10,
        15,
        30,
        60,
    ]

    for m in lag_minutes:
        slots = m // 5

        df[f"lag_{m}"] = (
            grp.shift(slots)
        )

    # motion
    df["delta_5"] = (
        df["occ_now"]
        - df["lag_5"]
    )

    df["delta_15"] = (
        df["occ_now"]
        - df["lag_15"]
    )

    df["delta_30"] = (
        df["occ_now"]
        - df["lag_30"]
    )

    # rolling mean
    df["roll_mean_15"] = (
        df.groupby("parking_id")["occ"]
        .transform(
            lambda x:
            x.rolling(
                window=3,
                min_periods=1,
            ).mean()
        )
    )

    df["roll_mean_30"] = (
        df.groupby("parking_id")["occ"]
        .transform(
            lambda x:
            x.rolling(
                window=6,
                min_periods=1,
            ).mean()
        )
    )

    df["roll_mean_60"] = (
        df.groupby("parking_id")["occ"]
        .transform(
            lambda x:
            x.rolling(
                window=12,
                min_periods=1,
            ).mean()
        )
    )

    # -----------------------------------------------------
    # 시간 feature
    # -----------------------------------------------------
    df["hour"] = df["ts_kst"].dt.hour
    df["minute"] = df["ts_kst"].dt.minute

    df["minute_of_day"] = (
        df["hour"] * 60
        + df["minute"]
    )

    df["hour_sin"] = np.sin(
        2 * np.pi
        * df["minute_of_day"]
        / 1440
    )

    df["hour_cos"] = np.cos(
        2 * np.pi
        * df["minute_of_day"]
        / 1440
    )

    df["day_of_week"] = (
        df["ts_kst"].dt.dayofweek
    )

    df["is_weekend"] = (
        df["day_of_week"] >= 5
    ).astype(int)

    return df


# =========================================================
# LightGBM
# =========================================================
def make_model():

    return LGBMRegressor(
        n_estimators=500,
        learning_rate=0.03,
        num_leaves=31,
        max_depth=-1,

        subsample=0.9,
        colsample_bytree=0.9,

        random_state=42,

        verbosity=-1,
    )


# =========================================================
# 메인
# =========================================================
def main():

    print("=" * 76)
    print("A13-4 Spatial Forecast Model - PRELIMINARY")
    print("=" * 76)

    obs = load_parking()
    spatial = load_spatial()

    # -----------------------------------------------------
    # parking 전체 5분 시계열
    # -----------------------------------------------------
    base = build_base_series(
        obs
    )

    base = add_features(
        base
    )

    # -----------------------------------------------------
    # GITS spatial 5분 시계열
    # -----------------------------------------------------
    spatial_5m = build_spatial_series(
        spatial
    )

    spatial_start = (
        spatial_5m["ts_kst"].min()
    )

    spatial_end = (
        spatial_5m["ts_kst"].max()
    )

    print()
    print("[GITS overlap]")
    print(
        f"{spatial_start} "
        f"~ {spatial_end}"
    )

    # -----------------------------------------------------
    # 공간 feature merge
    # -----------------------------------------------------
    data = base.merge(
        spatial_5m,
        on=[
            "parking_id",
            "ts_kst",
        ],
        how="left",
    )

    # GITS 존재 구간만 실험
    data = data[
        (data["ts_kst"] >= spatial_start)
        & (data["ts_kst"] <= spatial_end)
    ].copy()

    # 이웃 수 없는 행
    data["compet_n_500"] = (
        data["compet_n_500"]
        .fillna(0)
    )

    # compet_occ_500은 NaN 그대로 유지
    # LightGBM이 NaN 자체 처리

    # -----------------------------------------------------
    # 전체 overlap 기준 split timestamp
    # -----------------------------------------------------
    unique_times = (
        data["ts_kst"]
        .dropna()
        .sort_values()
        .unique()
    )

    split_idx = int(
        len(unique_times)
        * TRAIN_RATIO
    )

    split_time = pd.Timestamp(
        unique_times[split_idx]
    )

    print()
    print("[Global time split]")
    print(
        f"split_time : {split_time}"
    )

    print(
        f"train 구간 비율 : "
        f"{TRAIN_RATIO:.0%}"
    )

    # -----------------------------------------------------
    # feature 정의
    # -----------------------------------------------------
    base_features = [
        "occ_now",

        "lag_5",
        "lag_10",
        "lag_15",
        "lag_30",
        "lag_60",

        "delta_5",
        "delta_15",
        "delta_30",

        "roll_mean_15",
        "roll_mean_30",
        "roll_mean_60",

        "hour",
        "minute",
        "minute_of_day",

        "hour_sin",
        "hour_cos",

        "day_of_week",
        "is_weekend",
    ]

    spatial_features = (
        base_features
        + [
            "compet_occ_500",
            "compet_n_500",
        ]
    )

    rows = []

    # =====================================================
    # horizon별
    # =====================================================
    for horizon in HORIZONS:

        print()
        print("-" * 76)
        print(
            f"Horizon = {horizon} min"
        )
        print("-" * 76)

        slots = horizon // 5

        temp = data.copy()

        # ---------------------------------------------
        # future target
        # ---------------------------------------------
        temp["target"] = (
            temp.groupby(
                "parking_id"
            )["occ"]
            .shift(-slots)
        )

        temp["target_time"] = (
            temp.groupby(
                "parking_id"
            )["ts_kst"]
            .shift(-slots)
        )

        # target 존재 필수
        temp = temp[
            temp["occ_now"].notna()
            & temp["target"].notna()
            & temp["target_time"].notna()
        ].copy()

        # ---------------------------------------------
        # PURGED TRAIN
        #
        # 현재 ts도 split 이전이고
        # 미래 target 시각도 split 이전이어야 함
        # ---------------------------------------------
        train = temp[
            (temp["ts_kst"] < split_time)
            & (
                temp["target_time"]
                < split_time
            )
        ].copy()

        test = temp[
            temp["ts_kst"] >= split_time
        ].copy()

        # -------------------------------------------------
        # lag 등의 기본 feature가 충분한 rows만
        #
        # GITS NaN은 허용하므로 spatial feature는
        # drop하지 않음.
        # -------------------------------------------------
        required = base_features

        train = train.dropna(
            subset=required
        )

        test = test.dropna(
            subset=required
        )

        print(
            f"Train : {len(train):,}"
        )

        print(
            f"Test  : {len(test):,}"
        )

        if (
            len(train) < 100
            or len(test) < 100
        ):
            print(
                "표본이 너무 적어 "
                "이 horizon은 건너뜀."
            )
            continue

        # =================================================
        # 1. Persistence
        # =================================================
        y_test = test["target"]

        pred_persistence = (
            test["occ_now"]
        )

        m = metrics(
            y_test,
            pred_persistence,
        )

        rows.append(
            {
                "horizon_min": horizon,
                "model": "Persistence",
                "MAE": m["MAE"],
                "MedianAE": m["MedianAE"],
                "P90AE": m["P90AE"],
                "train_rows": len(train),
                "test_rows": len(test),
                "spatial_test_coverage_pct":
                    test[
                        "compet_occ_500"
                    ].notna().mean()
                    * 100,
            }
        )

        print(
            "Persistence       "
            f"MAE={m['MAE']:.3f} "
            f"Median={m['MedianAE']:.3f} "
            f"P90={m['P90AE']:.3f}"
        )

        # =================================================
        # 2. A12 style
        #    lag + motion + time
        # =================================================
        model_time = make_model()

        model_time.fit(
            train[base_features],
            train["target"],
        )

        pred_time = model_time.predict(
            test[base_features]
        )

        pred_time = np.clip(
            pred_time,
            0,
            100,
        )

        m = metrics(
            y_test,
            pred_time,
        )

        rows.append(
            {
                "horizon_min": horizon,
                "model":
                    "ML_lag_motion_time",
                "MAE": m["MAE"],
                "MedianAE": m["MedianAE"],
                "P90AE": m["P90AE"],
                "train_rows": len(train),
                "test_rows": len(test),
                "spatial_test_coverage_pct":
                    test[
                        "compet_occ_500"
                    ].notna().mean()
                    * 100,
            }
        )

        print(
            "A12 Time          "
            f"MAE={m['MAE']:.3f} "
            f"Median={m['MedianAE']:.3f} "
            f"P90={m['P90AE']:.3f}"
        )

        # =================================================
        # 3. A13 spatial
        # =================================================
        model_spatial = make_model()

        model_spatial.fit(
            train[spatial_features],
            train["target"],
        )

        pred_spatial = (
            model_spatial.predict(
                test[spatial_features]
            )
        )

        pred_spatial = np.clip(
            pred_spatial,
            0,
            100,
        )

        m = metrics(
            y_test,
            pred_spatial,
        )

        rows.append(
            {
                "horizon_min": horizon,
                "model":
                    "ML_time_GITS500",
                "MAE": m["MAE"],
                "MedianAE": m["MedianAE"],
                "P90AE": m["P90AE"],
                "train_rows": len(train),
                "test_rows": len(test),
                "spatial_test_coverage_pct":
                    test[
                        "compet_occ_500"
                    ].notna().mean()
                    * 100,
            }
        )

        print(
            "A13 Time + GITS   "
            f"MAE={m['MAE']:.3f} "
            f"Median={m['MedianAE']:.3f} "
            f"P90={m['P90AE']:.3f}"
        )

        # -------------------------------------------------
        # GITS feature coverage
        # -------------------------------------------------
        spatial_cov = (
            test[
                "compet_occ_500"
            ].notna().mean()
            * 100
        )

        print(
            f"GITS test coverage : "
            f"{spatial_cov:.2f}%"
        )

        # -------------------------------------------------
        # feature importance 참고
        # -------------------------------------------------
        importance = pd.Series(
            model_spatial.feature_importances_,
            index=spatial_features,
        ).sort_values(
            ascending=False
        )

        print()
        print(
            "[A13 feature importance TOP 10]"
        )

        print(
            importance
            .head(10)
            .to_string()
        )

        print()
        print(
            "compet_occ_500 importance : "
            f"{importance.get('compet_occ_500', 0)}"
        )

        print(
            "compet_n_500 importance   : "
            f"{importance.get('compet_n_500', 0)}"
        )

    # =====================================================
    # 저장
    # =====================================================
    result = pd.DataFrame(
        rows
    )

    OUT_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    result.to_csv(
        OUT_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print("=" * 76)
    print("FINAL SUMMARY")
    print("=" * 76)

    if len(result) > 0:
        print(
            result[
                [
                    "horizon_min",
                    "model",
                    "MAE",
                    "MedianAE",
                    "P90AE",
                    "train_rows",
                    "test_rows",
                    "spatial_test_coverage_pct",
                ]
            ].to_string(
                index=False
            )
        )

    print()
    print(
        f"저장 완료: {OUT_CSV}"
    )

    print()
    print(
        "※ 현재 GITS 수집 기간이 매우 짧으므로 "
        "A13-4 결과는 PRELIMINARY로 해석할 것."
    )


if __name__ == "__main__":
    main()