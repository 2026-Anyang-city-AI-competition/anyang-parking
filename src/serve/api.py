#!/usr/bin/env python3
"""안양 주차 추천 HTTP API.

실행:
    python -m uvicorn src.serve.api:app --host 0.0.0.0 --port 8000 --workers 1

모델은 프로세스 시작 때 한 번만 읽는다. 요청 전 최신 관측 피처만 필요할 때
갱신하며, 경로 API가 실패해도 기존 폴백 정책으로 추천 응답은 유지한다.
"""
import os
import logging
import time
import uuid
from datetime import date, datetime, timedelta
from contextlib import asynccontextmanager
from functools import partial

from fastapi import Cookie, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from src.serve.predictor import Predictor
from src.serve.recommend import recommend
from src.serve.request_polling import RequestPoller
from src.serve.access_rules import AccessRulesRepository
from src.serve.prediction_gate import PredictionGate
from src.serve import auth
from src.serve.access_check import is_korean_holiday
from src.serve.fare import resolve_type
from src.serve import metrics
from src.serve import reports
from src.serve.candidates import load_lot
from src.serve.benefits import catalog as benefit_catalog
from src.serve.fare_quote import UnknownBenefit, UnknownParking, quote_fare
from src.serve.places import MAX_QUERY_LEN, SearchUnavailable, search_places
from src.serve import fare_tables

LOG = logging.getLogger(__name__)


class Coordinate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(ge=36.0, le=39.0, description="WGS84 위도")
    lng: float = Field(ge=125.0, le=129.0, description="WGS84 경도")


class RecommendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    destination: Coordinate
    origin: Coordinate | None = None
    parking_minutes: int = Field(ge=1, le=10080)
    depart_in_minutes: int = Field(default=0, ge=0, le=10080)
    minimum_candidates: int = Field(default=5, ge=1, le=30)
    full_probability_cutoff: float = Field(default=.5, ge=0, le=1)
    discount: str | None = None
    benefit_codes: list[str] = Field(default_factory=list, max_length=5)
    include_alternatives: bool = True
    access_safety_margin_minutes: int = Field(default=0, ge=0, le=60)


class FareQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parking_id: int = Field(ge=1)
    arrival_at: datetime
    parking_minutes: int = Field(ge=1, le=10080)
    benefit_codes: list[str] = Field(default_factory=list, max_length=5)
    access_safety_margin_minutes: int = Field(default=0, ge=0, le=60)


class PreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # 감면 **코드**만 받는다. 증빙 서류·등급·식별번호는 받지 않는다.
    benefit_codes: list[str] = Field(default_factory=list, max_length=5)
    default_minutes: int = Field(default=120, ge=1, le=10080)


class ReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parking_id: int = Field(ge=1)
    kind: str
    message: str = Field(default="", max_length=reports.MAX_MESSAGE)
    # 연락처·이름은 받지 않는다. extra="forbid" 라 보내도 422 가 된다.


class ApiProblem(Exception):
    def __init__(self, status_code, code, message, details=None):
        self.status_code, self.code = status_code, code
        self.message, self.details = message, details


def _windows(windows):
    """분 단위 구간을 사람이 읽는 `HH:MM-HH:MM` 으로. 빈 목록은 "없음"이 아니라 빈 목록이다."""
    def fmt(minute):
        return f"{minute // 60:02d}:{minute % 60:02d}" if minute < 1440 else "24:00"
    return [f"{fmt(w.start_min)}-{fmt(w.end_min)}" for w in windows]


