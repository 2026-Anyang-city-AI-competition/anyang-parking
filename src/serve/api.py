#!/usr/bin/env python3
"""안양 주차 추천 HTTP API.

실행:
    python -m uvicorn src.serve.api:app --host 0.0.0.0 --port 8000 --workers 1

모델은 프로세스 시작 때 한 번만 읽는다. 요청 전 최신 관측 피처만 필요할 때
갱신하며, 경로 API가 실패해도 기존 폴백 정책으로 추천 응답은 유지한다.
"""
import os
import logging
import uuid
from datetime import datetime
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from src.serve.predictor import Predictor
from src.serve.recommend import recommend
from src.serve.request_polling import RequestPoller
from src.serve.access_rules import AccessRulesRepository
from src.serve.prediction_gate import PredictionGate
from src.serve.fare_quote import (MultipleBenefitsUnsupported, UnknownBenefit,
                                  UnknownParking, quote_fare)
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
    include_alternatives: bool = True
    access_safety_margin_minutes: int = Field(default=0, ge=0, le=60)


class FareQuoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parking_id: int = Field(ge=1)
    arrival_at: datetime
    parking_minutes: int = Field(ge=1, le=10080)
    benefit_codes: list[str] = Field(default_factory=list, max_length=5)
    access_safety_margin_minutes: int = Field(default=0, ge=0, le=60)


class ApiProblem(Exception):
    def __init__(self, status_code, code, message, details=None):
        self.status_code, self.code = status_code, code
        self.message, self.details = message, details


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

    allowed = [x.strip() for x in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"
    ).split(",") if x.strip()]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=allowed,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
    )

    @application.middleware("http")
    async def request_id_middleware(request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

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
        return {"status": overall, "service": status, "access_rules": access_status,
                "prediction_gate": request.app.state.prediction_gate.status(),
                "request_id": _request_id(request)}

    @application.post("/api/v1/recommend")
    async def recommend_endpoint(body: RecommendRequest, request: Request):
        if body.discount is not None and body.discount not in fare_tables.DISCOUNTS:
            raise ApiProblem(422, "unknown_discount", "지원하지 않는 감면 유형입니다",
                             {"allowed": sorted(fare_tables.DISCOUNTS)})
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
        except MultipleBenefitsUnsupported:
            raise ApiProblem(422, "multiple_benefits_unsupported",
                             "중복 감면 가능 여부가 확인되지 않아 한 번에 한 가지만 계산합니다",
                             {"benefit_codes": body.benefit_codes})
        result.update({"request_id": _request_id(request),
                       "access_rules": access_status,
                       "request": body.model_dump(mode="json")})
        return result

    return application


app = create_app()
