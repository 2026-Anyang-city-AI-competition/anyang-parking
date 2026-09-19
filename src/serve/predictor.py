#!/usr/bin/env python3
"""U11과 동일한 Δ 분위 모델, 양성 클래스 확률, 검증된 구간 노출 정책."""
import pickle
import sqlite3
import sys
import threading
import time
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.features.temporal import oprtime_features
from src.serve.accuracy_gate import AccuracyGate
from src.serve.calibration import CalibrationTable

MODEL_PATH = ROOT / "data/processed/predictor.pkl"
DB_PATH = ROOT / "data/raw/parking.db"
KST = timezone(timedelta(hours=9))
STALE_MIN = 20
LIVE_HISTORY_HOURS = 4
FULL_THRESHOLD = 90.0
QUANTILES = (.1, .5, .9)
# 학습된 지평선 격자. u11_evaluate.HORIZONS 와 **반드시 같아야 한다** —
# 여기에만 넣으면 없는 모델을 부르고, 저기에만 넣으면 학습해 두고 안 쓴다.
HORIZ_GRID = (15, 30, 60, 120, 180, 240, 360)


def _logodds(p):
    q = np.clip(np.asarray(p, dtype=float), 1e-6, 1-1e-6)
    return np.log(q/(1-q))


def _lazy():
    from src.analysis.a16_stratified_weekend import load, series, feats, PID, INTX
    return load, series, feats, PID, INTX


