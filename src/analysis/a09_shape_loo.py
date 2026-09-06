#!/usr/bin/env python3
"""
a09 · 시간대 Shape interaction LOO

목적
----
a08에서 가장 좋았던 "BASE 9 + 공동주택 세대수"를 기준으로,
주변 환경이 시간대별 혼잡 패턴(Shape)을 설명하는지 검증한다.

새 API 없이 현재 확보한 데이터만 사용:
- parking.db                         : 실시간 혼잡도
- apt_features_measured.csv         : 공동주택 세대수
- shop_features_measured.csv        : 상권 대분류

비교
----
1) A08 reference
   - BASE 9 + 공동주택 세대수로 주차장 Level 예측
   - Shape는 train 주차장들의 시간대 평균 사용
   - a08의 약 19.99%p가 재현되는지 확인용

2) Row BASE + TIME
   - BASE 9 + 세대수 + hour sin/cos + 운영시간
   - 각 시간대 혼잡도 자체를 Ridge로 예측

3) + HOUSEHOLDS × TIME
   - 세대수가 많은 곳의 시간대 곡선이 다른지 학습

4) + FOOD/EDU × TIME
   - 음식/교육 상권 밀도와 시간대의 interaction 추가

5) + ALL INTERACTIONS
   - 세대수 + 음식 + 교육의 시간 interaction 모두 사용

검증
----
Leave-One-Location-Out:
한 주차장의 모든 관측값을 통째로 제외한 채 학습하고,
그 주차장의 시간대 혼잡도를 예측한다.

실행
----
python src/analysis/a09_shape_loo.py
"""

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import RidgeCV


ROOT = Path(__file__).resolve().parents[2]

DB = ROOT / "data/raw/parking.db"
KOTSA = ROOT / "data/raw/kotsa_v2_anyang.jsonl"
SHOP = ROOT / "data/processed/shop_features_measured.csv"
APT_FEATURE = ROOT / "data/processed/apt_features_measured.csv"

TAB = ROOT / "reports/tables"
TAB.mkdir(parents=True, exist_ok=True)

REPORT = []


def say(s=""):
    print(s, flush=True)
    REPORT.append(s)


# ============================================================
# 1. 실시간 주차 데이터
# ============================================================

con = sqlite3.connect(DB)

obs = pd.read_sql(
    "SELECT * FROM obs",
    con
)

L = pd.read_sql(
    "SELECT * FROM lots",
    con
).set_index("parking_id")

con.close()

obs["ts"] = pd.to_datetime(
    obs["ts_kst"],
    format="mixed"
)

obs["occ"] = (
    obs["park_count"]
    / obs["cell_cnt"].replace(0, np.nan)
).clip(0, 1.2)

obs = obs.dropna(
    subset=["occ", "ts"]
)

obs["hour"] = obs["ts"].dt.hour
obs["minute"] = obs["ts"].dt.minute

# 변동이 전혀 없는 주차장 제외
sd = obs.groupby(
    "parking_id"
)["park_count"].std()

zero_var_ids = set(
    sd[sd == 0].index
)

live = obs[
    ~obs.parking_id.isin(zero_var_ids)
].copy()


# ============================================================
# 2. 운영시간
# ============================================================

def hhmm(s):
    try:
        h, m = str(s).split(":")
        return int(h) + int(m) / 60
    except Exception:
        return np.nan


st = L["wdays_start"].map(hhmm)
en = L["wdays_end"].map(hhmm)

live["_h"] = (
    live["ts"].dt.hour
    + live["ts"].dt.minute / 60
)

live["op"] = (
    (
        live["_h"]
        >= live.parking_id.map(st)
    )
    &
    (
        live["_h"]
        < live.parking_id.map(en)
    )
).astype(int)


# ============================================================
# 3. BASE 9 피처
# ============================================================

ka = pd.read_json(
    KOTSA,
    lines=True
).drop_duplicates(
    "prk_center_id"
)

for c, sc in (
    ("la", "prk_plce_entrc_la"),
    ("lo", "prk_plce_entrc_lo"),
    ("cells", "prk_cmprt_co"),
):
    ka[c] = pd.to_numeric(
        ka[sc],
        errors="coerce"
    )

