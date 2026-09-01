#!/usr/bin/env python3
"""
a05 · shape 진단 + LOO MAE 판정

a04 의 ρ≈0 이 「shape 이 원래 다 비슷해서(세계 1)」인지
「lot 마다 다른데 피처가 못 잡아서(세계 2)」인지 split-half 신뢰도로 구분하고,
Leave-One-Location-Out 으로 실제 MAE 를 측정한다.

★ 판정 기준은 ρ 가 아니라 LOO MAE ≤ 15%p 이고 level Spearman ≥ 0.6.
  ρ 는 진단용으로만 쓴다.

  python3 src/analysis/a05_shape_check.py
"""
import json, sqlite3, sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import RidgeCV
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from statsmodels.stats.outliers_influence import variance_inflation_factor as vif_fn

RANDOM_STATE = 42
RHO_A04 = -0.089          # a04 게이트 결과 (진단용 참조값)
BETWEEN_A04 = 0.339       # a04 lot 간 평균 프로파일 상관

ROOT  = Path(__file__).resolve().parents[2]
DB    = ROOT / "data/raw/parking.db"
KOTSA = ROOT / "data/raw/kotsa_v2_anyang.jsonl"
FIG   = ROOT / "reports/figures"; TAB = ROOT / "reports/tables"
FIG.mkdir(parents=True, exist_ok=True); TAB.mkdir(parents=True, exist_ok=True)

