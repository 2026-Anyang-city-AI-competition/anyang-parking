import csv
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from src.serve.prediction_gate import FIELDS, PredictionGate
from tests.test_access_rules import make_db
from tests.test_recommend_access import candidates, lot
from src.serve.recommend import recommend


class PredictionGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "gate.csv"
        self.db = Path(self.temp.name) / "parking.db"
        make_db(self.db)
        self.gate = PredictionGate(self.path, self.db)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, rows):
        with self.path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(FIELDS)
            writer.writerows(rows)
        return self.gate.refresh(force=True)

    def test_empty_blocks_unverified_predictions(self):
        self.assertEqual(self.write([])["status"], "empty")
        self.assertFalse(self.gate.check(39, datetime(2026, 9, 16, 10),
                                         datetime(2026, 9, 16, 11))["allowed"])

    def test_frozen_only_allows_verified_window_and_checks_both_times(self):
        self.write([[39, "weekday", "09:00-17:00", "frozen", "frozen_outside",
                     "reports/diagnosis.csv", "2026-09-16"]])
        inside = datetime(2026, 9, 16, 16)
        outside = datetime(2026, 9, 16, 17)
        self.assertTrue(self.gate.check(39, inside, inside)["allowed"])
        self.assertFalse(self.gate.check(39, inside, outside)["allowed"])
        self.assertFalse(self.gate.check(39, outside, inside)["allowed"])
        self.assertEqual(self.gate.validity_mask([39, 39], [inside, inside],
                                               [inside, outside]), [True, False])

    def test_invalid_update_disables_previous_allowance(self):
        row = [39, "weekday", "09:00-17:00", "available", "verified", "report", "2026-09-16"]
        self.write([row])
        row[2] = "00:00-00:00"
        self.assertEqual(self.write([row])["status"], "invalid")
        now = datetime(2026, 9, 16, 10)
        self.assertFalse(self.gate.check(39, now, now)["allowed"])

    def test_blocked_card_keeps_fare_but_hides_prediction_and_frozen_current(self):
        self.write([[39, "weekday", "09:00-17:00", "frozen", "frozen_outside", "report", "2026-09-16"]])
        item = lot(39)
        with patch("src.serve.recommend.find_candidates", return_value=candidates([item], 1000)), \
             patch("src.serve.recommend.routing.multi_eta", return_value={39: {"duration": 0}}), \
             patch("src.serve.recommend.walking.walk_times", return_value={39: {"duration": 60}}), \
             patch("src.serve.recommend.calc_fare", return_value={"total": 0, "total_prepaid": None,
                                                                 "recommend_prepaid": False}), \
             patch("src.serve.recommend.oprtime_features", return_value={"is_operating": False}):
            from unittest.mock import Mock
            predictor = Mock()
            result = recommend((37.4, 126.9), 60, now=datetime(2026, 9, 16, 18),
                               min_n=1, predictor=predictor, prediction_gate=self.gate,
                               with_alternatives=False)
        card = result["cards"][0]
        predictor.predict.assert_not_called()
        for key in ("avail_pred", "full_prob", "pred_p10", "pred_p90", "avail_now", "occ_now"):
            self.assertIsNone(card[key])
        self.assertEqual(card["prediction_status"], "frozen")
        self.assertEqual(card["fare_payg"], 0)
        self.assertEqual(len(result["by_walk"]), 1)


if __name__ == "__main__":
    unittest.main()