def _passes(grade, lot):
    """정기권 정보. **계약·공식 데이터가 없으므로 판매 가능 여부는 말하지 않는다.**

    별표 5 의 일일권 정가만 운영시간 길이로 조회해 참고값으로 준다. 월정기권은
    원본 대조에서 미확정 항목이 남아 있어(노외 1급지 두 행) 금액을 내보내지 않는다."""
    from src.serve.fare import _open_window
    start, end = _open_window(lot, 0)
    hours = (end - start) / 60
    daily = (fare_tables.daily_pass(hours, grade)
             if grade is not None and float(hours).is_integer() else None)
    return {
        "daily_pass_price": daily,
        "daily_pass_scope": "입차 당일의 유료시간",
        "daily_pass_note": (None if daily is not None else
                            f"운영시간 {hours:g}시간은 별표 5 표 밖이라 금액을 산출하지 않습니다"),
        "monthly_pass_price": None,
        "monthly_pass_note": "월정기권은 조례 원본 대조에서 미확정 항목이 남아 금액을 제공하지 않습니다",
        # 계약·공식 데이터가 없으면 재고를 말하지 않는다(§10).
        "purchasable": None,
        "sold_out": None,
        "availability_note": "판매 가능일·매진 여부는 공식 데이터가 없어 안내하지 않습니다. 현장에서 확인해 주세요.",
    }


def _request_id(request):
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


def _problem(request, status, code, message, details=None):
    return JSONResponse(status_code=status, content={
        "error": {"code": code, "message": message, "details": details},
        "request_id": _request_id(request),
    })