ka = ka.dropna(
    subset=["la", "lo", "cells"]
)


def compet(lat, lon, r=500):
    d = (
        6371000
        * 2
        * np.arcsin(
            np.sqrt(
                np.sin(
                    np.radians(
                        ka.la - lat
                    ) / 2
                ) ** 2
                +
                np.cos(np.radians(lat))
                * np.cos(np.radians(ka.la))
                * np.sin(
                    np.radians(
                        ka.lo - lon
                    ) / 2
                ) ** 2
            )
        )
    )

    m = d <= r

    return (
        int(m.sum()),
        float(
            ka.loc[m, "cells"].sum()
        )
    )


cn, cc = zip(
    *[
        compet(r.lat, r.lng)
        if pd.notna(r.lat)
        and pd.notna(r.lng)
        else (np.nan, np.nan)
        for r in L.itertuples()
    ]
)

op_h = (
    L["wdays_end"].map(hhmm)
    - L["wdays_start"].map(hhmm)
)

BASE = pd.DataFrame(
    {
        "log_cells":
            np.log1p(L["cell_cnt"]),

        "grade":
            pd.to_numeric(
                L["grade"],
                errors="coerce"
            ),

        "is_nosang":
            (L["div"] == "노상").astype(float),

        "is_underground":
            L["name"]
            .str.contains("지하", na=False)
            .astype(float),

        "is_transfer":
            L["name"]
            .str.contains("환승", na=False)
            .astype(float),

        "is_manan":
            (L["gu"] == "만안구").astype(float),

        "open_hours":
            op_h.where(op_h > 0, 24.0),

        "compet_n_500":
            np.log1p(
                pd.Series(cn, index=L.index)
            ),

        "compet_cells_500":
            np.log1p(
                pd.Series(cc, index=L.index)
            ),
    }
).replace(
    [np.inf, -np.inf],
    np.nan
)

BASE.index = BASE.index.astype(str)
live["parking_id"] = live["parking_id"].astype(str)


# ============================================================
# 4. 공동주택 피처
# ============================================================

apt = pd.read_csv(
    APT_FEATURE,
    encoding="utf-8-sig"
)

apt["parking_id"] = apt["parking_id"].astype(str)
apt = apt.set_index("parking_id")

APT_HOUSEHOLDS = [
    "apt_households_300",
    "apt_households_500",
]

missing_apt = [
    c for c in APT_HOUSEHOLDS
    if c not in apt.columns
]

if missing_apt:
    raise RuntimeError(
        f"공동주택 세대수 컬럼이 없습니다: {missing_apt}"
    )

APT = apt[APT_HOUSEHOLDS].copy()

for c in APT.columns:
    APT[c] = pd.to_numeric(
        APT[c],
        errors="coerce"
    ).fillna(0)

APT = np.log1p(
    APT.clip(lower=0)
)


# ============================================================
# 5. 상권 피처
# ============================================================

shop = pd.read_csv(
    SHOP,
    encoding="utf-8-sig"
)

shop["parking_id"] = shop["parking_id"].astype(str)
shop = shop.set_index("parking_id")

large_cols = [
    c for c in shop.columns
    if c.startswith("L_")
]

if not large_cols:
    raise RuntimeError(
        "L_ 로 시작하는 상권 대분류 컬럼을 찾지 못했습니다."
    )


def find_category_col(columns, keywords):
    """
    실제 컬럼명이 프로젝트 버전에 따라 조금 달라도
    코드/한글 키워드로 음식·교육 대분류를 자동 탐색한다.
    """
    for c in columns:
        uc = c.upper()
        if any(k.upper() in uc for k in keywords):
            return c
    return None


food_col = find_category_col(
    large_cols,
    ["I2", "음식", "FOOD"]
)

edu_col = find_category_col(
    large_cols,
    ["P1", "교육", "EDU"]
)

if food_col is None:
    raise RuntimeError(
        f"음식 상권 대분류 컬럼을 찾지 못했습니다. 후보: {large_cols}"
    )

