#!/usr/bin/env python3
"""U11과 동일한 Δ 분위 모델, 양성 클래스 확률, 검증된 구간 노출 정책."""
import pickle
import sys
from datetime import timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.features.temporal import oprtime_features

MODEL_PATH = ROOT / "data/processed/predictor.pkl"
KST = timezone(timedelta(hours=9))
FULL_THRESHOLD = 90.0
QUANTILES = (.1, .5, .9)
HORIZ_GRID = (15, 30, 60, 120)


def _logodds(p):
    q = np.clip(np.asarray(p, dtype=float), 1e-6, 1-1e-6)
    return np.log(q/(1-q))


def _lazy():
    from src.analysis.a16_stratified_weekend import load, series, feats, PID, INTX
    return load, series, feats, PID, INTX


class Predictor:
    def __init__(self, path=MODEL_PATH):
        self.ok = False
        self.dead, self.hist = set(), {}
        try:
            with open(path, "rb") as f:
                b = pickle.load(f)
            self.models, self.full_models = b["models"], b["full_models"]
            self.full_calibrators = b.get("full_calibrators", {})
            self.feat_cols = b["feat_cols"]
            self.dead, self.hist = set(b["dead"]), b["hist"]
            self.meta, self.cqr = b.get("meta", {}), b.get("cqr", {})
            self.interval_gate = b.get("interval_gate", {})
            self.model_version = b.get("model_version", "unverified")
            self.ok = True
        except (OSError, KeyError, pickle.UnpicklingError) as e:
            print(f"[predictor] 예측 모델을 읽을 수 없습니다: {type(e).__name__}")

    def is_live(self, parking_id):
        return parking_id not in self.dead

    def _row(self, parking_id, target_time, h):
        hist = self.hist.get(parking_id)
        if hist is None or not len(hist):
            return None, None
        base = pd.Timestamp(target_time).tz_localize(None)-pd.Timedelta(minutes=h)
        i = hist.ts_kst.searchsorted(base, side="right")-1
        if i < 0:
            return None, None
        row = hist.iloc[i]
        # 오래된 관측으로 미래의 확률을 가장하지 않는다.
        if base-row.ts_kst > pd.Timedelta(minutes=5) or pd.isna(row.get("occ_now")):
            return None, None
        return row, float(row.occ_now)

    def predict(self, parking_id, target_time, horizon_min):
        out = dict(p10=None, p50=None, p90=None, full_prob=None,
                   is_live=self.is_live(parking_id), source=None,
                   interval_status="unverified", model_version=self.model_version if self.ok else None)
        if not out["is_live"]:
            out["source"]="dead_feed"
            return out
        if not self.ok:
            out["source"]="no_model"
            return out
        h = min(HORIZ_GRID,key=lambda x:abs(x-horizon_min))
        # 관측 시각은 실제 horizon으로 찾고 모델만 가장 가까운 격자를 사용한다.
        row,occ = self._row(parking_id,target_time,horizon_min)
        if row is None:
            out["source"]="no_fresh_history"
            return out
        required = [c for c in self.feat_cols if not c.startswith("opr_")]
        if any(c not in row or pd.isna(row[c]) for c in required):
            out["source"]="missing_features"
            return out
        tt = pd.Timestamp(target_time)
        op = oprtime_features(self.meta.get(parking_id,{}),tt.to_pydatetime())
        features = row[required].to_dict()
        features.update({"opr_"+k:v for k,v in op.items()})
        x = pd.DataFrame([features])[self.feat_cols]
        for c in ("parking_id_cat","pid_we"):
            if c in x:
                x[c]=x[c].astype("category")
        q=sorted(float(np.clip(occ+self.models[(h,a)].predict(x)[0],0,120)) for a in QUANTILES)
        out["p50"]=q[1]
        # predict_proba[0,1]만 보정한다. 음성 클래스 [0,0]을 만차 확률로 쓰지 않는다.
        p=self.full_models[h].predict_proba(x)[:,1]
        platt=self.full_calibrators.get(h)
        if platt is not None:
            p=platt.predict_proba(_logodds(p).reshape(-1,1))[:,1]
        out["full_prob"]=float(p[0])
        state=("operating" if op["is_operating"] else "outside")+("|we" if tt.weekday()>=5 else "|wd")
        key=f"{h}|{state}"
        adj=self.cqr.get(key)
        # 반올림된 horizon의 구간은 검증된 정확한 horizon인 경우에만 표시한다.
        if self.interval_gate.get(key,False) and adj is not None and horizon_min == h:
            lo,hi=max(0,q[0]-adj),min(120,q[2]+adj)
            if lo<=hi:
                out.update(p10=lo,p90=hi,interval_status="pass")
        else:
            out["interval_status"]="withheld"
        out["source"]="ml"
        return out


def train(save=True):
    if not save:
        raise ValueError("U11 평가는 산출물과 함께 실행해야 합니다")
    from src.models.u11_evaluate import run
    return run()


if __name__=="__main__":
    train()
