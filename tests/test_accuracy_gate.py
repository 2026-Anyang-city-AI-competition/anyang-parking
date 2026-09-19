import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.serve.accuracy_gate import FIELDS, AccuracyGate

HEADER = ",".join(FIELDS)


def row(pid, horizon, status, mae="3.0"):
    return (f"{pid},{horizon},{status},{mae},{mae},{mae},500,500,사유,"
            f"reports/tables/accuracy_gate_detail.csv,2026-09-19")


class AccuracyGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "accuracy.csv"

    def gate(self, *rows):
        self.path.write_text(HEADER + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
        gate = AccuracyGate(self.path)
        gate.refresh(force=True)
        return gate

    def test_certified_cell_is_served(self):
        gate = self.gate(row(39, 60, "certified"))
        self.assertTrue(gate.check(39, 60)["allowed"])
        self.assertEqual(gate.status()["certified"], 1)

    def test_failed_and_not_reproduced_are_blocked(self):
        gate = self.gate(row(39, 60, "failed"), row(40, 60, "not_reproduced"),
                         row(41, 60, "insufficient"))
        for pid in (39, 40, 41):
            self.assertFalse(gate.check(pid, 60)["allowed"], pid)
        self.assertEqual(gate.status()["blocked"], 3)

    def test_unlisted_cell_is_blocked_not_assumed_good(self):
        gate = self.gate(row(39, 60, "certified"))
        result = gate.check(39, 120)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["status"], "not_evaluated")

    def test_missing_file_does_not_block_everything(self):
        # 게이트를 아직 만들지 않은 상태로 서비스를 통째로 끄지 않는다.
        gate = AccuracyGate(Path(self.tmp.name) / "없는파일.csv")
        gate.refresh(force=True)
        self.assertEqual(gate.status()["status"], "unavailable")
        self.assertTrue(gate.check(39, 60)["allowed"])
        self.assertEqual(gate.check(39, 60)["status"], "gate_absent")

    def test_broken_header_opens_nothing(self):
        self.path.write_text("wrong,header\n1,2\n", encoding="utf-8")
        gate = AccuracyGate(self.path)
        gate.refresh(force=True)
        self.assertEqual(gate.status()["status"], "invalid")
        self.assertEqual(gate.status()["certified"], 0)

    def test_certified_horizons_lists_only_served_cells(self):
        gate = self.gate(row(39, 15, "certified"), row(39, 60, "certified"),
                         row(39, 120, "failed"))
        self.assertEqual(gate.certified_horizons(39), [15, 60])

    def test_reload_picks_up_changes(self):
        gate = self.gate(row(39, 60, "failed"))
        self.assertFalse(gate.check(39, 60)["allowed"])
        self.path.write_text(HEADER + "\n" + row(39, 60, "certified") + "\n", encoding="utf-8")
        gate.refresh(force=True)
        self.assertTrue(gate.check(39, 60)["allowed"])


class GateBuilderTests(unittest.TestCase):
    def test_reproduction_is_required_not_just_average(self):
        import pandas as pd
        from scripts.build_accuracy_gate import evaluate
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pred.csv.gz"
            # 전반은 정확, 후반은 크게 틀림 → 평균은 통과해도 인증하면 안 된다.
            rows = []
            for day, error in (("2026-09-11", 0.0), ("2026-09-12", 0.0),
                               ("2026-09-13", 30.0), ("2026-09-14", 30.0)):
                for i in range(200):
                    rows.append({"parking_id": 1, "horizon": 60, "eligible": True,
                                 "actual_occ": 50.0, "pred_ml": 50.0 - error,
                                 "test_date": day})
            pd.DataFrame(rows).to_csv(path, index=False, compression="gzip")
            table, _, _ = evaluate(source=path)
        record = table.iloc[0]
        self.assertLess(record["mae_first"], 10)
        self.assertGreater(record["mae_second"], 10)
        self.assertEqual(record["status"], "not_reproduced")


if __name__ == "__main__":
    unittest.main()
