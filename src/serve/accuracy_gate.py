#!/usr/bin/env python3
"""주차장 × 지평선 정확도 게이트 — 맞히는 곳에서만 예측을 내보낸다.

  from src.serve.accuracy_gate import AccuracyGate
  AccuracyGate().check(39, 60)   # -> {"allowed": True, "status": "certified", ...}

`prediction_gate.py` 와 짝이지만 묻는 것이 다르다.

  prediction_gate  값이 움직이는가 (경직·고정 피드·이상)
  accuracy_gate    맞히는가 (주차장 × 지평선 MAE)

둘 다 통과해야 예측이 나간다. 어느 쪽에 막히든 **위치·요금·실시간 관측은 그대로** 나간다.

★ fail-closed 다. 인증 행이 없으면 그 주차장·지평선의 예측은 막힌다.
  단, 파일 자체가 비어 있으면(아직 만들지 않았으면) 막지 않는다 — 없는 게이트로
  서비스를 통째로 끄지 않기 위해서다. 상태는 `status()` 로 드러난다.
"""
import csv
import threading
from pathlib import Path

from src.config import PREDICTION_ACCURACY_CSV

SERVED_STATUS = "certified"
FIELDS = ("parking_id", "horizon", "status", "mae_first", "mae_second", "mae_overall",
          "n_first", "n_second", "reason", "evidence_report", "evaluated_at")


class AccuracyGate:
    def __init__(self, path=PREDICTION_ACCURACY_CSV):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._rules = {}
        self._status = {"status": "not_loaded", "certified": 0, "blocked": 0, "errors": []}
        self._signature = object()

    def refresh(self, force=False):
        with self._lock:
            try:
                stat = self.path.stat()
                signature = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                signature = None
            if signature == self._signature and not force:
                return self.status()
            self._signature = signature
            rules, errors = {}, []
            if signature is None:
                self._rules = {}
                self._status = {"status": "unavailable", "certified": 0, "blocked": 0,
                                "errors": ["파일 없음"]}
                return self.status()
            try:
                with self.path.open(encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    if tuple(reader.fieldnames or ()) != FIELDS:
                        errors.append("invalid_header")
                    else:
                        for line, raw in enumerate(reader, 2):
                            try:
                                key = (int(raw["parking_id"]), int(raw["horizon"]))
                            except (TypeError, ValueError):
                                errors.append(f"line {line}: invalid_id_or_horizon")
                                continue
                            if key in rules:
                                errors.append(f"line {line}: duplicate")
                            rules[key] = (raw["status"], raw.get("reason") or "",
                                          raw.get("mae_overall") or "")
            except (OSError, UnicodeError, csv.Error):
                errors.append("unreadable")
            # 불량 파일로 잘못된 칸을 열지 않는다. 전부 버린다.
            self._rules = {} if errors else rules
            certified = sum(1 for v in self._rules.values() if v[0] == SERVED_STATUS)
            self._status = {
                "status": ("invalid" if errors else "ready" if self._rules else "empty"),
                "certified": certified,
                "blocked": len(self._rules) - certified,
                "errors": sorted(set(errors)),
            }
            return self.status()

    def status(self):
        with self._lock:
            return {**self._status, "errors": list(self._status["errors"])}

    def check(self, parking_id, horizon_min):
        """(주차장, 지평선) 이 예측을 내보내도 되는 칸인지."""
        with self._lock:
            if not self._rules:
                # 게이트를 아직 만들지 않았다. 막지 않되 검증되지 않았음을 알린다.
                return {"allowed": True, "status": "gate_absent",
                        "reason": "정확도 게이트가 아직 없습니다"}
            rule = self._rules.get((int(parking_id), int(horizon_min)))
            if rule is None:
                return {"allowed": False, "status": "not_evaluated",
                        "reason": "이 주차장·예측시간은 평가된 적이 없습니다"}
            status, reason, mae = rule
            if status == SERVED_STATUS:
                return {"allowed": True, "status": status, "reason": reason, "mae": mae}
            return {"allowed": False, "status": status, "reason": reason, "mae": mae}

    def certified_horizons(self, parking_id):
        """그 주차장이 예측을 제공할 수 있는 지평선 목록. UI 안내에 쓴다."""
        with self._lock:
            return sorted(h for (pid, h), (status, _, _) in self._rules.items()
                          if pid == int(parking_id) and status == SERVED_STATUS)
