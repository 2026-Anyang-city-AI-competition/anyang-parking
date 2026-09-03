#!/usr/bin/env python3
"""
67곳 시간 프로파일 클러스터링 — 곡선을 가르는 게 뭔지 눈으로 확인한다.

a05 에서 shape 오차가 17.4%p 로 최대 병목인데 설명 변수가 0개다.
어떤 피처를 만들지 데이터가 알려주게 하는 것이 목적. API 호출 없음.

  python3 src/features/cluster_shape.py
"""
import json, sqlite3, sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RANDOM_STATE = 42
ROOT = Path(__file__).resolve().parents[2]
DB   = ROOT / "data/raw/parking.db"
ZON  = ROOT / "data/interim/zoning.csv"          # zoning.py 산출물(있으면 사용)
FIG  = ROOT / "reports/figures"; TAB = ROOT / "reports/tables"
FIG.mkdir(parents=True, exist_ok=True); TAB.mkdir(parents=True, exist_ok=True)

for _f in ("AppleGothic", "NanumGothic", "Malgun Gothic"):
    if any(_f == f.name for f in matplotlib.font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = _f; break
plt.rcParams["axes.unicode_minus"] = False

REPORT = []
def say(s=""):
    print(s); REPORT.append(s)

# ── 프로파일 (a05 와 동일 정의) ─────────────────────────────
con  = sqlite3.connect(DB)
obs  = pd.read_sql("SELECT * FROM obs", con)
lots = pd.read_sql("SELECT * FROM lots", con).set_index("parking_id")
con.close()

obs["ts"]   = pd.to_datetime(obs["ts_kst"], format="mixed")
obs["occ"]  = (obs["park_count"] / obs["cell_cnt"].replace(0, np.nan)).clip(0, 1.2)
obs["hour"] = obs["ts"].dt.hour
obs = obs.dropna(subset=["occ"])

sd   = obs.groupby("parking_id")["park_count"].std()
flat = set(sd[sd == 0].index)
live = obs[~obs.parking_id.isin(flat)]

prof = live.pivot_table(index="parking_id", columns="hour", values="occ", aggfunc="mean")
prof = prof.div(prof.mean(axis=1), axis=0)
n_hours = prof.notna().sum(axis=1).max()
prof = prof.dropna(thresh=max(3, int(round(min(18, n_hours * 0.75)))))
prof = prof.apply(lambda r: r.fillna(r.mean()), axis=1)
X = prof.values

say("# 시간 프로파일 클러스터링")
say()
say(f"- 대상 **{len(prof)}곳** × {prof.shape[1]}시간 "
    f"(변동 0 인 {len(flat)}곳 제외, 34h 기준 잠정)")
say(f"- 프로파일 = lot 별 시간대 평균 점유율 ÷ 자기 평균 (level 제거, shape 만 남김)")
say()

# ── k 선택 ────────────────────────────────────────────────
say("## k 선택 (실루엣)")
say()
say("| k | 실루엣 | 클러스터 크기 |")
say("|---:|---:|---|")
best_k, best_s, fits = None, -2, {}
for k in (2, 3, 4):
    km = KMeans(n_clusters=k, n_init=20, random_state=RANDOM_STATE).fit(X)
    s  = silhouette_score(X, km.labels_)
    fits[k] = km
    sizes = np.bincount(km.labels_, minlength=k)
    say(f"| {k} | {s:.3f} | {' / '.join(str(int(v)) for v in sizes)} |")
    if s > best_s: best_k, best_s = k, s
say()
say(f"- **선택: k={best_k}** (실루엣 {best_s:.3f})")
say()
km = fits[best_k]
prof_lab = pd.Series(km.labels_, index=prof.index, name="cluster")

# ── 클러스터별 구성 ───────────────────────────────────────
info = lots.loc[prof.index].copy()
info["cluster"] = prof_lab
lvl = live.groupby("parking_id")["occ"].mean()
info["level"] = lvl.loc[info.index]

zon = None
if ZON.exists():
    z = pd.read_csv(ZON)
    if "parking_id" in z.columns and "daynight_axis" in z.columns:
        zon = z.set_index("parking_id")["daynight_axis"]
        info["daynight_axis"] = zon.reindex(info.index)

say("## 클러스터별 구성")
say()
hdr = "| 클러스터 | n | 노상 | 노외 | 위탁 | 급지 중앙 | 면수 중앙 | level 평균 |"
sep = "|---|---:|---:|---:|---:|---:|---:|---:|"
if zon is not None:
    hdr += " daynight_axis |"; sep += "---:|"
say(hdr); say(sep)
for c in range(best_k):
    g = info[info.cluster == c]
    row = (f"| C{c} | {len(g)} | {(g['div']=='노상').sum()} | {(g['div']=='노외').sum()} | "
           f"{(g['div']=='위탁').sum()} | {g['grade'].median():.0f} | "
           f"{g['cell_cnt'].median():.0f} | {g['level'].mean():.3f} |")
    if zon is not None:
        v = g.get("daynight_axis")
        row += f" {v.mean():.3f} |" if v is not None and v.notna().any() else " - |"
    say(row)
say()
for c in range(best_k):
    g = info[info.cluster == c]
    say(f"- **C{c}** ({len(g)}곳): " + ", ".join(g["name"].head(5).tolist()))
say()

# ── 곡선이 실제로 갈리는가 ────────────────────────────────
say("## 클러스터가 실제로 다른가")
say()
cen = pd.DataFrame(km.cluster_centers_, columns=prof.columns)
peak = cen.idxmax(axis=1); trough = cen.idxmin(axis=1)
say("| 클러스터 | 피크 시각 | 저점 시각 | 최대/최소 비 |")
say("|---|---:|---:|---:|")
for c in range(best_k):
    say(f"| C{c} | {peak[c]}시 | {trough[c]}시 | {cen.loc[c].max()/max(cen.loc[c].min(),1e-6):.1f}배 |")
say()
sep_amt = float(np.abs(cen.values[:, None, :] - cen.values[None, :, :]).max())
say(f"- 클러스터 중심 간 최대 시간대 격차 **{sep_amt:.2f}** (정규화 점유율 단위)")
say()

info.reset_index()[["parking_id","name","div","grade","cell_cnt","level","cluster"]] \
    .to_csv(TAB / "shape_clusters.csv", index=False)

# ── 그림 ──────────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(8, 4.5))
for c in range(best_k):
    g = prof[prof_lab == c]
    ax.plot(prof.columns, g.mean(), marker="o", ms=3, lw=2, label=f"C{c} (n={len(g)})")
    ax.fill_between(prof.columns, g.mean()-g.std(), g.mean()+g.std(), alpha=.12)
ax.axhline(1.0, color="gray", lw=.6, ls="--")
ax.set_xlabel("시각(시)"); ax.set_ylabel("정규화 점유율 (자기평균=1)")
ax.set_title(f"시간 프로파일 클러스터 (k={best_k}, 실루엣 {best_s:.3f})")
ax.legend(); fig.tight_layout()
fig.savefig(FIG / "shape_clusters.png", dpi=130); plt.close(fig)

fig, ax = plt.subplots(figsize=(8, 4.5))
order = prof_lab.sort_values().index
im = ax.imshow(prof.loc[order].values, aspect="auto", cmap="magma",
               extent=[prof.columns.min(), prof.columns.max(), len(prof), 0])
for b in np.cumsum(np.bincount(km.labels_, minlength=best_k))[:-1]:
    ax.axhline(b, color="cyan", lw=1)
ax.set_xlabel("시각(시)"); ax.set_ylabel("주차장 (클러스터순)")
ax.set_title("lot × 시간 프로파일 히트맵")
fig.colorbar(im, ax=ax, label="정규화 점유율"); fig.tight_layout()
fig.savefig(FIG / "shape_heatmap.png", dpi=130); plt.close(fig)

say("그림: `shape_clusters.png` · `shape_heatmap.png` · 표: `shape_clusters.csv`")
(TAB / "shape_clusters.md").write_text("\n".join(REPORT) + "\n", encoding="utf-8")
print(f"\n→ {TAB/'shape_clusters.md'}")
