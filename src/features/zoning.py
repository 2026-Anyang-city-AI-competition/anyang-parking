#!/usr/bin/env python3
"""
용도지역 + 공시지가 피처 (E군) — 급지 대체와 shape 설명을 동시에 노린다.

  python3 src/features/zoning.py            # 수집 + 검증
  python3 src/features/zoning.py --force    # 캐시 무시하고 재수집

레이어·필드는 src/utils/geo.py 상단 주석 참조(GetCapabilities + 실응답으로 확인).
면적은 EPSG:5186 으로 변환 후 계산한다.
"""
import argparse, json, sqlite3, sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import RidgeCV
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.utils import geo as G

RANDOM_STATE = 42
RADIUS = 500
DB    = ROOT / "data/raw/parking.db"
GEOC  = ROOT / "data/interim/geocode_cache.csv"
CACHE = ROOT / "data/interim/zoning_cache.csv"
OUT   = ROOT / "data/interim/zoning.csv"
FIG   = ROOT / "reports/figures"; TAB = ROOT / "reports/tables"
FIG.mkdir(parents=True, exist_ok=True); TAB.mkdir(parents=True, exist_ok=True)

for _f in ("AppleGothic", "NanumGothic", "Malgun Gothic"):
    if any(_f == f.name for f in matplotlib.font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = _f; break
plt.rcParams["axes.unicode_minus"] = False

REPORT = []
def say(s=""):
    print(s, flush=True); REPORT.append(s)

# 용도지역 대분류 — uname 문자열로 분류한다(코드 추측 금지)
def zone_class(uname):
    u = uname or ""
    if "상업" in u: return "commercial"
    if "주거" in u: return "residential"
    if "공업" in u: return "industrial"
    if "녹지" in u: return "green"
    return "other"

def collect(points, key, force=False):
    """points: {pid: (lat, lon)}. 반환 DataFrame. 캐시 재사용."""
    cache = pd.read_csv(CACHE) if (CACHE.exists() and not force) else pd.DataFrame()
    done = set(cache["pid"].astype(str)) if len(cache) else set()
    rows = cache.to_dict("records") if len(cache) else []
    todo = [(p, c) for p, c in points.items() if str(p) not in done]
    say(f"- 수집 대상 {len(points)}곳 / 캐시 {len(done)}곳 / 신규 **{len(todo)}곳**")
    for i, (pid, (lat, lon)) in enumerate(todo, 1):
        circ = G.circle_m(lat, lon, RADIUS)
        pt   = G.point_m(lat, lon)
        rec  = {"pid": pid, "lat": lat, "lon": lon}
        # 용도지역 — 면적 비율
        try:
            fs, tr = G.wfs(G.LAYER_ZONING, lat, lon, RADIUS, key)
            areas = Counter()
            for gm, pr in G.geoms_with_props(fs):
                a = gm.intersection(circ).area
                if a > 0: areas[zone_class(pr.get("uname"))] += a
            tot = sum(areas.values())
            for k in ("commercial", "residential", "industrial", "green", "other"):
                rec[f"zone_{k}_ratio"] = (areas[k] / tot) if tot else np.nan
            rec["zone_covered"] = tot / circ.area if circ.area else np.nan
            rec["zone_truncated"] = int(tr)
        except Exception as e:
            rec["zone_error"] = str(e)[:120]
        # 지적도 — 공시지가
        try:
            fs, tr = G.wfs(G.LAYER_CADASTRE, lat, lon, RADIUS, key)
            here, near = np.nan, []
            for gm, pr in G.geoms_with_props(fs):
                try: j = float(pr.get("jiga"))
                except (TypeError, ValueError): continue
                if j <= 0: continue
                near.append(j)
                if gm.contains(pt): here = j
            rec["land_price"] = here
            rec["land_price_500m_mean"] = float(np.mean(near)) if near else np.nan
            rec["land_price_n"] = len(near)
            rec["cad_truncated"] = int(tr)
        except Exception as e:
            rec["cad_error"] = str(e)[:120]
        rows.append(rec)
        if i % 20 == 0 or i == len(todo):
            pd.DataFrame(rows).to_csv(CACHE, index=False)
            say(f"  {i}/{len(todo)} 수집")
    df = pd.DataFrame(rows)
    df.to_csv(CACHE, index=False)
    c, r = df.get("zone_commercial_ratio"), df.get("zone_residential_ratio")
    df["daynight_axis"] = c / (c + r).replace(0, np.nan)
    return df

def main(force=False):
    if not GEOC.exists():
        sys.exit(f"[선행 조건 미충족] {GEOC} 없음. geocode.py 를 먼저 돌릴 것.")
    key = G.vworld_key()
    if not key: sys.exit("VWORLD_KEY 없음")

    con  = sqlite3.connect(DB)
    lots = pd.read_sql("SELECT * FROM lots", con)
    obs  = pd.read_sql("SELECT * FROM obs", con); con.close()

    pts = {f"auc:{r.parking_id}": (r.lat, r.lng)
           for r in lots.itertuples() if pd.notna(r.lat) and pd.notna(r.lng)}
    gc = pd.read_csv(GEOC)
    for r in gc.itertuples():
        if pd.notna(r.lat) and pd.notna(r.lon):
            pts[f"std:{r.parking_id}"] = (r.lat, r.lon)

    say("# 용도지역 · 공시지가 (E군)")
    say()
    say(f"- 반경 {RADIUS}m · EPSG:5186 면적 · 레이어 `{G.LAYER_ZONING}` / `{G.LAYER_CADASTRE}`")
    z = collect(pts, key, force)
    say()

    zz = z[z.pid.astype(str).str.startswith("auc:")].copy()
    zz["parking_id"] = zz.pid.str.split(":").str[1].astype(int)
    zz = zz.set_index("parking_id")
    z.to_csv(OUT, index=False)

    say(f"- 커버리지: 용도지역 원 대비 {z['zone_covered'].mean():.1%} · "
        f"공시지가 필지 중앙 {z['land_price_n'].median():.0f}개/500m")
    say(f"- `land_price` 결측 {int(z['land_price'].isna().sum())} / {len(z)}"
        f" (점이 도로·하천 필지 위면 비어 있다 → 500m 평균으로 대체 가능)")
    say()

    # ── 1-3. 급지를 대체할 수 있는가 ───────────────────────
    L = lots.set_index("parking_id")
    idx = zz.index.intersection(L.index)
    grade = pd.to_numeric(L.loc[idx, "grade"], errors="coerce")
    say("## 1-3. 급지 대체 가능성 (정답 = 도시공사 89곳 `grade`)")
    say()
    say("| 피처 | n | Spearman ρ | p |")
    say("|---|---:|---:|---:|")
    res = {}
    for c in ("land_price", "land_price_500m_mean", "zone_commercial_ratio", "daynight_axis"):
        v = pd.to_numeric(zz.loc[idx, c], errors="coerce")
        m = v.notna() & grade.notna()
        if m.sum() < 5: continue
        r_, p_ = stats.spearmanr(v[m], grade[m]); res[c] = r_
        say(f"| {c} | {int(m.sum())} | **{r_:+.3f}** | {p_:.4f} |")
    say()
    best = max(res, key=lambda k: abs(res[k])) if res else None
    if best:
        say(f"- ### 결론: 최강 `{best}` |ρ|={abs(res[best]):.3f} — "
            + ("**|ρ| ≥ 0.6 이므로 급지를 공시지가·용도지역으로 대체할 수 있다.** "
               "공단 7,456곳으로 타깃을 넓힐 길이 열린다."
               if abs(res[best]) >= 0.6 else
               "**|ρ| < 0.6 이라 급지를 대체하기엔 부족하다.** "
               "타깃 확장은 표준데이터 107곳에 묶인다."))
    say()

    # ── 1-4. shape 을 설명하는가 ───────────────────────────
    obs["ts"]  = pd.to_datetime(obs["ts_kst"], format="mixed")
    obs["occ"] = (obs["park_count"] / obs["cell_cnt"].replace(0, np.nan)).clip(0, 1.2)
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

    prof = live.pivot_table(index="parking_id", columns="hour", values="occ", aggfunc="mean")
    prof = prof.div(prof.mean(axis=1), axis=0)
    nh = prof.notna().sum(axis=1).max()
    prof = prof.dropna(thresh=max(3, int(round(min(18, nh*0.75)))))
    prof = prof.apply(lambda r: r.fillna(r.mean()), axis=1)

    night = (live[live.op == 0].groupby("parking_id")["occ"].mean() /
             live[live.op == 1].groupby("parking_id")["occ"].mean())
    night = night.replace([np.inf, -np.inf], np.nan)

    ids = prof.index.intersection(zz.index)
    dn = pd.to_numeric(zz.loc[ids, "daynight_axis"], errors="coerce")
    say("## 1-4. ★ shape 을 설명하는가")
    say()
    m = dn.notna() & night.reindex(ids).notna()
    if m.sum() >= 5:
        r_, p_ = stats.spearmanr(dn[m], night.reindex(ids)[m])
        say(f"- `daynight_axis` vs 운영외/운영중 점유율 비: "
            f"**Spearman ρ = {r_:+.3f}** (p={p_:.4f}, n={int(m.sum())})")
        say(f"  → 상업지일수록 야간 비율이 {'낮다' if r_ < 0 else '높다'}. "
            f"{'예상과 일치(상업=주간형).' if r_ < 0 else '예상과 반대다.'}")
    say()

    q = dn.dropna()
    if len(q) >= 8:
        hi = q[q >= q.quantile(.75)].index; lo = q[q <= q.quantile(.25)].index
        fig, ax = plt.subplots(figsize=(8, 4.5))
        for g, lb, col in ((hi, f"상업 우세 상위25% (n={len(hi)})", "crimson"),
                           (lo, f"주거 우세 하위25% (n={len(lo)})", "steelblue")):
            sub = prof.loc[prof.index.intersection(g)]
            if len(sub):
                ax.plot(prof.columns, sub.mean(), marker="o", ms=3, lw=2, color=col, label=lb)
                ax.fill_between(prof.columns, sub.mean()-sub.std(), sub.mean()+sub.std(),
                                color=col, alpha=.12)
        ax.axhline(1.0, color="gray", lw=.6, ls="--")
        ax.set_xlabel("시각(시)"); ax.set_ylabel("정규화 점유율 (자기평균=1)")
        ax.set_title("daynight_axis 상·하위 25% 의 시간 프로파일")
        ax.legend(); fig.tight_layout()
        fig.savefig(FIG / "zoning_daynight_profiles.png", dpi=130); plt.close(fig)
        gap = float(np.abs(prof.loc[prof.index.intersection(hi)].mean()
                           - prof.loc[prof.index.intersection(lo)].mean()).max())
        say(f"- 두 그룹 평균 프로파일 최대 격차 **{gap:.2f}** "
            f"({'뚜렷이 갈린다 → 발표자료용' if gap > 0.3 else '차이가 작다'})")
        say(f"- 그림: `zoning_daynight_profiles.png`")
    say()

    # ── LOO MAE 재측정 ────────────────────────────────────
    op_h = (L["wdays_end"].map(hhmm) - L["wdays_start"].map(hhmm))
    base = pd.DataFrame({
        "log_cells": np.log1p(L["cell_cnt"]),
        "grade": pd.to_numeric(L["grade"], errors="coerce"),
        "is_nosang": (L["div"] == "노상").astype(float),
        "is_underground": L["name"].str.contains("지하", na=False).astype(float),
        "is_transfer": L["name"].str.contains("환승", na=False).astype(float),
        "is_manan": (L["gu"] == "만안구").astype(float),
        "open_hours": op_h.where(op_h > 0, 24.0)})
    # a05 의 9피처 기준선을 그대로 재현한다 — compet_* 를 빼면 20.6%p 와 비교가 안 된다
    ka = pd.DataFrame([json.loads(l) for l in
                       open(ROOT/"data/raw/kotsa_v2_anyang.jsonl", encoding="utf-8")])
    for c, sc in (("la","prk_plce_entrc_la"),("lo","prk_plce_entrc_lo"),("cells","prk_cmprt_co")):
        ka[c] = pd.to_numeric(ka[sc], errors="coerce")
    ka = ka.dropna(subset=["la","lo","cells"]).drop_duplicates("prk_center_id")
    def compet(lat, lon, r_m=500):
        d = 6371000*2*np.arcsin(np.sqrt(
            np.sin(np.radians(ka.la-lat)/2)**2 +
            np.cos(np.radians(lat))*np.cos(np.radians(ka.la))*np.sin(np.radians(ka.lo-lon)/2)**2))
        m = d <= r_m
        return int(m.sum()), float(ka.loc[m,"cells"].sum())
    cn, cc = zip(*[compet(r.lat, r.lng) if pd.notna(r.lat) and pd.notna(r.lng) else (np.nan,np.nan)
                   for r in L.itertuples()])
    base["compet_n_500"]     = np.log1p(pd.Series(cn, index=L.index))
    base["compet_cells_500"] = np.log1p(pd.Series(cc, index=L.index))

    zone_cols  = ["zone_commercial_ratio","zone_residential_ratio","zone_green_ratio","daynight_axis"]
    price_cols = ["land_price_500m_mean"]
    E = zz[zone_cols + price_cols].apply(pd.to_numeric, errors="coerce")
    E["land_price_500m_mean"] = np.log1p(E["land_price_500m_mean"])

    def loo(feat):
        f = feat.replace([np.inf,-np.inf], np.nan).dropna()
        f = f.drop(columns=[c for c in f.columns if f[c].std() == 0])
        ii = prof.index.intersection(f.index)
        d = live[live.parking_id.isin(ii)]
        lvl = d.groupby("parking_id")["occ"].mean()
        d = d.assign(sh=d["occ"] / d.parking_id.map(lvl))
        Z = (f.loc[ii] - f.loc[ii].mean()) / f.loc[ii].std()
        errs = []
        for pid in ii:
            tr = [i for i in ii if i != pid]
            mdl = RidgeCV(alphas=np.logspace(-2,3,20)).fit(Z.loc[tr], lvl.loc[tr])
            lh = float(mdl.predict(Z.loc[[pid]])[0])
            dtr = d[d.parking_id.isin(tr)]
            sh_ho = dtr.groupby(["hour","op"])["sh"].mean()
            sh_h  = dtr.groupby("hour")["sh"].mean()
            te = d[d.parking_id == pid]
            s1 = np.nan_to_num(te["hour"].map(sh_h).to_numpy(float), nan=1.0)
            s2 = sh_ho.reindex(pd.MultiIndex.from_arrays([te["hour"], te["op"]])).to_numpy(float)
            s2 = np.where(np.isfinite(s2), s2, s1)
            errs.append(np.abs(te["occ"].values - lh * s2))
        return np.concatenate(errs).mean()*100, len(ii)

    say("## 1-4b. LOO MAE 재측정 — 최종 판정 숫자")
    say()
    say("| 피처셋 | n | MAE(%p) |")
    say("|---|---:|---:|")
    m0, n0 = loo(base)
    say(f"| 기본 9개 (a05 재현) | {n0} | **{m0:.1f}** |")
    mz, _ = loo(base.join(E[zone_cols],  how="inner"))
    say(f"| + 용도지역 4개만 | {n0} | **{mz:.1f}** |")
    mp, _ = loo(base.join(E[price_cols], how="inner"))
    say(f"| + 공시지가 1개만 | {n0} | **{mp:.1f}** |")
    m1, n1 = loo(base.join(E, how="inner"))
    say(f"| + E군 전체 5개 | {n1} | **{m1:.1f}** |")
    say()
    say(f"- ### 판정: {m0:.1f} → **{m1:.1f}%p** ({m1-m0:+.1f}%p). "
        + ("🟢 **E군이 오차를 줄인다.**" if m1 < m0 - 0.5 else
           "🔴 **E군을 넣어도 의미 있게 줄지 않는다.**"))
    say(f"- 기여 분해: 용도지역 {mz-m0:+.1f}%p · 공시지가 {mp-m0:+.1f}%p")
    say(f"- ⚠️ 1-4 에서 `daynight_axis` 의 shape 상관이 무의미했으므로, "
        f"개선이 나온다면 **shape 이 아니라 level 예측이 좋아진 것**일 가능성이 크다. "
        f"위 분해로 확인할 것.")
    say()
    say("표: `data/interim/zoning.csv`")
    (TAB / "zoning.md").write_text("\n".join(REPORT) + "\n", encoding="utf-8")
    print(f"\n→ {TAB/'zoning.md'}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    main(**vars(ap.parse_args()))
