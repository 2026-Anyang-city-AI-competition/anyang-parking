import unittest
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from src.serve.api import create_app


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


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client_context = TestClient(create_app(FakePredictor(), FakePoller()))
        self.client = self.client_context.__enter__()

    def tearDown(self):
        self.client_context.__exit__(None, None, None)

    def test_health_exposes_model_and_observation_status(self):
        response = self.client.get("/api/v1/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["service"]["live_lots"], 68)
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


if __name__ == "__main__":
    unittest.main()
