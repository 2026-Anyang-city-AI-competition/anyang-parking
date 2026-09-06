#!/usr/bin/env python3
"""
예측기 — 추천 파이프라인에 꽂는 단일 진입점.

  Predictor().predict(parking_id, target_time, horizon_min)
    -> {"p10","p50","p90","full_prob","is_live","source"}

★ 설계 (a17 실측 근거)
  - 타깃은 **Δ**: y = occ(t+h) − occ(t) · 복원 `clip(occ + Δ̂, 0, 120)`
  - 목적함수는 **L1**. 기본 L2 를 쓰면 15분 MAE 가 1.72 → 3.32 로 2배 나빠진다
    (Δ 의 63.3% 가 정확히 0 인 뾰족한 분포라 L2 는 조건부 평균을 좇는다)
  - **하이브리드 라우팅 없음.** a17 에서 Δ+L1 이 평일 15·30분에서도 persistence 를
    이겼다(−2.8% · −10.1%). 지시서의 `horizon<=30 → persistence` 경계는 사라졌다.
    주말은 아직 못 이기지만 그건 train 에 주말이 없어서다 — 9/12~13 후 재측정.
  - `full_prob` 는 persistence 가 못 낸다. **분위 모델만 낼 수 있다.**

★ 예측 시점은 「차가 주차장에 도착하는 시각」이다. 도보 시간을 더하지 않는다.

  python3 src/serve/predictor.py     # 학습 + 캘리브레이션 + 자체 점검
"""
import pickle, sqlite3, sys, warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.features.temporal import oprtime_features, OPR_COLS
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
MODEL_PATH = ROOT / "data/processed/predictor.pkl"
KST = timezone(timedelta(hours=9))
FULL_THRESHOLD = 90.0        # 점유율 %p. 이 이상이면 '만차'
QUANTILES = (0.1, 0.5, 0.9)
HORIZ_GRID = (15, 30, 60, 120)


def _lazy():
    from src.analysis.a16_stratified_weekend import load, series, feats, PID, INTX
    return load, series, feats, PID, INTX


class Predictor:
    """학습된 분위 모델 + isotonic 보정을 들고 예측한다."""

    def __init__(self, path=MODEL_PATH):
        self.ok = False
        try:
            with open(path, "rb") as f:
                b = pickle.load(f)
            self.models, self.iso, self.feat_cols = b["models"], b["iso"], b["feat_cols"]
            self.dead, self.hist = set(b["dead"]), b["hist"]
            self.meta = b.get("meta", {})
            self.ok = True
        except Exception as e:
            print(f"[predictor] 모델 없음 ({str(e)[:60]}) — predict 는 None 을 돌려준다")

    def is_live(self, parking_id):
        return parking_id not in self.dead

    def _row(self, parking_id, target_time, horizon_min):
        """target_time 에서 horizon 만큼 **거슬러 올라간** 시점의 피처를 만든다.
        즉 「지금 관측으로 target_time 을 맞힌다」."""
        h = self.hist.get(parking_id)
        if h is None or not len(h): return None, None
        base_t = pd.Timestamp(target_time).tz_localize(None) - pd.Timedelta(minutes=horizon_min)
        i = h.ts_kst.searchsorted(base_t, side="right") - 1
        if i < 0: return None, None
        r = h.iloc[i]
        if pd.isna(r.get("occ_now")): return None, None
        return r, float(r["occ_now"])

    def predict(self, parking_id, target_time, horizon_min):
        out = {"p10": None, "p50": None, "p90": None, "full_prob": None,
               "is_live": self.is_live(parking_id), "source": None}
        if not self.ok or not out["is_live"]:
            out["source"] = "dead_feed" if not out["is_live"] else "no_model"
            return out
        H = min(HORIZ_GRID, key=lambda x: abs(x - horizon_min))
        r, occ = self._row(parking_id, target_time, H)
        if r is None:
            out["source"] = "no_history"; return out
        row = r[[c for c in self.feat_cols if not c.startswith("opr_")]].to_dict()
        # ★ 운영시간 피처는 **도착 시각(target_time)** 기준이다. 관측 시각이 아니다.
        lot = self.meta.get(parking_id, {})
        for k, v in oprtime_features(lot, pd.Timestamp(target_time).to_pydatetime()).items():
            row["opr_" + k] = v
        X = pd.DataFrame([row])[self.feat_cols]
        for c in ("parking_id_cat", "pid_we"):
            if c in X: X[c] = X[c].astype("category")
        q = {}
        for a in QUANTILES:
            d = float(self.models[(H, a)].predict(X)[0])
            q[a] = float(np.clip(occ + d, 0, 120))          # ★ 복원 시 클리핑 필수
        p10, p50, p90 = sorted((q[0.1], q[0.5], q[0.9]))    # 분위 교차 방지
        # 만차확률 — 분위수 사이 선형 보간
        raw = self._interp_prob(p10, p50, p90, FULL_THRESHOLD)
        iso = self.iso.get(H)
        out.update({"p10": round(p10, 1), "p50": round(p50, 1), "p90": round(p90, 1),
                    "full_prob": round(float(iso.predict([raw])[0]) if iso is not None else raw, 3),
                    "source": f"quantile_delta_l1_h{H}"})
        return out

    @staticmethod
    def _interp_prob(p10, p50, p90, thr):
        """P(occ >= thr) 를 세 분위수로 근사. CDF 는 (p10,0.1)(p50,0.5)(p90,0.9)."""
        xs, ys = [p10, p50, p90], [0.1, 0.5, 0.9]
        if thr <= xs[0]: return float(min(1.0, 1 - 0.1 * (thr / max(xs[0], 1e-9))))
        if thr >= xs[2]: return 0.05
        cdf = float(np.interp(thr, xs, ys))
        return float(np.clip(1 - cdf, 0.0, 1.0))


