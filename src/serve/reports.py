#!/usr/bin/env python3
"""사용자 제보 큐 — 받아서 쌓아 두기만 한다.

★★ **제보가 서비스 데이터를 바꾸지 않는다.** 확정 규칙은 `parking_access_rules.csv` 뿐이고,
   그 파일은 사람이 조사표로만 고친다. 제보는 「무엇을 다시 확인할지」의 단서다.
   검수를 통과해도 자동 반영하지 않는다 — 재검증 큐로 넘어갈 뿐이다.
★  연락처·이름 같은 개인정보를 받지 않는다. 받아 두면 지켜야 할 것이 생긴다.
★  본문은 길이를 자르고 제어문자를 지워서 넣는다.
"""
import sqlite3
import threading
import time
import uuid

from src.config import INTERIM

DB_PATH = INTERIM / "reports.sqlite"
MAX_MESSAGE = 500
KINDS = {
    "access_hours": "입출차 가능시간이 달라요",
    "fee": "요금이 달라요",
    "closed": "문을 닫았거나 없어졌어요",
    "occupancy": "빈자리 수가 실제와 달라요",
    "other": "그 밖의 문제",
}
STATUSES = ("pending", "accepted", "rejected")
_lock = threading.RLock()


class InvalidReport(Exception):
    pass


def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS report(
        report_id TEXT PRIMARY KEY, parking_id INTEGER, kind TEXT, message TEXT,
        created_at REAL, status TEXT, reviewed_at REAL, reviewer_note TEXT)""")
    return con


def _clean(message):
    text = "".join(ch for ch in str(message or "") if ch.isprintable() or ch == "\n")
    return text.strip()[:MAX_MESSAGE]


def submit(parking_id, kind, message=""):
    if kind not in KINDS:
        raise InvalidReport(f"알 수 없는 제보 유형: {kind}")
    try:
        parking_id = int(parking_id)
    except (TypeError, ValueError):
        raise InvalidReport("주차장 ID가 잘못됐습니다")
    report_id = uuid.uuid4().hex
    with _lock, _db() as con:
        con.execute("INSERT INTO report VALUES (?,?,?,?,?,?,?,?)",
                    (report_id, parking_id, kind, _clean(message), time.time(),
                     "pending", None, None))
    return {"report_id": report_id, "status": "pending",
            "note": "제보는 검수 후 재조사 대상으로만 쓰입니다. 바로 반영되지는 않아요."}


def pending(limit=50):
    with _lock, _db() as con:
        rows = con.execute(
            "SELECT report_id, parking_id, kind, message, created_at FROM report "
            "WHERE status = 'pending' ORDER BY created_at LIMIT ?", (limit,)).fetchall()
    return [{"report_id": r[0], "parking_id": r[1], "kind": r[2],
             "label": KINDS.get(r[2], r[2]), "message": r[3], "created_at": r[4]}
            for r in rows]


def review(report_id, status, note=""):
    """검수 결과를 기록한다. **CSV 는 건드리지 않는다.**"""
    if status not in ("accepted", "rejected"):
        raise InvalidReport("검수 결과는 accepted 또는 rejected 여야 합니다")
    with _lock, _db() as con:
        cursor = con.execute(
            "UPDATE report SET status = ?, reviewed_at = ?, reviewer_note = ? "
            "WHERE report_id = ? AND status = 'pending'",
            (status, time.time(), _clean(note), report_id))
    if cursor.rowcount == 0:
        raise InvalidReport("없는 제보이거나 이미 검수됐습니다")
    return {"report_id": report_id, "status": status}


def summary():
    with _lock, _db() as con:
        rows = con.execute("SELECT status, COUNT(*) FROM report GROUP BY status").fetchall()
    counts = {status: 0 for status in STATUSES}
    counts.update(dict(rows))
    return {"counts": counts, "applies_automatically": False}


def accepted_lots():
    """검수를 통과한 제보가 가리키는 주차장. 재검증 큐가 참고한다."""
    with _lock, _db() as con:
        rows = con.execute("SELECT DISTINCT parking_id FROM report "
                           "WHERE status = 'accepted'").fetchall()
    return sorted(r[0] for r in rows)
