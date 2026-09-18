import unittest
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from src.serve.api import create_app
from src.serve.fare_quote import (MultipleBenefitsUnsupported, UnknownBenefit,
                                  UnknownParking)


STATUS = {
    "model_status": "ready", "model_version": "test", "data_status": "fresh",
    "observation_at": "2026-09-15T01:00:00+09:00", "observation_age_min": 1.0,
    "refreshed_at": "2026-09-15T01:01:00+09:00", "refresh_error": None,
    "live_lots": 68, "fixed_feeds": 21, "prediction_ready_lots": 68,
}


class FakePredictor:
    def refresh_from_db(self, **kwargs):
        return STATUS.copy()


class FakePoller:
    def poll_if_due(self):
        return {"status": "polled", "attempted": True, "reason": None,
                "observation_at": "2026-09-15T01:00:00+09:00", "lots": 89,
                "duration_ms": 10, "error": None}


class FakeAccessRules:
    def refresh(self, force=False):
        return self.status()

    def status(self):
        return {"status": "empty", "rows_total": 0, "rules_loaded": 0,
                "lots_loaded": 0, "ignored_unconfirmed": 0,
                "using_previous": False, "validation_errors": [], "loaded_at": None}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client_context = TestClient(
            create_app(FakePredictor(), FakePoller(), FakeAccessRules()))
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)

    def test_health_exposes_model_and_observation_status(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["service"]["live_lots"], 68)
        self.assertEqual(response.json()["access_rules"]["status"], "empty")
        self.assertTrue(response.headers["X-Request-ID"])

    def test_recommend_validates_and_returns_service_metadata(self):
        stub = {"cards": [], "by_walk": [], "by_fare": [], "unavailable": []}
        payload = {
            "destination": {"lat": 37.394259, "lng": 126.956861},
            "origin": {"lat": 37.4018, "lng": 126.9226},
            "parking_minutes": 120,
            "include_alternatives": False,
        }
        with patch("src.serve.api.recommend", return_value=stub) as called:
            response = self.client.post("/api/v1/recommend", json=payload,
                                        headers={"X-Request-ID": "demo-1"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["request_id"], "demo-1")
        self.assertEqual(body["service"]["data_status"], "fresh")
        self.assertEqual(body["poll"]["status"], "polled")
        self.assertEqual(body["request"]["parking_minutes"], 120)
        self.assertEqual(called.call_args.args[0], (37.394259, 126.956861))

    def test_invalid_coordinate_has_stable_error_shape(self):
        response = self.client.post("/api/v1/recommend", json={
            "destination": {"lat": 91, "lng": 126.9}, "parking_minutes": 60,
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_unknown_discount_lists_allowed_values(self):
        response = self.client.post("/api/v1/recommend", json={
            "destination": {"lat": 37.4, "lng": 126.9},
            "parking_minutes": 60, "discount": "없는감면",
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "unknown_discount")

    def test_fare_quote_returns_quote_and_request_echo(self):
        stub = {"parking_id": 39, "fare": {"payg": 1000}}
        payload = {"parking_id": 39, "arrival_at": "2026-09-16T16:00:00+09:00",
                   "parking_minutes": 240, "benefit_codes": []}
        with patch("src.serve.api.quote_fare", return_value=stub) as called:
            response = self.client.post("/api/v1/fare/quote", json=payload,
                                        headers={"X-Request-ID": "quote-1"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["request_id"], "quote-1")
        self.assertEqual(body["fare"]["payg"], 1000)
        self.assertEqual(body["access_rules"]["status"], "empty")
        self.assertEqual(body["request"]["parking_minutes"], 240)
        self.assertEqual(called.call_args.args[:3], (39, datetime(
            2026, 9, 16, 16, tzinfo=timezone(timedelta(hours=9))), 240))

    def test_fare_quote_maps_domain_errors_to_status_codes(self):
        payload = {"parking_id": 999999, "arrival_at": "2026-09-16T16:00:00+09:00",
                   "parking_minutes": 60}
        cases = [(UnknownParking(999999), 404, "unknown_parking"),
                 (UnknownBenefit({"없는코드"}), 422, "unknown_benefit"),
                 (MultipleBenefitsUnsupported(), 422, "multiple_benefits_unsupported")]
        for error, status, code in cases:
            with self.subTest(code=code), patch("src.serve.api.quote_fare", side_effect=error):
                response = self.client.post("/api/v1/fare/quote", json=payload)
            self.assertEqual(response.status_code, status)
            self.assertEqual(response.json()["error"]["code"], code)

    def test_fare_quote_rejects_bad_payload(self):
        response = self.client.post("/api/v1/fare/quote", json={
            "parking_id": 39, "arrival_at": "어제", "parking_minutes": 0})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "validation_error")


if __name__ == "__main__":
    unittest.main()
