#!/usr/bin/env python3
"""카카오 로그인 — 설정 동기화만을 위한 최소 계정.

저장 정책 (이 파일의 존재 이유다):

★★ **액세스 토큰을 저장하지 않는다.** 코드를 토큰으로 바꾸고, 그 토큰으로 계정 식별자를
   한 번 읽은 뒤 즉시 버린다. 저장하지 않으므로 암호화할 토큰도, 유출될 토큰도 없다.
   카카오 API 를 대신 호출할 일이 없으니 토큰을 들고 있을 이유가 없다.
★★ **카카오 계정 식별자도 원본으로 저장하지 않는다.** 서버 페퍼로 HMAC 해시해서 넣는다.
   같은 사람을 다시 알아보는 데는 충분하고, DB 가 새도 원본 ID 는 복원되지 않는다.
★★ **감면 증빙자료를 저장하지 않는다.** 저장하는 설정은 감면 **코드**와 기본 주차시간뿐이다.
★  세션 토큰도 해시로만 저장한다. 쿠키에 담긴 원본은 서버 DB 어디에도 없다.

환경변수:
  KAKAO_CLIENT_ID      REST API 키 (없으면 KAKAO_REST_KEY 를 쓴다)
  KAKAO_CLIENT_SECRET  선택 — 카카오 콘솔에서 켰을 때만
  KAKAO_REDIRECT_URI   콜백 주소
  SESSION_PEPPER       해시용 서버 비밀. 없으면 로그인 기능을 켜지 않는다.
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlencode

import requests

from src.config import INTERIM
from src.serve import fare_tables as T
from src.serve.routing import _key as _rest_key

AUTHORIZE = "https://kauth.kakao.com/oauth/authorize"
TOKEN = "https://kauth.kakao.com/oauth/token"
PROFILE = "https://kapi.kakao.com/v2/user/me"
UNLINK = "https://kapi.kakao.com/v1/user/unlink"
TIMEOUT = 10

STATE_TTL_SEC = 600            # 10분. OAuth 왕복에 그 이상 걸릴 이유가 없다.
SESSION_TTL_SEC = 60 * 60 * 24 * 30
SESSION_COOKIE = "anyang_session"
DB_PATH = INTERIM / "accounts.sqlite"

_lock = threading.RLock()


class AuthNotConfigured(Exception):
    """환경변수가 없어 로그인 기능을 켜지 않은 상태. 500 이 아니라 503 으로 알린다."""


class AuthFailed(Exception):
    """state 불일치·코드 교환 실패 등. 원인을 사용자에게 자세히 말하지 않는다."""


class NotAuthenticated(Exception):
    pass


def config():
    # OAuth client_id 는 카카오 REST API 키와 같다. 기존 .env 설정을 그대로 쓴다.
    client_id = os.getenv("KAKAO_CLIENT_ID") or _rest_key() or ""
    pepper = os.getenv("SESSION_PEPPER") or ""
    redirect = os.getenv("KAKAO_REDIRECT_URI") or ""
    return {
        "client_id": client_id,
        "client_secret": os.getenv("KAKAO_CLIENT_SECRET") or "",
        "redirect_uri": redirect,
        "pepper": pepper,
        "secure_cookie": not redirect.startswith("http://localhost")
                         and not redirect.startswith("http://127.0.0.1"),
        "enabled": bool(client_id and pepper and redirect),
    }


def _require(cfg=None):
    cfg = cfg or config()
    if not cfg["enabled"]:
        raise AuthNotConfigured(
            "KAKAO_CLIENT_ID · KAKAO_REDIRECT_URI · SESSION_PEPPER 가 설정되지 않았습니다")
    return cfg


def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
        CREATE TABLE IF NOT EXISTS account(
            account_id TEXT PRIMARY KEY,      -- 카카오 ID 의 HMAC. 원본은 저장하지 않는다.
            created_at REAL, updated_at REAL,
            preferences TEXT);
        CREATE TABLE IF NOT EXISTS session(
            token_hash TEXT PRIMARY KEY,      -- 쿠키 원본은 저장하지 않는다.
            account_id TEXT, created_at REAL, expires_at REAL);
        CREATE TABLE IF NOT EXISTS oauth_state(
            state_hash TEXT PRIMARY KEY, created_at REAL);
    """)
    return con


def _hash(value, pepper):
    return hmac.new(pepper.encode(), str(value).encode(), hashlib.sha256).hexdigest()


def _sweep(con, now=None):
    now = now or time.time()
    con.execute("DELETE FROM oauth_state WHERE created_at < ?", (now - STATE_TTL_SEC,))
    con.execute("DELETE FROM session WHERE expires_at < ?", (now,))


def clean_preferences(value):
    """저장 전에 좁힌다. 감면 **코드**와 기본 주차시간 말고는 무엇도 받지 않는다."""
    value = value if isinstance(value, dict) else {}
    codes = value.get("benefit_codes")
    codes = [c for c in codes if c in T.DISCOUNTS][:5] if isinstance(codes, list) else []
    minutes = value.get("default_minutes")
    minutes = int(minutes) if isinstance(minutes, (int, float)) and 1 <= minutes <= 10080 else 120
    return {"benefit_codes": list(dict.fromkeys(codes)), "default_minutes": minutes}


