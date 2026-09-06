#!/usr/bin/env python3
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

# 1. 실시간 주차 데이터
con = sqlite3.connect(DB)
obs = pd.read_sql("SELECT * FROM obs", con)
L = pd.read_sql("SELECT * FROM lots", con).set_index("parking_id")
con.close()

obs["ts"] = pd.to_datetime(obs["ts_kst"], format="mixed")
obs["occ"] = (obs["park_count"] / obs["cell_cnt"].replace(0, np.nan)).clip(0, 1.2)
obs["hour"] = obs["ts"].dt.hour
obs = obs.dropna(subset=["occ"])

sd = obs.groupby("parking_id")["park_count"].std()
zero_var_ids = set(sd[sd == 0].index)
live = obs[~obs.parking_id.isin(zero_var_ids)].copy()

# 2. 운영시간
def hhmm(s):
    try:
        h, m = str(s).split(":")
        return int(h) + int(m) / 60
    except Exception:
        return np.nan

st = L["wdays_start"].map(hhmm)
en = L["wdays_end"].map(hhmm)

live["_h"] = live["ts"].dt.hour + live["ts"].dt.minute / 60
live["op"] = (
    (live["_h"] >= live.parking_id.map(st))
    & (live["_h"] < live.parking_id.map(en))
).astype(int)

# 3. BASE 9
ka = pd.read_json(KOTSA, lines=True).drop_duplicates("prk_center_id")

for c, sc in (
    ("la", "prk_plce_entrc_la"),
    ("lo", "prk_plce_entrc_lo"),
    ("cells", "prk_cmprt_co"),
):
    ka[c] = pd.to_numeric(ka[sc], errors="coerce")

ka = ka.dropna(subset=["la", "lo", "cells"])

def compet(lat, lon, r=500):
    d = (
        6371000 * 2 * np.arcsin(
            np.sqrt(
                np.sin(np.radians(ka.la - lat) / 2) ** 2
                + np.cos(np.radians(lat))
                * np.cos(np.radians(ka.la))
                * np.sin(np.radians(ka.lo - lon) / 2) ** 2
            )
        )
    )
    m = d <= r
    return int(m.sum()), float(ka.loc[m, "cells"].sum())

cn, cc = zip(*[
    compet(r.lat, r.lng)
    if pd.notna(r.lat) and pd.notna(r.lng)
    else (np.nan, np.nan)
    for r in L.itertuples()
])

op_h = L["wdays_end"].map(hhmm) - L["wdays_start"].map(hhmm)

BASE = pd.DataFrame({
    "log_cells": np.log1p(L["cell_cnt"]),
    "grade": pd.to_numeric(L["grade"], errors="coerce"),
    "is_nosang": (L["div"] == "노상").astype(float),
    "is_underground": L["name"].str.contains("지하", na=False).astype(float),
    "is_transfer": L["name"].str.contains("환승", na=False).astype(float),
    "is_manan": (L["gu"] == "만안구").astype(float),
    "open_hours": op_h.where(op_h > 0, 24.0),
    "compet_n_500": np.log1p(pd.Series(cn, index=L.index)),
    "compet_cells_500": np.log1p(pd.Series(cc, index=L.index)),
}).replace([np.inf, -np.inf], np.nan)

BASE.index = BASE.index.astype(str)
live["parking_id"] = live["parking_id"].astype(str)

# 4. 상권
shop = pd.read_csv(SHOP, encoding="utf-8-sig")
shop["parking_id"] = shop["parking_id"].astype(str)
shop = shop.set_index("parking_id")

large_cols = [c for c in shop.columns if c.startswith("L_")]

SHOP_TOTAL = shop[["shops_total_500"]].copy()
SHOP_LARGE = shop[large_cols].copy()

for df in (SHOP_TOTAL, SHOP_LARGE):
    for c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

SHOP_TOTAL = np.log1p(SHOP_TOTAL.clip(lower=0))
SHOP_LARGE = np.log1p(SHOP_LARGE.clip(lower=0))
SHOP_ALL = SHOP_TOTAL.join(SHOP_LARGE, how="inner")

# 5. 공동주택
apt = pd.read_csv(APT_FEATURE, encoding="utf-8-sig")
apt["parking_id"] = apt["parking_id"].astype(str)
apt = apt.set_index("parking_id")

APT_COLS = [
    "apt_count_300",
    "apt_count_500",
    "apt_households_300",
    "apt_households_500",
    "apt_parking_300",
    "apt_parking_500",
    "nearest_apt_m",
]

missing_apt_cols = [c for c in APT_COLS if c not in apt.columns]
if missing_apt_cols:
    raise RuntimeError(f"공동주택 피처 컬럼이 없습니다: {missing_apt_cols}")

APT = apt[APT_COLS].copy()

for c in APT.columns:
    APT[c] = pd.to_numeric(APT[c], errors="coerce")

APT = np.log1p(APT.clip(lower=0))

APT_HOUSEHOLDS = [
    "apt_households_300",
    "apt_households_500",
]

APT_COUNT_HOUSEHOLDS = [
    "apt_count_300",
    "apt_count_500",
    "apt_households_300",
    "apt_households_500",
]

# 6. 피처셋
FEAT_BASE = BASE.copy()
FEAT_APT_HOUSEHOLDS = BASE.join(APT[APT_HOUSEHOLDS], how="inner")
FEAT_APT_COUNT_HOUSEHOLDS = BASE.join(APT[APT_COUNT_HOUSEHOLDS], how="inner")
FEAT_APT_ALL = BASE.join(APT[APT_COLS], how="inner")
FEAT_SHOP_ALL = BASE.join(SHOP_ALL, how="inner")
FEAT_SHOP_APT = BASE.join(SHOP_ALL, how="inner").join(APT[APT_COLS], how="inner")

