#!/usr/bin/env python3
"""요청·외부 호출 계수기 — `/health` 가 읽는 자리.

프로세스 메모리에만 있다. 재시작하면 0부터 다시 센다. 그걸로 충분하다 —
장기 보관은 모니터링 쪽 일이고, 여기서 필요한 건 「지금 실패하고 있는가」다.

★ 경로·상태·소요시간만 센다. 쿼리 문자열·쿠키·키는 절대 넣지 않는다.
  `/health` 응답은 외부에 나가므로 여기 담긴 값이 곧 공개 정보다.
"""
import threading
import time
from collections import defaultdict

_lock = threading.RLock()
_started = time.time()
_buckets = defaultdict(lambda: {"count": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0})
_external = defaultdict(lambda: {"count": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0})

# 경로를 그대로 키로 쓰면 ID 마다 칸이 생긴다. 알려진 경로만 센다.
KNOWN = ("/api/v1/health", "/api/v1/recommend", "/api/v1/fare/quote",
         "/api/v1/places/search", "/api/v1/benefits", "/api/v1/me",
         "/api/v1/me/preferences", "/api/v1/auth/kakao/login",
         "/api/v1/auth/kakao/callback", "/api/v1/auth/logout")


def _normalise(path):
    return path if path in KNOWN else "other"


def record(path, status, elapsed_ms):
    with _lock:
        bucket = _buckets[_normalise(path)]
        bucket["count"] += 1
        bucket["errors"] += 1 if status >= 500 else 0
        bucket["total_ms"] += elapsed_ms
        bucket["max_ms"] = max(bucket["max_ms"], elapsed_ms)


def record_external(name, ok, elapsed_ms):
    """카카오 경로·검색, 폴링처럼 우리 밖에서 실패할 수 있는 호출."""
    with _lock:
        bucket = _external[name]
        bucket["count"] += 1
        bucket["errors"] += 0 if ok else 1
        bucket["total_ms"] += elapsed_ms
        bucket["max_ms"] = max(bucket["max_ms"], elapsed_ms)


def _view(bucket):
    count = bucket["count"] or 1
    return {"count": bucket["count"], "errors": bucket["errors"],
            "error_rate": round(bucket["errors"] / count, 4),
            "avg_ms": round(bucket["total_ms"] / count, 1),
            "max_ms": round(bucket["max_ms"], 1)}


def snapshot():
    with _lock:
        return {"uptime_sec": round(time.time() - _started, 1),
                "endpoints": {path: _view(bucket) for path, bucket in sorted(_buckets.items())},
                "external": {name: _view(bucket) for name, bucket in sorted(_external.items())}}


def reset():
    """테스트용. 운영 경로에서는 부르지 않는다."""
    with _lock:
        _buckets.clear()
        _external.clear()
