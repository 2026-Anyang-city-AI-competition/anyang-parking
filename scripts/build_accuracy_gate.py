#!/usr/bin/env python3
"""주차장 × 지평선 정확도 게이트를 만든다.

  .venv/bin/python scripts/build_accuracy_gate.py            # 미리보기
  .venv/bin/python scripts/build_accuracy_gate.py --write

지평선 전체를 켜고 끄는 대신 **주차장마다** 판정한다. 평균은 목표 안인데 특정
주차장에서만 크게 틀리는 상태였고, 그 주차장들이 지평선 전체를 막고 있었다.
못 맞추는 곳만 빼면 나머지는 더 먼 미래까지 예측할 수 있다.

★★ **한 번 잘 맞은 것으로는 인증하지 않는다.** test 날짜를 전·후반으로 갈라
   **양쪽 모두** 합격해야 한다. 홀드아웃 성적이 좋은 주차장만 골라 서비스에 넣으면
   그 성적 자체가 선택 편향이 된다. 재현을 요구하는 것이 그 방어다.
★  표본이 얇은 칸은 합격으로 치지 않는다(`insufficient`).
★  이 파일은 **예측 제공 여부**만 정한다. 예측을 막아도 위치·요금·실시간은 그대로 나간다.

`prediction_availability.csv`(데이터 품질 게이트)와 목적이 다르다.
그쪽은 "값이 움직이는가", 이쪽은 "맞히는가"다. 둘 다 통과해야 예측이 나간다.
"""
import argparse
import csv
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import PROCESSED, TABLES

SOURCE = ROOT / "data/processed/a23/predictions.csv.gz"
OUTPUT = PROCESSED / "prediction_accuracy.csv"
EVIDENCE = "reports/tables/accuracy_gate_detail.csv"

FIELDS = ("parking_id", "horizon", "status", "mae_first", "mae_second", "mae_overall",
          "n_first", "n_second", "reason", "evidence_report", "evaluated_at")
MAE_TARGET_PP = 10.0
MIN_ROWS_PER_HALF = 100


def evaluate(source=SOURCE, target=MAE_TARGET_PP, min_rows=MIN_ROWS_PER_HALF):
    frame = pd.read_csv(source)
    frame = frame[frame.eligible & frame.pred_ml.notna()].copy()
    frame["ae"] = (frame.actual_occ - frame.pred_ml).abs()
    days = sorted(frame.test_date.unique())
    if len(days) < 2:
        raise SystemExit("test 날짜가 2일 미만이라 재현을 볼 수 없다")
    first, second = days[: len(days) // 2], days[len(days) // 2:]

    rows = []
    for (pid, horizon), group in frame.groupby(["parking_id", "horizon"]):
        a = group[group.test_date.isin(first)].ae
        b = group[group.test_date.isin(second)].ae
        record = {
            "parking_id": int(pid), "horizon": int(horizon),
            "n_first": int(len(a)), "n_second": int(len(b)),
            "mae_first": round(float(a.mean()), 3) if len(a) else "",
            "mae_second": round(float(b.mean()), 3) if len(b) else "",
            "mae_overall": round(float(group.ae.mean()), 3),
            "evidence_report": EVIDENCE, "evaluated_at": date.today().isoformat(),
        }
        if len(a) < min_rows or len(b) < min_rows:
            rows.append({**record, "status": "insufficient",
                         "reason": f"표본 부족(전반 {len(a)}행/후반 {len(b)}행)"})
            continue
        ok_first, ok_second = a.mean() <= target, b.mean() <= target
        if ok_first and ok_second:
            rows.append({**record, "status": "certified",
                         "reason": f"전·후반 모두 MAE {target:g}%p 이하"})
        elif ok_first or ok_second:
            # 한쪽만 통과한 곳은 재현되지 않은 것이다. 켜지 않는다.
            rows.append({**record, "status": "not_reproduced",
                         "reason": "한쪽 구간에서만 합격 — 재현되지 않음"})
        else:
            rows.append({**record, "status": "failed",
                         "reason": f"양쪽 모두 MAE {target:g}%p 초과"})
    table = pd.DataFrame(rows).sort_values(["horizon", "parking_id"])
    return table, first, second


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--target", type=float, default=MAE_TARGET_PP)
    parser.add_argument("--min-rows", type=int, default=MIN_ROWS_PER_HALF)
    args = parser.parse_args()

    table, first, second = evaluate(target=args.target, min_rows=args.min_rows)
    print(f"재현 검증: 전반 {first[0]}~{first[-1]} / 후반 {second[0]}~{second[-1]}")
    print(f"목표 MAE {args.target:g}%p · 반쪽당 최소 {args.min_rows}행\n")

    print("지평선 | 인증 | 미재현 | 미달 | 표본부족 | 인증 주차장 평균MAE")
    for horizon, group in table.groupby("horizon"):
        counts = group.status.value_counts()
        certified = group[group.status.eq("certified")]
        mae = f"{certified.mae_overall.mean():.2f}" if len(certified) else "—"
        print(f"{horizon:>6}분 | {counts.get('certified', 0):>4} | "
              f"{counts.get('not_reproduced', 0):>6} | {counts.get('failed', 0):>4} | "
              f"{counts.get('insufficient', 0):>8} | {mae:>18}")

    total = len(table)
    certified = int((table.status == "certified").sum())
    print(f"\n전체 {total}칸 중 인증 {certified}칸 ({certified/total:.0%})")
    print("인증되지 않은 칸은 예측을 내보내지 않는다 — 위치·요금·실시간은 그대로 제공한다.")

    if not args.write:
        print("\n--write 를 붙이면 적재한다.")
        return 0

    TABLES.mkdir(parents=True, exist_ok=True)
    table.to_csv(TABLES / "accuracy_gate_detail.csv", index=False, encoding="utf-8-sig")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        for row in table.to_dict("records"):
            writer.writerow({k: row.get(k, "") for k in FIELDS})
    print(f"\n적재: {OUTPUT}")
    print(f"근거: {TABLES / 'accuracy_gate_detail.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
