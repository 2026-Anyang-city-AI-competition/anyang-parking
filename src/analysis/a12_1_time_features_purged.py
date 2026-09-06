from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd

from lightgbm import LGBMRegressor
from sklearn.metrics import mean_absolute_error


ROOT = Path(__file__).resolve().parents[2]

DB_PATH = ROOT / "data/raw/parking.db"

OUT_CSV = ROOT / "reports/tables/a12_1_time_features_purged.csv"
HORIZONS = [15, 30, 60, 120]

# 전체 시간의 뒤 20%를 TEST로 사용
TEST_RATIO = 0.20


# =========================================================
# 1. 데이터 로드
# =========================================================
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

    df = df[
        df["cell_cnt"].notna()
        & df["park_count"].notna()
        & (df["cell_cnt"] > 0)
        & (df["park_count"] >= 0)
    ].copy()

    df["occ"] = df["park_count"] / df["cell_cnt"]
    df["occ"] = df["occ"].clip(0, 1)

    return df


# =========================================================
# 2. 5분 단위로 정렬
# =========================================================
def resample_lot(group):
    group = group.sort_values("ts_kst").copy()

    group = group.set_index("ts_kst")

    # 실제 수집 시간이 04:56:37 같은 식이라
    # 5분 단위로 정리
    group = (
        group[["occ"]]
        .resample("5min")
        .mean()
    )

    # 너무 짧은 결측만 보간
    group["occ"] = group["occ"].interpolate(
        method="time",
        limit=1,
    )

    return group


def prepare_series(df):
    frames = []

    for parking_id, group in df.groupby("parking_id"):
        g = resample_lot(group)

        g["parking_id"] = parking_id

        frames.append(
            g.reset_index()
        )

    result = pd.concat(
        frames,
        ignore_index=True,
    )

    return result


# =========================================================
# 3. Feature 생성
# =========================================================
def make_features(df):
    df = df.sort_values(
        ["parking_id", "ts_kst"]
    ).copy()

    grouped = df.groupby(
        "parking_id",
        group_keys=False,
    )

    # 현재
    df["occ_now"] = df["occ"]

    # -------------------------
    # LAG
    # -------------------------
    df["lag_5"] = grouped["occ"].shift(1)
    df["lag_10"] = grouped["occ"].shift(2)
    df["lag_15"] = grouped["occ"].shift(3)
    df["lag_30"] = grouped["occ"].shift(6)
    df["lag_60"] = grouped["occ"].shift(12)

    # -------------------------
    # 변화량
    # -------------------------
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

    # -------------------------
    # 이동평균
    #
    # 현재 시점 포함
    # -------------------------
    df["rolling_mean_15"] = (
        grouped["occ"]
        .rolling(
            window=3,
            min_periods=3,
        )
        .mean()
        .reset_index(
            level=0,
            drop=True,
        )
    )

    df["rolling_mean_30"] = (
        grouped["occ"]
        .rolling(
            window=6,
            min_periods=6,
        )
        .mean()
        .reset_index(
            level=0,
            drop=True,
        )
    )

    df["rolling_mean_60"] = (
        grouped["occ"]
        .rolling(
            window=12,
            min_periods=12,
        )
        .mean()
        .reset_index(
            level=0,
            drop=True,
        )
    )
        # =====================================================
    # 시간 Feature
    # =====================================================

    # 현재 시각
    df["hour"] = df["ts_kst"].dt.hour
    df["minute"] = df["ts_kst"].dt.minute

    # 하루 중 몇 분째인지
    df["minute_of_day"] = (
        df["hour"] * 60
        + df["minute"]
    )

    # 23시와 0시가 멀리 떨어진 숫자로 인식되지 않도록
    # sin/cos 주기형 표현
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

    # 요일
    # 월=0 ... 일=6
    df["day_of_week"] = (
        df["ts_kst"].dt.dayofweek
    )

    # 주말 여부
    df["is_weekend"] = (
        df["day_of_week"] >= 5
    ).astype(int)

    return df
    


# =========================================================
# 4. 미래 정답 생성
# =========================================================
def add_target(df, horizon):
    steps = horizon // 5

    df = df.copy()

    df["target"] = (
        df.groupby("parking_id")["occ"]
        .shift(-steps)
    )

    # 이 행의 정답이 실제로 존재하는 미래 시각
    df["target_time"] = (
        df["ts_kst"]
        + pd.Timedelta(minutes=horizon)
    )

    return df


