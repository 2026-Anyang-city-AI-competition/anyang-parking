"""예측 서비스 상태를 요청 경로 밖에서 주기적으로 갱신한다."""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone


LOG = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


class BackgroundRefresher:
    """모델 번들·최신 관측·게이트를 한 작업으로 갱신한다.

    일반 요청은 이미 준비된 스냅샷을 바로 읽는다. 단, 요청 직전 폴러가 새 관측을
    저장한 경우에는 ``refresh(force=True)``를 기다려 그 관측으로 예측한다.
    """

    def __init__(self, predictor, access_rules, prediction_gate, interval_seconds=None):
        self.predictor = predictor
        self.access_rules = access_rules
        self.prediction_gate = prediction_gate
        self.interval_seconds = max(1.0, float(
            interval_seconds if interval_seconds is not None
            else os.getenv("BACKGROUND_REFRESH_SEC", "15")
        ))
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task = None
        self._stopping = False
        self._last_service = None
        self._last_error = None
        self._last_completed_at = None
        self._cycles = 0

    async def start(self):
        await self.refresh(force=True)
        self._task = asyncio.create_task(self._run(), name="prediction-background-refresh")

    async def stop(self):
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            await self._task
            self._task = None

    def trigger(self):
        """다음 주기까지 기다리지 말고 가능한 즉시 갱신한다."""
        self._wake.set()

    async def refresh(self, force=False):
        """동시 갱신을 하나로 합치고 무거운 동기 작업은 스레드에서 실행한다."""
        async with self._lock:
            try:
                reload_model = getattr(self.predictor, "reload_model_if_changed", None)
                model_changed = bool(await asyncio.to_thread(reload_model)) if reload_model else False
                service = await asyncio.to_thread(
                    self.predictor.refresh_from_db, force=force or model_changed)
                await asyncio.to_thread(self.access_rules.refresh, force=force)
                await asyncio.to_thread(self.prediction_gate.refresh, force=force)
                accuracy_gate = getattr(self.predictor, "accuracy_gate", None)
                if accuracy_gate is not None:
                    await asyncio.to_thread(accuracy_gate.refresh, force=force)
                calibration = getattr(self.predictor, "calibration", None)
                if calibration is not None:
                    await asyncio.to_thread(calibration.refresh, force=force)
                self._last_service = service
                self._last_error = None
                self._last_completed_at = datetime.now(KST).isoformat()
                self._cycles += 1
                return service
            except Exception as exc:  # 이전 정상 스냅샷으로 서비스는 계속한다.
                self._last_error = type(exc).__name__
                LOG.exception("background refresh failed")
                return self.service_status()

    def service_status(self):
        if self._last_service is not None:
            return dict(self._last_service)
        status = getattr(self.predictor, "service_status", None)
        return status() if status is not None else {}

    def status(self):
        return {
            "running": self._task is not None and not self._task.done(),
            "interval_seconds": self.interval_seconds,
            "cycles": self._cycles,
            "last_completed_at": self._last_completed_at,
            "last_error": self._last_error,
        }

    async def _run(self):
        while not self._stopping:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if self._stopping:
                break
            await self.refresh()
