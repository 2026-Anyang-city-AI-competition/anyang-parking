#!/usr/bin/env python3

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.config import PARKING_DB, PARKING_ACCESS_RULES_CSV
from src.features.observation_grid import observation_grid

DB = PARKING_DB
RULES = PARKING_ACCESS_RULES_CSV

FREQ = "5min"
HORIZONS = [15, 30, 60, 120]


def build_series(raw):
    """실제 관측만 다음 5분 격자에 배치한다. 보간하지 않는다."""
    return observation_grid(raw)


def hhmm_to_min(x):
    if pd.isna(x):
        return None

    s = str(x).strip()

    if not s or s.lower() == "nan":
        return None

    try:
        h, m = s.split(":")
        h, m = int(h), int(m)
        if not (0 <= h <= 24 and 0 <= m < 60) or (h == 24 and m != 0):
            return None
        return h * 60 + m
    except Exception:
        return None


def accessible_at(rule, ts):
    kind = str(rule.get("access_rule", "")).strip()

    # 24시간 실제 출입 가능
    if kind in ("FREE_AFTER_FEE", "ACCESS_24H_PAID_24H"):
        return True

    # 실제 출입시간 미확인
    if kind == "UNKNOWN":
        return None

    # 안양2동노외
    if kind == "FIXED_ACCESS_10_22_DAILY":
        start = 10 * 60
        end = 22 * 60

    elif kind == "SAME_AS_FEE_HOURS":
        day = ts.weekday()
        prefix = "weekday" if day <= 4 else "saturday" if day == 5 else "sunday"
        status = str(rule.get(prefix + "_access_status", "")).strip()
        if status == "closed":
            return False
        if status == "unknown":
            return None

        if day <= 4:  # 월~금
            start = hhmm_to_min(
                rule.get("weekday_access_start")
            )
            end = hhmm_to_min(
                rule.get("weekday_access_end")
            )

        elif day == 5:  # 토
            start = hhmm_to_min(
                rule.get("saturday_access_start")
            )
            end = hhmm_to_min(
                rule.get("saturday_access_end")
            )

        else:  # 일
            start = hhmm_to_min(
                rule.get("sunday_access_start")
            )
            end = hhmm_to_min(
                rule.get("sunday_access_end")
            )

        # 빈 시간은 미확인이다. 휴무는 *_access_status=closed로만 확정한다.
        if start is None or end is None:
            return None

    else:
        return None

    pos = ts.hour * 60 + ts.minute

    if start == end:
        return None

    if end > start:
        return start <= pos < end

    # 자정을 넘기는 운영시간
    return pos >= start or pos < end