# =========================================================
# 5. 시간 기준 Train/Test split
# =========================================================
def get_time_split(df):
    unique_times = np.array(
        sorted(df["ts_kst"].dropna().unique())
    )

    split_idx = int(
        len(unique_times)
        * (1 - TEST_RATIO)
    )

    split_time = unique_times[split_idx]

    return pd.Timestamp(split_time)


# =========================================================
# 6. 평가 함수
# =========================================================
def calc_metrics(y_true, y_pred):
    errors = np.abs(
        np.asarray(y_true)
        - np.asarray(y_pred)
    )

    return {
        "mae_pct_point":
            np.mean(errors) * 100,

        "median_ae_pct_point":
            np.median(errors) * 100,

        "p90_ae_pct_point":
            np.quantile(errors, 0.90) * 100,
    }


# =========================================================
# 7. ML 모델
# =========================================================
def train_model(
    train_df,
    test_df,
    feature_cols,
):
    X_train = train_df[feature_cols]
    y_train = train_df["target"]

    X_test = test_df[feature_cols]
    y_test = test_df["target"]

    model = LGBMRegressor(
    n_estimators=300,
    learning_rate=0.05,
    num_leaves=31,
    max_depth=-1,
    min_child_samples=30,
    subsample=0.9,
    colsample_bytree=0.9,
    reg_lambda=1.0,
    random_state=42,
    verbosity=-1,
)

    model.fit(
        X_train,
        y_train,
    )

    pred = model.predict(X_test)

    pred = np.clip(
        pred,
        0,
        1,
    )

    return y_test, pred


# =========================================================
# 8. Horizon별 실험
# =========================================================
def evaluate_horizon(
    base_df,
    horizon,
    split_time,
):
    df = add_target(
        base_df,
        horizon,
    )

    # -----------------------------------------------------
    # 모든 모델이 동일한 샘플을 쓰도록
    # 필요한 모든 feature가 존재하는 행만 남김
    # -----------------------------------------------------
    all_features = [
        "occ_now",
        "lag_5",
        "lag_10",
        "lag_15",
        "lag_30",
        "lag_60",
        "delta_5",
        "delta_15",
        "delta_30",
        "rolling_mean_15",
        "rolling_mean_30",
        "rolling_mean_60",
        "hour_sin",
        "hour_cos",
        "day_of_week",
        "is_weekend",
    ]

    usable = df.dropna(
        subset=
        all_features
        + ["target"]
    ).copy()
# Train:
# 현재 시각뿐 아니라 미래 정답 시각도
# 반드시 split 이전이어야 함
    train_df = usable[
        usable["target_time"] < split_time
    ].copy()
