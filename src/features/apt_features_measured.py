import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path

# ============================================================
# 경로
# ============================================================

ROOT = Path(__file__).resolve().parents[2]

DB = ROOT / "data" / "raw" / "parking.db"
APT = ROOT / "data" / "processed" / "apt_geocoded.csv"

OUTPUT = ROOT / "data" / "processed" / "apt_features_measured.csv"


# ============================================================
# 거리 계산
# ============================================================

def haversine_m(lat1, lon1, lat2, lon2):
    """
    위경도 -> 거리(m)
    lat1, lon1 : 단일 주차장
    lat2, lon2 : numpy array 공동주택
    """

    R = 6371000

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
        np.sqrt(1 - a)
    )

    return R * c


# ============================================================
# 측정 주차장 읽기
# ============================================================

con = sqlite3.connect(DB)

lots = pd.read_sql(
    "SELECT * FROM lots",
    con
)

con.close()

print("parking.db lots 컬럼:")
print(lots.columns.tolist())
print()


# 컬럼명 자동 탐색
def find_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    raise RuntimeError(
        f"필요한 컬럼을 찾지 못했습니다. 후보: {candidates}"
    )


id_col = find_col(
    lots,
    [
        "parking_id",
        "id",
        "parkingId"
    ]
)

name_col = find_col(
    lots,
    [
        "parking_name",
        "parking_nm",
        "name",
        "parkingName"
    ]
)

lat_col = find_col(
    lots,
    [
        "lat",
        "latitude",
        "y"
    ]
)

lon_col = find_col(
    lots,
    [
        "lon",
        "lng",
        "longitude",
        "x"
    ]
)


lots[lat_col] = pd.to_numeric(
    lots[lat_col],
    errors="coerce"
)

lots[lon_col] = pd.to_numeric(
    lots[lon_col],
    errors="coerce"
)

lots = lots.dropna(
    subset=[lat_col, lon_col]
).copy()


# ============================================================
# 공동주택 읽기
# ============================================================

apt = pd.read_csv(
    APT,
    encoding="utf-8-sig"
)

apt["latitude"] = pd.to_numeric(
    apt["latitude"],
    errors="coerce"
)

apt["longitude"] = pd.to_numeric(
    apt["longitude"],
    errors="coerce"
)

apt["households"] = pd.to_numeric(
    apt["households"],
    errors="coerce"
).fillna(0)

apt["parking_total"] = pd.to_numeric(
    apt["parking_total"],
    errors="coerce"
).fillna(0)

apt = apt.dropna(
    subset=["latitude", "longitude"]
).copy()


print("측정 주차장:", len(lots))
print("좌표 공동주택:", len(apt))
print()


apt_lat = apt["latitude"].to_numpy()
apt_lon = apt["longitude"].to_numpy()

households = apt["households"].to_numpy()
parking_total = apt["parking_total"].to_numpy()


# ============================================================
# 주차장별 공간 피처 생성
# ============================================================

rows = []

for idx, row in lots.iterrows():

    parking_id = row[id_col]
    parking_name = row[name_col]

    plat = row[lat_col]
    plon = row[lon_col]

    dist = haversine_m(
        plat,
        plon,
        apt_lat,
        apt_lon
    )

    mask300 = dist <= 300
    mask500 = dist <= 500

    feature = {
        "parking_id": parking_id,
        "parking_name": parking_name,

        # 공동주택 단지 수
        "apt_count_300": int(mask300.sum()),
        "apt_count_500": int(mask500.sum()),

        # 세대수
        "apt_households_300": float(
            households[mask300].sum()
        ),
        "apt_households_500": float(
            households[mask500].sum()
        ),

        # 공동주택 자체 주차 공급량
        "apt_parking_300": float(
            parking_total[mask300].sum()
        ),
        "apt_parking_500": float(
            parking_total[mask500].sum()
        ),

        # 가장 가까운 공동주택
        "nearest_apt_m": float(
            dist.min()
        ) if len(dist) else np.nan,
    }

    rows.append(feature)

    print(
        f"[{len(rows)}/{len(lots)}] "
        f"{parking_name} "
        f"-> 300m {feature['apt_count_300']}개 / "
        f"{int(feature['apt_households_300'])}세대 "
        f"| 500m {feature['apt_count_500']}개 / "
        f"{int(feature['apt_households_500'])}세대"
    )


# ============================================================
# 저장
# ============================================================

out = pd.DataFrame(rows)

OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True
)

out.to_csv(
    OUTPUT,
    index=False,
    encoding="utf-8-sig"
)

print()
print("=" * 60)
print("공동주택 Feature 생성 완료")
print("=" * 60)

print("주차장:", len(out))
print()

print("300m 공동주택 존재 주차장:",
      (out["apt_count_300"] > 0).sum())

print("500m 공동주택 존재 주차장:",
      (out["apt_count_500"] > 0).sum())

print()

print("평균 500m 공동주택 수:",
      round(out["apt_count_500"].mean(), 2))

print("평균 500m 세대수:",
      round(out["apt_households_500"].mean(), 2))

print("평균 최근접 공동주택 거리:",
      round(out["nearest_apt_m"].mean(), 2),
      "m")

print()
print("저장:", OUTPUT)