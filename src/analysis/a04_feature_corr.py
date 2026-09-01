#!/usr/bin/env python3
"""
피처 사전 검증 게이트 — 「계측 구간으로 학습해 미계측 구간을 추정한다」는
프로젝트 전제가 데이터로 성립하는지 판정한다.

핵심 지표: 피처거리 ↔ 프로파일유사도 Spearman ρ
           ρ ≤ -0.3 통과 / 0 근처면 설계 재검토

  python3 src/analysis/a04_feature_corr.py
"""
import json, sqlite3, sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from statsmodels.stats.outliers_influence import variance_inflation_factor as vif_fn

RANDOM_STATE = 42
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

obs["ts"]   = pd.to_datetime(obs["ts_kst"], format="mixed")
obs["occ"]  = obs["park_count"] / obs["cell_cnt"].replace(0, np.nan)
obs["occ"]  = obs["occ"].clip(0, 1.2)        # 면수 초과 관측이 실재한다
obs["hour"] = obs["ts"].dt.hour
obs["dow"]  = obs["ts"].dt.dayofweek

span_h = (obs.ts.max() - obs.ts.min()).total_seconds() / 3600
dows   = sorted(obs.dow.unique())
DOWNM  = ["월","화","수","목","금","토","일"]
has_weekend = bool({5, 6} & set(dows))

say("# a04 · 피처 사전 검증 게이트")
say()
say("## 1. 커버리지")
say()
say(f"- 기간: `{obs.ts.min()}` ~ `{obs.ts.max()}`  (**{span_h:.1f}시간**)")
say(f"- 요일: {', '.join(DOWNM[d] for d in dows)}  → **주말 데이터 {'있음' if has_weekend else '없음'}**")
say(f"- 주차장 {obs.parking_id.nunique()}곳 / 관측 {len(obs):,}행 "
    f"/ lot당 중앙값 {obs.groupby('parking_id').size().median():.0f}")
say()
say("> ⚠️ **데이터 한계 — 아래 모든 수치에 적용된다.**")
say(f"> - 평일만이고 주말이 없다. 무료 주차장은 주말 거동이 달라 shape 이 불완전하다. 첫 주말은 9/5~6.")
say(f"> - {span_h:.0f}시간은 일주기가 {span_h/24:.1f}회뿐이라 within 분산이 과소추정되고 **ICC 가 과대추정**된다.")
say("> - 경기데이터드림 상권 데이터가 아직 없다. 피처는 도시공사 89곳 + 공단 시설정보(경쟁)로만 구성했다.")
say("> - 따라서 ρ 가 0 근처여도 즉시 설계 폐기가 아니다. §6 원인 분해를 볼 것.")
say()

# ── 1. 변동 없는 lot (표시만, 제외 판정 아님) ───────────────
sd   = obs.groupby("parking_id")["park_count"].std()
flat = sd[sd == 0].index.tolist()
say("## 2. 변동 없는 lot")
say()
say(f"- 전 기간 `park_count` 표준편차 0: **{len(flat)}곳** {flat[:10]}")
say(f"- ⚠️ {span_h:.0f}시간 기준 **잠정**. 계측 불가로 단정하지 말 것(정본 §12: 12시간 미만 판정 금지, 24시간 권장)")
say()
live = obs[~obs.parking_id.isin(flat)].copy()

# ── 2. ICC ─────────────────────────────────────────────────
g    = live.groupby("parking_id")["occ"]
mu   = live["occ"].mean()
n_i  = g.size()
k    = n_i.mean()
ms_b = (n_i * (g.mean() - mu) ** 2).sum() / (len(n_i) - 1)
ms_w = ((live["occ"] - live.groupby("parking_id")["occ"].transform("mean")) ** 2).sum() \
       / (len(live) - len(n_i))
icc  = (ms_b - ms_w) / (ms_b + (k - 1) * ms_w)
var_b, var_w = g.mean().var(), g.var().mean()
verdict_icc = ("level 지배적 → 실측 앵커링 전략 정당" if icc > 0.5 else
               "shape 비중 큼 → 앵커 없이도 전이 가능할 수 있음" if icc < 0.3 else "중간")