# ── 학습 ──────────────────────────────────────────────────
def train(save=True):
    from lightgbm import LGBMRegressor
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import roc_auc_score, precision_score
    load, series, feats, PID, INTX = _lazy()
    o, L, dead = load()
    d = feats(series(o), L)
    ut = d.ts_kst.dropna().sort_values().unique()
    cut = pd.Timestamp(ut[int(len(ut) * 0.8)])
    meta = {r["parking_id"]: r for r in L.to_dict("records")}
    OC = ["opr_" + c for c in OPR_COLS]
    _c = {}
    def add_opr(sub, H):
        """★ 도착 시각(ts + H분) 기준으로 붙인다. a17 에서 주말 MAE −33% 를 준 피처다."""
        rows = []
        for pid, tt in zip(sub.parking_id.values, sub.ts_kst.values):
            tgt = pd.Timestamp(tt) + pd.Timedelta(minutes=H)
            key = (pid, tgt.weekday(), tgt.hour * 60 + tgt.minute)
            v = _c.get(key)
            if v is None:
                v = oprtime_features(meta.get(pid, {}), tgt.to_pydatetime()); _c[key] = v
            rows.append(v)
        f = pd.DataFrame(rows, index=sub.index)
        return pd.concat([sub, f.rename(columns={c: "opr_" + c for c in f.columns})], axis=1)
    F = INTX + OC
    models, iso, rep = {}, {}, []
    for H in HORIZ_GRID:
        k = H // 5
        t = d.copy()
        t["nx"] = t.groupby("parking_id")["occ"].shift(-k)
        t["y"] = t.nx - t.occ
        t = t.dropna(subset=["nx"] + [c for c in INTX if c not in ("parking_id_cat", "pid_we")])
        tr, te = t[t.ts_kst < cut], t[t.ts_kst >= cut]
        te = te[~te.parking_id.isin(dead)]
        if len(tr) < 500 or len(te) < 100: continue
        tr, te = add_opr(tr, H), add_opr(te, H)
        for a in QUANTILES:
            m = LGBMRegressor(objective="quantile", alpha=a, n_estimators=400,
                              learning_rate=0.05, num_leaves=63, min_child_samples=40,
                              random_state=42, n_jobs=-1, verbose=-1).fit(tr[F], tr.y)
            models[(H, a)] = m
        base = te.occ_now.values
        qs = {a: np.clip(base + models[(H, a)].predict(te[F]), 0, 120) for a in QUANTILES}
        P = np.sort(np.vstack([qs[0.1], qs[0.5], qs[0.9]]), axis=0)
        raw = np.array([Predictor._interp_prob(P[0, i], P[1, i], P[2, i], FULL_THRESHOLD)
                        for i in range(P.shape[1])])
        yb = (te.nx.values >= FULL_THRESHOLD).astype(int)
        ir = IsotonicRegression(out_of_bounds="clip", y_min=0, y_max=1).fit(raw, yb)
        iso[H] = ir
        cal = ir.predict(raw)
        wk = te.is_weekend.values
        def sc(mask):
            if mask.sum() < 30 or len(set(yb[mask])) < 2: return (np.nan, np.nan)
            pr = (cal[mask] >= 0.5).astype(int)
            return (roc_auc_score(yb[mask], cal[mask]),
                    precision_score(yb[mask], pr, zero_division=0))
        a_all, p_all = sc(np.ones(len(yb), bool))
        a_wd, p_wd = sc(wk == 0)
        a_we, p_we = sc(wk == 1)
        rep.append({"h": H, "n": len(te), "만차비율": yb.mean(),
                    "AUC": a_all, "정밀도": p_all,
                    "AUC_평일": a_wd, "AUC_주말": a_we,
                    "정밀도_평일": p_wd, "정밀도_주말": p_we,
                    "raw": raw, "cal": cal, "y": yb})
    # 최신 이력 (예측 시 피처 조회용)
    hist = {pid: g.sort_values("ts_kst").tail(600).reset_index(drop=True)
            for pid, g in d.groupby("parking_id")}
    if save:
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(MODEL_PATH, "wb") as f:
            pickle.dump({"models": models, "iso": iso, "feat_cols": F, "meta": meta,
                         "dead": list(dead), "hist": hist}, f)
    return rep, cut, dead


