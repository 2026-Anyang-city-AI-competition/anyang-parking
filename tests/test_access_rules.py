import csv
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.serve.access_rules import AccessRulesRepository, FIELDS, validate_access_rules


def make_db(path):
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE lots(parking_id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO lots VALUES(39, '안양7동노외')")


def valid_rows():
    base = {
        "parking_id": "39", "name": "안양7동노외",
        "entry_windows": "00:00-24:00", "exit_windows": "00:00-24:00",
        "fee_windows": "", "fee_mode": "free", "overnight_allowed": "true",
        "access_status": "confirmed_open", "general_public": "true",
        "effective_from": "2026-09-15", "effective_to": "", "checked_at": "2026-09-15",
        "evidence_method": "user_experience", "evidence_ref": "reports/태영.txt#ID-39",
        "note": "무료 개방",
    }
    rows = []
    for day in ("weekday", "saturday", "sunday_holiday"):
        row = base.copy()
        row["day_group"] = day
        if day == "weekday":
            row["fee_windows"] = "09:00-17:00"
            row["fee_mode"] = "paid_window_free_outside"
        rows.append(row)
    return rows


def write_csv(path, rows, fields=FIELDS):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class AccessRulesValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.db = self.directory / "parking.db"
        self.csv = self.directory / "rules.csv"
        make_db(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def codes(self, result):
        return {issue.code for issue in result.errors}

    def test_id39_example_is_valid(self):
        write_csv(self.csv, valid_rows())
        result = validate_access_rules(self.csv, self.db)
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.rows, 3)
        self.assertEqual(result.parking_lots, 1)

    def test_missing_day_group_and_unknown_id_are_rejected(self):
        rows = valid_rows()[:2]
        for row in rows:
            row["parking_id"] = "999"
        write_csv(self.csv, rows)
        result = validate_access_rules(self.csv, self.db)
        self.assertIn("unknown_parking_id", self.codes(result))
        self.assertIn("missing_day_groups", self.codes(result))

    def test_ambiguous_and_overlapping_windows_are_rejected(self):
        rows = valid_rows()
        rows[0]["entry_windows"] = "00:00-00:00"
        rows[1]["exit_windows"] = "09:00-12:00|11:00-14:00"
        write_csv(self.csv, rows)
        result = validate_access_rules(self.csv, self.db)
        self.assertIn("ambiguous_zero_window", self.codes(result))
        self.assertIn("overlapping_windows", self.codes(result))

    def test_confirmed_rule_requires_evidence(self):
        rows = valid_rows()
        rows[0]["evidence_ref"] = ""
        write_csv(self.csv, rows)
        result = validate_access_rules(self.csv, self.db)
        self.assertIn("required", self.codes(result))

    def test_unknown_rule_cannot_claim_access_hours(self):
        rows = valid_rows()
        rows[0]["access_status"] = "unknown"
        rows[0]["evidence_method"] = ""
        rows[0]["evidence_ref"] = ""
        write_csv(self.csv, rows)
        result = validate_access_rules(self.csv, self.db)
        self.assertIn("unknown_with_access_hours", self.codes(result))

    def test_header_order_is_fixed(self):
        fields = list(FIELDS)
        fields[0], fields[1] = fields[1], fields[0]
        write_csv(self.csv, valid_rows(), fields)
        result = validate_access_rules(self.csv, self.db)
        self.assertIn("invalid_header", self.codes(result))

    def test_extra_row_value_is_rejected_without_crashing(self):
        write_csv(self.csv, valid_rows())
        lines = self.csv.read_text(encoding="utf-8").splitlines()
        lines[1] += ",unexpected"
        self.csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = validate_access_rules(self.csv, self.db)
        self.assertIn("extra_values", self.codes(result))


class AccessRulesRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.db = self.directory / "parking.db"
        self.csv = self.directory / "rules.csv"
        make_db(self.db)

    def tearDown(self):
        self.temp.cleanup()

    def test_empty_dataset_starts_normally(self):
        write_csv(self.csv, [])
        repository = AccessRulesRepository(self.csv, self.db)
        status = repository.refresh()
        self.assertEqual(status["status"], "empty")
        self.assertEqual(status["rules_loaded"], 0)
        self.assertIsNone(repository.lookup(39, date(2026, 9, 15)))

    def test_only_confirmed_rows_load_and_day_group_is_selected(self):
        write_csv(self.csv, valid_rows())
        repository = AccessRulesRepository(self.csv, self.db)
        status = repository.refresh()
        weekday = repository.lookup(39, date(2026, 9, 15))
        saturday = repository.lookup(39, date(2026, 9, 19))
        holiday = repository.lookup(39, date(2026, 9, 16), is_holiday=True)
        self.assertEqual(status["status"], "ready")
        self.assertEqual(status["rules_loaded"], 3)
        self.assertEqual(weekday.fee_mode, "paid_window_free_outside")
        self.assertEqual((weekday.fee_windows[0].start_min, weekday.fee_windows[0].end_min),
                         (540, 1020))
        self.assertEqual(saturday.day_group, "saturday")
        self.assertEqual(holiday.day_group, "sunday_holiday")

    def test_unknown_rows_are_valid_but_not_loaded(self):
        rows = valid_rows()
        for row in rows:
            row.update({"entry_windows": "", "exit_windows": "", "fee_windows": "",
                        "fee_mode": "unknown", "access_status": "unknown",
                        "evidence_method": "", "evidence_ref": "", "checked_at": ""})
        write_csv(self.csv, rows)
        repository = AccessRulesRepository(self.csv, self.db)
        status = repository.refresh()
        self.assertEqual(status["status"], "empty")
        self.assertEqual(status["ignored_unconfirmed"], 3)
        self.assertIsNone(repository.lookup(39, date(2026, 9, 15)))

    def test_effective_period_selects_historical_or_current_rule(self):
        old = valid_rows()
        for row in old:
            row["effective_from"] = "2026-09-01"
            row["effective_to"] = "2026-09-14"
            row["note"] = "old"
        current = valid_rows()
        for row in current:
            row["note"] = "current"
        write_csv(self.csv, old + current)
        repository = AccessRulesRepository(self.csv, self.db)
        repository.refresh()
        self.assertEqual(repository.lookup(39, date(2026, 9, 10)).note, "old")
        self.assertEqual(repository.lookup(39, date(2026, 9, 15)).note, "current")

    def test_invalid_refresh_preserves_last_known_good_rules(self):
        rows = valid_rows()
        write_csv(self.csv, rows)
        repository = AccessRulesRepository(self.csv, self.db)
        repository.refresh()
        rows[0]["name"] = "잘못된이름"
        write_csv(self.csv, rows)
        status = repository.refresh(force=True)
        self.assertEqual(status["status"], "invalid")
        self.assertTrue(status["using_previous"])
        self.assertIn("parking_name_mismatch", status["validation_errors"])
        self.assertEqual(repository.lookup(39, date(2026, 9, 15)).name, "안양7동노외")


if __name__ == "__main__":
    unittest.main()