if edu_col is None:
    raise RuntimeError(
        f"교육 상권 대분류 컬럼을 찾지 못했습니다. 후보: {large_cols}"
    )

SHOP_SELECTED = shop[
    [food_col, edu_col]
].copy()

for c in SHOP_SELECTED.columns:
    SHOP_SELECTED[c] = pd.to_numeric(
        SHOP_SELECTED[c],
        errors="coerce"
    ).fillna(0)

SHOP_SELECTED = np.log1p(
    SHOP_SELECTED.clip(lower=0)
)

SHOP_SELECTED = SHOP_SELECTED.rename(
    columns={
        food_col: "shop_food_500",
        edu_col: "shop_edu_500",
    }
)


# ============================================================
# 6. 주차장 static feature table
# ============================================================

STATIC = (
    BASE
    .join(
        APT,
        how="inner"
    )
    .join(
        SHOP_SELECTED,
        how="inner"
    )
)

STATIC = STATIC.replace(
    [np.inf, -np.inf],
    np.nan
).dropna()


# ============================================================
# 7. row-level 데이터셋 생성
# ============================================================

data = live[
    live["parking_id"].isin(
        STATIC.index
    )
].copy()

# 시간 자체를 임의의 구간으로 자르지 않고,
# 24시간 주기를 sin/cos로 표현
angle = (
    2 * np.pi
    * (
        data["hour"]
        + data["minute"] / 60
    )
    / 24.0
)

data["hour_sin"] = np.sin(angle)
data["hour_cos"] = np.cos(angle)

# static feature를 각 관측 row에 붙임
data = data.join(
    STATIC,
    on="parking_id"
)

# interaction
data["hh500_x_sin"] = (
    data["apt_households_500"]
    * data["hour_sin"]
)

data["hh500_x_cos"] = (
    data["apt_households_500"]
    * data["hour_cos"]
)

data["hh300_x_sin"] = (
    data["apt_households_300"]
    * data["hour_sin"]
)

data["hh300_x_cos"] = (
    data["apt_households_300"]
    * data["hour_cos"]
)

data["food_x_sin"] = (
    data["shop_food_500"]
    * data["hour_sin"]
)

data["food_x_cos"] = (
    data["shop_food_500"]
    * data["hour_cos"]
)

data["edu_x_sin"] = (
    data["shop_edu_500"]
    * data["hour_sin"]
)

data["edu_x_cos"] = (
    data["shop_edu_500"]
    * data["hour_cos"]
)


# ============================================================
# 8. a08 reference용 Level + global Shape
# ============================================================

REF_FEATURES = list(BASE.columns) + APT_HOUSEHOLDS
REF_STATIC = STATIC[REF_FEATURES].copy()


def spearman_ci(
    rho,
    n,
    alpha=0.05
):
    if (
        not np.isfinite(rho)
        or n < 4
    ):
        return np.nan, np.nan

    z = np.arctanh(
        np.clip(
            rho,
            -0.999999,
            0.999999
        )
    )

    se = 1.0 / np.sqrt(n - 3)
    k = stats.norm.ppf(1 - alpha / 2)

    return (
        float(np.tanh(z - k * se)),
        float(np.tanh(z + k * se))
    )


