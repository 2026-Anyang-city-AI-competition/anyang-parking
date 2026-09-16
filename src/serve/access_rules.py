"""주차장 출입·과금 조사 CSV 스키마와 검증기.

조사 메모를 서비스가 직접 해석하지 않도록 구조화된 CSV만 입력으로 받는다.
검증과 안전한 적재까지만 담당하며 실제 추천 가능 판정은 별도 단계에서 수행한다.
"""
from __future__ import annotations

import csv
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from src.config import ACCESS_RULES_CSV, PARKING_DB


FIELDS = (
    "parking_id", "name", "day_group", "entry_windows", "exit_windows",
    "fee_windows", "fee_mode", "overnight_allowed", "access_status",
    "general_public", "effective_from", "effective_to", "checked_at",
    "evidence_method", "evidence_ref", "note",
)
DAY_GROUPS = frozenset({"weekday", "saturday", "sunday_holiday"})
ACCESS_STATUSES = frozenset({"confirmed_open", "confirmed_restricted", "unknown"})
FEE_MODES = frozenset({"paid_window_free_outside", "paid_24h", "free", "closed", "unknown"})
EVIDENCE_METHODS = frozenset({"official_web", "phone", "field", "user_experience"})
BOOLS = frozenset({"true", "false"})
WINDOW_RE = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$")


@dataclass(frozen=True)
class ValidationIssue:
    line: int
    field: str
    code: str
    message: str


@dataclass
class ValidationResult:
    path: Path
    rows: int = 0
    parking_lots: int = 0
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)

    @property
    def valid(self):
        return not self.errors


@dataclass(frozen=True)
class TimeWindow:
    start_min: int
    end_min: int


@dataclass(frozen=True)
class AccessRule:
    parking_id: int
    name: str
    day_group: str
    entry_windows: tuple[TimeWindow, ...]
    exit_windows: tuple[TimeWindow, ...]
    fee_windows: tuple[TimeWindow, ...]
    fee_mode: str
    overnight_allowed: bool
    access_status: str
    general_public: bool
    effective_from: date
    effective_to: date | None
    checked_at: date
    evidence_method: str
    evidence_ref: str
    note: str

    def applies_on(self, value):
        return self.effective_from <= value and (
            self.effective_to is None or value <= self.effective_to)


def _issue(result, line, column, code, message, warning=False):
    target = result.warnings if warning else result.errors
    target.append(ValidationIssue(line, column, code, message))


def _parse_date(value, result, line, column, required=False):
    if not value:
        if required:
            _issue(result, line, column, "required", "날짜가 필요합니다")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        _issue(result, line, column, "invalid_date", "YYYY-MM-DD 형식이어야 합니다")
        return None


def _minute(hour, minute, is_start):
    if hour == 24:
        return None if is_start or minute != 0 else 1440
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _parse_windows(value, result, line, column, allow_closed):
    """시간창을 분 단위 튜플로 바꾼다. 빈 값은 None, closed는 빈 목록이다."""
    if not value:
        return None
    if value == "closed":
        if allow_closed:
            return []
        _issue(result, line, column, "closed_not_allowed", "이 필드에는 closed를 쓸 수 없습니다")
        return None

    windows = []
    for token in value.split("|"):
        match = WINDOW_RE.fullmatch(token)
        if not match:
            _issue(result, line, column, "invalid_window",
                   "HH:MM-HH:MM 형식이어야 하며 복수 구간은 |로 구분합니다")
            return None
        sh, sm, eh, em = map(int, match.groups())
        start = _minute(sh, sm, True)
        end = _minute(eh, em, False)
        if start is None or end is None:
            _issue(result, line, column, "invalid_time", "24:00은 종료시각으로만 사용할 수 있습니다")
            return None
        if start >= end:
            code = "ambiguous_zero_window" if start == end else "cross_midnight_window"
            message = ("시작과 종료가 같은 구간은 허용하지 않습니다"
                       if start == end else "자정 통과 구간은 24:00에서 둘로 나눠야 합니다")
            _issue(result, line, column, code, message)
            return None
        windows.append((start, end))

    ordered = sorted(windows)
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            _issue(result, line, column, "overlapping_windows", "서로 겹치는 시간 구간이 있습니다")
            return None
    return ordered


def _load_lots(db_path, result):
    try:
        with sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True) as db:
            return {int(row[0]): str(row[1]) for row in db.execute(
                "SELECT parking_id, name FROM lots")}
    except (OSError, sqlite3.Error) as exc:
        _issue(result, 0, "parking_id", "parking_db_unavailable",
               f"parking.db의 lots 목록을 읽을 수 없습니다: {type(exc).__name__}")
        return None


