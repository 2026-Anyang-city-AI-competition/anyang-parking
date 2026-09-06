#!/usr/bin/env python3
"""
a17 · T2.5 — 타깃을 레벨에서 증분(Δ)으로 바꾼다.

  y = occ(t+h) − occ(t)      복원: pred = clip(occ(t) + delta_hat, 0, 120)

★ delta_hat = 0 이 정확히 persistence 다. 구조적으로 persistence 보다 나빠질 수 없다.
  그래도 진다면 복원식이나 클리핑 버그다.

근거: 레벨은 평일 36.1±27.5 → 주말 49.7±38.5 로 분포가 크게 이동하지만
      Δ 는 Δ15 평일 −0.037/7.52 vs 주말 −0.311/4.93 로 이동이 한 자릿수 배 작다.

  python3 src/analysis/a17_delta_target.py
"""
import sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.analysis.a16_stratified_weekend import (
    load, series, feats, BASE, PID, INTX, HORIZONS, TAB)
from lightgbm import LGBMRegressor

def model(objective="l1"):
    """★ 목적함수는 L1 이다. 기본값 L2 를 쓰면 MAE 평가에서 크게 진다.
    Δ 는 15분 기준 **63.3% 가 정확히 0** 인 뾰족한 분포라 L2 는 조건부 평균을 따라가며
    잡음을 키운다(15분 MAE 3.32). L1 은 조건부 중앙값(≈0)에 붙어 1.72 로 내려간다."""
    return LGBMRegressor(objective=objective, n_estimators=400, learning_rate=0.05,
                         num_leaves=63, min_child_samples=40, subsample=0.9,
                         colsample_bytree=0.9, random_state=42, n_jobs=-1, verbose=-1)

CLIP_LO, CLIP_HI = 0.0, 120.0
SPLITS = [("A_80", 0.20), ("B_70", 0.30)]
DN = ["월","화","수","목","금","토","일"]

R = []
def say(s=""): print(s, flush=True); R.append(s)

