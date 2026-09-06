#!/usr/bin/env python3
"""
a16 · A15 재실행 — ① 평가 층화 ② 주말 상호작용 피처

v15 §9. 기존 A10~A15 는 평일 5일·122,820행 기준이다. 현재 DB 는 주말 포함 162,514행.

세 가지에 답한다:
  1. A15 의 이득이 **죽은 22곳을 뺀 뒤에도** 유지되는가
  2. A15 의 이득이 **주말이 들어온 뒤에도** 유지되는가
  3. 상호작용 피처의 ablation 이득 (평일/주말 각각)

  python3 src/analysis/a16_stratified_weekend.py
"""
import sqlite3, sys, warnings
from pathlib import Path
import numpy as np, pandas as pd
from lightgbm import LGBMRegressor
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
TAB  = ROOT / "reports/tables"; TAB.mkdir(parents=True, exist_ok=True)
FREQ, TEST_RATIO, SEED = "5min", 0.20, 42
HORIZONS = [15, 30, 60, 120]
DEAD_RANGE = 0.01

R = []
def say(s=""): print(s, flush=True); R.append(s)

def load():
    con = sqlite3.connect(ROOT/"data/raw/parking.db")
    o = pd.read_sql("SELECT parking_id,ts_kst,cell_cnt,park_count FROM obs", con)
    L = pd.read_sql("SELECT parking_id,name,div,wdays_start,wdays_end,"
                    "wend_start,wend_end FROM lots", con); con.close()
    o["ts_kst"] = pd.to_datetime(o.ts_kst, format="mixed").dt.tz_localize(None)
    o["occ"] = (o.park_count / o.cell_cnt.replace(0, np.nan) * 100).clip(0, 120)
    o = o.dropna(subset=["occ"])
    g = o.groupby("parking_id")["occ"]
    rng = (g.max() - g.min()) / 100
    dead = set(rng[rng < DEAD_RANGE].index)
    return o, L, dead

def series(o):
    parts = []
    for pid, g in o.groupby("parking_id"):
        g = g.sort_values("ts_kst").set_index("ts_kst")[["occ"]].resample(FREQ).mean()
        g["occ"] = g["occ"].interpolate(method="linear", limit=1, limit_area="inside")
        g["parking_id"] = pid
        parts.append(g.reset_index())
    return pd.concat(parts, ignore_index=True)

def feats(df, L):
    df = df.sort_values(["parking_id", "ts_kst"]).copy()
    g = df.groupby("parking_id")["occ"]
    df["occ_now"] = df["occ"]
    for k in (1, 2, 3, 6, 12):
        df[f"lag_{k*5}"] = g.shift(k)
    for k in (1, 3, 6):
        df[f"delta_{k*5}"] = df["occ"] - g.shift(k)
    for w in (3, 6, 12):
        df[f"roll_mean_{w*5}"] = g.transform(lambda s: s.rolling(w, min_periods=1).mean())
    t = df["ts_kst"]
    df["hour"] = t.dt.hour; df["minute"] = t.dt.minute
    df["minute_of_day"] = df.hour*60 + df.minute
    df["hour_sin"] = np.sin(2*np.pi*df.minute_of_day/1440)
    df["hour_cos"] = np.cos(2*np.pi*df.minute_of_day/1440)
    df["day_of_week"] = t.dt.dayofweek
    df["is_weekend"] = (df.day_of_week >= 5).astype(int)
    df["is_sunday"]  = (df.day_of_week == 6).astype(int)
    # ★ 주말 상호작용 — hour 단독으로는 표현 불가
    df["we_hour_sin"] = df.is_weekend * df.hour_sin
    df["we_hour_cos"] = df.is_weekend * df.hour_cos
    df["parking_id_cat"] = df.parking_id.astype("category")
    df["pid_we"] = (df.parking_id.astype(str) + "_" + df.is_weekend.astype(str)).astype("category")
    # 운영시간 내/외
    def hh(s):
        try: h, m = str(s).split(":"); return int(h)*60+int(m)
        except Exception: return None
    Li = L.set_index("parking_id")
    ws, we = Li.wdays_start.map(hh), Li.wdays_end.map(hh)
    es, ee = Li.wend_start.map(hh), Li.wend_end.map(hh)
    st = np.where(df.is_weekend.eq(1), df.parking_id.map(es), df.parking_id.map(ws))
    en = np.where(df.is_weekend.eq(1), df.parking_id.map(ee), df.parking_id.map(we))
    st = pd.to_numeric(pd.Series(st), errors="coerce").values
    en = pd.to_numeric(pd.Series(en), errors="coerce").values
    closed = (st == en)                      # 00:00~00:00 은 미운영
    df["is_operating"] = np.where(
        np.isnan(st) | np.isnan(en), 1,
        np.where(closed, 0,
                 ((df.minute_of_day.values >= st) & (df.minute_of_day.values < en)).astype(int)))
    return df