def validate_access_rules(path=ACCESS_RULES_CSV, parking_db=PARKING_DB):
    """CSV 전체를 검사하고 모든 오류를 모아 반환한다."""
    path = Path(path)
    result = ValidationResult(path=path)
    if not path.exists():
        _issue(result, 0, "file", "file_not_found", "출입 규칙 CSV가 없습니다")
        return result

    lots = _load_lots(parking_db, result)
    try:
        handle = path.open("r", encoding="utf-8-sig", newline="")
    except OSError as exc:
        _issue(result, 0, "file", "file_unreadable", f"CSV를 읽을 수 없습니다: {type(exc).__name__}")
        return result

    records = []
    with handle:
        reader = csv.DictReader(handle)
        actual = tuple(reader.fieldnames or ())
        if actual != FIELDS:
            missing = [column for column in FIELDS if column not in actual]
            extra = [column for column in actual if column not in FIELDS]
            detail = []
            if missing:
                detail.append("누락=" + ",".join(missing))
            if extra:
                detail.append("추가=" + ",".join(extra))
            if not missing and not extra:
                detail.append("컬럼 순서가 정본과 다름")
            _issue(result, 1, "header", "invalid_header", "; ".join(detail))
            return result

        for line, raw in enumerate(reader, start=2):
            if raw.get(None):
                _issue(result, line, "row", "extra_values", "헤더보다 많은 값이 들어 있습니다")
            row = {key: (value or "").strip() for key, value in raw.items() if key is not None}
            if not any(row.values()):
                continue
            result.rows += 1

            try:
                parking_id = int(row["parking_id"])
                if parking_id <= 0:
                    raise ValueError
            except ValueError:
                parking_id = None
                _issue(result, line, "parking_id", "invalid_parking_id", "양의 정수 ID여야 합니다")

            if not row["name"]:
                _issue(result, line, "name", "required", "주차장명이 필요합니다")
            if parking_id is not None and lots is not None:
                if parking_id not in lots:
                    _issue(result, line, "parking_id", "unknown_parking_id", "parking.db에 없는 ID입니다")
                elif row["name"] and row["name"] != lots[parking_id]:
                    _issue(result, line, "name", "parking_name_mismatch",
                           f"parking.db 이름은 {lots[parking_id]!r}입니다")

            day_group = row["day_group"]
            if day_group not in DAY_GROUPS:
                _issue(result, line, "day_group", "invalid_enum",
                       "weekday, saturday, sunday_holiday 중 하나여야 합니다")

            access_status = row["access_status"]
            if access_status not in ACCESS_STATUSES:
                _issue(result, line, "access_status", "invalid_enum",
                       "confirmed_open, confirmed_restricted, unknown 중 하나여야 합니다")

            fee_mode = row["fee_mode"]
            if fee_mode not in FEE_MODES:
                _issue(result, line, "fee_mode", "invalid_enum", "지원하지 않는 과금 정책 코드입니다")

            for column in ("overnight_allowed", "general_public"):
                if row[column] not in BOOLS:
                    _issue(result, line, column, "invalid_boolean", "true 또는 false여야 합니다")

            entry = _parse_windows(row["entry_windows"], result, line, "entry_windows", True)
            exit_ = _parse_windows(row["exit_windows"], result, line, "exit_windows", True)
            fee = _parse_windows(row["fee_windows"], result, line, "fee_windows", False)

            if access_status == "unknown":
                if row["entry_windows"] or row["exit_windows"]:
                    _issue(result, line, "access_status", "unknown_with_access_hours",
                           "미확인 상태에는 입출차 시간을 채우지 않습니다")
            elif entry is None or exit_ is None:
                _issue(result, line, "access_status", "confirmed_without_access_hours",
                       "확정 상태에는 입차·출차 시간 또는 closed가 필요합니다")
            if access_status == "confirmed_open" and (entry == [] or exit_ == []):
                _issue(result, line, "access_status", "open_but_closed",
                       "confirmed_open 행에 closed를 사용할 수 없습니다")

            if fee_mode in {"free", "closed", "unknown"} and row["fee_windows"]:
                _issue(result, line, "fee_windows", "fee_mode_conflict",
                       f"fee_mode={fee_mode}이면 과금 구간은 비워야 합니다")
            elif fee_mode == "paid_24h" and fee != [(0, 1440)]:
                _issue(result, line, "fee_windows", "fee_mode_conflict",
                       "paid_24h의 과금 구간은 00:00-24:00이어야 합니다")
            elif fee_mode == "paid_window_free_outside" and not fee:
                _issue(result, line, "fee_windows", "fee_mode_conflict", "유료 과금 구간이 필요합니다")

            effective_from = _parse_date(row["effective_from"], result, line, "effective_from", True)
            effective_to = _parse_date(row["effective_to"], result, line, "effective_to")
            _parse_date(row["checked_at"], result, line, "checked_at",
                        required=access_status in {"confirmed_open", "confirmed_restricted"})
            if effective_from and effective_to and effective_to < effective_from:
                _issue(result, line, "effective_to", "invalid_date_range",
                       "종료일은 시작일보다 빠를 수 없습니다")

            evidence_method = row["evidence_method"]
            if evidence_method and evidence_method not in EVIDENCE_METHODS:
                _issue(result, line, "evidence_method", "invalid_enum", "지원하지 않는 확인 방법입니다")
            if access_status in {"confirmed_open", "confirmed_restricted"}:
                if not evidence_method:
                    _issue(result, line, "evidence_method", "required", "확정값에는 확인 방법이 필요합니다")
                if not row["evidence_ref"]:
                    _issue(result, line, "evidence_ref", "required", "확정값에는 재검증 가능한 근거가 필요합니다")

            records.append({"line": line, "parking_id": parking_id, "day_group": day_group,
                            "effective_from": effective_from, "effective_to": effective_to})

    result.parking_lots = len({r["parking_id"] for r in records if r["parking_id"] is not None})
    _validate_sets(records, result)
    return result