def login_url(cfg=None):
    """state 를 서버에 적어 두고 카카오 동의 화면 주소를 만든다."""
    cfg = _require(cfg)
    state = secrets.token_urlsafe(32)
    with _lock, _db() as con:
        _sweep(con)
        con.execute("INSERT INTO oauth_state VALUES (?,?)",
                    (_hash(state, cfg["pepper"]), time.time()))
    query = urlencode({"response_type": "code", "client_id": cfg["client_id"],
                       "redirect_uri": cfg["redirect_uri"], "state": state})
    return f"{AUTHORIZE}?{query}"


def _consume_state(state, cfg):
    """state 는 1회용이다. 쓰면 바로 지운다(재생 공격 차단)."""
    if not state:
        raise AuthFailed("state 없음")
    with _lock, _db() as con:
        _sweep(con)
        row = con.execute("DELETE FROM oauth_state WHERE state_hash = ? RETURNING created_at",
                          (_hash(state, cfg["pepper"]),)).fetchone()
    if row is None:
        raise AuthFailed("state 불일치 또는 만료")


def _kakao_account_id(code, cfg):
    """코드 → 토큰 → 계정 ID. **토큰은 이 함수 밖으로 나가지 않는다.**"""
    payload = {"grant_type": "authorization_code", "client_id": cfg["client_id"],
               "redirect_uri": cfg["redirect_uri"], "code": code}
    if cfg["client_secret"]:
        payload["client_secret"] = cfg["client_secret"]
    try:
        token_response = requests.post(TOKEN, data=payload, timeout=TIMEOUT)
        if token_response.status_code != 200:
            raise AuthFailed("토큰 교환 실패")
        access_token = token_response.json().get("access_token")
        if not access_token:
            raise AuthFailed("토큰 없음")
        profile = requests.get(PROFILE, headers={"Authorization": f"Bearer {access_token}"},
                               timeout=TIMEOUT)
        if profile.status_code != 200:
            raise AuthFailed("프로필 조회 실패")
        account_id = profile.json().get("id")
    except requests.RequestException:
        raise AuthFailed("카카오에 연결하지 못했습니다")
    finally:
        # 지역 변수를 남겨 두지 않는다. 저장은 애초에 하지 않는다.
        payload = None
    if account_id is None:
        raise AuthFailed("계정 식별자 없음")
    return account_id


def complete_login(code, state, cfg=None, account_id=None):
    """콜백 처리. 반환값은 세션 쿠키에 담을 원본 토큰이다(서버에는 해시만 남는다)."""
    cfg = _require(cfg)
    _consume_state(state, cfg)
    raw_id = account_id if account_id is not None else _kakao_account_id(code, cfg)
    hashed = _hash(raw_id, cfg["pepper"])
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock, _db() as con:
        _sweep(con, now)
        con.execute("""INSERT INTO account(account_id, created_at, updated_at, preferences)
                       VALUES (?,?,?,?) ON CONFLICT(account_id) DO NOTHING""",
                    (hashed, now, now, json.dumps(clean_preferences({}))))
        con.execute("INSERT INTO session VALUES (?,?,?,?)",
                    (_hash(token, cfg["pepper"]), hashed, now, now + SESSION_TTL_SEC))
    return token


def resolve(token, cfg=None):
    cfg = _require(cfg)
    if not token:
        raise NotAuthenticated
    with _lock, _db() as con:
        _sweep(con)
        row = con.execute(
            "SELECT account_id, expires_at FROM session WHERE token_hash = ?",
            (_hash(token, cfg["pepper"]),)).fetchone()
    if row is None or row[1] < time.time():
        raise NotAuthenticated
    return row[0]


def logout(token, cfg=None):
    cfg = _require(cfg)
    if not token:
        return False
    with _lock, _db() as con:
        cursor = con.execute("DELETE FROM session WHERE token_hash = ?",
                             (_hash(token, cfg["pepper"]),))
    return cursor.rowcount > 0


def get_preferences(account_id):
    with _lock, _db() as con:
        row = con.execute("SELECT preferences, created_at, updated_at FROM account "
                          "WHERE account_id = ?", (account_id,)).fetchone()
    if row is None:
        raise NotAuthenticated
    try:
        preferences = clean_preferences(json.loads(row[0]))
    except (TypeError, ValueError):
        preferences = clean_preferences({})
    return {"preferences": preferences, "created_at": row[1], "updated_at": row[2]}


def set_preferences(account_id, value):
    cleaned = clean_preferences(value)
    now = time.time()
    with _lock, _db() as con:
        cursor = con.execute(
            "UPDATE account SET preferences = ?, updated_at = ? WHERE account_id = ?",
            (json.dumps(cleaned), now, account_id))
    if cursor.rowcount == 0:
        raise NotAuthenticated
    return {"preferences": cleaned, "updated_at": now}


def delete_account(account_id):
    """계정과 설정, 모든 세션을 지운다. 남기는 것 없음."""
    with _lock, _db() as con:
        con.execute("DELETE FROM session WHERE account_id = ?", (account_id,))
        cursor = con.execute("DELETE FROM account WHERE account_id = ?", (account_id,))
    return cursor.rowcount > 0


def status():
    cfg = config()
    return {"enabled": cfg["enabled"],
            "missing": [name for name, value in
                        (("KAKAO_CLIENT_ID", cfg["client_id"]),
                         ("KAKAO_REDIRECT_URI", cfg["redirect_uri"]),
                         ("SESSION_PEPPER", cfg["pepper"])) if not value],
            "stores_access_token": False,
            "stores_provider_id": False}
