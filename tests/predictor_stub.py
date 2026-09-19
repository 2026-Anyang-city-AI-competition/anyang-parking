"""`Predictor.__new__` 로 만든 최소 객체를 한 곳에서 조립한다.

`predict()` 가 의존성을 하나 늘릴 때마다 세 개의 테스트가 따로 깨졌다
(`accuracy_gate` · `calibration` · `_state_lock` 로 네 번). 매번 프로덕션을
`getattr` 로 무르게 만들면 실제 설정 누락이 숨으므로, 조립 책임을 여기로 모은다.

새 의존성이 생기면 **이 파일만** 고치면 된다.
"""
import threading

from src.serve.predictor import Predictor


class AllowAllAccuracy:
    """정확도 게이트 통과 스텁. 다른 검증에 끼어들지 않는다."""

    def check(self, parking_id, horizon_min):
        return {"allowed": True, "status": "certified", "reason": "test"}


class BlockAllAccuracy:
    """정확도 게이트 차단 스텁. 격자 선택 이후 경로만 볼 때 쓴다."""

    def check(self, parking_id, horizon_min):
        return {"allowed": False, "status": "not_evaluated", "reason": "test"}


class StubCalibration:
    """보정표 스텁. 확률 수치 표시 여부만 정하고 계산에는 끼어들지 않는다."""

    def __init__(self, calibrated=True):
        self.calibrated = calibrated

    def is_calibrated(self, horizon_min):
        return self.calibrated


def bare_predictor(accuracy_gate=None, calibration=None, **overrides):
    """`__init__` 을 건너뛴 Predictor. `predict()` 가 읽는 속성을 모두 채운다."""
    predictor = Predictor.__new__(Predictor)
    predictor._state_lock = threading.RLock()
    predictor.ok = True
    predictor.dead = set()
    predictor.hist = {}
    predictor.models = {}
    predictor.full_models = {}
    predictor.full_calibrators = {}
    predictor.feat_cols = []
    predictor.meta = {}
    predictor.cqr = {}
    predictor.interval_gate = {}
    predictor.model_version = "test"
    predictor.accuracy_gate = accuracy_gate or AllowAllAccuracy()
    predictor.calibration = calibration or StubCalibration()
    for name, value in overrides.items():
        setattr(predictor, name, value)
    return predictor
