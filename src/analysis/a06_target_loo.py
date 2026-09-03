#!/usr/bin/env python3
"""
a06 · 타깃 조건에 맞춘 LOO 재측정.

89곳 전체 LOO(20.4%p)는 낙관 편향이다. 타깃(표준데이터 무료 노외 36곳)은
노상 0% · 무료 97% · 급지 전부 '기타' · 면수 중앙 24 로 학습 집단과 분포가 분리돼 있다.

학습 집단을 타깃 조건에 맞춰 좁혀가며 진짜 성능을 잰다.
기준선(피처셋)은 a05 의 9개로 고정한다 — 기준선을 바꿔가며 비교하지 않는다.

  python3 src/analysis/a06_target_loo.py
"""
import json, sqlite3, sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import RidgeCV

RANDOM_STATE = 42
ROOT = Path(__file__).resolve().parents[2]
DB    = ROOT / "data/raw/parking.db"
KOTSA = ROOT / "data/raw/kotsa_v2_anyang.jsonl"
STD   = ROOT / "data/raw/std_parking.csv"
TAB   = ROOT / "reports/tables"; TAB.mkdir(parents=True, exist_ok=True)

REPORT = []
def say(s=""):
    print(s, flush=True); REPORT.append(s)

con  = sqlite3.connect(DB)
obs  = pd.read_sql("SELECT * FROM obs", con)
L    = pd.read_sql("SELECT * FROM lots", con).set_index("parking_id"); con.close()

obs["ts"]   = pd.to_datetime(obs["ts_kst"], format="mixed")
obs["occ"]  = (obs["park_count"] / obs["cell_cnt"].replace(0, np.nan)).clip(0, 1.2)
obs["hour"] = obs["ts"].dt.hour
obs = obs.dropna(subset=["occ"])
sd = obs.groupby("parking_id")["park_count"].std()
live = obs[~obs.parking_id.isin(set(sd[sd == 0].index))].copy()

def hhmm(s):
    try: h, m = str(s).split(":"); return int(h)+int(m)/60
    except Exception: return np.nan
st, en = L["wdays_start"].map(hhmm), L["wdays_end"].map(hhmm)
live["_h"] = live["ts"].dt.hour + live["ts"].dt.minute/60
live["op"] = ((live["_h"] >= live.parking_id.map(st)) &
              (live["_h"] <  live.parking_id.map(en))).astype(int)

# ── 피처: a05 의 9개로 고정 ────────────────────────────────
ka = pd.read_json(KOTSA, lines=True).drop_duplicates("prk_center_id")
for c, sc in (("la","prk_plce_entrc_la"),("lo","prk_plce_entrc_lo"),("cells","prk_cmprt_co")):
    ka[c] = pd.to_numeric(ka[sc], errors="coerce")
ka = ka.dropna(subset=["la","lo","cells"])
def compet(lat, lon, r=500):
    d = 6371000*2*np.arcsin(np.sqrt(
        np.sin(np.radians(ka.la-lat)/2)**2 +
        np.cos(np.radians(lat))*np.cos(np.radians(ka.la))*np.sin(np.radians(ka.lo-lon)/2)**2))
    m = d <= r
    return int(m.sum()), float(ka.loc[m,"cells"].sum())
cn, cc = zip(*[compet(r.lat, r.lng) if pd.notna(r.lat) and pd.notna(r.lng) else (np.nan, np.nan)
               for r in L.itertuples()])
op_h = L["wdays_end"].map(hhmm) - L["wdays_start"].map(hhmm)
FEAT = pd.DataFrame({
    "log_cells": np.log1p(L["cell_cnt"]),
    "grade": pd.to_numeric(L["grade"], errors="coerce"),
    "is_nosang": (L["div"] == "노상").astype(float),
    "is_underground": L["name"].str.contains("지하", na=False).astype(float),
    "is_transfer": L["name"].str.contains("환승", na=False).astype(float),
    "is_manan": (L["gu"] == "만안구").astype(float),
    "open_hours": op_h.where(op_h > 0, 24.0),
    "compet_n_500": np.log1p(pd.Series(cn, index=L.index)),
    "compet_cells_500": np.log1p(pd.Series(cc, index=L.index)),
}).replace([np.inf,-np.inf], np.nan).dropna()