say("## 3. ICC — level 이 지배적인가")
say()
say(f"- **ICC(1) = {icc:.3f}** (ANOVA)")
say(f"- 분산비 {var_b/(var_b+var_w):.3f}  (between={var_b:.4f}, within={var_w:.4f})")
say(f"- → {verdict_icc}")
say(f"- ⚠️ {span_h:.0f}시간이라 within 과소추정 → **ICC 과대추정**. 7일치로 재실행 필요")
say()

# ── 3. shape 프로파일 ──────────────────────────────────────
prof = live.pivot_table(index="parking_id", columns="hour", values="occ", aggfunc="mean")
prof = prof.div(prof.mean(axis=1), axis=0)
n_hours = prof.notna().sum(axis=1).max()
thr = max(3, int(round(min(18, n_hours * 0.75))))
prof = prof.dropna(thresh=thr)
prof = prof.apply(lambda r: r.fillna(r.mean()), axis=1)

say(f"- shape 프로파일: {prof.shape[0]}곳 × {prof.shape[1]}시간 "
    f"(lot당 최대 관측 시간대 {int(n_hours)}개, 임계 {thr})")
say()

# ── 4. 피처 행렬 ───────────────────────────────────────────
def hhmm(s):
    try:
        h, m = str(s).split(":"); return int(h) + int(m) / 60
    except Exception:
        return np.nan

ka = pd.DataFrame([json.loads(l) for l in open(KOTSA, encoding="utf-8")])
ka["la"]    = pd.to_numeric(ka["prk_plce_entrc_la"], errors="coerce")
ka["lo"]    = pd.to_numeric(ka["prk_plce_entrc_lo"], errors="coerce")
ka["cells"] = pd.to_numeric(ka["prk_cmprt_co"], errors="coerce")
ka = ka.dropna(subset=["la", "lo", "cells"]).drop_duplicates("prk_center_id")

def compet(lat, lon, r_m=500):
    d = 6371000 * 2 * np.arcsin(np.sqrt(
        np.sin(np.radians(ka.la - lat) / 2) ** 2 +
        np.cos(np.radians(lat)) * np.cos(np.radians(ka.la)) *
        np.sin(np.radians(ka.lo - lon) / 2) ** 2))
    m = d <= r_m
    return int(m.sum()), float(ka.loc[m, "cells"].sum())

L = lots.copy()
op_h = (L["wdays_end"].map(hhmm) - L["wdays_start"].map(hhmm))
feat = pd.DataFrame({
    "log_cells":     np.log1p(L["cell_cnt"]),
    "grade":         pd.to_numeric(L["grade"], errors="coerce"),
    "is_free":       (L["free_yn"] == "Y").astype(float),
    "hourly_rate":   np.where(L["dflt_tm"] > 0, L["dflt_amt"] / L["dflt_tm"] * 60, 0.0),
    "oneday_amt":    pd.to_numeric(L["oneday_amt"], errors="coerce"),
    "open_hours":    op_h.where(op_h > 0, 24.0),
    "is_nosang":     (L["div"] == "노상").astype(float),
    "is_wital":      (L["div"] == "위탁").astype(float),
    "is_underground": L["name"].str.contains("지하", na=False).astype(float),
    "is_transfer":   L["name"].str.contains("환승", na=False).astype(float),
    "is_manan":      (L["gu"] == "만안구").astype(float),
    "hndcap_ratio":  L["hndcap_cnt"] / L["cell_cnt"].replace(0, np.nan),
})
cn, cc = zip(*[compet(r.lat, r.lng) if pd.notna(r.lat) and pd.notna(r.lng) else (np.nan, np.nan)
               for r in L.itertuples()])
feat["compet_n_500"]     = np.log1p(pd.Series(cn, index=L.index))
feat["compet_cells_500"] = np.log1p(pd.Series(cc, index=L.index))
feat = feat.replace([np.inf, -np.inf], np.nan).dropna()

const = [c for c in feat.columns if feat[c].std() == 0]
if const:
    say(f"> 분산 0 이라 제외한 피처: {const}"); say()
    feat = feat.drop(columns=const)