BASE = ["occ_now","lag_5","lag_10","lag_15","lag_30","lag_60",
        "delta_5","delta_15","delta_30","roll_mean_15","roll_mean_30","roll_mean_60",
        "hour","minute","minute_of_day","hour_sin","hour_cos","day_of_week","is_weekend"]
PID  = BASE + ["parking_id_cat"]
INTX = PID + ["is_sunday","we_hour_sin","we_hour_cos","pid_we"]

def model():
    return LGBMRegressor(n_estimators=400, learning_rate=0.05, num_leaves=63,
                         min_child_samples=40, subsample=0.9, colsample_bytree=0.9,
                         random_state=SEED, n_jobs=-1, verbose=-1)

def main():
    o, L, dead = load()
    say("# a16 · 평가 층화 + 주말 상호작용 (A15 재실행)")
    say()
    say(f"- DB {len(o):,}행 · {o.ts_kst.min()} ~ {o.ts_kst.max()}")
    say(f"- 죽은 피드 **{len(dead)}곳** 제외 → 평가 대상 **{o.parking_id.nunique()-len(dead)}곳**")
    say(f"  (학습·예측은 전부 하고 **평가만** 나눈다)")
    say()

    d = feats(series(o), L)
    ut = d.ts_kst.dropna().sort_values().unique()
    # 두 분할을 같이 본다. 6일치 마지막 이틀이 토·일이라 어느 쪽도 완벽하지 않다.
    #   A(80%): train 에 주말이 조금 있고 test 는 주말만
    #   B(70%): train 은 평일만이고 test 에 금요일이 들어가 **평일/주말 비교가 가능**
    SPLITS = [("A_80", 0.20), ("B_70", 0.30)]
    cut = pd.Timestamp(ut[int(len(ut)*(1-TEST_RATIO))])
    DN = ["월","화","수","목","금","토","일"]
    tr_m, te_m = d.ts_kst < cut, d.ts_kst >= cut
    say("## ⚠️ 분할 지점과 요일 구성")
    say()
    say(f"- chronological split {1-TEST_RATIO:.0%} · **cut `{cut}`**")
    say(f"- train {int(tr_m.sum()):,}행 · test {int(te_m.sum()):,}행")
    say(f"- **train 주말 비율 {d[tr_m].is_weekend.mean():.1%}** "
        f"({int(d[tr_m].is_weekend.sum()):,}행)")
    say(f"- **test 요일 구성**: "
        + " · ".join(f"{DN[k]} {v:,}" for k, v in
                     d[te_m].day_of_week.value_counts().sort_index().items()))
    say()
    say("> ★ **test 가 주말에 심하게 편중된다.** 6일치 중 마지막 이틀이 토·일이라 구조적이다.")
    say("> train 주말이 얇아 상호작용 피처가 학습할 재료가 적다 — **아래 이득은 하한이다.**")
    say()

    rows = []
    for split_name, ratio in SPLITS:
      cut = pd.Timestamp(ut[int(len(ut)*(1-ratio))])
      for H in HORIZONS:
          k = H // 5
          t = d.copy()
          t["y"] = t.groupby("parking_id")["occ"].shift(-k)
          t = t.dropna(subset=["y"] + BASE)
          tr, te = t[t.ts_kst < cut], t[t.ts_kst >= cut]
          if len(tr) < 500 or len(te) < 100: continue
          te = te.assign(live=~te.parking_id.isin(dead))
          preds = {"Persistence": te.occ_now.values}
          for nm, F in (("ML_pid", PID), ("ML_pid_intx", INTX)):
              m = model().fit(tr[F], tr.y)
              preds[nm] = m.predict(te[F])
          for nm, p in preds.items():
              e = np.abs(te.y.values - p)
              def mae(mask):
                  s = e[mask.values] if hasattr(mask, "values") else e[mask]
                  return float(np.mean(s)) if len(s) else np.nan
              rows.append({
                  "split": split_name, "cut": str(cut)[:16],
                  "horizon": H, "model": nm,
                  "전체": mae(pd.Series(True, index=te.index)),
                  "살아있는67": mae(te.live),
                  "살아67_평일": mae(te.live & te.is_weekend.eq(0)),
                  "살아67_주말": mae(te.live & te.is_weekend.eq(1)),
                  "살아67_운영중": mae(te.live & te.is_operating.eq(1)),
                  "살아67_운영외": mae(te.live & te.is_operating.eq(0)),
                  "죽은22": mae(~te.live),
                  "n_test": len(te)})
    df = pd.DataFrame(rows)
    df.to_csv(TAB/"a16_stratified.csv", index=False)

    for split_name, ratio in SPLITS:
      sub = df[df["split"] == split_name]
      cutv = sub["cut"].iloc[0] if len(sub) else "-"
      tr_m2, te_m2 = d.ts_kst < pd.Timestamp(cutv), d.ts_kst >= pd.Timestamp(cutv)
      say(f"## 분할 {split_name} — cut `{cutv}`")
      say()
      say(f"- train 주말 {d[tr_m2].is_weekend.mean():.1%} · test 요일 "
          + " · ".join(f"{DN[k]} {v:,}" for k,v in
                       d[te_m2].day_of_week.value_counts().sort_index().items()))
      say()
      say("| h | 모델 | 전체 | **살아있는67** | 평일 | 주말 | 운영중 | 운영외 | 죽은22 |")
      say("|---:|---|---:|---:|---:|---:|---:|---:|---:|")
      for _, r in sub.iterrows():
          f=lambda v: "-" if pd.isna(v) else f"{v:.2f}"
          say(f"| {r.horizon} | {r.model} | {f(r['전체'])} | **{f(r['살아있는67'])}** | "
              f"{f(r['살아67_평일'])} | {f(r['살아67_주말'])} | {f(r['살아67_운영중'])} | "
              f"{f(r['살아67_운영외'])} | {f(r['죽은22'])} |")
      say()

    say("## 답 1 — 죽은 22곳을 뺀 뒤에도 이득이 유지되는가  (분할 B_70)")
    say()
    say("| h | 기준 | Persistence | ML_pid | 이득 |")
    say("|---:|---|---:|---:|---:|")
    for H in sorted(df.horizon.unique()):
        s = df[(df.horizon == H) & (df["split"] == "B_70")].set_index("model")
        for col, lb in (("전체","전체 89곳"), ("살아있는67","살아있는 67곳")):
            p, m = s.loc["Persistence", col], s.loc["ML_pid", col]
            say(f"| {H} | {lb} | {p:.2f} | {m:.2f} | **{(m-p)/p*100:+.0f}%** |")
    say()

    say("## 답 2 — 주말이 들어온 뒤에도 유지되는가  (분할 B_70, test 에 금요일 포함)")
    say()
    say("| h | 구간 | Persistence | ML_pid | 이득 |")
    say("|---:|---|---:|---:|---:|")
    for H in sorted(df.horizon.unique()):
        s = df[(df.horizon == H) & (df["split"] == "B_70")].set_index("model")
        for col, lb in (("살아67_평일","평일"), ("살아67_주말","주말")):
            p, m = s.loc["Persistence", col], s.loc["ML_pid", col]
            if np.isnan(p) or np.isnan(m): continue
            say(f"| {H} | {lb} | {p:.2f} | {m:.2f} | **{(m-p)/p*100:+.0f}%** |")
    say()

    say("## 답 3 — 상호작용 피처 ablation  (분할 B_70)")
    say()
    say("`is_sunday` · `is_weekend×hour_sin/cos` · `parking_id×is_weekend` 추가")
    say()
    say("| h | 구간 | ML_pid | +상호작용 | 이득 |")
    say("|---:|---|---:|---:|---:|")
    for H in sorted(df.horizon.unique()):
        s = df[(df.horizon == H) & (df["split"] == "B_70")].set_index("model")
        for col, lb in (("살아있는67","전체"), ("살아67_평일","평일"), ("살아67_주말","주말")):
            a, b = s.loc["ML_pid", col], s.loc["ML_pid_intx", col]
            if np.isnan(a) or np.isnan(b): continue
            say(f"| {H} | {lb} | {a:.2f} | {b:.2f} | **{(b-a)/a*100:+.1f}%** |")
    say()
    (TAB/"a16_stratified.md").write_text("\n".join(R)+"\n", encoding="utf-8")
    print(f"\n→ {TAB/'a16_stratified.md'}")

if __name__ == "__main__":
    main()
