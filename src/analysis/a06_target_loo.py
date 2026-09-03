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

def spearman_ci(rho, n, alpha=0.05):
    """Fisher z 로 95% CI. n<4 면 계산 불가."""
    if not np.isfinite(rho) or n < 4: return (np.nan, np.nan)
    z  = np.arctanh(np.clip(rho, -0.999999, 0.999999))
    se = 1.0 / np.sqrt(n - 3)
    k  = stats.norm.ppf(1 - alpha/2)
    return float(np.tanh(z - k*se)), float(np.tanh(z + k*se))

def loo(train_pool, eval_pool, data, min_obs=10):
    """학습 집단과 평가 집단을 분리한다.
    각 평가 lot 마다 (학습집단 - 그 lot) 으로 학습하고 그 lot 만 예측한다.
    반환 (MAE%p, rho, lo, hi, n_eval, n_train, n_obs)"""
    f = FEAT.loc[FEAT.index.intersection(list(set(train_pool) | set(eval_pool)))]
    d = data[data.parking_id.isin(f.index)]
    cnt = d.groupby("parking_id").size()
    keep = [i for i in f.index if cnt.get(i, 0) >= min_obs]
    f = f.loc[keep]; d = d[d.parking_id.isin(keep)]
    tr_all = [i for i in keep if i in set(train_pool)]
    ev     = [i for i in keep if i in set(eval_pool)]
    if len(ev) < 4 or len(tr_all) < 6:
        return (np.nan,)*4 + (len(ev), len(tr_all), len(d))
    lvl = d.groupby("parking_id")["occ"].mean()
    d = d.assign(sh=d["occ"] / d.parking_id.map(lvl))
    errs, hat = [], {}
    for pid in ev:
        tr = [i for i in tr_all if i != pid]
        ftr = f.loc[tr]
        cols = [c for c in ftr.columns if ftr[c].std() > 0]      # 학습 집단 기준으로 판단
        mu, sg = ftr[cols].mean(), ftr[cols].std()
        m = RidgeCV(alphas=np.logspace(-2,3,20)).fit((ftr[cols]-mu)/sg, lvl.loc[tr])
        lh = float(m.predict(((f.loc[[pid], cols]-mu)/sg))[0]); hat[pid] = lh
        dtr = d[d.parking_id.isin(tr)]
        sh_ho = dtr.groupby(["hour","op"])["sh"].mean()
        sh_h  = dtr.groupby("hour")["sh"].mean()
        te = d[d.parking_id == pid]
        s1 = np.nan_to_num(te["hour"].map(sh_h).to_numpy(float), nan=1.0)
        s2 = sh_ho.reindex(pd.MultiIndex.from_arrays([te["hour"], te["op"]])).to_numpy(float)
        s2 = np.where(np.isfinite(s2), s2, s1)
        errs.append(np.abs(te["occ"].values - lh*s2))
    rho = stats.spearmanr(pd.Series(hat).loc[ev], lvl.loc[ev]).statistic
    lo, hi = spearman_ci(rho, len(ev))
    return (np.concatenate(errs).mean()*100, rho, lo, hi,
            len(ev), len(tr_all), len(d))

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
say("| 학습 / 평가 | 학습 | 평가 | MAE(%p) | Spearman [95% CI] |")
say("|---|---:|---:|---:|---|")
rows = [("67 / 67 (a05 재현)",            allid,  allid,  live),
        ("45 / 45 (학습까지 좁힘 · 과했던 설계)", noweoi, noweoi, live),
        ("**67 / 노외 45** ★",             allid,  noweoi, live),
        ("**67 / 노외 60면 이하 19**",       allid,  small,  live),
        ("**67 / 노외 45 · 운영시간 외만**",   allid,  noweoi, live[live.op == 0])]
res = {}
for lb, tp, ep, data in rows:
    mae, rho, lo, hi, ne, nt, nobs = loo(tp, ep, data)
    res[lb] = (mae, rho, lo, hi, ne)
    ci = f"{rho:+.3f} [{lo:+.2f}, {hi:+.2f}]" if np.isfinite(rho) else "-"
    say(f"| {lb} | {nt} | {ne} | " + (f"**{mae:.1f}** | {ci} |" if np.isfinite(mae) else "- | - |"))
say()
say("> 모든 상관에 n 과 Fisher z 95% CI 를 병기한다. CI 가 0 을 포함하면 판정 불가다.")
say()
a = res["67 / 67 (a05 재현)"]; b = res["45 / 45 (학습까지 좁힘 · 과했던 설계)"]
c = res["**67 / 노외 45** ★"]; e = res["**67 / 노외 60면 이하 19**"]
say(f"- **설계 수정 효과**: 노외 45곳 평가에서 학습을 45→67 로 되돌리면 "
    f"MAE {b[0]:.1f} → **{c[0]:.1f}%p**, Spearman {b[1]:+.3f} → **{c[1]:+.3f}**")
say(f"  `is_nosang` 이 학습 집단에 살아 있어 level 예측이 회복된다")
say(f"- **정정**: 60면 이하 19곳의 ρ={e[1]:+.3f} 는 CI [{e[2]:+.2f}, {e[3]:+.2f}] 로 "
    f"{'0 을 포함한다 → **판정 불가**(표본 부족). 부호를 해석하면 안 된다' if e[2]*e[3] < 0 else '0 을 배제한다'}")
say(f"- 타깃 조건 최선 추정: **MAE {c[0]:.1f}%p · Spearman {c[1]:+.3f} "
    f"[{c[2]:+.2f}, {c[3]:+.2f}] (n={c[4]})**")
say(f"  행정용 합격선 0.6 은 CI 상한 {c[3]:+.2f} 기준 "
    + ("**여전히 도달 가능 범위**" if c[3] >= 0.6 else "**범위 밖**"))
say()
pd.DataFrame([{"pool":k,"mae_pp":v[0],"spearman":v[1],"ci_lo":v[2],"ci_hi":v[3],"n_eval":v[4]}
              for k,v in res.items()]).to_csv(TAB/"a06_target_loo.csv", index=False)
(TAB/"a06_target_loo.md").write_text("\n".join(REPORT)+"\n", encoding="utf-8")
print(f"\n→ {TAB/'a06_target_loo.md'}")