def create_app(predictor=None, poller=None, access_rules=None):
    supplied = predictor
    supplied_poller = poller
    supplied_access_rules = access_rules

    @asynccontextmanager
    async def lifespan(application):
        service = supplied or Predictor()  # 프로세스당 모델 1회 로드
        application.state.predictor = service
        application.state.poller = supplied_poller or RequestPoller()
        application.state.access_rules = supplied_access_rules or AccessRulesRepository()
        application.state.prediction_gate = PredictionGate()
        await run_in_threadpool(service.refresh_from_db, force=True)
        await run_in_threadpool(application.state.access_rules.refresh, force=True)
        await run_in_threadpool(application.state.prediction_gate.refresh, force=True)
        yield

    application = FastAPI(
        title="안양 공영주차장 추천 API",
        version="1.0.0",
        lifespan=lifespan,
    )
    if supplied is not None:
        application.state.predictor = supplied
    if supplied_poller is not None:
        application.state.poller = supplied_poller
    if supplied_access_rules is not None:
        application.state.access_rules = supplied_access_rules

    # 운영에서는 `CORS_ORIGINS` 에 적힌 도메인만 연다. localhost 정규식은 개발 전용이며
    # `APP_ENV=production` 이면 붙이지 않는다 — 운영에 로컬 오리진을 열어 둘 이유가 없다.
    production = os.getenv("APP_ENV", "development").lower() == "production"
    allowed = [x.strip() for x in os.getenv(
        "CORS_ORIGINS", "" if production else "http://localhost:3000,http://localhost:5173"
    ).split(",") if x.strip()]
    if production and not allowed:
        raise RuntimeError("APP_ENV=production 에서는 CORS_ORIGINS 를 반드시 지정해야 합니다")
    cors = {"allow_origins": allowed,
            # 세션 쿠키를 쓰므로 자격증명을 허용한다. 그래서 와일드카드 오리진은 못 쓴다.
            "allow_credentials": True,
            "allow_methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            "allow_headers": ["Content-Type", "X-Request-ID"]}
    if not production:
        cors["allow_origin_regex"] = r"https?://(localhost|127\.0\.0\.1)(:\d+)?"
    application.add_middleware(CORSMiddleware, **cors)

    @application.middleware("http")
    async def request_id_middleware(request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        started = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000
            metrics.record(request.scope.get("route_path") or request.url.path,
                           status, elapsed_ms)
            # 경로와 상태·소요시간만 남긴다. 쿼리·쿠키·키는 로그에 넣지 않는다.
            LOG.info("%s %s %s %.0fms", request.method, request.url.path, status, elapsed_ms)

    @application.exception_handler(ApiProblem)
    async def api_problem_handler(request, exc):
        return _problem(request, exc.status_code, exc.code, exc.message, exc.details)

    @application.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        details = [{"field": ".".join(map(str, e["loc"][1:])),
                    "message": e["msg"], "type": e["type"]} for e in exc.errors()]
        return _problem(request, 422, "validation_error", "요청값을 확인해주세요", details)

    @application.exception_handler(Exception)
    async def unexpected_handler(request, exc):
        # 내부 경로·키·DB 문장은 외부 응답에 노출하지 않는다.
        LOG.exception("recommendation API failed")
        return _problem(request, 500, "internal_error", "추천 처리 중 오류가 발생했습니다")

    @application.get("/api/v1/health")
    async def health(request: Request):
        service = request.app.state.predictor
        status = await run_in_threadpool(service.refresh_from_db)
        access_status = request.app.state.access_rules.status()
        ready = status.get("prediction_ready_lots")
        overall = ("ok" if status["model_status"] == "ready"
                   and status["data_status"] == "fresh"
                   and status.get("refresh_error") is None
                   and ready is not None and ready >= (status.get("live_lots") or 1)
                   and access_status["status"] not in {"invalid", "unavailable"}
                   else "degraded")
        gate_status = request.app.state.prediction_gate.status()
        return {"status": overall, "service": status, "access_rules": access_status,
                "prediction_gate": gate_status,
                # 모니터링이 읽는 자리. 외부 API 실패율과 응답시간이 여기 모인다.
                "metrics": metrics.snapshot(),
                "auth": auth.status(),
                "checks": {
                    "model": status["model_status"] == "ready",
                    "observations_fresh": status["data_status"] == "fresh",
                    "access_rules": access_status["status"] not in {"invalid", "unavailable"},
                    "prediction_gate": gate_status["status"] == "ready",
                },
                "request_id": _request_id(request)}

    @application.post("/api/v1/recommend")
    async def recommend_endpoint(body: RecommendRequest, request: Request):
        # `discount`(단수)는 하위 호환. 새 입력은 `benefit_codes`다.
        codes = body.benefit_codes or ([body.discount] if body.discount else [])
        unknown = [c for c in codes if c not in fare_tables.DISCOUNTS]
        if unknown:
            raise ApiProblem(422, "unknown_discount", "지원하지 않는 감면 유형입니다",
                             {"unknown": unknown, "allowed": sorted(fare_tables.DISCOUNTS)})
        service = request.app.state.predictor
        poll_status = await run_in_threadpool(request.app.state.poller.poll_if_due)
        service_status = await run_in_threadpool(
            service.refresh_from_db, force=poll_status["status"] == "polled")
        access_status = await run_in_threadpool(request.app.state.access_rules.refresh)
        gate_status = await run_in_threadpool(request.app.state.prediction_gate.refresh)
        origin = ((body.origin.lat, body.origin.lng) if body.origin else None)
        work = partial(
            recommend,
            (body.destination.lat, body.destination.lng),
            body.parking_minutes,
            start=origin,
            depart_in_min=body.depart_in_minutes,
            min_n=body.minimum_candidates,
            full_prob_cutoff=body.full_probability_cutoff,
            predictor=service,
            discount=body.discount,
            benefit_codes=codes,
            with_alternatives=body.include_alternatives,
            access_rules=request.app.state.access_rules,
            access_safety_margin_minutes=body.access_safety_margin_minutes,
            prediction_gate=request.app.state.prediction_gate,
        )
        result = await run_in_threadpool(work)
        result.update({
            "request_id": _request_id(request),
            "service": service_status,
            "poll": poll_status,
            "access_rules": access_status,
            "prediction_gate": gate_status,
            "request": body.model_dump(),
        })
        return result

    def _account(token):
        try:
            return auth.resolve(token)
        except auth.AuthNotConfigured as exc:
            raise ApiProblem(503, "auth_not_configured", "로그인 기능이 설정되지 않았습니다",
                             {"missing": auth.status()["missing"]}) from exc
        except auth.NotAuthenticated as exc:
            raise ApiProblem(401, "not_authenticated", "로그인이 필요합니다") from exc

    def _set_session(response, token):
        # httponly: 스크립트가 못 읽는다. samesite=lax: 교차 사이트 POST 로 새지 않는다.
        response.set_cookie(auth.SESSION_COOKIE, token, httponly=True, samesite="lax",
                            secure=auth.config()["secure_cookie"],
                            max_age=auth.SESSION_TTL_SEC, path="/")

    @application.get("/api/v1/auth/kakao/login")
    async def kakao_login(request: Request):
        try:
            return {"authorize_url": await run_in_threadpool(auth.login_url),
                    "request_id": _request_id(request)}
        except auth.AuthNotConfigured:
            raise ApiProblem(503, "auth_not_configured", "로그인 기능이 설정되지 않았습니다",
                             {"missing": auth.status()["missing"]})

    @application.get("/api/v1/auth/kakao/callback")
    async def kakao_callback(request: Request, code: str = Query(min_length=1),
                             state: str = Query(min_length=1)):
        try:
            token = await run_in_threadpool(auth.complete_login, code, state)
        except auth.AuthNotConfigured:
            raise ApiProblem(503, "auth_not_configured", "로그인 기능이 설정되지 않았습니다")
        except auth.AuthFailed:
            # 실패 원인을 자세히 알리지 않는다. state 추측에 단서를 주지 않기 위해서다.
            raise ApiProblem(400, "auth_failed", "로그인을 완료하지 못했습니다")
        target = os.getenv("AUTH_SUCCESS_REDIRECT", "")
        response = (RedirectResponse(target, status_code=303) if target
                    else JSONResponse({"status": "ok", "request_id": _request_id(request)}))
        _set_session(response, token)
        return response

    @application.post("/api/v1/auth/logout")
    async def logout_endpoint(request: Request, response: Response,
                              session: str | None = Cookie(default=None,
                                                           alias=auth.SESSION_COOKIE)):
        try:
            await run_in_threadpool(auth.logout, session)
        except auth.AuthNotConfigured:
            pass                                   # 설정이 없으면 지울 세션도 없다
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"status": "ok", "request_id": _request_id(request)}

    @application.get("/api/v1/me")
    async def me_endpoint(request: Request,
                          session: str | None = Cookie(default=None,
                                                       alias=auth.SESSION_COOKIE)):
        account = _account(session)
        stored = await run_in_threadpool(auth.get_preferences, account)
        return {**stored, "storage": auth.status(), "request_id": _request_id(request)}

    @application.put("/api/v1/me/preferences")
    async def update_preferences(body: PreferencesRequest, request: Request,
                                 session: str | None = Cookie(default=None,
                                                              alias=auth.SESSION_COOKIE)):
        unknown = [c for c in body.benefit_codes if c not in fare_tables.DISCOUNTS]
        if unknown:
            raise ApiProblem(422, "unknown_discount", "지원하지 않는 감면 유형입니다",
                             {"unknown": unknown, "allowed": sorted(fare_tables.DISCOUNTS)})
        account = _account(session)
        saved = await run_in_threadpool(auth.set_preferences, account, body.model_dump())
        return {**saved, "request_id": _request_id(request)}

    @application.delete("/api/v1/me")
    async def delete_me(request: Request, response: Response,
                        session: str | None = Cookie(default=None,
                                                     alias=auth.SESSION_COOKIE)):
        account = _account(session)
        await run_in_threadpool(auth.delete_account, account)
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return {"status": "deleted", "request_id": _request_id(request)}

    @application.get("/api/v1/benefits")
    async def benefits_endpoint(request: Request):
        """지원 감면 코드와 증빙 안내. 증빙 서류 자체는 받지도 저장하지도 않는다."""
        return {**benefit_catalog(), "request_id": _request_id(request)}

    @application.post("/api/v1/reports")
    async def submit_report(body: ReportRequest, request: Request):
        """사용자 제보. **서비스 데이터를 바꾸지 않고** 검수 큐에만 쌓인다."""
        lot = await run_in_threadpool(load_lot, body.parking_id)
        if lot is None:
            raise ApiProblem(404, "unknown_parking", "존재하지 않는 주차장입니다",
                             {"parking_id": body.parking_id})
        try:
            saved = await run_in_threadpool(
                reports.submit, body.parking_id, body.kind, body.message)
        except reports.InvalidReport as exc:
            raise ApiProblem(422, "invalid_report", str(exc),
                             {"allowed_kinds": sorted(reports.KINDS)}) from exc
        return {**saved, "request_id": _request_id(request)}

    @application.get("/api/v1/reports/kinds")
    async def report_kinds(request: Request):
        return {"kinds": [{"code": code, "label": label}
                          for code, label in sorted(reports.KINDS.items())],
                "note": "제보는 검수 후 재조사 대상으로만 쓰입니다.",
                "request_id": _request_id(request)}

    @application.get("/api/v1/parkings/{parking_id}")
    async def parking_detail(parking_id: int, request: Request):
        """주차장 상세. 실시간·예측은 여기서 주지 않는다 — 추천 응답의 카드가 그 자리다."""
        lot = await run_in_threadpool(load_lot, parking_id)
        if lot is None:
            raise ApiProblem(404, "unknown_parking", "존재하지 않는 주차장입니다",
                             {"parking_id": parking_id})
        rules = request.app.state.access_rules
        await run_in_threadpool(rules.refresh)
        today = date.today()
        schedule = []
        for offset, group in ((0, "오늘"),):
            day = today + timedelta(days=offset)
            rule = await run_in_threadpool(
                rules.lookup, parking_id, day, is_korean_holiday(day))
            schedule.append({
                "date": day.isoformat(), "label": group,
                "confirmed": rule is not None,
                "entry_windows": _windows(rule.entry_windows) if rule else None,
                "exit_windows": _windows(rule.exit_windows) if rule else None,
                "fee_windows": _windows(rule.fee_windows) if rule else None,
                "fee_mode": rule.fee_mode if rule else None,
                "overnight_allowed": rule.overnight_allowed if rule else None,
                "note": rule.note if rule else "출입·과금 조건이 아직 확인되지 않았습니다",
            })
        grade = lot.get("grade")
        return {
            "parking_id": lot["parking_id"], "name": lot["name"],
            "lat": lot["lat"], "lng": lot["lng"],
            "cell_cnt": lot["cell_cnt"], "grade": grade,
            "type": resolve_type(lot.get("name"), lot.get("div")),
            "weekday_hours": f"{lot.get('wdays_start') or '-'}~{lot.get('wdays_end') or '-'}",
            "weekend_hours": f"{lot.get('wend_start') or '-'}~{lot.get('wend_end') or '-'}",
            "access_schedule": schedule,
            "passes": _passes(grade, lot),
            "request_id": _request_id(request),
        }

    @application.get("/api/v1/places/search")
    async def places_search_endpoint(
            request: Request,
            q: str = Query(min_length=1, max_length=MAX_QUERY_LEN, description="장소명 또는 주소")):
        """목적지 검색. 키는 서버에만 있고 응답에 원천 본문을 싣지 않는다."""
        try:
            result = await run_in_threadpool(search_places, q)
        except ValueError:
            raise ApiProblem(422, "empty_query", "검색어를 입력해주세요")
        except SearchUnavailable:
            # 빈 목록으로 위장하지 않는다. 결과 없음과 검색 불가는 다른 상태다.
            raise ApiProblem(503, "search_unavailable", "장소 검색을 지금 사용할 수 없습니다")
        return {**result, "query": q, "count": len(result["places"]),
                "request_id": _request_id(request)}

    @application.post("/api/v1/fare/quote")
    async def fare_quote_endpoint(body: FareQuoteRequest, request: Request):
        """추천을 다시 돌리지 않고 시간·할인만 바꿔 요금을 다시 계산한다."""
        access_rules = request.app.state.access_rules
        access_status = await run_in_threadpool(access_rules.refresh)
        work = partial(
            quote_fare,
            body.parking_id,
            body.arrival_at,
            body.parking_minutes,
            body.benefit_codes,
            access_rules=access_rules,
            safety_margin_minutes=body.access_safety_margin_minutes,
        )
        try:
            result = await run_in_threadpool(work)
        except UnknownParking:
            raise ApiProblem(404, "unknown_parking", "존재하지 않는 주차장입니다",
                             {"parking_id": body.parking_id})
        except UnknownBenefit as exc:
            raise ApiProblem(422, "unknown_benefit", "지원하지 않는 감면 유형입니다",
                             {"unknown": exc.codes, "allowed": sorted(fare_tables.DISCOUNTS)})
        result.update({"request_id": _request_id(request),
                       "access_rules": access_status,
                       "request": body.model_dump(mode="json")})
        return result

    return application


app = create_app()