def loo(pool, data):
    """pool: lot id 목록. data: 관측 부분집합. 반환 (MAE%p, Spearman, n, obs수)"""
    f = FEAT.loc[FEAT.index.intersection(pool)]
    f = f.drop(columns=[c for c in f.columns if f[c].std() == 0])
    d = data[data.parking_id.isin(f.index)]
    cnt = d.groupby("parking_id").size()
    ids = [i for i in f.index if cnt.get(i, 0) >= 10]      # 관측 10개 미만은 제외
    if len(ids) < 6: return (np.nan,)*2 + (len(ids), len(d))
    f = f.loc[ids]; d = d[d.parking_id.isin(ids)]
    lvl = d.groupby("parking_id")["occ"].mean()
    d = d.assign(sh=d["occ"] / d.parking_id.map(lvl))
    Z = (f - f.mean()) / f.std()
    errs, hat = [], {}
    for pid in ids:
        tr = [i for i in ids if i != pid]
        m = RidgeCV(alphas=np.logspace(-2,3,20)).fit(Z.loc[tr], lvl.loc[tr])
        lh = float(m.predict(Z.loc[[pid]])[0]); hat[pid] = lh
        dtr = d[d.parking_id.isin(tr)]
        sh_ho = dtr.groupby(["hour","op"])["sh"].mean()
        sh_h  = dtr.groupby("hour")["sh"].mean()
        te = d[d.parking_id == pid]
        s1 = np.nan_to_num(te["hour"].map(sh_h).to_numpy(float), nan=1.0)
        s2 = sh_ho.reindex(pd.MultiIndex.from_arrays([te["hour"], te["op"]])).to_numpy(float)
        s2 = np.where(np.isfinite(s2), s2, s1)
        errs.append(np.abs(te["occ"].values - lh*s2))
    rho = stats.spearmanr(pd.Series(hat).loc[ids], lvl.loc[ids]).statistic
    return np.concatenate(errs).mean()*100, rho, len(ids), len(d)

allid  = list(FEAT.index)
noweoi = [i for i in allid if L.loc[i,"div"] == "노외"]
small  = [i for i in noweoi if L.loc[i,"cell_cnt"] <= 60]

say("# a06 · 타깃 조건 LOO 재측정")
say()
if STD.exists():
    s = pd.read_csv(STD, dtype=str, encoding="utf-8-sig")
    s["c"] = pd.to_numeric(s["주차구획수"], errors="coerce")
    fr = s[s["요금정보"].astype(str).str.contains("무료", na=False)]
    say(f"- **타깃 분포**: 무료 {len(fr)}곳 · 유형 {fr['주차장유형'].value_counts().to_dict()} · "
        f"면수 중앙 {fr.c.median():.0f} · 급지 {fr['급지구분'].value_counts().to_dict()}")
say(f"- 학습 후보 {len(allid)}곳 (변동 0 제외) · 노외 {len(noweoi)}곳 · "
    f"노외&≤60면 {len(small)}곳")
say(f"- 피처는 a05 의 9개로 **고정**. 학습 집단만 바꾼다")
say()
say("| 학습 집단 | 곳 | 관측 | MAE(%p) | Spearman |")
say("|---|---:|---:|---:|---:|")
rows = [("89곳 전체 (a05 재현)", allid, live),
        ("**노외 49곳만**", noweoi, live),
        ("**노외 + 60면 이하**", small, live),
        ("**노외 + 운영시간 외 구간만** ★", noweoi, live[live.op == 0])]
res = {}
for lb, pool, data in rows:
    mae, rho, n, nobs = loo(pool, data)
    res[lb] = (mae, rho, n)
    say(f"| {lb} | {n} | {nobs:,} | "
        + (f"**{mae:.1f}** | {rho:+.3f} |" if np.isfinite(mae) else "- | - |"))
say()
base = res["89곳 전체 (a05 재현)"][0]
nw   = res["**노외 49곳만**"][0]
free = res["**노외 + 운영시간 외 구간만** ★"][0]
say(f"- 89곳 전체 {base:.1f}%p → 노외만 **{nw:.1f}%p** ({nw-base:+.1f}%p). "
    + ("**낙관 편향이 확인됐다.**" if nw > base + 0.5 else
       "노외만 봐도 크게 나빠지지 않는다." if nw < base + 0.5 else ""))
say(f"- ★ 무료 조건(운영시간 외)만: **{free:.1f}%p** — "
    f"타깃이 무료이므로 이 숫자가 가장 현실에 가깝다")
say(f"- 목표 15%p 대비: " + " · ".join(
    f"{k.strip('*') } {v[0]:.1f}" for k, v in res.items() if np.isfinite(v[0])))
say()
pd.DataFrame([{"pool":k,"mae_pp":v[0],"spearman":v[1],"n_lots":v[2]}
              for k,v in res.items()]).to_csv(TAB/"a06_target_loo.csv", index=False)
(TAB/"a06_target_loo.md").write_text("\n".join(REPORT)+"\n", encoding="utf-8")
print(f"\n→ {TAB/'a06_target_loo.md'}")
