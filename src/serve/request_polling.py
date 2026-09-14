"""추천 요청 직전 parking.db 단건 폴링.

비공식 원천 API를 보호하기 위해 DB의 최신 관측이 5분 이내면 다시 호출하지
않는다. 같은 프로세스의 동시 요청은 lock으로 한 번의 폴링으로 합친다.
"""
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.collect.poll_parking import poll_once
from src.config import PARKING_DB, POLL_INTERVAL_SEC

KST = timezone(timedelta(hours=9))


class RequestPoller:
    def __init__(self, path=PARKING_DB, min_interval_seconds=POLL_INTERVAL_SEC):
        self.path = Path(path)
        self.min_interval_seconds = min_interval_seconds
        self._lock = threading.Lock()

    def _latest(self):
        try:
            with sqlite3.connect(f"file:{self.path}?mode=ro", uri=True) as db:
                return db.execute("SELECT MAX(ts_kst) FROM obs").fetchone()[0]
        except (OSError, sqlite3.Error):
            return None

    def _is_recent(self, value):
        if not value:
            return False
        try:
            observed = datetime.fromisoformat(value)
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=KST)
            return (datetime.now(KST)-observed).total_seconds() < self.min_interval_seconds
        except (TypeError, ValueError):
            return False

    def poll_if_due(self):
        latest = self._latest()
        if self._is_recent(latest):
            return {"status": "skipped", "attempted": False,
                    "reason": "recent_observation", "observation_at": latest,
                    "lots": None, "duration_ms": 0, "error": None}

        with self._lock:
            # lock을 기다리는 동안 앞 요청이 저장했을 수 있으므로 다시 검사한다.
            latest = self._latest()
            if self._is_recent(latest):
                return {"status": "skipped", "attempted": False,
                        "reason": "concurrent_request_completed", "observation_at": latest,
                        "lots": None, "duration_ms": 0, "error": None}
            started = time.monotonic()
            try:
                with sqlite3.connect(self.path, timeout=30) as db:
                    ts, count = poll_once(db)
                return {"status": "polled", "attempted": True, "reason": None,
                        "observation_at": ts, "lots": count,
                        "duration_ms": round((time.monotonic()-started)*1000), "error": None}
            except Exception as exc:
                # 추천 자체는 기존 DB로 계속한다. 원천 응답 본문이나 경로는 노출하지 않는다.
                return {"status": "failed", "attempted": True, "reason": "poll_failed",
                        "observation_at": latest, "lots": None,
                        "duration_ms": round((time.monotonic()-started)*1000),
                        "error": type(exc).__name__}