def main():
    o, L, dead = load()
    d = feats(series(o), L)
    ut = d.ts_kst.dropna().sort_values().unique()

    say("# a17 · Δ 타깃 (T2.5)")
    say()
    say("`y = occ(t+h) − occ(t)` · 복원 `clip(occ(t) + Δ̂, 0, 120)`")
    say(f"- 죽은 피드 {len(dead)}곳 제외 평가 · 대상 {o.parking_id.nunique()-len(dead)}곳")
    say()

    rows = []
    for sname, ratio in SPLITS:
        cut = pd.Timestamp(ut[int(len(ut)*(1-ratio))])
        for H in HORIZONS:
            k = H // 5
            t = d.copy()
            t["occ_next"] = t.groupby("parking_id")["occ"].shift(-k)
            t["y_lvl"] = t["occ_next"]
            t["y_dlt"] = t["occ_next"] - t["occ"]
            t = t.dropna(subset=["occ_next"] + BASE)
            tr, te = t[t.ts_kst < cut], t[t.ts_kst >= cut]
            if len(tr) < 500 or len(te) < 100: continue
            te = te.assign(live=~te.parking_id.isin(dead))
            truth = te.occ_next.values
            base = te.occ_now.values

            preds = {"Persistence": base}
            for tag, F in (("pid", PID), ("pid_intx", INTX)):
                # 레벨 타깃
                m1 = model().fit(tr[F], tr.y_lvl)
                preds[f"LVL_{tag}"] = np.clip(m1.predict(te[F]), CLIP_LO, CLIP_HI)
                # Δ 타깃 — 복원 시 반드시 클리핑
                m2 = model().fit(tr[F], tr.y_dlt)
                preds[f"DLT_{tag}"] = np.clip(base + m2.predict(te[F]), CLIP_LO, CLIP_HI)

            for nm, p in preds.items():
                e = np.abs(truth - p)
                def mae(mask):
                    v = e[np.asarray(mask)]
                    return float(np.mean(v)) if len(v) else np.nan
                rows.append({"split": sname, "horizon": H, "model": nm,
                             "살아67": mae(te.live),
                             "평일": mae(te.live & te.is_weekend.eq(0)),
                             "주말": mae(te.live & te.is_weekend.eq(1)),
                             "운영중": mae(te.live & te.is_operating.eq(1)),
                             "운영외": mae(te.live & te.is_operating.eq(0))})
    df = pd.DataFrame(rows)
    df.to_csv(TAB/"a17_delta_target.csv", index=False)

    for sname, _ in SPLITS:
        sub = df[df["split"] == sname]
        if not len(sub): continue
        say(f"## 분할 {sname} — 살아있는 67곳 MAE (%p)")
        say()
        say("| h | 모델 | 전체 | 평일 | 주말 | 운영중 | 운영외 |")
        say("|---:|---|---:|---:|---:|---:|---:|")
        f = lambda v: "-" if pd.isna(v) else f"{v:.2f}"
        for _, r in sub.iterrows():
            say(f"| {r.horizon} | {r.model} | {f(r['살아67'])} | {f(r['평일'])} | "
                f"{f(r['주말'])} | {f(r['운영중'])} | {f(r['운영외'])} |")
        say()

    say("## ★ 레벨 타깃 vs Δ 타깃 — persistence 대비 (살아있는 67곳)")
    say()
    for sname, _ in SPLITS:
        sub = df[df["split"] == sname]
        if not len(sub): continue
        say(f"### {sname}")
        say()
        say("| h | 구간 | Persist | LVL_pid | Δ_pid | Δ_pid_intx | LVL 이득 | **Δ 이득** |")
        say("|---:|---|---:|---:|---:|---:|---:|---:|")
        for H in sorted(sub.horizon.unique()):
            s = sub[sub.horizon == H].set_index("model")
            for col in ("살아67", "평일", "주말"):
                p = s.loc["Persistence", col]
                if pd.isna(p): continue
                lv, dl = s.loc["LVL_pid", col], s.loc["DLT_pid", col]
                dx = s.loc["DLT_pid_intx", col]
                say(f"| {H} | {col} | {p:.2f} | {lv:.2f} | {dl:.2f} | {dx:.2f} | "
                    f"{(lv-p)/p*100:+.0f}% | **{(dl-p)/p*100:+.0f}%** |")
        say()

    # 핵심 판정
    say("## 판정")
    say()
    b = df[df["split"] == "B_70"]
    short = []
    for H in (15, 30):
        s = b[b.horizon == H].set_index("model")
        if "Persistence" not in s.index: continue
        for col in ("평일", "주말"):
            p, dl = s.loc["Persistence", col], s.loc["DLT_pid", col]
            if pd.isna(p): continue
            short.append((H, col, (dl-p)/p*100))
    say("**① 15·30분에서 persistence 를 이기는가**")
    say()
    for H, col, g in short:
        say(f"- {H}분 {col}: **{g:+.1f}%** {'✅ 이김' if g < 0 else '❌ 못 이김'}")
    say()
    say("**② 주말 붕괴가 얼마나 완화되는가** (B_70, train 주말 0%)")
    say()
    say("| h | 레벨 타깃 | Δ 타깃 | 완화폭 |")
    say("|---:|---:|---:|---:|")
    for H in sorted(b.horizon.unique()):
        s = b[b.horizon == H].set_index("model")
        p = s.loc["Persistence", "주말"]
        lv = (s.loc["LVL_pid","주말"]-p)/p*100
        dl = (s.loc["DLT_pid","주말"]-p)/p*100
        say(f"| {H} | {lv:+.0f}% | **{dl:+.0f}%** | {lv-dl:.0f}%p |")
    say()
    (TAB/"a17_delta_target.md").write_text("\n".join(R)+"\n", encoding="utf-8")
    print(f"\n→ {TAB/'a17_delta_target.md'}")

if __name__ == "__main__":
    main()