# 7. Spearman CI
def spearman_ci(rho, n, alpha=0.05):
    if not np.isfinite(rho) or n < 4:
        return np.nan, np.nan

    z = np.arctanh(np.clip(rho, -0.999999, 0.999999))
    se = 1.0 / np.sqrt(n - 3)
    k = stats.norm.ppf(1 - alpha / 2)

    return float(np.tanh(z - k * se)), float(np.tanh(z + k * se))

# 8. LOO
def loo(FEAT, data, min_obs=10):
    f = FEAT.replace([np.inf, -np.inf], np.nan).dropna()
    f = f.loc[f.index.intersection(data["parking_id"].unique())]

    d = data[data.parking_id.isin(f.index)].copy()

    cnt = d.groupby("parking_id").size()
    keep = [i for i in f.index if cnt.get(i, 0) >= min_obs]

    f = f.loc[keep]
    d = d[d.parking_id.isin(keep)]

    if len(keep) < 2:
        raise RuntimeError(f"LOO 가능한 주차장이 너무 적습니다: {len(keep)}곳")

    lvl = d.groupby("parking_id")["occ"].mean()
    d["sh"] = d["occ"] / d.parking_id.map(lvl)

    errs = []
    hat = {}

    for pid in keep:
        tr = [i for i in keep if i != pid]
        ftr = f.loc[tr]

        cols = [c for c in ftr.columns if ftr[c].std() > 0]

        mu = ftr[cols].mean()
        sg = ftr[cols].std()

        model = RidgeCV(alphas=np.logspace(-2, 3, 20))
        model.fit((ftr[cols] - mu) / sg, lvl.loc[tr])

        x_test = (f.loc[[pid], cols] - mu) / sg
        lh = float(model.predict(x_test)[0])
        hat[pid] = lh

        dtr = d[d.parking_id.isin(tr)]

        sh_ho = dtr.groupby(["hour", "op"])["sh"].mean()
        sh_h = dtr.groupby("hour")["sh"].mean()

        te = d[d.parking_id == pid]

        s1 = np.nan_to_num(
            te["hour"].map(sh_h).to_numpy(float),
            nan=1.0
        )

        idx = pd.MultiIndex.from_arrays([te["hour"], te["op"]])
        s2 = sh_ho.reindex(idx).to_numpy(float)
        s2 = np.where(np.isfinite(s2), s2, s1)

        pred = lh * s2
        errs.append(np.abs(te["occ"].values - pred))

    rho = stats.spearmanr(
        pd.Series(hat).loc[keep],
        lvl.loc[keep]
    ).statistic

    lo, hi = spearman_ci(rho, len(keep))
    mae = np.concatenate(errs).mean() * 100

    return mae, rho, lo, hi, len(keep), len(d)

# 9. 비교
say("# a08 · 공동주택 + 상권 피처 LOO")
say()
say(f"- 측정 상권 데이터: {len(shop)}곳")
say(f"- 측정 공동주택 피처: {len(apt)}곳")
say(f"- 상권 대분류: {len(large_cols)}개")
say("- 공동주택 반경: 300m / 500m")
say("- 상권·공동주택 count 계열은 log1p 변환")
say()

say("| 피처셋 | 피처 수 | 평가 주차장 | MAE(%p) | Spearman [95% CI] |")
say("|---|---:|---:|---:|---|")

SETS = [
    ("BASE 9", FEAT_BASE),
    ("BASE + 공동주택 세대수", FEAT_APT_HOUSEHOLDS),
    ("BASE + 단지수 + 세대수", FEAT_APT_COUNT_HOUSEHOLDS),
    ("BASE + 공동주택 전체", FEAT_APT_ALL),
    ("BASE + 상권 전체", FEAT_SHOP_ALL),
    ("BASE + 상권 + 공동주택", FEAT_SHOP_APT),
]

results = []

for name, feat in SETS:
    mae, rho, lo, hi, n, nobs = loo(feat, live)

    results.append({
        "feature_set": name,
        "n_features": feat.shape[1],
        "n_eval": n,
        "n_obs": nobs,
        "mae_pp": mae,
        "spearman": rho,
        "ci_lo": lo,
        "ci_hi": hi,
    })

    say(
        f"| {name} | {feat.shape[1]} | {n} | "
        f"**{mae:.2f}** | {rho:+.3f} [{lo:+.2f}, {hi:+.2f}] |"
    )

# 10. 성능 변화
say()
base = results[0]

for r in results[1:]:
    mae_diff = r["mae_pp"] - base["mae_pp"]
    rho_diff = r["spearman"] - base["spearman"]

    say(
        f"- **{r['feature_set']}**: "
        f"MAE 변화 {mae_diff:+.2f}%p · "
        f"Spearman 변화 {rho_diff:+.3f}"
    )

# 11. 저장
result_df = pd.DataFrame(results)

result_df.to_csv(
    TAB / "a08_apt_loo.csv",
    index=False,
    encoding="utf-8-sig"
)

(TAB / "a08_apt_loo.md").write_text(
    "\n".join(REPORT) + "\n",
    encoding="utf-8"
)

print()
print(f"→ {TAB / 'a08_apt_loo.md'}")
