from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor


ROOT = Path(__file__).resolve().parents[2]

PARKING_DB = ROOT / "data/raw/parking.db"

OUT_CSV = (
    ROOT
    / "reports/tables/a15_parking_id_model.csv"
)

FREQ = "5min"
HORIZONS = [15, 30, 60, 120]
TEST_RATIO = 0.20


# =========================================================
# Metrics
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
# Load parking observations
# =========================================================
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


# =========================================================
# Parking -> 5-minute series
# =========================================================
def build_base_series(obs):

    parts = []

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

        # A12/A14와 동일
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

        parts.append(
            g.reset_index()
        )

    return pd.concat(
        parts,
        ignore_index=True,
    )


# =========================================================
# A12 features
# =========================================================
def add_features(df):

    df = df.sort_values(
        ["parking_id", "ts_kst"]
    ).copy()

    grp = df.groupby(
        "parking_id"
    )["occ"]

    df["occ_now"] = df["occ"]

    # lag
    for m in [5, 10, 15, 30, 60]:

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

    # rolling
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

    # time
    df["hour"] = (
        df["ts_kst"].dt.hour
    )

    df["minute"] = (
        df["ts_kst"].dt.minute
    )

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

    # ---------------------------------------------
    # A15 핵심
    #
    # LightGBM이 categorical feature로 처리하도록
    # pandas category 타입으로 변환
    # ---------------------------------------------
    df["parking_id_cat"] = (
        df["parking_id"]
        .astype(str)
        .astype("category")
    )

    return df


# =========================================================
# Model
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
# Main
# =========================================================
def main():

    print("=" * 78)
    print("A15 Parking ID Categorical Ablation")
    print("=" * 78)

    obs = load_parking()

    data = build_base_series(
        obs
    )

    data = add_features(
        data
    )

    print()
    print("[전체 시계열]")
    print(
        f"{data['ts_kst'].min()} "
        f"~ {data['ts_kst'].max()}"
    )

    print(
        f"rows : {len(data):,}"
    )

    print(
        f"parking lots : "
        f"{data['parking_id'].nunique()}"
    )

    # =====================================================
    # Feature sets
    # =====================================================
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

    id_features = (
        base_features
        + ["parking_id_cat"]
    )

    # =====================================================
    # Global chronological split
    # =====================================================
    unique_times = (
        data["ts_kst"]
        .dropna()
        .sort_values()
        .unique()
    )

    split_idx = int(
        len(unique_times)
        * (1 - TEST_RATIO)
    )

    split_time = pd.Timestamp(
        unique_times[split_idx]
    )

    print()
    print("[Global split]")
    print(
        f"split_time : {split_time}"
    )

    print(
        f"test ratio : {TEST_RATIO:.0%}"
    )

    rows = []

    # =====================================================
    # Horizon loop
    # =====================================================
    for horizon in HORIZONS:

        print()
        print("-" * 78)
        print(
            f"Horizon = {horizon} min"
        )
        print("-" * 78)

        slots = horizon // 5

        temp = data.copy()

        # ---------------------------------------------
        # Future target
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

        temp = temp[
            temp["occ_now"].notna()
            & temp["target"].notna()
            & temp["target_time"].notna()
        ].copy()

        # ---------------------------------------------
        # Purged chronological split
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

        # A12와 A15가 정확히 같은 rows 사용
        train = train.dropna(
            subset=base_features
        )

        test = test.dropna(
            subset=base_features
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
                "표본 부족 -> skip"
            )
            continue

        y_train = train["target"]
        y_test = test["target"]

        # =================================================
        # 1. Persistence
        # =================================================
        pred_p = (
            test["occ_now"]
            .to_numpy()
        )

        m_p = metrics(
            y_test,
            pred_p,
        )

        print(
            "Persistence       "
            f"MAE={m_p['MAE']:.3f} "
            f"Median={m_p['MedianAE']:.3f} "
            f"P90={m_p['P90AE']:.3f}"
        )

        rows.append(
            {
                "horizon_min": horizon,
                "model": "Persistence",
                **m_p,
                "train_rows": len(train),
                "test_rows": len(test),
            }
        )

        # =================================================
        # 2. A12 Time
        # =================================================
        model_base = make_model()

        model_base.fit(
            train[base_features],
            y_train,
        )

        pred_base = (
            model_base.predict(
                test[base_features]
            )
        )

        pred_base = np.clip(
            pred_base,
            0,
            100,
        )

        m_base = metrics(
            y_test,
            pred_base,
        )

        print(
            "A12 Time          "
            f"MAE={m_base['MAE']:.3f} "
            f"Median={m_base['MedianAE']:.3f} "
            f"P90={m_base['P90AE']:.3f}"
        )

        rows.append(
            {
                "horizon_min": horizon,
                "model":
                    "ML_lag_motion_time",
                **m_base,
                "train_rows": len(train),
                "test_rows": len(test),
            }
        )

        # =================================================
        # 3. A15 Time + Parking ID
        # =================================================
        model_id = make_model()

        model_id.fit(
            train[id_features],
            y_train,
            categorical_feature=[
                "parking_id_cat"
            ],
        )

        pred_id = (
            model_id.predict(
                test[id_features]
            )
        )

        pred_id = np.clip(
            pred_id,
            0,
            100,
        )

        m_id = metrics(
            y_test,
            pred_id,
        )

        print(
            "A15 Time+ID       "
            f"MAE={m_id['MAE']:.3f} "
            f"Median={m_id['MedianAE']:.3f} "
            f"P90={m_id['P90AE']:.3f}"
        )

        rows.append(
            {
                "horizon_min": horizon,
                "model":
                    "ML_time_parking_id",
                **m_id,
                "train_rows": len(train),
                "test_rows": len(test),
            }
        )

        # =================================================
        # Change vs A12
        # =================================================
        mae_change = (
            (
                m_id["MAE"]
                - m_base["MAE"]
            )
            / m_base["MAE"]
            * 100
        )

        print()
        print(
            "A15 MAE change vs A12 : "
            f"{mae_change:+.2f}%"
        )

        if mae_change < 0:

            print(
                "→ Parking ID 개선"
            )

        elif mae_change > 0:

            print(
                "→ Parking ID 악화"
            )

        else:

            print(
                "→ 동일"
            )

        # =================================================
        # Feature importance
        # =================================================
        importance = pd.Series(
            model_id.feature_importances_,
            index=id_features,
        ).sort_values(
            ascending=False
        )

        print()
        print(
            "[A15 feature importance TOP 12]"
        )

        print(
            importance
            .head(12)
            .to_string()
        )

        print()
        print(
            "parking_id_cat importance : "
            f"{importance.get('parking_id_cat', 0)}"
        )

    # =====================================================
    # Save
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
    print("=" * 78)
    print("FINAL SUMMARY")
    print("=" * 78)

    if len(result):

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
                ]
            ].to_string(
                index=False
            )
        )

    print()
    print(
        f"저장 완료: {OUT_CSV}"
    )


if __name__ == "__main__":
    main()