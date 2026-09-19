#!/usr/bin/env python3
"""U11 구간 커버리지 재평가 — 어떤 칸을 UI 에 열 수 있는지 (dev_todo 8-7).

  .venv/bin/python scripts/reevaluate_intervals.py
  .venv/bin/python scripts/reevaluate_intervals.py --band 0.77 0.83

p10~p90 구간은 **실제 포함률이 목표 구간 안에 들어온 칸에서만** 보여준다.
너무 좁으면 거짓 확신을, 너무 넓으면 쓸모없는 범위를 준다. 둘 다 안 된다.

칸은 `horizon | operating|outside | wd|we` 다. 배포 모델(`predictor.pkl`)이 들고 있는
`interval_gate` 와 대조해, **지금 열려 있는데 통과 못 한 칸**을 먼저 찍는다.

★ 이 스크립트는 게이트를 바꾸지 않는다. 게이트는 학습·보정과 함께 정해져야 하므로
  `python -m src.models.u11_evaluate` 가 만든다. 여기서는 근거만 만든다.
★ 표본이 얇은 칸은 통과로 치지 않는다. `insufficient` 로 남긴다.
"""
import argparse
import json
import pickle
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import TABLES
from src.serve.predictor import MODEL_PATH

PREDICTIONS = ROOT / "data/processed/u11/predictions.parquet"
MIN_ROWS = 500
DEFAULT_BAND = (0.77, 0.83)


def deployed_gate(path=MODEL_PATH):
    if not Path(path).exists():
        return {}
    try:
        with open(path, "rb") as handle:
            return pickle.load(handle).get("interval_gate", {}) or {}
    except Exception:
        return {}


def coverage(predictions, band=DEFAULT_BAND, min_rows=MIN_ROWS):
    lo, hi = band
    frame = predictions.copy()
    frame["has_interval"] = frame.p10.notna() & frame.p90.notna()
    rows = []
    for (horizon, state), group in frame.groupby(["horizon", "state"]):
        served = group[group.has_interval]
        n = len(served)
        key = f"{int(horizon)}|{state}"
        record = {"key": key, "horizon": int(horizon), "state": state,
                  "rows_total": len(group), "rows_with_interval": n,
                  "interval_share": round(n / len(group), 4) if len(group) else 0.0}
        if n < min_rows:
            rows.append({**record, "coverage": None, "avg_width": None,
                         "status": "insufficient", "pass": False})
            continue
        inside = ((served.nx >= served.p10) & (served.nx <= served.p90)).mean()
        rows.append({**record,
                     "coverage": round(float(inside), 4),
                     "avg_width": round(float((served.p90 - served.p10).mean()), 2),
                     "status": "ok",
                     "pass": bool(lo <= inside <= hi)})
    return pd.DataFrame(rows).sort_values(["horizon", "state"]).reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--band", nargs=2, type=float, default=list(DEFAULT_BAND))
    parser.add_argument("--min-rows", type=int, default=MIN_ROWS)
    args = parser.parse_args()

    if not PREDICTIONS.exists():
        raise SystemExit(f"예측 파일이 없다: {PREDICTIONS}")
    predictions = pd.read_parquet(PREDICTIONS)
    band = tuple(args.band)
    table = coverage(predictions, band, args.min_rows)
    gate = deployed_gate()

    table["deployed_open"] = table.key.map(lambda k: bool(gate.get(k, False)))
    print(f"목표 커버리지 {band[0]:.2f}~{band[1]:.2f} · 표본 하한 {args.min_rows}행")
    print(f"배포 모델 게이트: 열린 칸 {sum(gate.values())}/{len(gate)}\n")
    print(table[["key", "rows_with_interval", "interval_share", "coverage",
                 "avg_width", "pass", "deployed_open", "status"]].to_string(index=False))

    wrongly_open = table[table.deployed_open & ~table["pass"]]
    can_open = table[~table.deployed_open & table["pass"]]

    print()
    if len(wrongly_open):
        print("⚠️ 열려 있는데 통과 못 한 칸 — 즉시 닫아야 한다:")
        print(wrongly_open[["key", "coverage"]].to_string(index=False))
    else:
        print("열려 있는데 통과 못 한 칸: 없음")

    if len(can_open):
        print(f"\n열 수 있는 칸 {len(can_open)}개 (재학습 후 게이트가 이 값을 갖는지 확인):")
        print(can_open[["key", "coverage", "avg_width"]].to_string(index=False))
        print("  → python -m src.models.u11_evaluate 로 게이트를 다시 만든다.")
    else:
        print("\n새로 열 수 있는 칸: 없음")

    failed = table[(table.status == "ok") & ~table["pass"]]
    if len(failed):
        narrow = failed[failed.coverage < band[0]]
        wide = failed[failed.coverage > band[1]]
        print(f"\n미달 {len(failed)}칸 — 좁음(거짓 확신) {len(narrow)} · 넓음(쓸모 낮음) {len(wide)}")

    TABLES.mkdir(parents=True, exist_ok=True)
    table.to_csv(TABLES / "u11_interval_reevaluation.csv", index=False)
    (TABLES / "u11_interval_reevaluation.json").write_text(json.dumps({
        "band": list(band), "min_rows": args.min_rows,
        "cells": int(len(table)), "passing": int(table["pass"].sum()),
        "deployed_open": int(sum(gate.values())),
        "wrongly_open": wrongly_open.key.tolist(),
        "can_open": can_open.key.tolist(),
        "note": "게이트는 u11_evaluate 가 학습·보정과 함께 정한다. 이 표는 근거다.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n산출: {TABLES}/u11_interval_reevaluation.csv")
    return 1 if len(wrongly_open) else 0


if __name__ == "__main__":
    sys.exit(main())
