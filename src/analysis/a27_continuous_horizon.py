#!/usr/bin/env python3
"""A27 · 지평선을 피처로 넣은 단일 모델 — 5분 단위 연속 예측이 되는가.

  .venv/bin/python -m src.analysis.a27_continuous_horizon
  .venv/bin/python -m src.analysis.a27_continuous_horizon --step 5 --max-horizon 120

**지금 구조의 문제.** 서비스는 15/30/60/120분 모델 4개를 따로 학습해 두고, 그 밖의
요청이 오면 가장 가까운 격자로 **반올림**한다. 45분 요청에 60분 모델을 쓰는 식이라
예측이 실제보다 먼 미래를 가정한다. 240·360분을 쓰려면 모델을 또 학습해야 한다.

**CLAUDE.md 의 규칙은 원래 단일 모델이다** — "`horizon_min` 을 피처로 넣은 단일 모델.
4개 따로 만들지 않는다." 코드가 그 규칙에서 벗어나 있다. 이 실험은 규칙대로 했을 때
격자 없이 5분 단위로 예측할 수 있는지, 그리고 격자 모델만큼 정확한지를 본다.

비교는 **같은 평가행·같은 fold** 에서 한다.
  P  격자별 모델 (지금 구조)  — 각 지평선을 따로 학습
  C  연속 모델 (규칙대로)     — 여러 지평선을 모아 한 번 학습, `horizon_min` 을 피처로

★ 학습에 쓴 지평선과 **쓰지 않은 지평선**을 나눠 본다. 안 배운 지평선에서도 맞아야
  "5분 단위로 바로 나온다"고 말할 수 있다. 배운 것만 맞으면 격자가 촘촘해진 것뿐이다.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.config import TABLES
from src.models.u11_evaluate import FEATURES, PARAMS, horizon_frame, load_snapshot

# 학습에 넣는 지평선(성긴 격자)과, 학습에 없던 검증용 지평선.
TRAIN_HORIZONS = (15, 30, 60, 120)
HELDOUT_HORIZONS = (45, 75, 90, 105)
SERVICE_HORIZONS = TRAIN_HORIZONS


def build_frames(data, meta, horizons):
    frames = {}
    for horizon in horizons:
        frame = horizon_frame(data, horizon, meta)
        frame["horizon_min"] = horizon
        frames[horizon] = frame.dropna(subset=FEATURES + ["nx"])
    return frames


def split(frame, start):
    """u11 과 같은 purge 규칙. 정답 시각이 경계를 넘지 않게 한다."""
    train = frame[frame.target_time < start]
    test = frame[(frame.ts_kst >= start)
                 & (frame.target_time < start + pd.Timedelta(days=1))]
    return train, test


def fit(train, features):
    return LGBMRegressor(objective="quantile", alpha=.5, **PARAMS).fit(
        train[features], train.y)


def run(step=5, max_horizon=120, sample=250_000, seed=42, days_back=1):
    started = time.time()
    data, lots, info = load_snapshot()
    meta = {r["parking_id"]: r for r in lots.to_dict("records")}
    dense = tuple(range(step, max_horizon + 1, step))
    wanted = sorted(set(TRAIN_HORIZONS) | set(HELDOUT_HORIZONS) | set(dense))
    print(f"지평선 {len(wanted)}개 프레임 생성 중 ({info['start'][:10]}~{info['end'][:10]})",
          flush=True)
    frames = build_frames(data, meta, wanted)

    last_day = pd.Timestamp(max(f.target_time.max() for f in frames.values())).normalize()
    start = last_day - pd.Timedelta(days=days_back)   # test 로 쓸 하루
    print(f"test 시작 {start.date()}", flush=True)

    rng = np.random.default_rng(seed)
    # 연속 모델: 성긴 격자만 모아 학습한다. 촘촘히 넣으면 "격자를 늘린 것"과 구분이 안 된다.
    pooled = pd.concat([split(frames[h], start)[0] for h in TRAIN_HORIZONS],
                       ignore_index=True)
    # ★ 학습량을 맞춘다. 격자 모델은 지평선마다 `sample` 행을 쓰므로, 연속 모델에는
    #   지평선 수만큼 곱한 예산을 준다. 이걸 빠뜨리면 데이터가 적어서 진 것을
    #   구조 때문에 진 것으로 읽게 된다.
    pooled_budget = sample * len(TRAIN_HORIZONS)
    if len(pooled) > pooled_budget:
        pooled = pooled.iloc[rng.permutation(len(pooled))[:pooled_budget]]
    continuous_features = FEATURES + ["horizon_min"]
    print(f"연속 모델 학습: {len(pooled):,}행 · 피처 {len(continuous_features)}개 "
          f"(지평선당 ~{len(pooled)//len(TRAIN_HORIZONS):,}행)", flush=True)
    continuous = fit(pooled, continuous_features)

    # 격자별 모델: 지금 구조 그대로, 지평선마다 따로.
    per_horizon = {}
    for horizon in TRAIN_HORIZONS:
        train, _ = split(frames[horizon], start)
        if len(train) > sample:
            train = train.iloc[rng.permutation(len(train))[:sample]]
        per_horizon[horizon] = fit(train, FEATURES)
    print(f"격자 모델 {len(per_horizon)}개 학습 완료", flush=True)

    rows = []
    for horizon in wanted:
        _, test = split(frames[horizon], start)
        if len(test) < 500:
            rows.append({"horizon": horizon, "n": len(test), "status": "insufficient"})
            continue
        actual = test.nx.to_numpy()
        base = test.occ_now.to_numpy()
        # 연속 모델은 Δ를 예측한다. 복원할 때 클리핑을 빠뜨리지 않는다.
        cont = np.clip(base + continuous.predict(test[continuous_features]), 0, 120)
        record = {
            "horizon": horizon, "n": len(test),
            "trained_on": horizon in TRAIN_HORIZONS,
            "persistence_mae": float(np.abs(actual - base).mean()),
            "continuous_mae": float(np.abs(actual - cont).mean()),
            "status": "ok",
        }
        # 지금 구조가 이 지평선에서 내놓는 값: 가장 가까운 격자 모델
        snapped = min(TRAIN_HORIZONS, key=lambda x: abs(x - horizon))
        grid = np.clip(base + per_horizon[snapped].predict(test[FEATURES]), 0, 120)
        record["snapped_to"] = snapped
        record["grid_mae"] = float(np.abs(actual - grid).mean())
        record["continuous_better"] = record["continuous_mae"] < record["grid_mae"]
        rows.append(record)

    table = pd.DataFrame(rows)
    scored = table[table.status.eq("ok")].copy()
    scored["vs_grid_pct"] = ((scored.grid_mae - scored.continuous_mae)
                             / scored.grid_mae * 100).round(2)
    scored["beats_persistence"] = scored.continuous_mae < scored.persistence_mae

    print("\n── 학습한 지평선 (격자 모델과 정면 비교) ──")
    print(scored[scored.trained_on][
        ["horizon", "n", "persistence_mae", "grid_mae", "continuous_mae",
         "vs_grid_pct", "beats_persistence"]].to_string(index=False))

    print("\n── 학습하지 않은 지평선 (연속 예측이 되는가) ──")
    unseen = scored[~scored.trained_on]
    print(unseen[["horizon", "n", "snapped_to", "persistence_mae", "grid_mae",
                  "continuous_mae", "vs_grid_pct", "beats_persistence"]].to_string(index=False))

    TABLES.mkdir(parents=True, exist_ok=True)
    scored.to_csv(TABLES / "a27_continuous_horizon.csv", index=False)
    verdict = {
        "trained_horizons": list(TRAIN_HORIZONS),
        "evaluated_horizons": [int(h) for h in scored.horizon],
        "continuous_beats_grid_on_trained":
            bool(scored[scored.trained_on].continuous_better.all()),
        "continuous_beats_persistence_everywhere":
            bool(scored.beats_persistence.all()),
        "worst_unseen_gap_pct": float(unseen.vs_grid_pct.min()) if len(unseen) else None,
        "test_start": str(start.date()),
        "runtime_sec": round(time.time() - started, 1),
        "note": ("학습하지 않은 지평선에서도 격자 반올림보다 낫고 persistence 를 이기면 "
                 "5분 단위 연속 예측을 쓸 근거가 된다. 재현은 다른 fold 에서 확인한다."),
    }
    (TABLES / "a27_manifest.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("\n── 판정 ──")
    for key, value in verdict.items():
        if key not in ("evaluated_horizons", "note"):
            print(f"  {key}: {value}")
    return scored, verdict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", type=int, default=5)
    parser.add_argument("--max-horizon", type=int, default=120)
    parser.add_argument("--sample", type=int, default=250_000)
    parser.add_argument("--days-back", type=int, default=1, help="test 로 쓸 날 (뒤에서 n번째)")
    args = parser.parse_args()
    run(args.step, args.max_horizon, args.sample, days_back=args.days_back)


if __name__ == "__main__":
    main()
