#!/usr/bin/env python3
"""
A17 · 운영시간 상대 피처 절제 실험 (T3)

Δ 타깃(y = occ(t+h) − occ(t)) · L1 목적함수 위에서 OPR 피처를 넣고 뺀다.
전부 target_time 기준이다. 평일/주말을 나눠 본다.

  python3 src/analysis/a17_oprtime_features.py
"""
import sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from src.analysis.a16_stratified_weekend import load, series, feats, INTX
from src.features.temporal import oprtime_features, OPR_COLS, open_window
from lightgbm import LGBMRegressor

HORIZONS = (15, 30, 60, 120)


def model():
    return LGBMRegressor(objective="l1", n_estimators=400, learning_rate=0.05,
                         num_leaves=63, min_child_samples=40, random_state=42,
                         n_jobs=-1, verbose=-1)


def main():
    obs, lots, dead = load()
    d = feats(series(obs), lots)
    meta = {r["parking_id"]: r for r in lots.to_dict("records")}

    # ★ a16 의 is_operating 은 **관측 시각** 기준 층화용 컬럼이다.
    #   T3 이 요구하는 건 **도착 시각(ts + H분)** 기준이므로 horizon 마다 다시 만든다.
    #   컬럼명은 opr_ 를 붙여 a16 컬럼과 충돌시키지 않는다.
    cache = {}
    def opr_frame(sub, H):
        rows = []
        for pid, tt in zip(sub.parking_id.values, sub.ts_kst.values):
            tgt = pd.Timestamp(tt) + pd.Timedelta(minutes=H)      # ← 도착 시각
            key = (pid, tgt.weekday(), tgt.hour * 60 + tgt.minute)
            v = cache.get(key)
            if v is None:
                v = oprtime_features(meta.get(pid, {}), tgt.to_pydatetime())
                cache[key] = v
            rows.append(v)
        f = pd.DataFrame(rows, index=sub.index)
        return f.rename(columns={c: "opr_" + c for c in f.columns})

    ut = d.ts_kst.dropna().sort_values().unique()
    out = []
    for sname, ratio in (("A_80", 0.20), ("B_70", 0.30)):
        cut = pd.Timestamp(ut[int(len(ut) * (1 - ratio))])
        for H in HORIZONS:
            k = H // 5
            t = d.copy()
            t["nx"] = t.groupby("parking_id")["occ"].shift(-k)
            t["y"] = t.nx - t.occ                       # ★ Δ 타깃
            need = [c for c in INTX if c not in ("parking_id_cat", "pid_we")]
            t = t.dropna(subset=["nx"] + need)
            tr, te = t[t.ts_kst < cut], t[t.ts_kst >= cut]
            te = te[~te.parking_id.isin(dead)]          # 죽은 피드 제외
            if len(tr) < 500 or len(te) < 200:
                continue
            tr = pd.concat([tr, opr_frame(tr, H)], axis=1)
            te = pd.concat([te, opr_frame(te, H)], axis=1)
            OC = ["opr_" + c for c in OPR_COLS]
            res = {}
            for tag, cols in (("base", INTX), ("+opr", INTX + OC)):
                m = model().fit(tr[cols], tr.y)
                pred = np.clip(te.occ.values + m.predict(te[cols]), 0, 120)
                err = np.abs(pred - te.nx.values)
                wk = te.is_weekend.values
                res[tag] = (err.mean(),
                            err[wk == 0].mean() if (wk == 0).sum() > 30 else np.nan,
                            err[wk == 1].mean() if (wk == 1).sum() > 30 else np.nan)
            b, o = res["base"], res["+opr"]
            out.append({"split": sname, "horizon_min": H, "n_test": len(te),
                        "mae_base": round(b[0], 3), "mae_opr": round(o[0], 3),
                        "delta_pct": round((o[0] - b[0]) / b[0] * 100, 1),
                        "mae_base_wd": round(b[1], 3), "mae_opr_wd": round(o[1], 3),
                        "delta_pct_wd": round((o[1] - b[1]) / b[1] * 100, 1) if b[1] == b[1] else np.nan,
                        "mae_base_we": round(b[2], 3), "mae_opr_we": round(o[2], 3),
                        "delta_pct_we": round((o[2] - b[2]) / b[2] * 100, 1) if b[2] == b[2] else np.nan})
            print(f"  {sname} h={H:>3}  전체 {b[0]:.2f}→{o[0]:.2f} ({out[-1]['delta_pct']:+.1f}%)"
                  f"  평일 {b[1]:.2f}→{o[1]:.2f}  주말 {b[2]:.2f}→{o[2]:.2f}")

    df = pd.DataFrame(out)
    p = ROOT / "reports/tables/a17_oprtime_features.csv"
    df.to_csv(p, index=False, encoding="utf-8-sig")
    print(f"\n→ {p.relative_to(ROOT)}")
    return df


if __name__ == "__main__":
    main()
