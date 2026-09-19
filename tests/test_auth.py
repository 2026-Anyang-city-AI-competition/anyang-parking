import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from src.serve import auth

CONFIG = {"client_id": "rest-key", "client_secret": "", "redirect_uri": "http://localhost:5173/cb",
          "pepper": "test-pepper", "secure_cookie": False, "enabled": True}


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(auth, "DB_PATH", Path(self.tmp.name) / "accounts.sqlite")
        patcher.start()
        self.addCleanup(patcher.stop)
        config = patch.object(auth, "config", return_value=dict(CONFIG))
        config.start()
        self.addCleanup(config.stop)

    def login(self, account_id=12345):
        state = parse_qs(urlparse(auth.login_url()).query)["state"][0]
        return auth.complete_login("code", state, account_id=account_id), state

    # ── state ───────────────────────────────────────────────
    def test_login_url_carries_state_and_client_id(self):
        query = parse_qs(urlparse(auth.login_url()).query)
        self.assertEqual(query["client_id"], ["rest-key"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertTrue(len(query["state"][0]) >= 32)

    def test_state_is_single_use(self):
        token, state = self.login()
        self.assertTrue(token)
        with self.assertRaises(auth.AuthFailed):
            auth.complete_login("code", state, account_id=12345)

    def test_unknown_or_missing_state_is_rejected(self):
        for state in ("", "made-up-state"):
            with self.assertRaises(auth.AuthFailed):
                auth.complete_login("code", state, account_id=1)

    def test_expired_state_is_rejected(self):
        state = parse_qs(urlparse(auth.login_url()).query)["state"][0]
        with patch.object(auth, "STATE_TTL_SEC", -1):
            with self.assertRaises(auth.AuthFailed):
                auth.complete_login("code", state, account_id=1)

    # ── 저장 정책 ────────────────────────────────────────────
    def test_provider_id_is_never_stored_in_the_clear(self):
        self.login(account_id=987654321)
        blob = Path(auth.DB_PATH).read_bytes()
        self.assertNotIn(b"987654321", blob)

    def test_session_cookie_value_is_not_stored(self):
        token, _ = self.login()
        blob = Path(auth.DB_PATH).read_bytes()
        self.assertNotIn(token.encode(), blob)

    def test_access_token_is_fetched_then_discarded(self):
        calls = {}

        class Response:
            status_code = 200

            def __init__(self, payload):
                self._payload = payload

            def json(self):
                return self._payload

        def post(url, data=None, timeout=None):
            calls["token"] = True
            return Response({"access_token": "SECRET-TOKEN"})

        def get(url, headers=None, timeout=None):
            calls["profile"] = headers["Authorization"]
            return Response({"id": 555})

        state = parse_qs(urlparse(auth.login_url()).query)["state"][0]
        with patch.object(auth.requests, "post", side_effect=post), \
             patch.object(auth.requests, "get", side_effect=get):
            auth.complete_login("code", state)
        self.assertTrue(calls["token"])
        self.assertIn("SECRET-TOKEN", calls["profile"])       # 쓰기는 한다
        blob = Path(auth.DB_PATH).read_bytes()
        self.assertNotIn(b"SECRET-TOKEN", blob)               # 저장은 하지 않는다
        self.assertFalse(auth.status()["stores_access_token"])

    # ── 세션 ────────────────────────────────────────────────
    def test_session_resolves_and_logout_revokes(self):
        token, _ = self.login()
        account = auth.resolve(token)
        self.assertTrue(account)
        self.assertTrue(auth.logout(token))
        with self.assertRaises(auth.NotAuthenticated):
            auth.resolve(token)

    def test_expired_session_is_rejected(self):
        with patch.object(auth, "SESSION_TTL_SEC", -1):
            token, _ = self.login()
        with self.assertRaises(auth.NotAuthenticated):
            auth.resolve(token)

    def test_same_account_reuses_one_record(self):
        first, _ = self.login(account_id=42)
        second, _ = self.login(account_id=42)
        self.assertNotEqual(first, second)
        self.assertEqual(auth.resolve(first), auth.resolve(second))

    # ── 설정 ────────────────────────────────────────────────
    def test_preferences_round_trip(self):
        token, _ = self.login()
        account = auth.resolve(token)
        auth.set_preferences(account, {"benefit_codes": ["경형자동차"], "default_minutes": 60})
        stored = auth.get_preferences(account)["preferences"]
        self.assertEqual(stored["benefit_codes"], ["경형자동차"])
        self.assertEqual(stored["default_minutes"], 60)

    def test_evidence_fields_are_dropped_before_storage(self):
        token, _ = self.login()
        account = auth.resolve(token)
        auth.set_preferences(account, {
            "benefit_codes": ["장애인_중"], "default_minutes": 120,
            "disability_grade": "1급", "certificate_no": "123-45", "rrn": "900101-1234567"})
        stored = auth.get_preferences(account)["preferences"]
        self.assertEqual(set(stored), {"benefit_codes", "default_minutes"})
        blob = Path(auth.DB_PATH).read_bytes()
        for leaked in (b"900101", b"123-45", b"disability_grade"):
            self.assertNotIn(leaked, blob)

    def test_unknown_benefit_codes_are_dropped(self):
        self.assertEqual(
            auth.clean_preferences({"benefit_codes": ["경형자동차", "없는코드"]})["benefit_codes"],
            ["경형자동차"])

    def test_delete_removes_account_and_sessions(self):
        token, _ = self.login()
        account = auth.resolve(token)
        self.assertTrue(auth.delete_account(account))
        with self.assertRaises(auth.NotAuthenticated):
            auth.resolve(token)
        with self.assertRaises(auth.NotAuthenticated):
            auth.get_preferences(account)

    # ── 미설정 ──────────────────────────────────────────────
    def test_unconfigured_raises_rather_than_half_working(self):
        with patch.object(auth, "config", return_value={**CONFIG, "enabled": False,
                                                        "pepper": ""}):
            for call in (lambda: auth.login_url(),
                         lambda: auth.complete_login("c", "s", account_id=1),
                         lambda: auth.resolve("t")):
                with self.assertRaises(auth.AuthNotConfigured):
                    call()


if __name__ == "__main__":
    unittest.main()