class Predictor:
    def __init__(self, path=MODEL_PATH, accuracy_gate=None):
        self.ok = False
        self.dead, self.hist = set(), {}
        self.path = Path(path)
        self._state_lock = threading.RLock()
        self._model_signature = None
        self.model_version = None
        self.model_loaded_at = None
        self.model_reload_error = None
        self.models, self.full_models, self.full_calibrators = {}, {}, {}
        self.feat_cols, self.meta, self.cqr, self.interval_gate = [], {}, {}, {}
        # 주차장 × 지평선 정확도 게이트. 모델 번들과 별개 파일이라 재학습 없이 갱신된다.
        self.accuracy_gate = accuracy_gate or AccuracyGate()
        self.accuracy_gate.refresh(force=True)
        # 확률을 숫자로 보여줘도 되는지. **순위 사용은 막지 않는다**(A25: 피해 0건).
        self.calibration = CalibrationTable()
        self.calibration.refresh(force=True)
        self._refresh_lock = threading.Lock()
        self._last_refresh_attempt = 0.0
        self.observation_at = None
        self.refreshed_at = None
        self.last_refresh_error = None
        self.refresh_count = 0
        self.prediction_ready_lots = None
        self.total_lots = None
        self.reload_model_if_changed(force=True)

    def reload_model_if_changed(self, force=False):
        """새 모델 파일을 완전히 읽은 뒤 한 번에 교체한다.

        배포 중 파일이 불완전하거나 손상됐으면 이전 정상 모델을 유지한다. 요청 처리
        스레드에서는 이 함수를 부르지 않고 백그라운드 갱신기만 호출한다.
        """
        try:
            stat = self.path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if not force and signature == self._model_signature:
                return False
            with self.path.open("rb") as f:
                b = pickle.load(f)
            models, full_models = b["models"], b["full_models"]
            feat_cols = b["feat_cols"]
            if not models or not full_models or not feat_cols:
                raise ValueError("모델 번들의 필수 항목이 비었습니다")
            with self._state_lock:
                self.models, self.full_models = models, full_models
                self.full_calibrators = b.get("full_calibrators", {})
                self.feat_cols = feat_cols
                self.dead, self.hist = set(b["dead"]), b["hist"]
                self.meta, self.cqr = b.get("meta", {}), b.get("cqr", {})
                self.total_lots = len(self.meta) or None
                self.interval_gate = b.get("interval_gate", {})
                self.model_version = b.get("model_version", "unverified")
                self._model_signature = signature
                self.model_loaded_at = datetime.now(KST).isoformat()
                self.model_reload_error = None
                self.ok = True
            return True
        except Exception as e:
            # 이전 정상 모델을 버리지 않는다. 상태에 오류만 남긴다.
            self.model_reload_error = type(e).__name__
            if not self.ok:
                print(f"[predictor] 예측 모델을 읽을 수 없습니다: {type(e).__name__}")
            return False

    def service_status(self):
        """API가 그대로 내보낼 수 있는 모델·관측 최신성 상태."""
        age = None
        if self.observation_at:
            try:
                observed = datetime.fromisoformat(self.observation_at)
                if observed.tzinfo is None:
                    observed = observed.replace(tzinfo=KST)
                age = max(0.0, (datetime.now(KST)-observed).total_seconds()/60)
            except (TypeError, ValueError):
                pass
        data_status = ("unavailable" if age is None else
                       "stale" if age > STALE_MIN else "fresh")
        return {
            "model_status": "ready" if self.ok else "unavailable",
            "model_version": getattr(self, "model_version", None) if self.ok else None,
            "model_loaded_at": getattr(self, "model_loaded_at", None),
            "model_reload_error": getattr(self, "model_reload_error", None),
            "data_status": data_status,
            "observation_at": self.observation_at,
            "observation_age_min": round(age, 1) if age is not None else None,
            "refreshed_at": self.refreshed_at,
            "refresh_error": self.last_refresh_error,
            "live_lots": self.total_lots-len(self.dead) if self.total_lots is not None else None,
            "fixed_feeds": len(self.dead),
            "prediction_ready_lots": self.prediction_ready_lots,
        }

    def refresh_from_db(self, path=DB_PATH, force=False, min_interval_seconds=30):
        """재학습 없이 최신 DB로 최근 lag/rolling 피처만 다시 만든다.

        모델 객체는 건드리지 않는다. API 요청마다 호출해도 실제 DB 읽기는 최대
        `min_interval_seconds`마다 한 번이고, 같은 최신 시각이면 즉시 끝난다.
        """
        now_mono = time.monotonic()
        if not force and now_mono-self._last_refresh_attempt < min_interval_seconds:
            return self.service_status()
        with self._refresh_lock:
            now_mono = time.monotonic()
            if not force and now_mono-self._last_refresh_attempt < min_interval_seconds:
                return self.service_status()
            self._last_refresh_attempt = now_mono
            try:
                with sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True) as db:
                    db.execute("BEGIN")
                    raw_max = db.execute("SELECT MAX(ts_kst) FROM obs").fetchone()[0]
                    if not raw_max:
                        raise ValueError("obs 테이블에 관측이 없습니다")
                    if not force and raw_max == self.observation_at:
                        self.last_refresh_error = None
                        return self.service_status()
                    latest = datetime.fromisoformat(raw_max)
                    cutoff = (latest-timedelta(hours=LIVE_HISTORY_HOURS)).isoformat()
                    obs = pd.read_sql_query(
                        "SELECT parking_id,ts_kst,park_count,cell_cnt FROM obs "
                        "WHERE ts_kst>=? ORDER BY parking_id,ts_kst", db, params=(cutoff,))
                    lots = pd.read_sql_query("SELECT * FROM lots", db)
                    ranges = pd.read_sql_query(
                        "SELECT parking_id,MIN(1.0*park_count/cell_cnt) AS lo,"
                        "MAX(1.0*park_count/cell_cnt) AS hi FROM obs "
                        "WHERE cell_cnt>0 GROUP BY parking_id", db)
                if obs.empty:
                    raise ValueError("최근 관측 구간이 비었습니다")
                obs = obs[(obs.cell_cnt > 0) & (obs.park_count >= 0)].copy()
                obs["occ"] = (100*obs.park_count/obs.cell_cnt).clip(0, 120)
                obs["ts_kst"] = (pd.to_datetime(obs.ts_kst, format="mixed")
                                  .dt.tz_localize(None).dt.ceil("5min"))
                parts = []
                for pid, group in obs.groupby("parking_id"):
                    series = (group.groupby("ts_kst").occ.last().asfreq("5min")
                              .rename("occ").reset_index())
                    series["parking_id"] = pid
                    parts.append(series)
                _, _, feats, _, _ = _lazy()
                frame = feats(pd.concat(parts, ignore_index=True), lots)
                new_hist = {int(pid): group.reset_index(drop=True)
                            for pid, group in frame.groupby("parking_id")}
                new_meta = {int(row["parking_id"]): row
                            for row in lots.to_dict("records")}
                new_dead = set(ranges.loc[(ranges.hi-ranges.lo) < .01, "parking_id"].astype(int))
                required = [c for c in self.feat_cols if not c.startswith("opr_")] if self.ok else []
                base_time = pd.Timestamp(latest).tz_localize(None)
                ready = 0
                for pid, group in new_hist.items():
                    if pid in new_dead:
                        continue
                    eligible = group[group.ts_kst <= base_time]
                    if eligible.empty or base_time-eligible.iloc[-1].ts_kst > pd.Timedelta(minutes=5):
                        continue
                    if not required or eligible.iloc[-1][required].notna().all():
                        ready += 1
                with getattr(self, "_state_lock", nullcontext()):
                    self.hist = new_hist
                    self.meta = new_meta
                    self.total_lots = len(lots)
                    self.dead = new_dead
                    self.prediction_ready_lots = ready
                    self.observation_at = raw_max
                    self.refreshed_at = datetime.now(KST).isoformat()
                    self.last_refresh_error = None
                    self.refresh_count += 1
            except Exception as e:
                self.last_refresh_error = f"{type(e).__name__}: {str(e)[:160]}"
            return self.service_status()

    def is_live(self, parking_id):
        with self._state_lock:
            return parking_id not in self.dead

    def _snapshot(self):
        """한 예측이 모델 교체 전후 객체를 섞어 읽지 않게 참조를 묶는다."""
        with self._state_lock:
            return {
                "ok": self.ok, "dead": self.dead, "hist": self.hist,
                "models": self.models, "full_models": self.full_models,
                "full_calibrators": self.full_calibrators, "feat_cols": self.feat_cols,
                "meta": self.meta, "cqr": self.cqr, "interval_gate": self.interval_gate,
                "model_version": self.model_version,
            }

    def _row(self, parking_id, target_time, h, histories=None):
        hist = (histories if histories is not None else self.hist).get(parking_id)
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
        state = self._snapshot()
        out = dict(p10=None, p50=None, p90=None, full_prob=None,
                   is_live=parking_id not in state["dead"], source=None,
                   interval_status="unverified",
                   model_version=state["model_version"] if state["ok"] else None,
                   accuracy_status=None, accuracy_reason=None,
                   full_prob_calibrated=None)
        out["model_horizon_min"] = None
        # 학습 범위를 넘는 요청을 120분 예측으로 가장하지 않는다.
        if not np.isfinite(horizon_min) or not 1 <= horizon_min <= max(HORIZ_GRID):
            out["source"] = "unsupported_horizon"
            out["interval_status"] = "unavailable"
            return out
        if not out["is_live"]:
            out["source"]="dead_feed"
            return out
        if not state["ok"]:
            out["source"]="no_model"
            return out
        h = min(HORIZ_GRID,key=lambda x:abs(x-horizon_min))
        out["model_horizon_min"] = h
        # 이 주차장이 이 지평선에서 맞히는지. 평균이 좋아도 크게 틀리는 곳이 있다.
        accuracy = self.accuracy_gate.check(parking_id, h)
        out["accuracy_status"] = accuracy["status"]
        if not accuracy["allowed"]:
            out["source"] = "lot_horizon_not_certified"
            out["interval_status"] = "unavailable"
            out["accuracy_reason"] = accuracy["reason"]
            return out
        # 관측 시각은 실제 horizon으로 찾고 모델만 가장 가까운 격자를 사용한다.
        row,occ = self._row(parking_id,target_time,horizon_min,state["hist"])
        if row is None:
            out["source"]="no_fresh_history"
            return out
        required = [c for c in state["feat_cols"] if not c.startswith("opr_")]
        if any(c not in row or pd.isna(row[c]) for c in required):
            out["source"]="missing_features"
            return out
        tt = pd.Timestamp(target_time)
        op = oprtime_features(state["meta"].get(parking_id,{}),tt.to_pydatetime())
        features = row[required].to_dict()
        features.update({"opr_"+k:v for k,v in op.items()})
        x = pd.DataFrame([features])[state["feat_cols"]]
        for c in ("parking_id_cat","pid_we"):
            if c in x:
                x[c]=x[c].astype("category")
        q=sorted(float(np.clip(occ+state["models"][(h,a)].predict(x)[0],0,120)) for a in QUANTILES)
        out["p50"]=q[1]
        # predict_proba[0,1]만 보정한다. 음성 클래스 [0,0]을 만차 확률로 쓰지 않는다.
        p=state["full_models"][h].predict_proba(x)[:,1]
        platt=state["full_calibrators"].get(h)
        if platt is not None:
            p=platt.predict_proba(_logodds(p).reshape(-1,1))[:,1]
        out["full_prob"]=float(p[0])
        # 보정이 어긋난 지평선에서는 순위에만 쓰고 수치는 화면에 내보내지 않는다.
        out["full_prob_calibrated"]=self.calibration.is_calibrated(h)
        # ★ 지역변수 이름을 `state` 로 쓰지 않는다 — 위의 `state = self._snapshot()`(dict)를
        #   덮어써서 `state["cqr"]` 가 문자열 인덱싱이 된다. 실제로 그렇게 깨져 있었다.
        op_state=("operating" if op["is_operating"] else "outside")+("|we" if tt.weekday()>=5 else "|wd")
        key=f"{h}|{op_state}"
        adj=state["cqr"].get(key)
        # 반올림된 horizon의 구간은 검증된 정확한 horizon인 경우에만 표시한다.
        if state["interval_gate"].get(key,False) and adj is not None and horizon_min == h:
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
