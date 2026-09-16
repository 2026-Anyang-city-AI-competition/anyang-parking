"""검증된 예측 시간창만 허용한다. 미검증·불량 설정에서는 예측을 차단한다."""
import csv
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from src.config import PARKING_DB, PREDICTION_AVAILABILITY_CSV
from src.serve.access_rules import ValidationResult, _issue, _load_lots, _parse_windows, DAY_GROUPS, day_group_for
from src.serve.access_check import is_korean_holiday

FIELDS = ("parking_id", "day_group", "prediction_windows", "status", "reason",
          "evidence_report", "evaluated_at")
STATUSES = {"available", "frozen", "evaluation_pending", "anomaly", "dead_feed"}
KST = timezone(timedelta(hours=9))


class PredictionGate:
    def __init__(self, path=PREDICTION_AVAILABILITY_CSV, parking_db=PARKING_DB):
        self.path, self.parking_db = Path(path), Path(parking_db)
        self._lock = threading.RLock()
        self._rules = {}
        self._status = {"status": "not_loaded", "rules_loaded": 0, "errors": []}
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
            result = ValidationResult(self.path)
            lots = _load_lots(self.parking_db, result)
            indexed = {}
            try:
                with self.path.open(encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    if tuple(reader.fieldnames or ()) != FIELDS:
                        _issue(result, 1, "header", "invalid_header", "예측 게이트 컬럼이 다릅니다")
                    else:
                        for line, raw in enumerate(reader, 2):
                            if raw.get(None):
                                _issue(result, line, "row", "extra_values", "값이 너무 많습니다")
                            row = {k: (v or "").strip() for k, v in raw.items() if k is not None}
                            try:
                                pid = int(row["parking_id"])
                                if lots is not None and pid not in lots:
                                    raise ValueError
                                date.fromisoformat(row["evaluated_at"])
                            except ValueError:
                                _issue(result, line, "row", "invalid_id_or_date", "ID 또는 평가일이 잘못됐습니다")
                                continue
                            if row["day_group"] not in DAY_GROUPS or row["status"] not in STATUSES:
                                _issue(result, line, "row", "invalid_enum", "요일 또는 상태가 잘못됐습니다")
                            if not row["reason"] or not row["evidence_report"]:
                                _issue(result, line, "row", "missing_evidence", "진단 사유와 근거가 필요합니다")
                            windows = _parse_windows(row["prediction_windows"], result, line,
                                                     "prediction_windows", False)
                            if row["status"] == "available" and not windows:
                                _issue(result, line, "prediction_windows", "missing_windows", "허용시간이 필요합니다")
                            if row["status"] in {"dead_feed", "evaluation_pending"} and windows:
                                _issue(result, line, "prediction_windows", "status_conflict", "미검증·고정 피드는 허용시간을 비워야 합니다")
                            key = (pid, row["day_group"])
                            if key in indexed:
                                _issue(result, line, "row", "duplicate_rule", "같은 ID·요일 규칙이 중복됩니다")
                            indexed[key] = (tuple(windows or ()), row["status"], row["reason"])
            except (OSError, UnicodeError, csv.Error):
                _issue(result, 0, "file", "unreadable", "게이트 파일을 읽을 수 없습니다")
            # 불량 갱신 때는 이전 허용 규칙도 끈다. 확률 노출은 fail-closed이다.
            self._rules = indexed if result.valid else {}
            self._status = {"status": "invalid" if not result.valid else "ready" if indexed else "empty",
                            "rules_loaded": len(self._rules),
                            "errors": sorted({e.code for e in result.errors})}
            return self.status()

    def status(self):
        with self._lock:
            return {**self._status, "errors": list(self._status["errors"])}

    def check(self, pid, observed_at, arrival_at):
        with self._lock:
            for value in (observed_at, arrival_at):
                value = value.replace(tzinfo=KST) if value.tzinfo is None else value.astimezone(KST)
                group = day_group_for(value, is_holiday=is_korean_holiday(value.date()))
                rule = self._rules.get((int(pid), group))
                if rule is None:
                    return {"allowed": False, "status": "evaluation_pending", "reason": "no_verified_window"}
                windows, status, reason = rule
                minute = value.hour * 60 + value.minute + value.second / 60
                if not any(start <= minute < end for start, end in windows):
                    return {"allowed": False, "status": status if status != "available" else "unavailable",
                            "reason": reason}
            return {"allowed": True, "status": "available", "reason": "verified_window"}

    def validity_mask(self, parking_ids, observation_times, target_times):
        """학습·평가에서 재사용할 동일 게이트 마스크. 세 입력의 길이는 같아야 한다."""
        if not (len(parking_ids) == len(observation_times) == len(target_times)):
            raise ValueError("마스크 입력 길이가 다릅니다")
        return [self.check(pid, observed, target)["allowed"]
                for pid, observed, target in zip(parking_ids, observation_times, target_times)]
