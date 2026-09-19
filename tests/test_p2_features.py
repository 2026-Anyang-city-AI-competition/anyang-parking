import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.serve import reports
from src.serve.api import create_app
from src.serve.access_rules import AccessRulesRepository
from scripts.revalidation_queue import build
from tests.test_api import FakePoller, FakePredictor


class ParkingDetailTests(unittest.TestCase):
    def setUp(self):
        self.ctx = TestClient(create_app(FakePredictor(), FakePoller(), AccessRulesRepository()))
        self.client = self.ctx.__enter__()
        self.addCleanup(lambda: self.ctx.__exit__(None, None, None))

    def test_detail_returns_static_facts_only(self):
        body = self.client.get("/api/v1/parkings/39").json()
        self.assertEqual(body["parking_id"], 39)
        self.assertIn("access_schedule", body)
        # 실시간·예측은 상세에서 주지 않는다. 추천 카드가 그 자리다.
        for field in ("avail_now", "full_prob", "prediction_status"):
            self.assertNotIn(field, body)

    def test_pass_info_never_claims_availability(self):
        passes = self.client.get("/api/v1/parkings/39").json()["passes"]
        self.assertIsNone(passes["purchasable"])
        self.assertIsNone(passes["sold_out"])
        self.assertIsNone(passes["monthly_pass_price"])
        self.assertIn("현장", passes["availability_note"])

    def test_unknown_parking_is_404(self):
        self.assertEqual(self.client.get("/api/v1/parkings/999999").status_code, 404)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(reports, "DB_PATH", Path(self.tmp.name) / "reports.sqlite")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ctx = TestClient(create_app(FakePredictor(), FakePoller(), AccessRulesRepository()))
        self.client = self.ctx.__enter__()
        self.addCleanup(lambda: self.ctx.__exit__(None, None, None))

    def test_report_is_queued_not_applied(self):
        body = self.client.post("/api/v1/reports", json={
            "parking_id": 39, "kind": "access_hours", "message": "저녁 8시에 닫혀 있었어요"}).json()
        self.assertEqual(body["status"], "pending")
        self.assertFalse(reports.summary()["applies_automatically"])
        self.assertEqual(len(reports.pending()), 1)

    def test_contact_details_are_refused(self):
        response = self.client.post("/api/v1/reports", json={
            "parking_id": 39, "kind": "fee", "message": "x",
            "phone": "010-1234-5678"})
        self.assertEqual(response.status_code, 422)

    def test_unknown_kind_and_unknown_lot_are_rejected(self):
        bad_kind = self.client.post("/api/v1/reports", json={
            "parking_id": 39, "kind": "무엇", "message": ""})
        self.assertEqual(bad_kind.status_code, 422)
        bad_lot = self.client.post("/api/v1/reports", json={
            "parking_id": 999999, "kind": "fee", "message": ""})
        self.assertEqual(bad_lot.status_code, 404)

    def test_review_marks_but_does_not_change_rules(self):
        report_id = reports.submit(39, "fee", "요금 달라요")["report_id"]
        reports.review(report_id, "accepted", "재조사 필요")
        self.assertEqual(reports.accepted_lots(), [39])
        self.assertEqual(reports.pending(), [])
        with self.assertRaises(reports.InvalidReport):
            reports.review(report_id, "accepted")      # 두 번 검수되지 않는다

    def test_message_is_truncated_and_sanitised(self):
        saved = reports.submit(39, "other", "a" * 999 + "\x00bad")
        self.assertTrue(saved["report_id"])
        stored = reports.pending()[0]["message"]
        self.assertLessEqual(len(stored), reports.MAX_MESSAGE)
        self.assertNotIn("\x00", stored)


class RevalidationQueueTests(unittest.TestCase):
    def queue(self, rows, today=date(2026, 9, 19)):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.csv"
            header = ("parking_id,name,day_group,entry_windows,exit_windows,fee_windows,"
                      "fee_mode,overnight_allowed,access_status,general_public,effective_from,"
                      "effective_to,checked_at,evidence_method,evidence_ref,note")
            path.write_text(header + "\n" + "\n".join(rows), encoding="utf-8")
            return build(path, today=today)

    def row(self, status="confirmed_open", effective_to="", checked="2026-09-15",
            evidence="official_web"):
        return (f"39,테스트,weekday,00:00-24:00,00:00-24:00,09:00-17:00,paid_window_free_outside,"
                f"true,{status},true,2026-01-01,{effective_to},{checked},{evidence},ref,")

    def test_expired_rule_is_top_priority(self):
        queue = self.queue([self.row(effective_to="2026-09-01")])
        self.assertEqual(queue[0]["reason"], "expired")
        self.assertEqual(queue[0]["priority"], 1)

    def test_expiring_soon_is_flagged(self):
        self.assertEqual(self.queue([self.row(effective_to="2026-10-01")])[0]["reason"],
                         "expiring_soon")

    def test_unknown_rows_are_queued(self):
        self.assertEqual(self.queue([self.row(status="unknown")])[0]["reason"], "unknown")

    def test_weak_evidence_is_queued(self):
        self.assertEqual(self.queue([self.row(evidence="user_experience")])[0]["reason"],
                         "weak_evidence")

    def test_stale_check_is_queued(self):
        self.assertEqual(self.queue([self.row(checked="2026-01-01")])[0]["reason"], "stale")

    def test_healthy_row_is_not_queued(self):
        self.assertEqual(self.queue([self.row()]), [])


if __name__ == "__main__":
    unittest.main()
