#!/usr/bin/env python3
"""만차확률 보정 게이트 — 확률이 믿을 만한 지평선에서만 쓴다.

  .venv/bin/python scripts/build_probability_gate.py
  .venv/bin/python scripts/build_probability_gate.py --write

**이 게이트는 「순위에 쓸지」가 아니라 「숫자로 보여줄지」를 정한다.**

기울기가 0.9 미만이면 과신이다 — "80%" 라고 말했는데 실제로는 그보다 덜 찬다.
지평선이 길수록 심해진다.

  15분 0.98 · 30분 0.96 · 60분 0.89 · 120분 0.88 · 240분 0.82 · 360분 0.73

★★ **그렇다고 강등에서 빼면 안 된다.** A25 로 측정해 보니 모든 지평선에서 피해(harmed)가
   0 이고, persistence 대비 추가 기여는 **지평선이 길수록 커진다**
   (15분 +0 · 60분 +10 · 240분 +27 · 360분 +37).
   확률을 강등에서 빼면 긴 지평선에서 가장 쓸모 있는 기능을 없애게 된다.

   즉 이 확률은 **순위를 매기기에는 충분하지만 숫자로 인용하기에는 부족하다.**
   보정이 어긋난 지평선에서는 순위에는 쓰되 "만차 78%" 같은 수치는 내보내지 않는다.

정확도 게이트(`prediction_accuracy.csv`)와 다른 것을 잰다.
  정확도 게이트  몇 면 남을지(p50)를 맞히는가 — 주차장별
  보정 게이트    만차확률을 숫자로 보여줘도 되는가 — 지평선별
"""
import argparse
import csv
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import PROCESSED, TABLES

PREDICTIONS = ROOT / "data/processed/u11/predictions.parquet"
OUTPUT = PROCESSED / "probability_calibration.csv"
EVIDENCE = "reports/tables/u11_probability.csv"

FIELDS = ("horizon", "status", "reliability_slope", "brier", "brier_persistence",
          "n", "reason", "evidence_report", "evaluated_at")
SLOPE_BAND = (0.9, 1.1)
FULL_AT = 90
BINS = 10
MIN_ROWS = 5000


def evaluate(predictions, band=SLOPE_BAND, min_rows=MIN_ROWS):
    rows = []
    for horizon, group in predictions.groupby("horizon"):
        actual = group.nx.ge(FULL_AT).astype(int).to_numpy()
        prob = group.full_prob.to_numpy()
        persistence = group.occ_now.ge(FULL_AT).astype(int).to_numpy()
        index = np.minimum((prob * BINS).astype(int), BINS - 1)
        points = [(float(prob[index == b].mean()), float(actual[index == b].mean()))
                  for b in range(BINS) if (index == b).any()]
        slope = float(np.polyfit(*np.array(points).T, 1)[0]) if len(points) > 1 else np.nan
        brier = float(np.mean((prob - actual) ** 2))
        base = float(np.mean((persistence - actual) ** 2))
        record = {"horizon": int(horizon), "n": len(group),
                  "reliability_slope": round(slope, 4) if np.isfinite(slope) else "",
                  "brier": round(brier, 6), "brier_persistence": round(base, 6),
                  "evidence_report": EVIDENCE, "evaluated_at": date.today().isoformat()}
        if len(group) < min_rows or not np.isfinite(slope):
            rows.append({**record, "status": "insufficient",
                         "reason": f"표본 부족 또는 기울기 산출 불가 ({len(group)}행)"})
        elif brier >= base:
            rows.append({**record, "status": "no_better_than_persistence",
                         "reason": "Brier 가 persistence 이상 — 모델을 쓸 이유가 없다"})
        elif band[0] <= slope <= band[1]:
            rows.append({**record, "status": "calibrated",
                         "reason": f"기울기 {slope:.3f} — 목표 {band[0]}~{band[1]} 안"})
        else:
            direction = "과신(구간보다 낮음)" if slope < band[0] else "과소(구간보다 높음)"
            rows.append({**record, "status": "miscalibrated",
                         "reason": f"기울기 {slope:.3f} — {direction}"})
    return pd.DataFrame(rows).sort_values("horizon")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--band", nargs=2, type=float, default=list(SLOPE_BAND))
    args = parser.parse_args()

    if not PREDICTIONS.exists():
        raise SystemExit(f"예측 파일이 없다: {PREDICTIONS}")
    table = evaluate(pd.read_parquet(PREDICTIONS), tuple(args.band))
    print(f"목표 기울기 {args.band[0]}~{args.band[1]}\n")
    print(table[["horizon", "n", "reliability_slope", "brier", "brier_persistence",
                 "status"]].to_string(index=False))

    served = table[table.status.eq("calibrated")]
    blocked = table[~table.status.eq("calibrated")]
    print(f"\n확률을 숫자로 표시: {served.horizon.tolist() or '없음'}")
    print(f"숫자 비표시(순위에는 계속 사용): {blocked.horizon.tolist() or '없음'}")
    if len(blocked):
        print("  → 강등에서 빼지 않는다. 피해 0건이고 긴 지평선일수록 기여가 크다(A25).")
        print("  → 사용자에게는 수치 대신 '혼잡 예상' 같은 정성 표현을 쓴다.")

    if not args.write:
        print("\n--write 를 붙이면 적재한다.")
        return 0

    TABLES.mkdir(parents=True, exist_ok=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in table.to_dict("records"):
            writer.writerow({k: row.get(k, "") for k in FIELDS})
    print(f"\n적재: {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