# ── 5. ★ 게이트 — Mantel ───────────────────────────────────
common = prof.index.intersection(feat.index)
if len(common) < 5:
    say("## 4. ★ 게이트 — 계산 불가")
    say()
    say(f"- 프로파일 확보 lot {len(prof)}곳 / 피처 확보 {len(feat)}곳 / 교집합 **{len(common)}곳**")
    say(f"- 관측된 시간대가 {int(n_hours)}개뿐이라(임계 {thr}) 프로파일을 만들 수 없다. 데이터를 더 모을 것.")
    (TAB/"a04_gate.md").write_text("\n".join(REPORT)+"\n", encoding="utf-8")
    sys.exit(f"게이트 계산 불가: 교집합 {len(common)}곳")
# 게이트 표본(common) 안에서 상수인 피처는 표준화 시 0 나누기가 된다 → 먼저 제거
sub = feat.loc[common]
dead = [c for c in sub.columns if sub[c].std() == 0]
if dead:
    say(f"> 게이트 표본에서 분산 0 이라 제외: {dead}"); say()
    feat = feat.drop(columns=dead)
P = prof.loc[common].values
F = feat.loc[common].values.astype(float)
# 프로파일이 완전히 평평한 lot 은 상관이 정의되지 않는다 → 제외
keep = np.nanstd(P, axis=1) > 1e-12
n_flatprof = int((~keep).sum())
common, P, F = common[keep], P[keep], F[keep]
F = (F - F.mean(0)) / F.std(0)

D_feat = np.sqrt(((F[:, None, :] - F[None, :, :]) ** 2).sum(-1))
S_prof = np.corrcoef(P)
iu = np.triu_indices(len(common), 1)
d_all, s_all = D_feat[iu], S_prof[iu]
msk = np.isfinite(d_all) & np.isfinite(s_all)
d_all, s_all = d_all[msk], s_all[msk]
rho = stats.spearmanr(d_all, s_all).statistic

rng, perm = np.random.default_rng(RANDOM_STATE), []
for _ in range(999):
    q = rng.permutation(len(common))
    perm.append(stats.spearmanr(D_feat[np.ix_(q, q)][iu][msk], s_all).statistic)
perm = np.array(perm)
pval = (np.sum(perm <= rho) + 1) / 1000

gate = "🟢 통과 (전이 가능)" if rho <= -0.3 else ("🔴 전제 흔들림" if rho > -0.1 else "🟡 애매")
say("## 4. ★ 게이트 — 피처거리 vs 프로파일유사도")
say()
say(f"- n = **{len(common)}곳** / {msk.sum():,}쌍 (프로파일 평평해 제외 {n_flatprof}곳)")
say(f"- **Spearman ρ = {rho:+.3f}**,  Mantel p = {pval:.3f} (999 순열, seed={RANDOM_STATE})")
say(f"- 순열분포: 평균 {perm.mean():+.3f}, 표준편차 {perm.std():.3f}")
say(f"- ### 판정: {gate}  (기준 ρ ≤ -0.3)")
say()

# ── 6. between-lot 상관 ────────────────────────────────────
lvl = g.mean().rename("level")
idx = feat.index.intersection(lvl.index)
sub = feat.loc[idx]
drop2 = [c for c in sub.columns if sub[c].std() == 0]
if drop2:
    say(f"> 유효 lot 범위에서 분산 0 이라 제외: {drop2}"); say()
    feat = feat.drop(columns=drop2)
rows = []
for c in feat.columns:
    r_, p_ = stats.spearmanr(feat.loc[idx, c], lvl.loc[idx])
    rows.append((c, r_, p_))
btw = pd.DataFrame(rows, columns=["feature", "rho", "p"]).sort_values(
    "rho", key=lambda s: s.abs(), ascending=False)
say("## 5. between-lot 상관 (level 예측용, lot당 1값이라 p값 유효)")
say()
say("| 피처 | ρ | p | |")
say("|---|---:|---:|---|")
for _, r in btw.iterrows():
    say(f"| {r.feature} | {r.rho:+.3f} | {r.p:.3f} | {'★' if abs(r.rho) > 0.3 else ''} |")
say()
btw.to_csv(TAB / "a04_between_lot_corr.csv", index=False)