# Test:
# split 이후 시점에서 미래를 예측
    test_df = usable[
        usable["ts_kst"] >= split_time
    ].copy()

    results = []

    # =====================================================
    # Model 0: Persistence
    # =====================================================
    persistence_pred = test_df["occ_now"]

    metrics = calc_metrics(
        test_df["target"],
        persistence_pred,
    )

    results.append(
        {
            "horizon_min": horizon,
            "model": "Persistence",
            "n_train": len(train_df),
            "n_test": len(test_df),
            **metrics,
        }
    )

    # =====================================================
    # Model 1: 현재 점유율만
    # =====================================================
    features_current = [
        "occ_now",
    ]

    y_test, pred = train_model(
        train_df,
        test_df,
        features_current,
    )

    metrics = calc_metrics(
        y_test,
        pred,
    )

    results.append(
        {
            "horizon_min": horizon,
            "model": "ML_current",
            "n_train": len(train_df),
            "n_test": len(test_df),
            **metrics,
        }
    )

    # =====================================================
    # Model 2: 현재 + LAG
    # =====================================================
    features_lag = [
        "occ_now",
        "lag_5",
        "lag_10",
        "lag_15",
        "lag_30",
        "lag_60",
    ]

    y_test, pred = train_model(
        train_df,
        test_df,
        features_lag,
    )

    metrics = calc_metrics(
        y_test,
        pred,
    )

    results.append(
        {
            "horizon_min": horizon,
            "model": "ML_lag",
            "n_train": len(train_df),
            "n_test": len(test_df),
            **metrics,
        }
    )

    # =====================================================
    # Model 3: LAG + 변화량 + 이동평균
    # =====================================================
    features_motion = [
        "occ_now",

        "lag_5",
        "lag_10",
        "lag_15",
        "lag_30",
        "lag_60",

        "delta_5",
        "delta_15",
        "delta_30",

        "rolling_mean_15",
        "rolling_mean_30",
        "rolling_mean_60",
    ]

    y_test, pred = train_model(
        train_df,
        test_df,
        features_motion,
    )

    metrics = calc_metrics(
        y_test,
        pred,
    )

    results.append(
        {
            "horizon_min": horizon,
            "model": "ML_lag_motion",
            "n_train": len(train_df),
            "n_test": len(test_df),
            **metrics,
        }
    )

  
    # =====================================================
    # Model 4:
    # LAG + 변화량 + 이동평균 + 시간
    # =====================================================

    features_time = [
        "occ_now",

        "lag_5",
        "lag_10",
        "lag_15",
        "lag_30",
        "lag_60",

        "delta_5",
        "delta_15",
        "delta_30",

        "rolling_mean_15",
        "rolling_mean_30",
        "rolling_mean_60",

        "hour_sin",
        "hour_cos",
        "day_of_week",
        "is_weekend",
    ]

    y_test, pred = train_model(
        train_df,
        test_df,
        features_time,
    )

    metrics = calc_metrics(
        y_test,
        pred,
    )

    results.append(
        {
            "horizon_min": horizon,
            "model": "ML_lag_motion_time",
            "n_train": len(train_df),
            "n_test": len(test_df),
            **metrics,
        }
    )

    return results    

# =========================================================
# MAIN
# =========================================================
def main():
    print("=" * 70)
    print("A12.1 Time Features - Purged Split")
    print("=" * 70)

    raw_df = load_data()

    print()
    print(
        f"원본 관측치 : {len(raw_df):,}"
    )

    print(
        f"주차장 수   : "
        f"{raw_df['parking_id'].nunique()}"
    )

    print(
        f"원본 기간   : "
        f"{raw_df['ts_kst'].min()} "
        f"~ "
        f"{raw_df['ts_kst'].max()}"
    )

    print()
    print("5분 단위 시계열 생성 중...")

    df = prepare_series(raw_df)

    df = make_features(df)

    split_time = get_time_split(df)

    print()
    print(
        f"Train/Test 기준 시각 : "
        f"{split_time}"
    )

    print()

    all_results = []

    for horizon in HORIZONS:
        print(
            "=" * 70
        )

        print(
            f"{horizon}분 후 예측"
        )

        results = evaluate_horizon(
            df,
            horizon,
            split_time,
        )

        all_results.extend(results)

        for r in results:
            print(
                f"{r['model']:<16} | "
                f"MAE "
                f"{r['mae_pct_point']:.2f}%p | "
                f"Median "
                f"{r['median_ae_pct_point']:.2f}%p | "
                f"P90 "
                f"{r['p90_ae_pct_point']:.2f}%p | "
                f"Train "
                f"{r['n_train']:,} | "
                f"Test "
                f"{r['n_test']:,}"
            )

        print()

    result_df = pd.DataFrame(
        all_results
    )

    # -----------------------------------------------------
    # Persistence 대비 개선율
    # -----------------------------------------------------
    baseline = (
        result_df[
            result_df["model"]
            == "Persistence"
        ]
        .set_index("horizon_min")
        ["mae_pct_point"]
    )

    def improvement(row):
        base = baseline.loc[
            row["horizon_min"]
        ]

        return (
            (base - row["mae_pct_point"])
            / base
            * 100
        )

    result_df[
        "mae_improvement_pct"
    ] = result_df.apply(
        improvement,
        axis=1,
    )

    OUT_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    result_df.to_csv(
        OUT_CSV,
        index=False,
        encoding="utf-8-sig",
    )

    print("=" * 70)
    print("최종 MAE 비교")
    print("=" * 70)

    pivot = result_df.pivot(
        index="model",
        columns="horizon_min",
        values="mae_pct_point",
    )

    print(
        pivot.round(2)
    )

    print()
    print(
        f"저장 완료: {OUT_CSV}"
    )


if __name__ == "__main__":
    main()