#!/usr/bin/env python3
"""
U4/U8-6 · 순위 민감도 — 예측이 실제로 추천 순위를 바꾸는가.

★ 실험 설계: 도보·차 ETA·요금을 **고정**하고 점유율 출처만 바꾼다
  (ML p50 vs persistence occ_now). 그래야 순위 변화가 예측 때문임이 분리된다.
  이 설계 덕에 카카오·TMAP 호출이 0 이다 — 쿼터를 쓰지 않는다.

  python3 src/analysis/u4_rank_sensitivity.py
"""
import sys, pickle, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
from src.serve.predictor import _lazy, oprtime_features
from src.serve.walking import haversine_m
from src.serve.fare import calc_fare, resolve_type
from src.serve.candidates import _labeled_lots

FULL = 90.0
DESTS = [("안양시청", 37.394259, 126.956861), ("석수역", 37.435093, 126.902321),
         ("범계역", 37.389784, 126.950783), ("안양역", 37.401800, 126.922600),
         ("평촌역", 37.394400, 126.963900), ("인덕원역", 37.401600, 126.976000)]
HOURS = list(range(0, 24, 2))
HORIZONS = (15, 30, 60, 120)
RADIUS_M = 2000


def rank(lots, occ, axis):
    """만차(>=90%p)는 뒤로 밀고, 그 다음 axis 로 정렬한다."""
    keyed = []
    for d in lots:
        o = occ.get(d["parking_id"])
        full = 1 if (o is not None and o >= FULL) else 0
        keyed.append((full, d["_fare"] if axis == "fare" else d["_walk"], d["parking_id"]))
    return [k[2] for k in sorted(keyed)]


def main():
    load, series, feats, PID, INTX = _lazy()
    obs, L, dead0 = load()
    d = feats(series(obs), L)
    b = pickle.load(open(ROOT / "data/processed/predictor.pkl", "rb"))
    models, F, dead = b["models"], b["feat_cols"], set(b["dead"])
    meta = {r["parking_id"]: r for r in L.to_dict("records")}
    # ★ a16 의 load() 가 주는 L 에는 좌표가 없다. 좌표는 candidates 쪽에 있다.
    coords = {c["parking_id"]: c for c in _labeled_lots()}
    d = d[~d.parking_id.isin(dead)]

    ut = d.ts_kst.dropna().sort_values().unique()
    cut = pd.Timestamp(ut[int(len(ut) * .8)])
    te_times = pd.Series(ut[ut >= cut.to_datetime64()])

    rows = []
    for dname, dlat, dlon in DESTS:
        near = []
        for r in L.to_dict("records"):
            c = coords.get(r["parking_id"])
            if r["parking_id"] in dead or not c or c.get("lat") is None:
                continue
            if haversine_m(dlat, dlon, c["lat"], c["lng"]) <= RADIUS_M:
                near.append({**r, "lat": c["lat"], "lng": c["lng"],
                             "grade": r.get("grade", c.get("grade"))})
        if len(near) < 3:
            continue
        for r in near:
            r["_walk"] = haversine_m(dlat, dlon, r["lat"], r["lng"])   # 고정 변수
        pids = {r["parking_id"] for r in near}
        for H in HORIZONS:
            k = H // 5
            t = d.copy()
            t["nx"] = t.groupby("parking_id")["occ"].shift(-k)
            t = t.dropna(subset=["nx"] + [c for c in INTX
                                          if c not in ("parking_id_cat", "pid_we")])
            for hh in HOURS:
                cand = te_times[te_times.dt.hour == hh]
                if not len(cand):
                    continue
                ts = pd.Timestamp(cand.iloc[len(cand) // 2])
                snap = t[(t.ts_kst == ts) & t.parking_id.isin(pids)]
                if len(snap) < 3:
                    continue
                tgt = ts + pd.Timedelta(minutes=H)
                op = {}
                for r in near:
                    op[r["parking_id"]] = oprtime_features(meta.get(r["parking_id"], {}),
                                                           tgt.to_pydatetime())
                    f = calc_fare({"type": resolve_type(r.get("name"), r.get("div")),
                                   "name": r.get("name"), "grade": r.get("grade"),
                                   "wdays_start": r.get("wdays_start"),
                                   "wdays_end": r.get("wdays_end"),
                                   "wend_start": r.get("wend_start"),
                                   "wend_end": r.get("wend_end")},
                                  tgt.to_pydatetime(), 60)
                    r["_fare"] = f["total"] if f["total"] is not None else 10 ** 9
                sub = snap.copy()
                for c in ("is_operating", "min_to_open", "min_to_close", "is_free_now"):
                    sub["opr_" + c] = [op[p][c] for p in sub.parking_id]
                pml = np.clip(sub.occ_now.values + models[(H, .5)].predict(sub[F]), 0, 120)
                occ_ml = dict(zip(sub.parking_id, pml))
                occ_pe = dict(zip(sub.parking_id, sub.occ_now.values))
                lots = [r for r in near if r["parking_id"] in occ_ml]
                if len(lots) < 3:
                    continue
                state = "운영 중" if np.mean([op[r["parking_id"]]["is_operating"]
                                            for r in lots]) >= .5 else "운영 외"
                for axis in ("fare", "walk"):
                    a1, a2 = rank(lots, occ_ml, axis), rank(lots, occ_pe, axis)
                    rows.append({"dest": dname, "horizon": H, "hour": hh, "state": state,
                                 "axis": axis, "n_lots": len(lots),
                                 "changed": int(a1 != a2),
                                 "top1_changed": int(a1[0] != a2[0])})
    df = pd.DataFrame(rows)
    out = ["# U4 · 순위 민감도 — 예측이 추천 순위를 바꾸는가", "",
           "도보·차 ETA·요금을 고정하고 **점유율 출처만** ML p50 / persistence occ_now 로 바꿨다.",
           f"목적지 {len(DESTS)}곳 × 시각 {len(HOURS)}개(0~23시 2시간 간격) × horizon "
           f"{len(HORIZONS)}개 × 축 2개 · 질의 {len(df):,}건 · 반경 {RADIUS_M}m · 죽은 피드 제외.",
           "**카카오·TMAP 호출 0건** — 경로는 고정 변수라 재호출이 불필요하다.", "",
           "## horizon × 운영 상태", "",
           "| horizon | 구간 | 질의 | 순위 변화율 | 1위 변화율 |", "|---:|---|---:|---:|---:|"]
    for (H, st), g in df.groupby(["horizon", "state"]):
        out.append(f"| {H} | {st} | {len(g):,} | {g.changed.mean()*100:.1f}% | "
                   f"{g.top1_changed.mean()*100:.1f}% |")
    out += ["", "## 정렬 축별", "", "| 축 | 질의 | 순위 변화율 | 1위 변화율 |", "|---|---:|---:|---:|"]
    for ax, g in df.groupby("axis"):
        out.append(f"| {'요금순' if ax=='fare' else '도보순'} | {len(g):,} | "
                   f"{g.changed.mean()*100:.1f}% | {g.top1_changed.mean()*100:.1f}% |")
    (ROOT / "reports/tables/u4_rank_sensitivity.md").write_text("\n".join(out) + "\n",
                                                                encoding="utf-8")
    print("\n".join(out[6:]))
    print("\n→ reports/tables/u4_rank_sensitivity.md")


if __name__ == "__main__":
    main()
