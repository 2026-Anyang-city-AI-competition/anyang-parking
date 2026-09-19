#!/usr/bin/env python3
"""A25 · 만차 강등의 가치와 cutoff 선택 (dev_todo 8-5).

  .venv/bin/python -m src.analysis.a25_demotion_value

두 가지를 본다.

1. **`full_prob` 보정** — Brier 와 10-bin reliability 를 persistence 와 **함께** 낸다.
   ★ AUC 로 판단하지 않는다. 이 문제는 persistence 만으로도 AUC 0.99 가 나온다.
     `full_prob` 의 가치는 판별력이 아니라 보정된 확률값이다.

2. **cutoff 0.4 / 0.5 / 0.6 비교** — 강등을 켰을 때 순위가 얼마나 바뀌고,
   그 대가로 도보·요금을 얼마나 더 치르며, 헛걸음을 실제로 몇 번 막았는지.

   회피 성공(rescued) = A(강등 없음) 1위가 도착 시 실제 만차인데 B(강등) 1위는 여유
   피해(harmed)       = A 1위는 여유인데 B 1위가 만차
   ★ 변경률의 분모는 **질의 수**다(CLAUDE.md).

학습을 다시 하지 않는다. `data/processed/u11/predictions.parquet` 의 홀드아웃 예측을
그대로 쓰므로 test 를 다시 들여다보는 셈이 되지 않는다 — cutoff 는 이 결과를 보고
고르는 값이므로, 고른 뒤에는 이후 fold 에서 재현을 확인해야 한다.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.config import TABLES
from src.models.u11_evaluate import DESTS, route_snapshot
from src.serve.candidates import find_candidates
from src.serve.fare import calc_fare, resolve_type
from src.serve.ranking import rank_cards
from src.serve.walking import _grid

PREDICTIONS = ROOT / "data/processed/u11/predictions.parquet"
LOTS = ROOT / "data/processed/u11/lots.parquet"
CUTOFFS = (0.4, 0.5, 0.6)
FULL_AT = 90          # 점유율 90% 이상을 '만차'로 본다(U11 정의와 동일)
BINS = 10


def calibration(predictions):
    """지평선별 Brier·reliability. persistence 를 항상 같은 표에 넣는다."""
    rows, bins = [], []
    for horizon, group in predictions.groupby("horizon"):
        actual = group.nx.ge(FULL_AT).astype(int).to_numpy()
        prob = group.full_prob.to_numpy()
        # persistence 는 확률이 아니라 0/1 이다. 그래도 Brier 는 정의된다.
        persistence = group.occ_now.ge(FULL_AT).astype(int).to_numpy()
        index = np.minimum((prob * BINS).astype(int), BINS - 1)
        xs, ys = [], []
        for b in range(BINS):
            mask = index == b
            n = int(mask.sum())
            bins.append({"horizon": horizon, "bin": b, "n": n,
                         "predicted": float(prob[mask].mean()) if n else np.nan,
                         "observed": float(actual[mask].mean()) if n else np.nan})
            if n:
                xs.append(float(prob[mask].mean()))
                ys.append(float(actual[mask].mean()))
        slope = float(np.polyfit(xs, ys, 1)[0]) if len(xs) > 1 else np.nan
        rows.append({
            "horizon": horizon, "n": len(group),
            "base_rate": float(actual.mean()),
            "brier": float(np.mean((prob - actual) ** 2)),
            "brier_persistence": float(np.mean((persistence - actual) ** 2)),
            "reliability_slope": slope,
            "slope_pass": bool(0.9 <= slope <= 1.1) if np.isfinite(slope) else False,
        })
    frame = pd.DataFrame(rows)
    frame["brier_better_than_persistence"] = frame.brier < frame.brier_persistence
    return frame, pd.DataFrame(bins)


MIN_CARDS = 3          # 강등이 순위를 옮길 자리가 있어야 의미가 있다


def _candidate_cards(snapshot, near, routes, grid, target, fare_cache):
    """예측·경로·요금이 모두 있는 후보만 카드로 만든다.

    ★ 없는 후보를 채워 넣지 않는다. 대신 몇 곳이 빠졌는지 함께 돌려주고,
      요약에서 후보 충족률을 같이 본다 — 적은 후보 안에서만 비교한 결과를
      전체 후보에서의 결과처럼 읽으면 안 된다."""
    cards, dropped = [], 0
    for lot in near:
        pid = lot["parking_id"]
        if pid not in snapshot.index or (pid, *grid) not in routes:
            dropped += 1
            continue
        row = snapshot.loc[pid]
        key = (pid, target.hour, target.weekday())
        if key not in fare_cache:
            fare_cache[key] = calc_fare(
                {**lot, "type": resolve_type(lot["name"], lot.get("div"))},
                target.to_pydatetime(), 60)["total"]
        fare = fare_cache[key]
        if fare is None:
            dropped += 1
            continue
        cards.append({"parking_id": pid, "is_live": True, "estimated": False,
                      "walk_min": round(routes[(pid, *grid)][0] / 60),
                      "fare_payg": fare, "occ_now": float(row.occ_now),
                      "full_prob": float(row.full_prob), "actual": float(row.nx)})
    if len(cards) < MIN_CARDS:
        return None, dropped
    return cards, dropped


def cutoff_sweep(predictions, lots, routes, cutoffs=CUTOFFS):
    """cutoff 마다 강등 on/off 를 비교한다. 같은 질의 집합에서만 센다."""
    labeled = [{**r, "dead_feed": False} for r in lots.to_dict("records")
               if pd.notna(r.get("lat")) and pd.notna(r.get("lng"))]
    rows, skipped, fare_cache = [], 0, {}
    for horizon, frame in predictions.groupby("horizon"):
        by_time = {ts: group.set_index("parking_id")
                   for ts, group in frame.groupby("ts_kst")}
        # 2시간 간격만 본다. 5분마다 세면 같은 상황을 24번 세는 셈이다.
        times = [ts for ts in sorted(by_time) if pd.Timestamp(ts).hour % 2 == 0
                 and pd.Timestamp(ts).minute == 0]
        for name, lat, lon in DESTS:
            near = find_candidates(lat, lon, labeled=labeled, unlabeled=[])["lots"]
            grid = _grid(lat, lon)
            for ts in times:
                snapshot = by_time[ts]
                target = pd.Timestamp(ts) + pd.Timedelta(minutes=int(horizon))
                cards, dropped = _candidate_cards(snapshot, near, routes, grid,
                                                  target, fare_cache)
                if cards is None:
                    skipped += 1
                    continue
                for axis in ("walk", "fare"):
                    baseline = rank_cards(cards, axis, mode="A")
                    top_a = baseline[0]
                    # C = 현재 점유율 90% 이상이면 강등. 모델 없이 되는 일을 재는 기준선이다.
                    persistence = rank_cards(cards, axis, mode="C")
                    top_c = persistence[0]
                    fail_c = top_c["actual"] >= FULL_AT
                    for cutoff in cutoffs:
                        demoted = rank_cards(cards, axis, cutoff=cutoff, mode="B")
                        top_b = demoted[0]
                        fail_a = top_a["actual"] >= FULL_AT
                        fail_b = top_b["actual"] >= FULL_AT
                        rows.append({
                            "c_fail": int(fail_c),
                            "c_rescued": int(fail_a and not fail_c),
                            "c_extra_walk": top_c["walk_min"] - top_a["walk_min"],
                            "both_failed": int(fail_a and fail_b),
                            "horizon": horizon, "axis": axis, "cutoff": cutoff,
                            "dest": name, "ts_kst": str(ts),
                            "changed": int([c["parking_id"] for c in baseline]
                                           != [c["parking_id"] for c in demoted]),
                            "top1_changed": int(top_a["parking_id"] != top_b["parking_id"]),
                            "a_fail": int(fail_a), "b_fail": int(fail_b),
                            "rescued": int(fail_a and not fail_b),
                            "harmed": int(not fail_a and fail_b),
                            "extra_walk": top_b["walk_min"] - top_a["walk_min"],
                            "extra_fare": top_b["fare_payg"] - top_a["fare_payg"],
                            "n_candidates": len(cards), "dropped_candidates": dropped,
                        })
    return pd.DataFrame(rows), skipped


def summarise(queries):
    """질의 수를 분모로 한 요약. 축·지평선·cutoff 별로 낸다."""
    out = []
    for (horizon, axis, cutoff), group in queries.groupby(["horizon", "axis", "cutoff"]):
        n = len(group)
        rescued, harmed = int(group.rescued.sum()), int(group.harmed.sum())
        out.append({
            "horizon": horizon, "axis": axis, "cutoff": cutoff, "queries": n,
            "change_rate": round(group.changed.sum() / n, 4),
            "top1_change_rate": round(group.top1_changed.sum() / n, 4),
            "a_fail_rate": round(group.a_fail.sum() / n, 4),
            "b_fail_rate": round(group.b_fail.sum() / n, 4),
            "rescued": rescued, "harmed": harmed, "net_rescued": rescued - harmed,
            "both_failed": int(group.both_failed.sum()),
            # 모델 없이 persistence 만으로 걸러낸 수. 이것과의 차이가 AI 의 기여다.
            "rescued_persistence": int(group.c_rescued.sum()),
            "gain_over_persistence": rescued - int(group.c_rescued.sum()),
            "c_extra_walk_median": float(group.loc[group.c_rescued.eq(1), "c_extra_walk"].median())
                                   if group.c_rescued.any() else 0.0,
            # 순위를 바꿨을 때만 대가가 생긴다. 안 바꾼 질의까지 평균에 넣으면 희석된다.
            "extra_walk_median": float(group.loc[group.top1_changed.eq(1), "extra_walk"].median())
                                 if group.top1_changed.any() else 0.0,
            "extra_fare_median": float(group.loc[group.top1_changed.eq(1), "extra_fare"].median())
                                 if group.top1_changed.any() else 0.0,
            "avg_candidates": round(float(group.n_candidates.mean()), 2),
            "avg_dropped": round(float(group.dropped_candidates.mean()), 2),
        })
    return pd.DataFrame(out)


def run():
    if not PREDICTIONS.exists():
        raise SystemExit(f"예측 파일이 없다: {PREDICTIONS} — 먼저 u11_evaluate 를 돌린다")
    predictions = pd.read_parquet(PREDICTIONS)
    lots = pd.read_parquet(LOTS)
    routes = route_snapshot(lots)

    calib, calib_bins = calibration(predictions)
    print("── full_prob 보정 (persistence 병기) ──")
    print(calib.to_string(index=False))

    queries, skipped = cutoff_sweep(predictions, lots, routes)
    if queries.empty:
        print(f"\n질의를 만들지 못했다(건너뜀 {skipped}). 경로·관측 캐시를 확인한다.")
        return calib, queries
    summary = summarise(queries)
    print(f"\n── cutoff 비교 (질의 {len(queries)//len(CUTOFFS)}개 · 건너뜀 {skipped}) ──")
    print(summary.to_string(index=False))

    TABLES.mkdir(parents=True, exist_ok=True)
    calib.to_csv(TABLES / "a25_calibration.csv", index=False)
    calib_bins.to_csv(TABLES / "a25_calibration_bins.csv", index=False)
    queries.to_csv(TABLES / "a25_cutoff_queries.csv", index=False)
    summary.to_csv(TABLES / "a25_cutoff_summary.csv", index=False)

    overall = summary.groupby("cutoff").agg(
        rescued=("rescued", "sum"), harmed=("harmed", "sum"),
        both_failed=("both_failed", "sum"),
        rescued_persistence=("rescued_persistence", "sum"),
        change_rate=("change_rate", "mean")).reset_index()
    overall["net_rescued"] = overall.rescued - overall.harmed
    overall["gain_over_persistence"] = overall.rescued - overall.rescued_persistence
    print("\n── cutoff 총계 ──")
    print(overall.to_string(index=False))

    manifest = {
        "predictions_rows": int(len(predictions)),
        "horizons": sorted(int(h) for h in predictions.horizon.unique()),
        "cutoffs": list(CUTOFFS),
        "queries_per_cutoff": int(len(queries) // len(CUTOFFS)),
        "skipped_queries": skipped,
        "note": ("cutoff 는 이 홀드아웃 결과를 보고 고르는 값이다. "
                 "고른 뒤 이후 fold 에서 재현을 확인하기 전에는 기본값을 바꾸지 않는다."),
    }
    (TABLES / "a25_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n산출: {TABLES}/a25_*.csv")
    return calib, summary


if __name__ == "__main__":
    run()
