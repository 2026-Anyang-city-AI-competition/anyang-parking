from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]

PARKING_DB = ROOT / "data/raw/parking.db"
GITS_DB = ROOT / "data/raw/gits.db"


# =========================================================
# haversine 거리 계산
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
# parking.db 읽기
# =========================================================
def load_parking():
    con = sqlite3.connect(PARKING_DB)

    lots = pd.read_sql_query(
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
        format="mixed"
        
    )

    return lots, obs


# =========================================================
# gits.db 읽기
# =========================================================
def load_gits():
    con = sqlite3.connect(GITS_DB)

    lots = pd.read_sql_query(
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

    obs = pd.read_sql_query(
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

    obs["ts_kst"] = pd.to_datetime(
        obs["ts_kst"],
        format="mixed"
    )

    return lots, obs


def main():
    print("=" * 70)
    print("A13-0 GITS Overlap Check")
    print("=" * 70)

    parking_lots, parking_obs = load_parking()
    gits_lots, gits_obs = load_gits()

    print()
    print("[parking.db]")
    print(
        f"주차장 수   : "
        f"{parking_lots['parking_id'].nunique():,}"
    )
    print(
        f"관측치 수   : "
        f"{len(parking_obs):,}"
    )
    print(
        f"기간        : "
        f"{parking_obs['ts_kst'].min()} "
        f"~ "
        f"{parking_obs['ts_kst'].max()}"
    )

    print()
    print("[gits.db]")
    print(
        f"주차장 수   : "
        f"{len(gits_lots):,}"
    )
    print(
        f"관측치 수   : "
        f"{len(gits_obs):,}"
    )
    print(
        f"기간        : "
        f"{gits_obs['ts_kst'].min()} "
        f"~ "
        f"{gits_obs['ts_kst'].max()}"
    )

    # =====================================================
    # 시간 겹침
    # =====================================================
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

    if overlap_start <= overlap_end:
        print(
            f"겹치는 기간 : "
            f"{overlap_start} "
            f"~ "
            f"{overlap_end}"
        )

        print(
            f"겹치는 시간 : "
            f"{overlap_end - overlap_start}"
        )
    else:
        print("겹치는 시간이 없음")

    # =====================================================
    # 좌표 없는 데이터 제거
    # =====================================================
    p = parking_lots.dropna(
        subset=["lat", "lng"]
    ).copy()

    g = gits_lots.dropna(
        subset=["lat", "lon"]
    ).copy()

    print()
    print("[좌표 보유]")
    print(
        f"parking : {len(p):,}"
    )
    print(
        f"GITS    : {len(g):,}"
    )

    # =====================================================
    # 각 parking lot 기준 주변 GITS 개수 계산
    # =====================================================
    rows = []

    for _, prow in p.iterrows():
        distances = haversine_m(
            prow["lat"],
            prow["lng"],
            g["lat"].to_numpy(),
            g["lon"].to_numpy(),
        )

        within_500 = distances <= 500
        within_300 = distances <= 300

        n_300 = int(
            np.sum(within_300)
        )

        n_500 = int(
            np.sum(within_500)
        )

        nearest = (
            float(np.min(distances))
            if len(distances) > 0
            else np.nan
        )

        rows.append(
            {
                "parking_id": prow["parking_id"],
                "name": prow["name"],
                "gits_n_300": n_300,
                "gits_n_500": n_500,
                "nearest_gits_m": nearest,
            }
        )

    match_df = pd.DataFrame(rows)

    print()
    print("[500m 주변 GITS 주차장 매칭]")

    print(
        f"500m 내 1개 이상 : "
        f"{(match_df['gits_n_500'] >= 1).sum()} / "
        f"{len(match_df)}"
    )

    print(
        f"500m 내 2개 이상 : "
        f"{(match_df['gits_n_500'] >= 2).sum()} / "
        f"{len(match_df)}"
    )

    print(
        f"500m 내 3개 이상 : "
        f"{(match_df['gits_n_500'] >= 3).sum()} / "
        f"{len(match_df)}"
    )

    print(
        f"500m 내 평균 개수 : "
        f"{match_df['gits_n_500'].mean():.2f}"
    )

    print(
        f"500m 내 중앙값    : "
        f"{match_df['gits_n_500'].median():.0f}"
    )

    print()
    print("[300m 주변 GITS 주차장 매칭]")

    print(
        f"300m 내 1개 이상 : "
        f"{(match_df['gits_n_300'] >= 1).sum()} / "
        f"{len(match_df)}"
    )

    print(
        f"300m 내 평균 개수 : "
        f"{match_df['gits_n_300'].mean():.2f}"
    )

    print()
    print("[가까운 GITS 거리]")

    print(
        match_df[
            "nearest_gits_m"
        ].describe()
    )

    print()
    print("[500m 매칭 적은 주차장 예시]")

    print(
        match_df.sort_values(
            [
                "gits_n_500",
                "nearest_gits_m",
            ]
        )
        .head(15)
        .to_string(
            index=False
        )
    )

    out_path = (
        ROOT
        / "reports/tables/"
        / "a13_0_gits_overlap_check.csv"
    )

    out_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    match_df.to_csv(
        out_path,
        index=False,
        encoding="utf-8-sig",
    )

    print()
    print(
        f"저장 완료: {out_path}"
    )


if __name__ == "__main__":
    main()