def loo_reference(
    FEAT,
    d,
    min_obs=10
):
    f = FEAT.replace(
        [np.inf, -np.inf],
        np.nan
    ).dropna()

    f = f.loc[
        f.index.intersection(
            d["parking_id"].unique()
        )
    ]

    dd = d[
        d.parking_id.isin(f.index)
    ].copy()

    cnt = dd.groupby(
        "parking_id"
    ).size()

    keep = [
        i
        for i in f.index
        if cnt.get(i, 0) >= min_obs
    ]

    f = f.loc[keep]
    dd = dd[
        dd.parking_id.isin(keep)
    ]

    lvl = dd.groupby(
        "parking_id"
    )["occ"].mean()

    dd["sh"] = (
        dd["occ"]
        / dd.parking_id.map(lvl)
    )

    errs = []
    hat = {}

    for pid in keep:
        tr = [
            i
            for i in keep
            if i != pid
        ]

        ftr = f.loc[tr]

        cols = [
            c for c in ftr.columns
            if ftr[c].std() > 0
        ]

        mu = ftr[cols].mean()
        sg = ftr[cols].std()

        model = RidgeCV(
            alphas=np.logspace(
                -2,
                3,
                20
            )
        )

        model.fit(
            (ftr[cols] - mu) / sg,
            lvl.loc[tr]
        )

        x_test = (
            f.loc[[pid], cols]
            - mu
        ) / sg

        lh = float(
            model.predict(x_test)[0]
        )

        hat[pid] = lh

        dtr = dd[
            dd.parking_id.isin(tr)
        ]

        sh_ho = (
            dtr
            .groupby(
                ["hour", "op"]
            )["sh"]
            .mean()
        )

        sh_h = (
            dtr
            .groupby("hour")["sh"]
            .mean()
        )

        te = dd[
            dd.parking_id == pid
        ]

        s1 = np.nan_to_num(
            te["hour"]
            .map(sh_h)
            .to_numpy(float),
            nan=1.0
        )

        idx = pd.MultiIndex.from_arrays(
            [
                te["hour"],
                te["op"]
            ]
        )

        s2 = (
            sh_ho
            .reindex(idx)
            .to_numpy(float)
        )

        s2 = np.where(
            np.isfinite(s2),
            s2,
            s1
        )

        pred = lh * s2

        errs.append(
            np.abs(
                te["occ"].values
                - pred
            )
        )

    rho = stats.spearmanr(
        pd.Series(hat).loc[keep],
        lvl.loc[keep]
    ).statistic

    lo, hi = spearman_ci(
        rho,
        len(keep)
    )

    mae = (
        np.concatenate(errs).mean()
        * 100
    )

    return (
        mae,
        rho,
        lo,
        hi,
        len(keep),
        len(dd)
    )


# ============================================================
# 9. row-level LOO
# ============================================================

ROW_BASE_TIME = (
    list(BASE.columns)
    + APT_HOUSEHOLDS
    + [
        "hour_sin",
        "hour_cos",
        "op",
    ]
)

ROW_HOUSEHOLDS = (
    ROW_BASE_TIME
    + [
        "hh300_x_sin",
        "hh300_x_cos",
        "hh500_x_sin",
        "hh500_x_cos",
    ]
)

ROW_SHOP = (
    ROW_BASE_TIME
    + [
        "shop_food_500",
        "shop_edu_500",
        "food_x_sin",
        "food_x_cos",
        "edu_x_sin",
        "edu_x_cos",
    ]
)

ROW_ALL = (
    ROW_HOUSEHOLDS
    + [
        "shop_food_500",
        "shop_edu_500",
        "food_x_sin",
        "food_x_cos",
        "edu_x_sin",
        "edu_x_cos",
    ]
)


def loo_row(
    feature_cols,
    d,
    min_obs=10
):
    dd = d[
        ["parking_id", "occ"] + feature_cols
    ].replace(
        [np.inf, -np.inf],
        np.nan
    ).dropna().copy()

    cnt = dd.groupby(
        "parking_id"
    ).size()

    keep = [
        pid
        for pid, n in cnt.items()
        if n >= min_obs
    ]

    dd = dd[
        dd.parking_id.isin(keep)
    ].copy()

    errs = []
    lot_true = {}
    lot_pred = {}

    for pid in keep:
        tr = dd[
            dd.parking_id != pid
        ].copy()

        te = dd[
            dd.parking_id == pid
        ].copy()

        # train에서 변화가 없는 컬럼 제외
        cols = [
            c for c in feature_cols
            if tr[c].std() > 0
        ]

        mu = tr[cols].mean()
        sg = tr[cols].std()

        Xtr = (
            tr[cols] - mu
        ) / sg

        Xte = (
            te[cols] - mu
        ) / sg

        model = RidgeCV(
            alphas=np.logspace(
                -2,
                3,
                20
            )
        )

        model.fit(
            Xtr,
            tr["occ"]
        )

        pred = model.predict(
            Xte
        )

        # 물리적으로 너무 과한 예측 방지
        pred = np.clip(
            pred,
            0,
            1.2
        )

        errs.append(
            np.abs(
                te["occ"].to_numpy()
                - pred
            )
        )

        lot_true[pid] = float(
            te["occ"].mean()
        )

        lot_pred[pid] = float(
            np.mean(pred)
        )

    mae = (
        np.concatenate(errs).mean()
        * 100
    )

    common = list(lot_true.keys())

    rho = stats.spearmanr(
        pd.Series(lot_pred).loc[common],
        pd.Series(lot_true).loc[common]
    ).statistic

    lo, hi = spearman_ci(
        rho,
        len(common)
    )

    return (
        mae,
        rho,
        lo,
        hi,
        len(common),
        len(dd)
    )