# ── 7. VIF ─────────────────────────────────────────────────
X = feat.loc[idx].values.astype(float)
X = np.column_stack([np.ones(len(X)), X])
vifs = [(c, vif_fn(X, i + 1)) for i, c in enumerate(feat.columns)]
say("## 6. VIF")
say()
bad = [(c, v) for c, v in vifs if v > 10]
say("| 피처 | VIF | |")
say("|---|---:|---|")
for c, v in sorted(vifs, key=lambda x: -x[1]):
    say(f"| {c} | {v:.2f} | {'⚠️ 공선성' if v > 10 else ''} |")
say()
pd.DataFrame(vifs, columns=["feature", "vif"]).to_csv(TAB / "a04_vif.csv", index=False)

# ── 8. 그림 ────────────────────────────────────────────────
def prof_by(mask, labels, title, fname):
    fig, ax = plt.subplots(figsize=(7, 4))
    for mk, lb in zip(mask, labels):
        ids = [i for i in prof.index if i in mk]
        if not ids: continue
        ax.plot(prof.columns, prof.loc[ids].mean(), marker="o", ms=3, label=f"{lb} (n={len(ids)})")
    ax.axhline(1.0, color="gray", lw=.6, ls="--")
    ax.set_xlabel("시각(시)"); ax.set_ylabel("정규화 점유율 (자기평균=1)")
    ax.set_title(title); ax.legend(); fig.tight_layout()
    fig.savefig(FIG / fname, dpi=130); plt.close(fig)

free_ids = set(lots.index[lots["free_yn"] == "Y"]); paid_ids = set(lots.index[lots["free_yn"] == "N"])
prof_by([free_ids, paid_ids], ["무료", "유료"], "무료 vs 유료 시간 프로파일", "a04_profile_free_vs_paid.png")
prof_by([set(lots.index[lots["div"] == "노상"]), set(lots.index[lots["div"] == "노외"]),
         set(lots.index[lots["div"] == "위탁"])], ["노상", "노외", "위탁"],
        "유형별 시간 프로파일", "a04_profile_by_div.png")

fig, ax = plt.subplots(figsize=(6, 5))
ax.scatter(d_all, s_all, s=4, alpha=.15, edgecolors="none")
z = np.polyfit(d_all, s_all, 1)
xs = np.linspace(d_all.min(), d_all.max(), 50)
ax.plot(xs, np.polyval(z, xs), color="crimson", lw=2)
ax.set_xlabel("피처 유클리드 거리"); ax.set_ylabel("시간 프로파일 상관")
ax.set_title(f"전이 가능성  ρ={rho:+.3f}  (Mantel p={pval:.3f})")
fig.tight_layout(); fig.savefig(FIG / "a04_transfer_gate.png", dpi=130); plt.close(fig)

fig, ax = plt.subplots(figsize=(7, 4))
ax.hist(lvl.values, bins=25, color="steelblue", edgecolor="white")
ax.set_xlabel("lot 평균 점유율 (level)"); ax.set_ylabel("주차장 수")
ax.set_title(f"lot별 level 분포 (n={len(lvl)})")
fig.tight_layout(); fig.savefig(FIG / "a04_level_dist.png", dpi=130); plt.close(fig)

# ── 8-b. 운영시간 외(=무료) 구간 커버리지 ──────────────────
_st = lots["wdays_start"].map(hhmm); _en = lots["wdays_end"].map(hhmm)
live["_h"]  = live["ts"].dt.hour + live["ts"].dt.minute / 60
live["_op"] = (live["_h"] >= live.parking_id.map(_st)) & (live["_h"] < live.parking_id.map(_en))
off_ratio = float((~live["_op"]).mean())
off_lots  = int(live.groupby("parking_id")["_op"].apply(lambda t: (~t).any()).sum())
occ_op    = float(live.loc[live["_op"], "occ"].mean())
occ_off   = float(live.loc[~live["_op"], "occ"].mean())
say("## 7. 운영시간 외(=무료) 구간 — `free_now` 학습 재료")
say()
say(f"- 운영시간 외 관측 **{off_ratio:.1%}** ({int((~live['_op']).sum()):,}행) / "
    f"해당 구간이 있는 lot **{off_lots}곳**")
say(f"- 평균 점유율: 운영중 {occ_op:.3f} vs 운영외 **{occ_off:.3f}** "
    f"({'운영시간 외가 더 붐빈다 → 야간 거주자 주차' if occ_off > occ_op else '운영시간 외가 한산하다'})")
