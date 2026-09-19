#!/usr/bin/env python3
"""만차확률 보정표 — 확률을 **숫자로** 보여줘도 되는 지평선.

★★ 순위 사용 여부와는 다른 문제다. 보정이 어긋나도 강등에서 빼지 않는다 —
   A25 측정에서 모든 지평선의 피해가 0건이고, persistence 대비 기여는 오히려
   지평선이 길수록 커졌다(360분 +37). 확률은 **순위를 매기기에는 충분하지만
   숫자로 인용하기에는 부족하다**. 이 표는 그 둘을 가른다.
"""
import csv
import threading
from pathlib import Path

from src.config import PROBABILITY_CALIBRATION_CSV

CALIBRATED = "calibrated"


class CalibrationTable:
    def __init__(self, path=PROBABILITY_CALIBRATION_CSV):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._rows = {}
        self._status = {"status": "not_loaded", "calibrated": [], "uncalibrated": []}
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
            rows = {}
            if signature is not None:
                try:
                    with self.path.open(encoding="utf-8-sig", newline="") as handle:
                        for raw in csv.DictReader(handle):
                            try:
                                rows[int(raw["horizon"])] = raw["status"]
                            except (TypeError, ValueError, KeyError):
                                continue
                except (OSError, UnicodeError, csv.Error):
                    rows = {}
            self._rows = rows
            self._status = {
                "status": "ready" if rows else "unavailable",
                "calibrated": sorted(h for h, s in rows.items() if s == CALIBRATED),
                "uncalibrated": sorted(h for h, s in rows.items() if s != CALIBRATED),
            }
            return self.status()

    def status(self):
        with self._lock:
            return {k: (list(v) if isinstance(v, list) else v)
                    for k, v in self._status.items()}

    def is_calibrated(self, horizon_min):
        """표가 없으면 None — "모른다"이지 "괜찮다"가 아니다."""
        with self._lock:
            if not self._rows:
                return None
            return self._rows.get(int(horizon_min)) == CALIBRATED