if __name__ == "__main__":
    rep, cut, dead = train()
    print(f"학습 완료 · cut {cut} · 죽은 피드 {len(dead)}곳 제외 · → {MODEL_PATH.name}")
    print(f"\n{'h':>4}{'n':>8}{'만차비율':>9}{'AUC':>7}{'정밀도':>7}"
          f"{'AUC평일':>8}{'AUC주말':>8}")
    for r in rep:
        f = lambda v: "  -  " if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.3f}"
        print(f"{r['h']:>4}{r['n']:>8,}{r['만차비율']:>9.3f}{f(r['AUC']):>7}"
              f"{f(r['정밀도']):>7}{f(r['AUC_평일']):>8}{f(r['AUC_주말']):>8}")

    # calibration plot
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for _f in ("AppleGothic", "NanumGothic"):
        if any(_f == x.name for x in matplotlib.font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = _f; break
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(1, len(rep), figsize=(4*len(rep), 3.6), squeeze=False)
    for i, r in enumerate(rep):
        a = ax[0][i]
        for arr, lb, c in ((r["raw"], "보정 전", "tab:orange"), (r["cal"], "보정 후", "tab:blue")):
            bins = np.linspace(0, 1, 11)
            idx = np.digitize(arr, bins) - 1
            xs, ys = [], []
            for b in range(10):
                m = idx == b
                if m.sum() >= 20: xs.append(arr[m].mean()); ys.append(r["y"][m].mean())
            a.plot(xs, ys, "o-", color=c, label=lb, ms=4)
        a.plot([0, 1], [0, 1], "--", color="gray", lw=.8)
        a.set_title(f"h={r['h']}분  AUC {r['AUC']:.3f}")
        a.set_xlabel("예측 만차확률"); a.set_ylabel("실제 만차 비율" if i == 0 else "")
        a.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(ROOT/"reports/figures/calibration.png", dpi=130)
    print("\n→ reports/figures/calibration.png")

    print("\n=== 자체 점검 ===")
    P = Predictor()
    tgt = datetime(2026, 9, 7, 14, 0, tzinfo=KST)
    for pid in (16, 10, 46):
        r = P.predict(pid, tgt, 60)
        print(f"  id {pid:>4} live={str(r['is_live']):<5} p50={r['p50']} "
              f"[{r['p10']}, {r['p90']}] full_prob={r['full_prob']} · {r['source']}")
