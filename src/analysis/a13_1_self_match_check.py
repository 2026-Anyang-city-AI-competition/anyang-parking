from pathlib import Path
import sqlite3

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]

PARKING_DB = ROOT / "data/raw/parking.db"
GITS_DB = ROOT / "data/raw/gits.db"

OUT_CSV = (
    ROOT
    / "reports/tables/a13_1_self_match_check.csv"
)


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
# 데이터 읽기
# =========================================================
def load_lots():
    con = sqlite3.connect(PARKING_DB)

    parking = pd.read_sql_query(
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

    con.close()

    con = sqlite3.connect(GITS_DB)

    gits = pd.read_sql_query(
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

    con.close()

    parking = parking.dropna(
        subset=["lat", "lng"]
    ).copy()

    gits = gits.dropna(
        subset=["lat", "lon"]
    ).copy()

    return parking, gits


# =========================================================
# 메인
# =========================================================
def main():
    print("=" * 70)
    print("A13-1 Self Match Check")
    print("=" * 70)

    parking, gits = load_lots()

    print()
    print(f"parking 대상 : {len(parking):,}")
    print(f"GITS 좌표 보유 : {len(gits):,}")

    rows = []

    # -----------------------------------------------------
    # 각 parking 대상마다 GITS 거리 계산
    # -----------------------------------------------------
    for _, p in parking.iterrows():

        distances = haversine_m(
            p["lat"],
            p["lng"],
            gits["lat"].to_numpy(),
            gits["lon"].to_numpy(),
        )

        temp = gits.copy()
        temp["distance_m"] = distances

        temp = temp.sort_values(
            "distance_m"
        )

        nearest = temp.iloc[0]

        # -------------------------------------------------
        # self 판정
        #
        # 좌표가 10m 이내이고,
        # 총 주차면 수도 같으면
        # 동일 주차장일 가능성이 매우 높다고 판단
        # -------------------------------------------------
        same_cell = (
            pd.notna(p["cell_cnt"])
            and pd.notna(nearest["cell_cnt"])
            and int(p["cell_cnt"])
            == int(nearest["cell_cnt"])
        )

        self_match = (
            nearest["distance_m"] <= 10
            and same_cell
        )

        # -------------------------------------------------
        # self 제거 전 주변 개수
        # -------------------------------------------------
        before_300 = int(
            (temp["distance_m"] <= 300).sum()
        )

        before_500 = int(
            (temp["distance_m"] <= 500).sum()
        )

        # -------------------------------------------------
        # self 제거
        # -------------------------------------------------
        if self_match:
            temp_no_self = temp.drop(
                index=nearest.name
            ).copy()
        else:
            temp_no_self = temp.copy()

        # -------------------------------------------------
        # self 제거 후 주변 개수
        # -------------------------------------------------
        after_300 = int(
            (
                temp_no_self["distance_m"]
                <= 300
            ).sum()
        )

        after_500 = int(
            (
                temp_no_self["distance_m"]
                <= 500
            ).sum()
        )

        # self 제거 후 가장 가까운 GITS
        if len(temp_no_self) > 0:
            nearest_other = (
                temp_no_self.iloc[0]
            )

            nearest_other_m = float(
                nearest_other["distance_m"]
            )

            nearest_other_name = (
                nearest_other["pkplc_nm"]
            )
        else:
            nearest_other_m = np.nan
            nearest_other_name = None

        rows.append(
            {
                "parking_id": p["parking_id"],
                "parking_name": p["name"],
                "parking_cells": p["cell_cnt"],

                "nearest_gits_name":
                    nearest["pkplc_nm"],

                "nearest_gits_cells":
                    nearest["cell_cnt"],

                "nearest_distance_m":
                    nearest["distance_m"],

                "self_match":
                    self_match,

                "before_n_300":
                    before_300,

                "after_n_300":
                    after_300,

                "before_n_500":
                    before_500,

                "after_n_500":
                    after_500,

                "nearest_other_name":
                    nearest_other_name,

                "nearest_other_m":
                    nearest_other_m,
            }
        )

    result = pd.DataFrame(rows)

    # =====================================================
    # 결과 출력
    # =====================================================
    print()
    print("[Self match]")

    n_self = int(
        result["self_match"].sum()
    )

    print(
        f"self 판정 : "
        f"{n_self} / {len(result)}"
    )

    print()
    print("[Self 제거 전/후 500m]")

    print(
        f"제거 전 1개 이상 : "
        f"{(result['before_n_500'] >= 1).sum()} "
        f"/ {len(result)}"
    )

    print(
        f"제거 후 1개 이상 : "
        f"{(result['after_n_500'] >= 1).sum()} "
        f"/ {len(result)}"
    )

    print(
        f"제거 후 2개 이상 : "
        f"{(result['after_n_500'] >= 2).sum()} "
        f"/ {len(result)}"
    )

    print(
        f"제거 후 3개 이상 : "
        f"{(result['after_n_500'] >= 3).sum()} "
        f"/ {len(result)}"
    )

    print(
        f"제거 후 평균 개수 : "
        f"{result['after_n_500'].mean():.2f}"
    )

    print(
        f"제거 후 중앙값    : "
        f"{result['after_n_500'].median():.0f}"
    )

    print()
    print("[Self 제거 후 300m]")

    print(
        f"1개 이상 : "
        f"{(result['after_n_300'] >= 1).sum()} "
        f"/ {len(result)}"
    )

    print(
        f"평균 개수 : "
        f"{result['after_n_300'].mean():.2f}"
    )

    # =====================================================
    # 주변 GITS가 없는 대상
    # =====================================================
    no_neighbor = result[
        result["after_n_500"] == 0
    ]

    print()
    print("[Self 제거 후 500m 이웃 0개]")

    if len(no_neighbor) == 0:
        print("없음")
    else:
        print(
            no_neighbor[
                [
                    "parking_id",
                    "parking_name",
                    "nearest_gits_name",
                    "nearest_distance_m",
                    "nearest_other_name",
                    "nearest_other_m",
                ]
            ].to_string(
                index=False
            )
        )

    # =====================================================
    # self 판정 예시
    # =====================================================
    print()
    print("[Self 판정 예시]")

    print(
        result[
            result["self_match"]
        ][
            [
                "parking_id",
                "parking_name",
                "parking_cells",
                "nearest_gits_name",
                "nearest_gits_cells",
                "nearest_distance_m",
            ]
        ]
        .head(20)
        .to_string(
            index=False
        )
    )

    # =====================================================
    # self 판정 안 된 가까운 주차장도 확인
    # =====================================================
    print()
    print("[Self 미판정 중 거리 50m 이하]")

    suspicious = result[
        (~result["self_match"])
        & (result["nearest_distance_m"] <= 50)
    ]

    if len(suspicious) == 0:
        print("없음")
    else:
        print(
            suspicious[
                [
                    "parking_id",
                    "parking_name",
                    "parking_cells",
                    "nearest_gits_name",
                    "nearest_gits_cells",
                    "nearest_distance_m",
                ]
            ].to_string(
                index=False
            )
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