import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.serve import metrics
from src.serve.api import create_app
from tests.test_api import FakeAccessRules, FakePoller, FakePredictor


class MetricsTests(unittest.TestCase):
    def setUp(self):
        metrics.reset()
        self.addCleanup(metrics.reset)

    def test_endpoint_latency_and_error_rate_are_recorded(self):
        metrics.record("/api/v1/recommend", 200, 120.0)
        metrics.record("/api/v1/recommend", 500, 80.0)
        bucket = metrics.snapshot()["endpoints"]["/api/v1/recommend"]
        self.assertEqual(bucket["count"], 2)
        self.assertEqual(bucket["errors"], 1)
        self.assertEqual(bucket["error_rate"], 0.5)
        self.assertEqual(bucket["avg_ms"], 100.0)
        self.assertEqual(bucket["max_ms"], 120.0)

    def test_unknown_paths_do_not_create_a_bucket_each(self):
        for suffix in range(5):
            metrics.record(f"/api/v1/parkings/{suffix}", 200, 1.0)
        self.assertEqual(list(metrics.snapshot()["endpoints"]), ["other"])

    def test_external_failures_are_counted_separately(self):
        metrics.record_external("kakao_route_multi", True, 50.0)
        metrics.record_external("kakao_route_multi", False, 50.0)
        bucket = metrics.snapshot()["external"]["kakao_route_multi"]
        self.assertEqual(bucket["error_rate"], 0.5)

    def test_health_exposes_metrics_after_a_request(self):
        with TestClient(create_app(FakePredictor(), FakePoller(), FakeAccessRules())) as client:
            client.get("/api/v1/health")
            body = client.get("/api/v1/health").json()
        self.assertIn("/api/v1/health", body["metrics"]["endpoints"])
        self.assertIn("checks", body)

    def test_metrics_never_contain_query_strings_or_cookies(self):
        with TestClient(create_app(FakePredictor(), FakePoller(), FakeAccessRules())) as client:
            client.get("/api/v1/health?secret=abc", headers={"Cookie": "anyang_session=tok"})
            body = client.get("/api/v1/health").json()
        blob = str(body["metrics"])
        self.assertNotIn("secret", blob)
        self.assertNotIn("tok", blob)


class CorsTests(unittest.TestCase):
    def test_production_requires_explicit_origins(self):
        with patch.dict(os.environ, {"APP_ENV": "production", "CORS_ORIGINS": ""}, clear=False):
            with self.assertRaises(RuntimeError):
                create_app(FakePredictor(), FakePoller(), FakeAccessRules())

    def test_production_does_not_open_localhost(self):
        env = {"APP_ENV": "production", "CORS_ORIGINS": "https://parking.example.kr"}
        with patch.dict(os.environ, env, clear=False):
            app = create_app(FakePredictor(), FakePoller(), FakeAccessRules())
        with TestClient(app) as client:
            allowed = client.get("/api/v1/health",
                                 headers={"Origin": "https://parking.example.kr"})
            local = client.get("/api/v1/health", headers={"Origin": "http://localhost:5173"})
        self.assertEqual(allowed.headers.get("access-control-allow-origin"),
                         "https://parking.example.kr")
        self.assertIsNone(local.headers.get("access-control-allow-origin"))

    def test_development_still_allows_localhost(self):
        with patch.dict(os.environ, {"APP_ENV": "development"}, clear=False):
            app = create_app(FakePredictor(), FakePoller(), FakeAccessRules())
        with TestClient(app) as client:
            local = client.get("/api/v1/health", headers={"Origin": "http://localhost:5173"})
        self.assertEqual(local.headers.get("access-control-allow-origin"),
                         "http://localhost:5173")


if __name__ == "__main__":
    unittest.main()