def main():

    # --------------------------------------------------
    # LOAD
    # --------------------------------------------------

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

    obs = pd.read_sql(
        """
        SELECT parking_id, ts_kst, cell_cnt, park_count
        FROM obs
        """,
        con,
    )

    con.close()

    rules = pd.read_csv(RULES)
    if rules["parking_id"].isna().any() or rules["parking_id"].duplicated().any():
        raise ValueError("출입 규칙의 parking_id가 비어 있거나 중복됩니다.")

    obs["ts_kst"] = pd.to_datetime(
        obs["ts_kst"],
        format="mixed",
    )

    # 68개 predict_ok만 사용
    active = rules[
        rules["predict_ok"] == 1
    ].copy()

    active_ids = set(
        active["parking_id"].astype(int)
    )

    obs = obs[
        obs["parking_id"].isin(active_ids)
    ].copy()

    rule_map = {
        int(r["parking_id"]): r
        for r in active.to_dict("records")
    }

    name_map = dict(
        zip(
            active["parking_id"].astype(int),
            active["name"],
        )
    )

    print("=== A19 Persistence baseline ===")
    print("예측 대상:", obs["parking_id"].nunique())
    print(
        "기간:",
        obs["ts_kst"].min(),
        "~",
        obs["ts_kst"].max(),
    )
    print()

    # --------------------------------------------------
    # 평가 pair 생성
    # --------------------------------------------------

    pairs = []

    for pid, raw in obs.groupby("parking_id"):

        raw = raw.sort_values("ts_kst")
        g = build_series(raw)

        rule = rule_map[pid]

        for H in HORIZONS:

            steps = H // 5

            d = g.copy()

            # 현재 시점 t
            d["current_occ"] = d["occ"]

            # 실제 t+H
            d["actual_occ"] = d["occ"].shift(-steps)

            # Persistence
            d["pred_occ"] = d["current_occ"]

            # --------------------------------------------------
            # t ~ t+H 사이 전체가 연속인지 검사
            #
            # 예: H=60이면
            # t, t+5, ... t+60
            # 13개가 전부 있어야 함
            # --------------------------------------------------

            valid = d["occ"].notna()

            continuous = (
                valid.iloc[::-1]
                .rolling(
                    window=steps + 1,
                    min_periods=steps + 1,
                )
                .sum()
                .eq(steps + 1)
                .iloc[::-1]
            )

            d["continuous"] = continuous

            d = d[
                d["continuous"]
                & d["current_occ"].notna()
                & d["actual_occ"].notna()
            ].copy()

            if d.empty:
                continue

            d = d.reset_index()

            d["parking_id"] = pid
            d["name"] = name_map.get(pid, "")
            d["horizon"] = H

            # target timestamp
            d["target_time"] = (
                d["ts_kst"]
                + pd.Timedelta(minutes=H)
            )

            d["weekday_group"] = np.where(
                d["target_time"].dt.weekday >= 5,
                "weekend",
                "weekday",
            )

            # 실제 도착시점 출입 가능 여부
            access = []

            for ts in d["target_time"]:
                access.append(
                    accessible_at(rule, ts)
                )

            d["accessible"] = access

            d["abs_error"] = (
                d["actual_occ"]
                - d["pred_occ"]
            ).abs()

            d["sq_error"] = (
                d["actual_occ"]
                - d["pred_occ"]
            ) ** 2

            pairs.append(
                d[
                    [
                        "parking_id",
                        "name",
                        "ts_kst",
                        "target_time",
                        "horizon",
                        "weekday_group",
                        "accessible",
                        "current_occ",
                        "actual_occ",
                        "pred_occ",
                        "abs_error",
                        "sq_error",
                    ]
                ]
            )

    if not pairs:
        raise RuntimeError("평가 가능한 pair가 없습니다.")

    ev = pd.concat(
        pairs,
        ignore_index=True,
    )

    # --------------------------------------------------
    # 전체 horizon 성능
    # --------------------------------------------------

    overall = (
        ev.groupby("horizon")
        .agg(
            n=("abs_error", "size"),
            mae=("abs_error", "mean"),
            rmse=(
                "sq_error",
                lambda x: np.sqrt(x.mean()),
            ),
        )
        .reset_index()
    )

    # --------------------------------------------------
    # 평일 / 주말
    # --------------------------------------------------

    daytype = (
        ev.groupby(
            ["horizon", "weekday_group"]
        )
        .agg(
            n=("abs_error", "size"),
            mae=("abs_error", "mean"),
            rmse=(
                "sq_error",
                lambda x: np.sqrt(x.mean()),
            ),
        )
        .reset_index()
    )

    # --------------------------------------------------
    # 실제 출입 가능 여부
    #
    # UNKNOWN은 별도 unknown
    # --------------------------------------------------

    ev["access_group"] = ev["accessible"].map(
        {
            True: "accessible",
            False: "closed",
        }
    ).fillna("unknown")

    access_perf = (
        ev.groupby(
            ["horizon", "access_group"]
        )
        .agg(
            n=("abs_error", "size"),
            mae=("abs_error", "mean"),
            rmse=(
                "sq_error",
                lambda x: np.sqrt(x.mean()),
            ),
        )
        .reset_index()
    )

    # --------------------------------------------------
    # 주차장별 × horizon
    # --------------------------------------------------

    per_lot = (
        ev.groupby(
            [
                "parking_id",
                "name",
                "horizon",
            ]
        )
        .agg(
            n=("abs_error", "size"),
            mae=("abs_error", "mean"),
            rmse=(
                "sq_error",
                lambda x: np.sqrt(x.mean()),
            ),
        )
        .reset_index()
    )

    # --------------------------------------------------
    # 출입가능한 시점만 따로
    # 실제 서비스에서 가장 중요한 baseline
    # --------------------------------------------------

    service_ev = ev[
        ev["accessible"] == True
    ].copy()

    service = (
        service_ev.groupby("horizon")
        .agg(
            n=("abs_error", "size"),
            mae=("abs_error", "mean"),
            rmse=(
                "sq_error",
                lambda x: np.sqrt(x.mean()),
            ),
        )
        .reset_index()
    )

    # --------------------------------------------------
    # SAVE
    # --------------------------------------------------

    out_dir = ROOT / "reports/tables"
    out_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    ev.to_csv(
        out_dir / "a19_persistence_pairs.csv",
        index=False,
        encoding="utf-8-sig",
    )

    overall.to_csv(
        out_dir / "a19_persistence_overall.csv",
        index=False,
        encoding="utf-8-sig",
    )

    daytype.to_csv(
        out_dir / "a19_persistence_daytype.csv",
        index=False,
        encoding="utf-8-sig",
    )

    access_perf.to_csv(
        out_dir / "a19_persistence_access.csv",
        index=False,
        encoding="utf-8-sig",
    )

    per_lot.to_csv(
        out_dir / "a19_persistence_per_lot.csv",
        index=False,
        encoding="utf-8-sig",
    )

    service.to_csv(
        out_dir / "a19_persistence_service.csv",
        index=False,
        encoding="utf-8-sig",
    )

    # --------------------------------------------------
    # PRINT
    # --------------------------------------------------

    print("=== 전체 Persistence ===")
    print(
        overall.to_string(
            index=False,
            float_format=lambda x: f"{x:.3f}",
        )
    )

    print()
    print("=== 평일 / 주말 ===")
    print(
        daytype.to_string(
            index=False,
            float_format=lambda x: f"{x:.3f}",
        )
    )

    print()
    print("=== 출입 상태별 ===")
    print(
        access_perf.to_string(
            index=False,
            float_format=lambda x: f"{x:.3f}",
        )
    )

    print()
    print("=== 실제 출입 가능 시점 Persistence ===")
    print(
        service.to_string(
            index=False,
            float_format=lambda x: f"{x:.3f}",
        )
    )

    print()
    print("평가 pair 총합:", len(ev))
    print(
        "출입가능 pair:",
        len(service_ev),
    )

    print()
    print("저장 완료:", out_dir)


if __name__ == "__main__":
    main()