for _f in ("AppleGothic", "NanumGothic", "Malgun Gothic"):
    if any(_f == f.name for f in matplotlib.font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = _f; break
plt.rcParams["axes.unicode_minus"] = False

REPORT = []
def say(s=""):
    print(s); REPORT.append(s)

# ── 0. 로드 ────────────────────────────────────────────────
con  = sqlite3.connect(DB)
obs  = pd.read_sql("SELECT * FROM obs", con)
lots = pd.read_sql("SELECT * FROM lots", con).set_index("parking_id")
con.close()
print("lots 컬럼:", list(lots.columns))     # 추측 금지 — 실제 컬럼 확인

obs["ts"]   = pd.to_datetime(obs["ts_kst"], format="mixed")
obs["occ"]  = (obs["park_count"] / obs["cell_cnt"].replace(0, np.nan)).clip(0, 1.2)
obs["hour"] = obs["ts"].dt.hour
obs["dow"]  = obs["ts"].dt.dayofweek
obs["date"] = obs["ts"].dt.date
obs = obs.dropna(subset=["occ"])

def hhmm(s):
    try:
        h, m = str(s).split(":"); return int(h) + int(m) / 60
    except Exception:
        return np.nan

st = lots["wdays_start"].map(hhmm)
en = lots["wdays_end"].map(hhmm)
obs["_h"] = obs["ts"].dt.hour + obs["ts"].dt.minute / 60
obs["is_operating"] = ((obs["_h"] >= obs.parking_id.map(st)) &
                       (obs["_h"] <  obs.parking_id.map(en))).astype(float)
obs["hours_since_open"] = (obs["_h"] - obs.parking_id.map(st)).where(
    obs["is_operating"] > 0, 0.0).clip(lower=0)
obs["hour_sin"]   = np.sin(2 * np.pi * obs["_h"] / 24)
obs["hour_cos"]   = np.cos(2 * np.pi * obs["_h"] / 24)
obs["is_weekend"] = obs["dow"].isin([5, 6]).astype(float)

span_h = (obs.ts.max() - obs.ts.min()).total_seconds() / 3600
DOWNM  = ["월","화","수","목","금","토","일"]

say("# a05 · shape 진단 + LOO MAE 판정")
say()
say("## 0. 데이터")
say()
say(f"- 기간 `{obs.ts.min()}` ~ `{obs.ts.max()}` ({span_h:.1f}시간), "
    f"요일 {', '.join(DOWNM[d] for d in sorted(obs.dow.unique()))}")
say(f"- 관측 {len(obs):,}행 / {obs.parking_id.nunique()}곳")
off = obs["is_operating"] == 0
say(f"- **운영시간 외 {off.mean():.1%}** ({int(off.sum()):,}행) · "
    f"평균 점유율 운영중 {obs.loc[~off,'occ'].mean():.3f} vs "
    f"운영외 **{obs.loc[off,'occ'].mean():.3f}**")
say(f"- 시간 피처: `is_operating` `hours_since_open` `hour_sin/cos` `dow` `is_weekend` "
    f"(주말 관측 0 → `is_weekend` 는 상수)")
say()

# ── 1. 제외 사유 ───────────────────────────────────────────
sd    = obs.groupby("parking_id")["park_count"].std()
flat  = set(sd[sd == 0].index)
nhour = obs.groupby("parking_id")["hour"].nunique()
thin  = set(nhour[nhour < 18].index)
live  = obs[~obs.parking_id.isin(flat)].copy()

say("## 1. 89곳 → 분석 대상 축소 사유")
say()
rows = []
for pid in lots.index:
    why = []
    if pid in flat: why.append("park_count 표준편차 0(34h 잠정)")
    if pid in thin: why.append(f"관측 시간대 {int(nhour.get(pid,0))}개 <18")
    if why: rows.append((pid, lots.loc[pid, "name"], lots.loc[pid, "div"],
                         int(lots.loc[pid, "cell_cnt"]), " · ".join(why)))
ex = pd.DataFrame(rows, columns=["parking_id","name","div","cell_cnt","reason"])
say(f"- 전체 {len(lots)}곳 → 제외 **{len(ex)}곳** → 대상 **{len(lots)-len(ex)}곳**")
say()
say("| id | 이름 | 유형 | 면수 | 사유 |")
say("|---|---|---|---:|---|")
for _, r in ex.iterrows():
    say(f"| {r.parking_id} | {r['name']} | {r['div']} | {r.cell_cnt} | {r.reason} |")
say()
say("> 변동 0 은 34시간 기준 **잠정**이다. 계측 불가로 단정하지 않는다.")
say()
ex.to_csv(TAB / "a05_excluded_lots.csv", index=False)

# ── 2. ★ split-half 신뢰도 ─────────────────────────────────
def prof_of(df):
    p = df.pivot_table(index="parking_id", columns="hour", values="occ", aggfunc="mean")
    return p.div(p.mean(axis=1), axis=0)

def half_corr(pa, pb, min_h=8):
    both = pa.index.intersection(pb.index)
    out = {}
    for pid in both:
        a, b = pa.loc[pid], pb.loc[pid]
        m = a.notna() & b.notna()
        if m.sum() >= min_h and a[m].std() > 1e-9 and b[m].std() > 1e-9:
            out[pid] = float(np.corrcoef(a[m], b[m])[0, 1])
    return pd.Series(out)

dates = sorted(live["date"].unique())
d1 = live[live["date"] == dates[0]]
d2 = live[live["date"] == dates[-1]]
sh = sorted(set(d1.hour.unique()) & set(d2.hour.unique()))
R_day = half_corr(prof_of(d1[d1.hour.isin(sh)]), prof_of(d2[d2.hour.isin(sh)]))

live = live.sort_values(["parking_id", "ts"])
live["_i"] = live.groupby("parking_id").cumcount()
R_oe = half_corr(prof_of(live[live._i % 2 == 0]), prof_of(live[live._i % 2 == 1]))

say("## 2. ★ split-half 신뢰도 — 세계 1 / 세계 2 구분")
say()
say("| 분할 | n | 중앙값 R | Q1 | Q3 | 뜻 |")
say("|---|---:|---:|---:|---:|---|")
say(f"| A. {dates[0]} vs {dates[-1]} (겹치는 {len(sh)}시간대) | {len(R_day)} | "
    f"**{R_day.median():.3f}** | {R_day.quantile(.25):.3f} | {R_day.quantile(.75):.3f} | 날짜 간 재현성 |")
say(f"| B. 5분 관측 홀/짝 | {len(R_oe)} | **{R_oe.median():.3f}** | "
    f"{R_oe.quantile(.25):.3f} | {R_oe.quantile(.75):.3f} | 측정 노이즈 바닥 |")
say(f"| (참고) a04 lot 간 평균 상관 | — | {BETWEEN_A04:.3f} | | | 남의 shape 과의 닮음 |")
say()
R = float(R_day.median())
world = ("**세계 1 (안전)** — 자기-자신 재현성이 lot 간 닮음과 비슷하다. "
         "shape 이 원래 서로 비슷하고 노이즈가 커서 ρ≈0 이 나온 것이지, "
         "피처가 특별히 무능한 게 아니다."
         if R < 0.5 else
         "**세계 2 (위기)** — 자기-자신은 잘 재현되는데(R 높음) 남과는 안 닮았다. "
         "즉 lot 마다 뚜렷이 다른 shape 이 실재하는데 현재 피처가 그걸 못 집는다.")
say(f"- 분할 A 중앙값 R = **{R:.3f}** vs lot 간 평균 {BETWEEN_A04:.3f}")
say(f"- 판정: {world}")
if R > 0:
    say(f"- 신뢰도 보정 ρ = {RHO_A04:.3f} / √{R:.3f} = **{RHO_A04/np.sqrt(R):+.3f}** "
        f"(감쇠 보정해도 {'기준 -0.3 미달' if RHO_A04/np.sqrt(R) > -0.3 else '기준 통과'})")
say()

fig, ax = plt.subplots(figsize=(7, 4))
ax.hist(R_day.values, bins=20, alpha=.75, label=f"A. 날짜 간 (중앙값 {R_day.median():.2f})")
ax.hist(R_oe.values, bins=20, alpha=.55, label=f"B. 홀/짝 (중앙값 {R_oe.median():.2f})")
ax.axvline(BETWEEN_A04, color="crimson", ls="--", lw=2, label=f"lot 간 평균 {BETWEEN_A04:.2f}")
ax.set_xlabel("자기-자신 프로파일 상관 R"); ax.set_ylabel("주차장 수")
ax.set_title("split-half 신뢰도"); ax.legend(fontsize=8); fig.tight_layout()
fig.savefig(FIG / "a05_split_half.png", dpi=130); plt.close(fig)

# ── 3. 피처 + 타깃 가용성 ──────────────────────────────────
ka = pd.DataFrame([json.loads(l) for l in open(KOTSA, encoding="utf-8")])
for c, s_ in (("la","prk_plce_entrc_la"), ("lo","prk_plce_entrc_lo"), ("cells","prk_cmprt_co")):
    ka[c] = pd.to_numeric(ka[s_], errors="coerce")
ka = ka.dropna(subset=["la","lo","cells"]).drop_duplicates("prk_center_id")

def compet(lat, lon, r_m=500):
    d = 6371000 * 2 * np.arcsin(np.sqrt(
        np.sin(np.radians(ka.la - lat)/2)**2 +
        np.cos(np.radians(lat))*np.cos(np.radians(ka.la))*np.sin(np.radians(ka.lo - lon)/2)**2))
    m = d <= r_m
    return int(m.sum()), float(ka.loc[m, "cells"].sum())

op_h = lots["wdays_end"].map(hhmm) - lots["wdays_start"].map(hhmm)
feat_all = pd.DataFrame({
    "log_cells":      np.log1p(lots["cell_cnt"]),
    "grade":          pd.to_numeric(lots["grade"], errors="coerce"),
    "is_nosang":      (lots["div"] == "노상").astype(float),
    "is_underground": lots["name"].str.contains("지하", na=False).astype(float),
    "is_transfer":    lots["name"].str.contains("환승", na=False).astype(float),
    "is_manan":       (lots["gu"] == "만안구").astype(float),
    "open_hours":     op_h.where(op_h > 0, 24.0),
    "oneday_amt":     pd.to_numeric(lots["oneday_amt"], errors="coerce"),
    "hourly_rate":    np.where(lots["dflt_tm"] > 0, lots["dflt_amt"]/lots["dflt_tm"]*60, 0.0),
    "hndcap_ratio":   lots["hndcap_cnt"] / lots["cell_cnt"].replace(0, np.nan),
    "is_wital":       (lots["div"] == "위탁").astype(float),
})
cn, cc = zip(*[compet(r.lat, r.lng) if pd.notna(r.lat) and pd.notna(r.lng) else (np.nan, np.nan)
               for r in lots.itertuples()])
feat_all["compet_n_500"]     = np.log1p(pd.Series(cn, index=lots.index))
feat_all["compet_cells_500"] = np.log1p(pd.Series(cc, index=lots.index))

AVAIL = {
    "log_cells":      ("O",  "표준데이터 주차면수"),
    "grade":          ("O",  "급지 — 지시에 따라 요금 대신 유지"),
    "is_nosang":      ("O",  "표준데이터 주차장유형(노상/노외)"),
    "is_underground": ("O",  "주차장명 문자열"),
    "is_transfer":    ("O",  "주차장명 문자열"),
    "is_manan":       ("O",  "주소"),
    "open_hours":     ("O",  "표준데이터 운영시간"),
    "compet_n_500":   ("△",  "좌표 필요 — 표준 60곳 결측, 지오코딩 후 가능"),
    "compet_cells_500":("△", "좌표 필요 — 지오코딩 후 가능"),
    "oneday_amt":     ("X",  "표준데이터 안양 107행 전부 공란"),
    "hourly_rate":    ("X",  "표준데이터 안양 107행 전부 공란"),
    "hndcap_ratio":   ("X",  "장애인전용주차구역 필드 107곳 전부 공란(정본 §4-②)"),
    "is_wital":       ("X",  "위탁 여부는 도시공사 운영형태 — 표준데이터에 없음"),
}
say("## 3. 피처 재선정 — 타깃 가용성 기준")
say()
say("| 피처 | 타깃에 값 | 근거 |")
say("|---|:---:|---|")
for k, (a, why) in AVAIL.items():
    say(f"| {k} | {a} | {why} |")
say()
KEEP = [k for k, (a, _) in AVAIL.items() if a in ("O", "△")]
say(f"- **최종 후보 {len(KEEP)}개**: {', '.join(KEEP)}")
say(f"- 제외: {', '.join(k for k,(a,_) in AVAIL.items() if a=='X')}")
say(f"- ⚠️ `hndcap_ratio` 는 지시에 없었지만 정본 §4-② 「107곳 전부 공란」에 걸려 제외했다. "
    f"a04 에서 ρ=-0.368 로 유의했던 피처라 되살리려면 표준데이터 재확인이 필요하다.")
say()

feat = feat_all[KEEP].replace([np.inf,-np.inf], np.nan).dropna()
feat = feat.drop(columns=[c for c in feat.columns if feat[c].std() == 0])

X = np.column_stack([np.ones(len(feat)), feat.values.astype(float)])
vifs = sorted([(c, vif_fn(X, i+1)) for i, c in enumerate(feat.columns)], key=lambda x: -x[1])
say("## 4. VIF 재계산 (요금 피처 제거 후)")
say()
say("| 피처 | VIF | |")
say("|---|---:|---|")
for c, v in vifs:
    say(f"| {c} | {v:.2f} | {'⚠️ 공선성' if v > 10 else ''} |")
say()
say(f"- 최대 VIF **{vifs[0][1]:.2f}** "
    f"({'여전히 공선성 있음' if vifs[0][1] > 10 else '**공선성 해소** — a04 의 grade 22.0 / oneday 13.6 / hourly 12.5 가 사라졌다'})")
say()
pd.DataFrame(vifs, columns=["feature","vif"]).to_csv(TAB / "a05_vif.csv", index=False)

# ── 4. ★ LOO MAE ───────────────────────────────────────────
ids = sorted(set(live.parking_id.unique()) & set(feat.index))
d = live[live.parking_id.isin(ids)].copy()
lvl_true = d.groupby("parking_id")["occ"].mean()
d["_shape_true"] = d["occ"] / d.parking_id.map(lvl_true)

Xf = feat.loc[ids]
Xz = (Xf - Xf.mean()) / Xf.std()
y  = lvl_true.loc[ids]

res = {k: [] for k in ("baseline","level","level_time","oracle",
                       "diag_shape_err","diag_level_err")}
lvl_hat = {}
alphas = np.logspace(-2, 3, 20)
for pid in ids:
    tr = [i for i in ids if i != pid]
    m  = RidgeCV(alphas=alphas).fit(Xz.loc[tr], y.loc[tr])
    lh = float(m.predict(Xz.loc[[pid]])[0]); lvl_hat[pid] = lh

    dtr = d[d.parking_id.isin(tr)]
    g_mean   = dtr["occ"].mean()
    shape_h  = dtr.groupby("hour")["_shape_true"].mean()
    shape_ho = dtr.groupby(["hour","is_operating"])["_shape_true"].mean()

    te = d[d.parking_id == pid]
    s1 = np.nan_to_num(te["hour"].map(shape_h).to_numpy(dtype=float), nan=1.0)
    key = pd.MultiIndex.from_arrays([te["hour"], te["is_operating"]])
    s2 = shape_ho.reindex(key).to_numpy(dtype=float)
    s2 = np.where(np.isfinite(s2), s2, s1)          # 미관측 (hour,운영여부) 조합은 시간만으로 대체
    a  = te["occ"].values
    res["baseline"].append(np.abs(a - g_mean))
    res["level"].append(np.abs(a - lh * s1))
    res["level_time"].append(np.abs(a - lh * s2))
    so = np.nan_to_num(te["hour"].map(
        te.groupby("hour")["_shape_true"].mean()).to_numpy(dtype=float), nan=1.0)
    res["oracle"].append(np.abs(a - lvl_true[pid] * so))
    # 진단: 오차가 level 에서 오나 shape 에서 오나
    res["diag_shape_err"].append(np.abs(a - lvl_true[pid] * s2))   # level 은 정답, shape 만 추정
    res["diag_level_err"].append(np.abs(a - lh * so))              # shape 은 정답, level 만 추정

lvl_hat = pd.Series(lvl_hat)
rows = []
for k, v in res.items():
    e = np.concatenate(v)
    rows.append((k, e.mean()*100, np.sqrt((e**2).mean())*100))
sp_rho, sp_p = stats.spearmanr(lvl_hat.loc[ids], y.loc[ids])

say("## 5. ★ LOO MAE — 진짜 판정")
say()
say(f"Leave-One-Location-Out, {len(ids)}곳 × (나머지 {len(ids)-1}곳 학습). 랜덤 분할 없음.")
say()
say("| 방식 | 설명 | MAE(%p) | RMSE(%p) |")
say("|---|---|---:|---:|")
desc = {"baseline":"전체 평균 점유율 상수", "level":"level̂(정적피처) × 글로벌 shape(hour)",
        "level_time":"level̂ × shape(hour, is_operating)", "oracle":"진짜 level × 진짜 shape (천장)",
        "diag_shape_err":"진짜 level × 추정 shape — **shape 오차만**",
        "diag_level_err":"추정 level × 진짜 shape — **level 오차만**"}
for k, mae, rmse in rows:
    if not k.startswith("diag"):
        say(f"| {k} | {desc[k]} | **{mae:.1f}** | {rmse:.1f} |")
say()
say("오차 분해 — 20%p 가 어디서 오는가:")
say()
say("| 무엇을 정답으로 주는가 | MAE(%p) |")
say("|---|---:|")
for k, mae, rmse in rows:
    if k.startswith("diag"):
        say(f"| {desc[k]} | **{mae:.1f}** |")
say()
mae_best = min(r[1] for r in rows if r[0] in ("level","level_time"))
say(f"- lot별 level Spearman ρ = **{sp_rho:+.3f}** (p={sp_p:.4f}, n={len(ids)})")
ok = (mae_best <= 15) and (sp_rho >= 0.6)
say(f"- ### 판정: {'🟢 통과' if ok else '🔴 미달'} "
    f"— MAE {mae_best:.1f}%p {'≤' if mae_best<=15 else '>'} 15%p, "
    f"Spearman {sp_rho:+.3f} {'≥' if sp_rho>=0.6 else '<'} 0.6")
say()
pd.DataFrame(rows, columns=["method","mae_pp","rmse_pp"]).to_csv(TAB/"a05_loo_mae.csv", index=False)
pd.DataFrame({"level_true": y.loc[ids], "level_hat": lvl_hat.loc[ids]}).to_csv(
    TAB/"a05_level_pred.csv")

fig, ax = plt.subplots(1, 2, figsize=(11, 4.5))
b = [r[1] for r in rows]
ax[0].bar([r[0] for r in rows], b, color=["gray","steelblue","seagreen","goldenrod"])
ax[0].axhline(15, color="crimson", ls="--", label="목표 15%p"); ax[0].legend()
for i, v in enumerate(b): ax[0].text(i, v+.3, f"{v:.1f}", ha="center", fontsize=9)
ax[0].set_ylabel("LOO MAE (%p)"); ax[0].set_title("방식별 LOO MAE")
ax[1].scatter(y.loc[ids], lvl_hat.loc[ids], s=22, alpha=.75)
lim = [min(y.min(), lvl_hat.min())-.03, max(y.max(), lvl_hat.max())+.03]
ax[1].plot(lim, lim, color="gray", lw=.8, ls="--")
ax[1].set_xlabel("실제 level"); ax[1].set_ylabel("예측 level")
ax[1].set_title(f"level 예측  Spearman ρ={sp_rho:+.3f}")
fig.tight_layout(); fig.savefig(FIG/"a05_loo_mae.png", dpi=130); plt.close(fig)

say("그림: `a05_split_half.png` · `a05_loo_mae.png`")
say(f"표: `a05_loo_mae.csv` · `a05_level_pred.csv` · `a05_vif.csv` · `a05_excluded_lots.csv`")
(TAB / "a05_shape_check.md").write_text("\n".join(REPORT) + "\n", encoding="utf-8")
print(f"\n→ {TAB/'a05_shape_check.md'}")