# ============================================================
# 10. 실행 / 비교
# ============================================================

say(
    "# a09 · 시간대 Shape interaction LOO"
)

say()

say(
    f"- 평가 후보 주차장: {data['parking_id'].nunique()}곳"
)

say(
    f"- 음식 상권 컬럼: {food_col}"
)

say(
    f"- 교육 상권 컬럼: {edu_col}"
)

say(
    "- 시간대는 임의 구간이 아니라 24시간 sin/cos 주기로 표현"
)

say(
    "- 모든 평가는 주차장 전체를 hold-out 하는 LOO"
)

say()

say(
    "| 모델 | 피처 수 | 평가 주차장 | MAE(%p) | Spearman [95% CI] |"
)

say(
    "|---|---:|---:|---:|---|"
)

results = []

# a08 reference
mae, rho, lo, hi, n, nobs = loo_reference(
    REF_STATIC,
    live
)

results.append(
    {
        "model": "A08 reference (Level + global Shape)",
        "n_features": len(REF_FEATURES),
        "n_eval": n,
        "n_obs": nobs,
        "mae_pp": mae,
        "spearman": rho,
        "ci_lo": lo,
        "ci_hi": hi,
    }
)

say(
    f"| A08 reference (Level + global Shape) | "
    f"{len(REF_FEATURES)} | {n} | "
    f"**{mae:.2f}** | "
    f"{rho:+.3f} [{lo:+.2f}, {hi:+.2f}] |"
)

row_sets = [
    (
        "Row BASE + TIME",
        ROW_BASE_TIME
    ),
    (
        "+ HOUSEHOLDS × TIME",
        ROW_HOUSEHOLDS
    ),
    (
        "+ FOOD/EDU × TIME",
        ROW_SHOP
    ),
    (
        "+ ALL interactions",
        ROW_ALL
    ),
]

for name, cols in row_sets:
    mae, rho, lo, hi, n, nobs = loo_row(
        cols,
        data
    )

    results.append(
        {
            "model": name,
            "n_features": len(cols),
            "n_eval": n,
            "n_obs": nobs,
            "mae_pp": mae,
            "spearman": rho,
            "ci_lo": lo,
            "ci_hi": hi,
        }
    )

    say(
        f"| {name} | "
        f"{len(cols)} | {n} | "
        f"**{mae:.2f}** | "
        f"{rho:+.3f} [{lo:+.2f}, {hi:+.2f}] |"
    )


# ============================================================
# 11. 성능 변화
# ============================================================

say()

ref = results[0]

for r in results[1:]:
    say(
        f"- **{r['model']}**: "
        f"A08 reference 대비 MAE "
        f"{r['mae_pp'] - ref['mae_pp']:+.2f}%p · "
        f"Spearman "
        f"{r['spearman'] - ref['spearman']:+.3f}"
    )


# ============================================================
# 12. 저장
# ============================================================

result_df = pd.DataFrame(
    results
)

result_df.to_csv(
    TAB / "a09_shape_loo.csv",
    index=False,
    encoding="utf-8-sig"
)

(
    TAB / "a09_shape_loo.md"
).write_text(
    "\n".join(REPORT) + "\n",
    encoding="utf-8"
)

print()
print(
    f"→ {TAB / 'a09_shape_loo.md'}"
)
