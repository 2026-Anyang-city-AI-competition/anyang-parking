#!/usr/bin/env python3
"""A21: A20과 같은 rolling test 행에서 만차확률(full_prob) 검증.

실행: python src/analysis/a21_full_probability_check.py
만차 정의는 서비스/U11과 동일하게 실제 점유율 >= 90%이다.
0.4/0.5/0.6 threshold의 precision/recall/FPR 및 Brier score를 저장한다.
"""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.linear_model import LogisticRegression

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.analysis.a20_matched_model_comparison import (
    HORIZONS, FEATURES, PARAMS, build_features, build_horizon_frame,
    load_inputs, split_frame, test_dates,
)
from src.config import PARKING_DB, PARKING_ACCESS_RULES_CSV, TABLES

FULL_OCC = 90.0
THRESHOLDS = (0.4, 0.5, 0.6)


def logodds(p):
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p)).reshape(-1, 1)


def metrics(frame, threshold):
    y = frame["actual_full"].to_numpy(dtype=bool)
    pred = frame["full_prob"].to_numpy() >= threshold
    tp = int((pred & y).sum()); fp = int((pred & ~y).sum())
    fn = int((~pred & y).sum()); tn = int((~pred & ~y).sum())
    return dict(
        n=len(frame), actual_full=int(y.sum()), predicted_full=int(pred.sum()),
        tp=tp, fp=fp, fn=fn, tn=tn,
        precision=tp/(tp+fp) if tp+fp else np.nan,
        recall=tp/(tp+fn) if tp+fn else np.nan,
        false_positive_rate=fp/(fp+tn) if fp+tn else np.nan,
        accuracy=(tp+tn)/len(frame) if len(frame) else np.nan,
    )


def summarize(pred):
    rows = []
    for h in HORIZONS:
        p = pred[pred.horizon.eq(h)]
        for access in ("accessible", "all"):
            a = p[p.access_group.eq("accessible")] if access == "accessible" else p
            for day in ("all", "weekday", "weekend"):
                d = a if day == "all" else a[a.weekday_group.eq(day)]
                for t in THRESHOLDS:
                    rows.append(dict(horizon=h, access_group=access, weekday_group=day,
                                     threshold=t, **metrics(d, t)))
    return pd.DataFrame(rows)


def main():
    obs, lots, rules, info = load_inputs(PARKING_DB, PARKING_ACCESS_RULES_CSV)
    print("=== A21 full_prob 검증 ===")
    print(f"대상 {info['active_lots']}곳 · {len(obs):,}행 · {info['start']} ~ {info['end']}")
    base = build_features(obs, lots, progress=True)
    dates = test_dates(obs.ts_kst.min(), obs.ts_kst.max())
    print("test 날짜(KST):", ", ".join(str(x.date()) for x in dates))
    out = []
    for h in HORIZONS:
        frame = build_horizon_frame(base, h, rules, lots)
        for fold, start in enumerate(dates, 1):
            train, cal, test = split_frame(frame, start)
            y_train = train.actual_occ.ge(FULL_OCC).astype(int)
            if y_train.nunique() < 2:
                raise ValueError(f"H={h} fold={fold}: train에 만차/비만차 두 클래스가 모두 필요합니다.")
            clf = LGBMClassifier(objective="binary", **PARAMS).fit(train[FEATURES], y_train)
            platt = None
            y_cal = cal.actual_occ.ge(FULL_OCC).astype(int)
            if len(cal) and y_cal.nunique() == 2:
                raw_cal = clf.predict_proba(cal[FEATURES])[:, 1]
                platt = LogisticRegression(C=1, max_iter=500).fit(logodds(raw_cal), y_cal)
            p = clf.predict_proba(test[FEATURES])[:, 1]
            if platt is not None:
                p = platt.predict_proba(logodds(p))[:, 1]
            r = test[["parking_id", "ts_kst", "target_time", "actual_occ",
                      "weekday_group", "access_group"]].copy()
            r["horizon"] = h; r["fold"] = fold
            r["actual_full"] = r.actual_occ.ge(FULL_OCC).astype(int)
            r["full_prob"] = p
            out.append(r)
            service = r[r.access_group.eq("accessible")]
            brier = np.mean((service.full_prob-service.actual_full)**2) if len(service) else np.nan
            print(f"H={h}분 {fold}/{len(dates)} · {start.date()} · 출입가능 n={len(service):,} · "
                  f"실제만차={int(service.actual_full.sum()):,} · Brier={brier:.4f}", flush=True)
    pred = pd.concat(out, ignore_index=True)
    summary = summarize(pred)
    TABLES.mkdir(parents=True, exist_ok=True)
    pred.to_csv(TABLES / "a21_full_probability_predictions.csv", index=False)
    summary.to_csv(TABLES / "a21_full_probability.csv", index=False)
    service = pred[pred.access_group.eq("accessible")]
    brier_rows = []
    for h in HORIZONS:
        p = service[service.horizon.eq(h)]
        brier_rows.append(dict(horizon=h, n=len(p), actual_full=int(p.actual_full.sum()),
                               prevalence=p.actual_full.mean(),
                               brier=np.mean((p.full_prob-p.actual_full)**2)))
    pd.DataFrame(brier_rows).to_csv(TABLES / "a21_full_probability_brier.csv", index=False)
    show = summary[(summary.access_group == "accessible") & (summary.weekday_group == "all")]
    print("\n=== 출입 가능 시간 · full_prob threshold 비교 ===")
    print(show[["horizon","threshold","n","actual_full","predicted_full","precision","recall","false_positive_rate"]]
          .to_string(index=False, float_format=lambda x:f"{x:.4f}"))
    print("\n저장 완료:", TABLES / "a21_full_probability.csv")


if __name__ == "__main__":
    main()