def _validate_sets(records, result):
    groups = {}
    histories = {}
    for record in records:
        pid, day = record["parking_id"], record["day_group"]
        start, end = record["effective_from"], record["effective_to"]
        if pid is None or day not in DAY_GROUPS or start is None:
            continue
        version = (pid, start, end)
        groups.setdefault(version, []).append(record)
        histories.setdefault((pid, day), []).append(record)

    for (pid, start, end), rows in groups.items():
        seen = {}
        for row in rows:
            day = row["day_group"]
            if day in seen:
                _issue(result, row["line"], "day_group", "duplicate_day_group",
                       f"ID {pid}의 같은 적용기간에 {day}가 중복됩니다")
            seen[day] = row["line"]
        missing = DAY_GROUPS - seen.keys()
        if missing:
            _issue(result, min(r["line"] for r in rows), "day_group", "missing_day_groups",
                   f"ID {pid}의 같은 적용기간에 누락된 요일그룹: {','.join(sorted(missing))}")

    max_date = date.max
    for (pid, day), rows in histories.items():
        ordered = sorted(rows, key=lambda r: r["effective_from"])
        for previous, current in zip(ordered, ordered[1:]):
            if current["effective_from"] <= (previous["effective_to"] or max_date):
                _issue(result, current["line"], "effective_from", "overlapping_effective_periods",
                       f"ID {pid} {day}의 적용기간이 이전 규칙과 겹칩니다")


def format_result(result):
    lines = [f"검사 파일: {result.path}",
             f"결과: {'통과' if result.valid else '실패'} · {result.rows}행 · {result.parking_lots}곳 · "
             f"오류 {len(result.errors)}건 · 경고 {len(result.warnings)}건"]
    for issue in result.errors:
        location = f"{issue.line}행" if issue.line else "파일"
        lines.append(f"- 오류 [{issue.code}] {location} {issue.field}: {issue.message}")
    for issue in result.warnings:
        location = f"{issue.line}행" if issue.line else "파일"
        lines.append(f"- 경고 [{issue.code}] {location} {issue.field}: {issue.message}")
    return "\n".join(lines)


def day_group_for(value, is_holiday=False):
    """날짜를 조사 CSV의 요일 그룹으로 바꾼다."""
    value = value.date() if isinstance(value, datetime) else value
    if is_holiday or value.weekday() == 6:
        return "sunday_holiday"
    if value.weekday() == 5:
        return "saturday"
    return "weekday"


def _decode_windows(value):
    if not value or value == "closed":
        return ()
    decoded = []
    for token in value.split("|"):
        match = WINDOW_RE.fullmatch(token)
        sh, sm, eh, em = map(int, match.groups())
        decoded.append(TimeWindow(sh * 60 + sm, 1440 if eh == 24 else eh * 60 + em))
    return tuple(decoded)