say(f"- → lot 단위 `is_free` 는 89곳 전부 유료라 쓸 수 없지만, "
    f"**시간 단위 `free_now` 는 지금 데이터로 학습 가능하다.**")
say()

# ── 9. 원인 분해 ───────────────────────────────────────────
say("## 8. 원인 분해")
say()
if rho <= -0.3:
    say(f"ρ={rho:+.3f} 로 기준을 통과했다. 주말·상권 데이터가 들어오면 더 좋아질 여지만 있다.")
else:
    fr = prof.index.intersection(list(free_ids)); pa = prof.index.intersection(list(paid_ids))
    sep = np.nan
    if len(fr) > 1 and len(pa) > 1:
        sep = float(np.abs(prof.loc[fr].mean() - prof.loc[pa].mean()).mean())
    within_free = np.nan
    if len(fr) > 2:
        C = np.corrcoef(prof.loc[fr].values); within_free = float(C[np.triu_indices(len(fr), 1)].mean())
    C_all = s_all
    say(f"ρ={rho:+.3f} 로 기준(-0.3)에 못 미친다. 원인 후보 3개의 무게를 근거와 함께 나눈다.")
    say()
    if len(free_ids) == 0:
        say(f"- **(a) 무료 lot 0곳 / 주말 부재** — 도시공사 89곳은 전부 유료라 "
            f"lot 단위 무료 표본이 없다. 다만 운영시간 외 구간이 {off_ratio:.0%}·{off_lots}곳 "
            f"확보돼 **`free_now` 로 무료 상태를 배울 재료는 이미 있다**(§7). "
            f"남은 결손은 주말({', '.join(DOWNM[d] for d in dows)}만 관측)뿐이다. "
            f"→ **(a)는 더 이상 주된 원인이 아니다.**")
    else:
        say(f"- **(a) 주말 부재** — 관측 요일 {', '.join(DOWNM[d] for d in dows)}. "
            f"주말이 0이라 무료 주차장 shape 의 절반이 비어 있다. "
            f"무료↔유료 프로파일 평균 격차 {sep:.3f} "
            f"{'(작다 → 평일만으로는 두 집단이 구분되지 않는다)' if sep < .1 else '(크다 → 평일만으로도 구분은 된다)'}")
    say(f"- **(b) 상권 피처 부재** — 현재 피처 {len(feat.columns)}개 전부 "
        f"공급·요금·경쟁 축이고, 수요 축(업종구성·인구·역거리)이 0개다. "
        f"정본 §7 C군이 통째로 빠져 있어 shape 을 설명할 변수가 원리적으로 부족하다.")
    say(f"- **(c) 진짜 전이 불가** — 프로파일 상관 자체의 평균이 {C_all.mean():+.3f} "
        + (f"(무료 그룹 내부끼리는 {within_free:+.3f}). " if len(fr) > 2 else "(무료 표본이 없어 그룹 내 비교 불가). ")
        + ("lot 들의 시간 패턴이 서로 비슷하지도 않다 → 피처 이전에 shape 자체가 노이즈에 가깝다. "
           "관측 기간이 짧아서일 가능성이 가장 크다."
           if C_all.mean() < .2 else
           "lot 끼리는 패턴이 닮았는데 피처가 그걸 못 집는다 → (b) 쪽 무게가 크다."))
    say()
    dominant = ("(c) 관측 기간 부족" if C_all.mean() < .2 else "(b) 상권 피처 부재")
    say(f"**무게가 실리는 쪽: {dominant}.** "
        f"어느 쪽이든 9/5~6 주말이 들어오고 상권 데이터를 붙인 뒤 재실행하기 전까지는 "
        f"설계를 폐기할 근거가 되지 못한다. 정본 §8 판정일을 그때로 미루는 것이 맞다.")
say()
say(); say(f"그림: `a04_transfer_gate.png` · `a04_profile_free_vs_paid.png` · "
    f"`a04_profile_by_div.png` · `a04_level_dist.png`")

(TAB / "a04_gate.md").write_text("\n".join(REPORT) + "\n", encoding="utf-8")
print(f"\n→ {TAB/'a04_gate.md'}")
