#!/usr/bin/env python3
"""A26 · 평일/주말 × 예측 가능/불가 층화 평가 (dev_todo 8장 '데이터 추가 시').

  .venv/bin/python -m src.analysis.a26_gate_stratified

전체 평균 하나만 보면 안 된다. A15 의 -42% 는 평일 장기 지평선에서만 성립했고
주말에선 +107~223% 로 무너졌다(CLAUDE.md). 그래서 네 칸으로 나눠 본다.

  평일 × 게이트 허용 / 평일 × 게이트 차단
  주말 × 게이트 허용 / 주말 × 게이트 차단

**게이트 차단 구간은 사용자에게 예측이 나가지 않는 시간대다.** 그 구간 성능을 대표
수치에 합산하면 리포트가 실제 서비스보다 좋게(또는 나쁘게) 보인다. 둘을 갈라 놓고
**허용 구간 수치를 대표값으로** 삼는다.

★ persistence 를 모든 칸에 병기한다. 개선폭은 기준선 대비로만 판단한다.
★ 표본이 얇은 칸은 수치를 내지 않고 `n` 과 함께 `insufficient` 로 남긴다 —
  평가불가를 성공으로 합산하지 않는다.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.config import TABLES
from src.serve.prediction_gate import PredictionGate

PREDICTIONS = ROOT / "data/processed/u11/predictions.parquet"
MIN_ROWS = 200          # 이보다 적으면 칸 수치를 내지 않는다
MAE_TARGET_PP = 10.0


def annotate(predictions, gate):
    """각 행에 요일 구분과 게이트 판정을 붙인다."""
    frame = predictions.copy()
    observed = pd.to_datetime(frame.ts_kst)
    target = pd.to_datetime(frame.target_time)
    frame["is_weekend"] = target.dt.dayofweek.ge(5)
    frame["gate_allowed"] = np.asarray(
        gate.validity_mask(frame.parking_id.tolist(),
                           observed.dt.to_pydatetime().tolist(),
                           target.dt.to_pydatetime().tolist()), dtype=bool)
    frame["ml_ae"] = (frame.nx - frame.p50).abs()
    frame["persistence_ae"] = (frame.nx - frame.occ_now).abs()
    return frame


def stratify(frame):
    rows = []
    for horizon, by_horizon in frame.groupby("horizon"):
        for weekend in (False, True):
            for allowed in (True, False):
                cell = by_horizon[by_horizon.is_weekend.eq(weekend)
                                  & by_horizon.gate_allowed.eq(allowed)]
                n = len(cell)
                label = {
                    "horizon": int(horizon),
                    "day_group": "주말" if weekend else "평일",
                    "gate": "허용" if allowed else "차단",
                    "n": n,
                    "lots": int(cell.parking_id.nunique()) if n else 0,
                }
                if n < MIN_ROWS:
                    rows.append({**label, "ml_mae": np.nan, "persistence_mae": np.nan,
                                 "improvement_pct": np.nan, "beats_persistence": False,
                                 "mae_target_ok": False, "status": "insufficient"})
                    continue
                ml = float(cell.ml_ae.mean())
                base = float(cell.persistence_ae.mean())
                rows.append({
                    **label, "ml_mae": ml, "persistence_mae": base,
                    "improvement_pct": round((base - ml) / base * 100, 2) if base else np.nan,
                    "beats_persistence": bool(ml < base),
                    "mae_target_ok": bool(ml <= MAE_TARGET_PP),
                    "status": "ok",
                })
    return pd.DataFrame(rows)


def headline(table):
    """대표 수치는 **게이트 허용 구간**이다. 차단 구간은 합산하지 않는다."""
    served = table[table.gate.eq("허용") & table.status.eq("ok")]
    rows = []
    for horizon, group in served.groupby("horizon"):
        n = int(group.n.sum())
        ml = float((group.ml_mae * group.n).sum() / n)
        base = float((group.persistence_mae * group.n).sum() / n)
        rows.append({"horizon": int(horizon), "n": n, "ml_mae": ml,
                     "persistence_mae": base,
                     "improvement_pct": round((base - ml) / base * 100, 2) if base else np.nan,
                     "beats_persistence": bool(ml < base),
                     "mae_target_ok": bool(ml <= MAE_TARGET_PP),
                     "cells": int(len(group))})
    return pd.DataFrame(rows)


def run():
    if not PREDICTIONS.exists():
        raise SystemExit(f"예측 파일이 없다: {PREDICTIONS}")
    gate = PredictionGate()
    status = gate.refresh(force=True)
    if status.get("rules_loaded", 0) == 0:
        raise SystemExit("예측 게이트가 비어 있다 — 층화의 기준이 없다. "
                         "scripts/build_prediction_availability.py 를 먼저 돌린다.")

    predictions = pd.read_parquet(PREDICTIONS)
    frame = annotate(predictions, gate)
    table = stratify(frame)
    summary = headline(table)

    allowed_share = float(frame.gate_allowed.mean())
    print(f"게이트 규칙 {status['rules_loaded']}행 · 예측 {len(frame):,}행 중 "
          f"허용 구간 {allowed_share:.1%}")
    print("\n── 네 칸 층화 (persistence 병기) ──")
    print(table.to_string(index=False))
    print("\n── 대표 수치: 게이트 허용 구간만 ──")
    print(summary.to_string(index=False))

    weak = table[(table.status == "ok") & (~table.beats_persistence)]
    if len(weak):
        print("\n⚠️ persistence 에 지는 칸:")
        print(weak[["horizon", "day_group", "gate", "n", "ml_mae",
                    "persistence_mae"]].to_string(index=False))
    thin = table[table.status == "insufficient"]
    if len(thin):
        print(f"\n표본 부족으로 평가하지 않은 칸 {len(thin)}개 (행 < {MIN_ROWS}) — "
              f"성공으로 합산하지 않는다.")

    TABLES.mkdir(parents=True, exist_ok=True)
    table.to_csv(TABLES / "a26_gate_stratified.csv", index=False)
    summary.to_csv(TABLES / "a26_served_summary.csv", index=False)
    (TABLES / "a26_manifest.json").write_text(json.dumps({
        "rows": int(len(frame)),
        "gate_rules": status["rules_loaded"],
        "allowed_share": round(allowed_share, 4),
        "min_rows_per_cell": MIN_ROWS,
        "cells_insufficient": int(len(thin)),
        "cells_below_persistence": int(len(weak)),
        "note": "대표 수치는 게이트 허용 구간이다. 차단 구간은 사용자에게 예측이 나가지 않는다.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n산출: {TABLES}/a26_*.csv")
    return table, summary


if __name__ == "__main__":
    run()
