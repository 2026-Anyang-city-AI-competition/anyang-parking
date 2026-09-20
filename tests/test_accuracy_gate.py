import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src.serve.accuracy_gate import AccuracyGate

HEADER = ("parking_id,horizon,status,mae_first,mae_second,mae_overall,"
          "baseline_mae,baseline_name,n_first,n_second,reason,"
          "evidence_report,evaluated_at")


def row(pid, horizon, status, mae="3.0", baseline="5.0"):
    return (f"{pid},{horizon},{status},{mae},{mae},{mae},{baseline},persistence,"
            f"500,500,사유,reports/tables/accuracy_gate_detail.csv,2026-09-19")


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

    def test_broken_header_blocks_rather_than_opens(self):
        # ★ 불량 파일에서 열어 버리면 검증되지 않은 예측이 조용히 나간다.
        self.path.write_text("wrong,header\n1,2\n", encoding="utf-8")
        gate = AccuracyGate(self.path)
        gate.refresh(force=True)
        self.assertEqual(gate.status()["status"], "invalid")
        self.assertEqual(gate.status()["certified"], 0)
        result = gate.check(39, 60)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["status"], "gate_invalid")

    def test_extra_evidence_columns_do_not_break_the_gate(self):
        # 근거 컬럼이 늘어도 필수 컬럼만 있으면 계속 동작해야 한다.
        self.path.write_text(HEADER + ",새근거\n" + row(39, 60, "certified") + ",x\n",
                             encoding="utf-8")
        gate = AccuracyGate(self.path)
        gate.refresh(force=True)
        self.assertEqual(gate.status()["status"], "ready")
        self.assertTrue(gate.check(39, 60)["allowed"])

    def test_display_blocked_is_not_served(self):
        gate = self.gate(row(39, 240, "display_blocked"), row(39, 360, "display_blocked"))
        for horizon in (240, 360):
            result = gate.check(39, horizon)
            self.assertFalse(result["allowed"])
            self.assertEqual(result["status"], "display_blocked")

    def test_below_baseline_is_not_served(self):
        gate = self.gate(row(39, 60, "below_baseline"))
        self.assertFalse(gate.check(39, 60)["allowed"])

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


class BaselineAndPolicyTests(unittest.TestCase):
    """빌더가 기준선 열세와 노출 보류를 실제로 걸러내는지."""

    def build(self, rows):
        import pandas as pd
        from scripts.build_accuracy_gate import evaluate
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "pred.csv.gz"
            pd.DataFrame(rows).to_csv(path, index=False, compression="gzip")
            table, _, _ = evaluate(source=path)
        return table

    def rows(self, horizon=60, ml_error=1.0, baseline_error=5.0):
        out = []
        for day in ("2026-09-11", "2026-09-12", "2026-09-13", "2026-09-14"):
            for _ in range(200):
                out.append({"parking_id": 1, "horizon": horizon, "eligible": True,
                            "actual_occ": 50.0, "pred_ml": 50.0 - ml_error,
                            "pred_persistence": 50.0 - baseline_error,
                            "pred_lag_24h": None, "pred_lag_7d": None,
                            "pred_seasonal_naive": None, "test_date": day})
        return out

    def test_beating_the_baseline_certifies(self):
        record = self.build(self.rows(ml_error=1.0, baseline_error=5.0)).iloc[0]
        self.assertEqual(record["status"], "certified")
        self.assertEqual(record["baseline_name"], "persistence")

    def test_losing_to_the_baseline_blocks_even_with_good_mae(self):
        # MAE 3%p 로 목표는 통과하지만 기준선(1%p)보다 나쁘다.
        record = self.build(self.rows(ml_error=3.0, baseline_error=1.0)).iloc[0]
        self.assertLess(record["mae_overall"], 10)
        self.assertEqual(record["status"], "below_baseline")

    def test_blocked_horizons_are_marked_regardless_of_accuracy(self):
        for horizon in (240, 360):
            record = self.build(self.rows(horizon=horizon, ml_error=0.1)).iloc[0]
            self.assertEqual(record["status"], "display_blocked")
            # 근거 수치는 남긴다 — 해제할 때 다시 쓴다.
            self.assertLess(record["mae_overall"], 1)

    def test_unblocked_horizons_are_unaffected(self):
        record = self.build(self.rows(horizon=180, ml_error=0.1)).iloc[0]
        self.assertEqual(record["status"], "certified")
