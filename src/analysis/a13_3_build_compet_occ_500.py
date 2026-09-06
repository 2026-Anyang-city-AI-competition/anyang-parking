from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]

PARKING_DB = ROOT / "data/raw/parking.db"
GITS_DB = ROOT / "data/raw/gits.db"

OUT_CSV = (
    ROOT
    / "reports/tables/a13_3_compet_occ_500.csv"
)

FRESHNESS_MIN = 15
RADIUS_M = 500


# =========================================================
# 거리 계산
# =========================================================
def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0

    lat1 = np.radians(lat1)
    lon1 = np.radians(lon1)
    lat2 = np.radians(lat2)
    lon2 = np.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1)
        * np.cos(lat2)
        * np.sin(dlon / 2) ** 2
    )

    c = 2 * np.arctan2(
        np.sqrt(a),
        np.sqrt(1 - a),
    )

    return R * c


# =========================================================
# 이름 정규화
# =========================================================
def normalize_name(x):
    if pd.isna(x):
        return ""

    return (
        str(x)
        .replace(" ", "")
        .replace(",", "")
        .replace(".", "")
        .strip()
    )


# =========================================================
# 데이터 로드
# =========================================================
def load_data():
    con = sqlite3.connect(PARKING_DB)

    parking_lots = pd.read_sql_query(
        """
        SELECT
            parking_id,
            name,
            lat,
            lng,
            cell_cnt
        FROM lots
        """,
        con,
    )

    parking_obs = pd.read_sql_query(
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

    con = sqlite3.connect(GITS_DB)

    gits_lots = pd.read_sql_query(
        """
        SELECT
            lae_id,
            lae_nm,
            pkplc_id,
            pkplc_nm,
            lat,
            lon,
            cell_cnt
        FROM gits_lots
        """,
        con,
    )

    gits_obs = pd.read_sql_query(
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

    parking_obs["ts_kst"] = pd.to_datetime(
        parking_obs["ts_kst"],
        format="mixed",
        errors="coerce",
    )

    gits_obs["ts_kst"] = pd.to_datetime(
        gits_obs["ts_kst"],
        format="mixed",
        errors="coerce",
    )

    return (
        parking_lots,
        parking_obs,
        gits_lots,
        gits_obs,
    )


# =========================================================
# self 및 500m 이웃 매핑 생성
# =========================================================
def build_neighbor_map(
    parking_lots,
    gits_lots,
):
    parking_lots = parking_lots.dropna(
        subset=["lat", "lng"]
    ).copy()

    gits_lots = gits_lots.dropna(
        subset=["lat", "lon"]
    ).copy()

    gits_lots["norm_name"] = (
        gits_lots["pkplc_nm"]
        .apply(normalize_name)
    )

    rows = []

    for _, p in parking_lots.iterrows():

        dist = haversine_m(
            p["lat"],
            p["lng"],
            gits_lots["lat"].to_numpy(),
            gits_lots["lon"].to_numpy(),
        )

        temp = gits_lots.copy()
        temp["distance_m"] = dist

        temp = temp[
            temp["distance_m"] <= RADIUS_M
        ].copy()

        p_name = normalize_name(p["name"])

        # -------------------------------------------------
        # self 판정
        #
        # 1) 이름 동일 + 면수 동일 + 100m 이내
        # 또는
        # 2) 10m 이내 + 면수 동일
        # -------------------------------------------------
        def is_self(row):
            same_cell = (
                pd.notna(p["cell_cnt"])
                and pd.notna(row["cell_cnt"])
                and int(p["cell_cnt"])
                == int(row["cell_cnt"])
            )

            same_name = (
                p_name == row["norm_name"]
            )

            return (
                same_cell
                and (
                    row["distance_m"] <= 10
                    or (
                        same_name
                        and row["distance_m"] <= 100
                    )
                )
            )

        temp["is_self"] = temp.apply(
            is_self,
            axis=1,
        )

        temp = temp[
            ~temp["is_self"]
        ].copy()

        for _, g in temp.iterrows():
            rows.append(
                {
                    "parking_id":
                        p["parking_id"],

                    "lae_id":
                        g["lae_id"],

                    "pkplc_id":
                        g["pkplc_id"],

                    "gits_name":
                        g["pkplc_nm"],

                    "distance_m":
                        g["distance_m"],
                }
            )

    return pd.DataFrame(rows)


# =========================================================
# GITS 관측 정리
# =========================================================
def prepare_gits_obs(gits_obs):

    g = gits_obs.copy()

    g = g[
        g["ts_kst"].notna()
        & g["cell_cnt"].notna()
        & g["avail_cnt"].notna()
    ].copy()

    g = g[
        g["cell_cnt"] > 0
    ].copy()

    g = g[
        (g["avail_cnt"] >= 0)
        & (
            g["avail_cnt"]
            <= g["cell_cnt"]
        )
    ].copy()

    g["occ"] = (
        g["cell_cnt"]
        - g["avail_cnt"]
    ) / g["cell_cnt"]

    g["occ_pct"] = (
        g["occ"] * 100
    )

    return g


# =========================================================
# 한 parking_id에 대해 feature 생성
# =========================================================
def build_for_one_parking(
    parking_id,
    target_obs,
    neighbor_map,
    gits_obs,
):

    neighbors = neighbor_map[
        neighbor_map["parking_id"]
        == parking_id
    ].copy()

    target = target_obs[
        target_obs["parking_id"]
        == parking_id
    ][
        [
            "ts_kst",
            "parking_id",
            "cell_cnt",
            "park_count",
        ]
    ].copy()

    target = target.sort_values(
        "ts_kst"
    )

    if len(target) == 0:
        return None

    # 주변 GITS 자체가 없는 경우
    if len(neighbors) == 0:
        target["compet_occ_500"] = np.nan
        target["compet_n_500"] = 0

        return target

    merged_list = []

    # -----------------------------------------------------
    # 주변 주차장 각각에 대해 backward asof
    # -----------------------------------------------------
    for _, nb in neighbors.iterrows():

        gg = gits_obs[
            (gits_obs["lae_id"] == nb["lae_id"])
            & (
                gits_obs["pkplc_id"]
                == nb["pkplc_id"]
            )
        ][
            [
                "ts_kst",
                "occ_pct",
            ]
        ].copy()

        if len(gg) == 0:
            continue

        gg = gg.sort_values(
            "ts_kst"
        )

        gg = gg.rename(
            columns={
                "ts_kst": "gits_ts",
                "occ_pct": "gits_occ_pct",
            }
        )

        left = target[
            ["ts_kst"]
        ].copy()

        # merge_asof를 위해 이름 맞춤
        right = gg.rename(
            columns={
                "gits_ts": "ts_kst"
            }
        )

        aligned = pd.merge_asof(
            left.sort_values("ts_kst"),
            right.sort_values("ts_kst"),
            on="ts_kst",
            direction="backward",
            tolerance=pd.Timedelta(
                minutes=FRESHNESS_MIN
            ),
        )

        aligned = aligned.rename(
            columns={
                "gits_occ_pct":
                    "neighbor_occ"
            }
        )

        aligned["neighbor_key"] = (
            str(nb["lae_id"])
            + "_"
            + str(nb["pkplc_id"])
        )

        aligned["distance_m"] = (
            nb["distance_m"]
        )

        merged_list.append(
            aligned
        )

    if len(merged_list) == 0:
        target["compet_occ_500"] = np.nan
        target["compet_n_500"] = 0

        return target

    long_df = pd.concat(
        merged_list,
        ignore_index=True,
    )

    # -----------------------------------------------------
    # 같은 ts에서 유효한 이웃 평균/개수
    # -----------------------------------------------------
    feature = (
        long_df.groupby(
            "ts_kst",
            as_index=False,
        )
        .agg(
            compet_occ_500=(
                "neighbor_occ",
                "mean",
            ),
            compet_n_500=(
                "neighbor_occ",
                "count",
            ),
        )
    )

    result = target.merge(
        feature,
        on="ts_kst",
        how="left",
    )

    result["compet_n_500"] = (
        result["compet_n_500"]
        .fillna(0)
        .astype(int)
    )

    return result


# =========================================================
# 메인
# =========================================================
def main():

    print("=" * 70)
    print("A13-3 Build compet_occ_500")
    print("=" * 70)

    (
        parking_lots,
        parking_obs,
        gits_lots,
        gits_obs,
    ) = load_data()

    neighbor_map = build_neighbor_map(
        parking_lots,
        gits_lots,
    )

    print()
    print("[500m non-self neighbor map]")

    n_targets = (
        neighbor_map["parking_id"]
        .nunique()
        if len(neighbor_map) > 0
        else 0
    )

    print(
        f"이웃 1개 이상 target : "
        f"{n_targets} / {parking_lots['parking_id'].nunique()}"
    )

    if len(neighbor_map) > 0:
        neighbor_counts = (
            neighbor_map.groupby(
                "parking_id"
            )
            .size()
        )

        print(
            f"평균 이웃 수 : "
            f"{neighbor_counts.mean():.2f}"
        )

        print(
            f"중앙값       : "
            f"{neighbor_counts.median():.0f}"
        )

    gits_obs = prepare_gits_obs(
        gits_obs
    )

    print()
    print("[유효 GITS 관측]")

    print(
        f"{len(gits_obs):,} rows"
    )

    print(
        f"점유율 범위 : "
        f"{gits_obs['occ_pct'].min():.2f}% "
        f"~ "
        f"{gits_obs['occ_pct'].max():.2f}%"
    )

    # -----------------------------------------------------
    # 시간 겹침 범위만 사용
    # -----------------------------------------------------
    overlap_start = max(
        parking_obs["ts_kst"].min(),
        gits_obs["ts_kst"].min(),
    )

    overlap_end = min(
        parking_obs["ts_kst"].max(),
        gits_obs["ts_kst"].max(),
    )

    print()
    print("[시간 겹침]")

    print(
        f"{overlap_start} "
        f"~ {overlap_end}"
    )

    target_obs = parking_obs[
        (
            parking_obs["ts_kst"]
            >= overlap_start
        )
        & (
            parking_obs["ts_kst"]
            <= overlap_end
        )
    ].copy()

    # -----------------------------------------------------
    # target별 feature 생성
    # -----------------------------------------------------
    results = []

    target_ids = sorted(
        target_obs["parking_id"]
        .dropna()
        .unique()
    )

    for i, parking_id in enumerate(
        target_ids,
        start=1,
    ):
        out = build_for_one_parking(
            parking_id,
            target_obs,
            neighbor_map,
            gits_obs,
        )

        if out is not None:
            results.append(out)

        if (
            i % 10 == 0
            or i == len(target_ids)
        ):
            print(
                f"처리 중: "
                f"{i}/{len(target_ids)}"
            )

    result = pd.concat(
        results,
        ignore_index=True,
    )

    # =====================================================
    # 커버리지
    # =====================================================
    print()
    print("[compet_occ_500 커버리지]")

    total = len(result)

    valid = (
        result["compet_occ_500"]
        .notna()
        .sum()
    )

    print(
        f"전체 target rows : "
        f"{total:,}"
    )

    print(
        f"feature 유효 rows : "
        f"{valid:,}"
    )

    print(
        f"row coverage : "
        f"{valid / total * 100:.2f}%"
    )

    print()
    print("[유효 이웃 수 분포]")

    print(
        result["compet_n_500"]
        .describe(
            percentiles=[
                0.25,
                0.50,
                0.75,
                0.90,
                0.95,
            ]
        )
    )

    # -----------------------------------------------------
    # parking별 coverage
    # -----------------------------------------------------
    lot_coverage = (
        result.groupby(
            "parking_id"
        )
        .agg(
            n_rows=(
                "ts_kst",
                "size",
            ),
            n_valid=(
                "compet_occ_500",
                "count",
            ),
            avg_neighbor_n=(
                "compet_n_500",
                "mean",
            ),
        )
        .reset_index()
    )

    lot_coverage["coverage_pct"] = (
        lot_coverage["n_valid"]
        / lot_coverage["n_rows"]
        * 100
    )

    print()
    print("[parking별 coverage 하위 20개]")

    print(
        lot_coverage.sort_values(
            [
                "coverage_pct",
                "avg_neighbor_n",
            ]
        )
        .head(20)
        .to_string(index=False)
    )

    # -----------------------------------------------------
    # feature 값 sanity check
    # -----------------------------------------------------
    print()
    print("[compet_occ_500 값 분포]")

    print(
        result["compet_occ_500"]
        .describe(
            percentiles=[
                0.10,
                0.25,
                0.50,
                0.75,
                0.90,
            ]
        )
    )

    # -----------------------------------------------------
    # 현재 target 혼잡률과 단순 상관
    # -----------------------------------------------------
    result["target_occ_pct"] = (
        result["park_count"]
        / result["cell_cnt"]
        * 100
    )

    corr_df = result[
        [
            "target_occ_pct",
            "compet_occ_500",
        ]
    ].dropna()

    if len(corr_df) > 0:
        pearson = corr_df.corr(
            method="pearson"
        ).iloc[0, 1]

        spearman = corr_df.corr(
            method="spearman"
        ).iloc[0, 1]

        print()
        print("[현재 target vs 주변 GITS 상관]")

        print(
            f"Pearson  : {pearson:.4f}"
        )

        print(
            f"Spearman : {spearman:.4f}"
        )

    # =====================================================
    # 저장
    # =====================================================
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
    print(f"저장 완료: {OUT_CSV}")


if __name__ == "__main__":
    main()