def _read_confirmed_rules(path):
    rules = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            row = {key: (value or "").strip() for key, value in raw.items() if key is not None}
            if not any(row.values()) or row["access_status"] == "unknown":
                continue
            rules.append(AccessRule(
                parking_id=int(row["parking_id"]), name=row["name"], day_group=row["day_group"],
                entry_windows=_decode_windows(row["entry_windows"]),
                exit_windows=_decode_windows(row["exit_windows"]),
                fee_windows=_decode_windows(row["fee_windows"]), fee_mode=row["fee_mode"],
                overnight_allowed=row["overnight_allowed"] == "true",
                access_status=row["access_status"], general_public=row["general_public"] == "true",
                effective_from=date.fromisoformat(row["effective_from"]),
                effective_to=(date.fromisoformat(row["effective_to"])
                              if row["effective_to"] else None),
                checked_at=date.fromisoformat(row["checked_at"]),
                evidence_method=row["evidence_method"], evidence_ref=row["evidence_ref"],
                note=row["note"],
            ))
    return rules


class AccessRulesRepository:
    """검증을 통과한 확정 규칙만 원자적으로 교체하는 메모리 저장소."""

    def __init__(self, path=ACCESS_RULES_CSV, parking_db=PARKING_DB):
        self.path = Path(path)
        self.parking_db = Path(parking_db)
        self._lock = threading.RLock()
        self._rules = {}
        self._attempted_fingerprint = object()
        self._status = {
            "status": "not_loaded", "rows_total": 0, "rules_loaded": 0,
            "lots_loaded": 0, "ignored_unconfirmed": 0, "using_previous": False,
            "validation_errors": [], "loaded_at": None,
        }

    def _fingerprint(self):
        try:
            stat = self.path.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def refresh(self, force=False):
        """변경된 CSV를 검증 후 적재한다. 실패하면 마지막 정상 규칙을 보존한다."""
        with self._lock:
            fingerprint = self._fingerprint()
            if not force and fingerprint == self._attempted_fingerprint:
                return self.status()
            self._attempted_fingerprint = fingerprint

            validation = validate_access_rules(self.path, self.parking_db)
            if not validation.valid:
                self._status = {
                    "status": "unavailable" if fingerprint is None else "invalid",
                    "rows_total": validation.rows,
                    "rules_loaded": sum(len(values) for values in self._rules.values()),
                    "lots_loaded": len({key[0] for key in self._rules}),
                    "ignored_unconfirmed": 0,
                    "using_previous": bool(self._rules),
                    "validation_errors": sorted({issue.code for issue in validation.errors}),
                    "loaded_at": self._status.get("loaded_at"),
                }
                return self.status()

            loaded = _read_confirmed_rules(self.path)
            indexed = {}
            for rule in loaded:
                indexed.setdefault((rule.parking_id, rule.day_group), []).append(rule)
            self._rules = {key: tuple(sorted(values, key=lambda item: item.effective_from))
                           for key, values in indexed.items()}
            self._status = {
                "status": "ready" if loaded else "empty",
                "rows_total": validation.rows,
                "rules_loaded": len(loaded),
                "lots_loaded": len({rule.parking_id for rule in loaded}),
                "ignored_unconfirmed": validation.rows - len(loaded),
                "using_previous": False,
                "validation_errors": [],
                "loaded_at": datetime.now(timezone.utc).isoformat(),
            }
            return self.status()

    def status(self):
        with self._lock:
            return self._status.copy()

    def lookup(self, parking_id, value, is_holiday=False):
        """해당 날짜에 적용되는 확정 규칙을 반환한다. 없으면 None이다."""
        value = value.date() if isinstance(value, datetime) else value
        group = day_group_for(value, is_holiday=is_holiday)
        with self._lock:
            for rule in self._rules.get((int(parking_id), group), ()):
                if rule.applies_on(value):
                    return rule
        return None

    def rules_for_date(self, value, is_holiday=False):
        """해당 날짜에 적용되는 확정 규칙을 parking_id별로 반환한다."""
        value = value.date() if isinstance(value, datetime) else value
        group = day_group_for(value, is_holiday=is_holiday)
        active = {}
        with self._lock:
            for (parking_id, day_group), rules in self._rules.items():
                if day_group != group:
                    continue
                for rule in rules:
                    if rule.applies_on(value):
                        active[parking_id] = rule
                        break
        return active
