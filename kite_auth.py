"""
Automated Zerodha Kite Connect login using credentials + TOTP from env.

Set values in bot_secrets.env (see bot_secrets.env.example).
Writes a fresh access_token to access_token.txt (valid until ~6 AM IST next day).
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pyotp
import requests
from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException

from bot_secrets import load_secrets, require_env

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent
TOKEN_FILE = ROOT / "access_token.txt"
LOGIN_LOCK_FILE = ROOT / ".kite_login.lock"

LOGIN_URL = "https://kite.zerodha.com/api/login"
TWOFA_URL = "https://kite.zerodha.com/api/twofa"


class KiteLoginError(RuntimeError):
    """Raised when automated Kite login fails."""


def load_kite_env() -> None:
    """Load secrets from bot_secrets.env (kept for backward compatibility)."""
    load_secrets()


def _require_env(name: str) -> str:
    try:
        return require_env(name)
    except RuntimeError as exc:
        raise KiteLoginError(str(exc)) from exc


def get_kite_credentials() -> dict[str, str]:
    load_kite_env()
    return {
        "api_key": _require_env("KITE_API_KEY"),
        "api_secret": _require_env("KITE_API_SECRET"),
        "user_id": _require_env("KITE_USER_ID"),
        "password": _require_env("KITE_PASSWORD"),
        "totp_secret": _require_env("KITE_TOTP_SECRET"),
    }


def read_access_token() -> str | None:
    if not TOKEN_FILE.exists():
        return None
    token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    return token or None


def write_access_token(token: str) -> None:
    TOKEN_FILE.write_text(token.strip() + "\n", encoding="utf-8")
    logger.info("Saved access token to %s", TOKEN_FILE)


def _parse_request_token(text: str) -> str | None:
    if not text:
        return None
    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    if "request_token" in query:
        return query["request_token"][0]
    match = re.search(r"request_token=([^&\s'\"]+)", text)
    return match.group(1) if match else None


def _extract_request_token(session: requests.Session, api_key: str) -> str:
    kite = KiteConnect(api_key=api_key)
    candidates = [
        kite.login_url(),
        f"https://kite.trade/connect/login?v=3&api_key={api_key}",
        f"https://kite.trade/connect/login?v=3&api_key={api_key}&skip_session=true",
    ]

    for url in candidates:
        try:
            resp = session.get(url, allow_redirects=True, timeout=30)
            for hop in list(resp.history) + [resp]:
                for source in (hop.url, hop.headers.get("Location", "")):
                    token = _parse_request_token(source)
                    if token:
                        return token
        except requests.RequestException as exc:
            token = _parse_request_token(str(exc))
            if token:
                return token

    raise KiteLoginError("Could not extract request_token after login (check redirect URL / API key).")


@contextmanager
def _login_lock():
    """Only one login flow at a time (bot + systemd timer share the same VM)."""
    LOGIN_LOCK_FILE.touch(exist_ok=True)
    fd = os.open(LOGIN_LOCK_FILE, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def login_and_get_access_token(creds: dict[str, str] | None = None) -> str:
    """Full automated login: password + TOTP → access_token."""
    creds = creds or get_kite_credentials()
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    kite = KiteConnect(api_key=creds["api_key"])
    session.get(kite.login_url(), timeout=30)

    login_resp = session.post(
        LOGIN_URL,
        data={"user_id": creds["user_id"], "password": creds["password"]},
        timeout=30,
    )
    login_data = login_resp.json()
    if login_data.get("status") != "success":
        raise KiteLoginError(login_data.get("message", "Kite login failed"))

    request_id = login_data["data"]["request_id"]
    totp_code = pyotp.TOTP(creds["totp_secret"]).now()

    twofa_resp = session.post(
        TWOFA_URL,
        data={
            "user_id": creds["user_id"],
            "request_id": request_id,
            "twofa_value": totp_code,
            "twofa_type": "totp",
        },
        timeout=30,
    )
    twofa_data = twofa_resp.json()
    if twofa_data.get("status") != "success":
        raise KiteLoginError(twofa_data.get("message", "Kite 2FA failed — check TOTP secret / clock"))

    request_token = _extract_request_token(session, creds["api_key"])
    try:
        session_data = kite.generate_session(request_token, api_secret=creds["api_secret"])
    except TokenException as exc:
        raise KiteLoginError(str(exc)) from exc
    access_token = session_data["access_token"]
    write_access_token(access_token)
    return access_token


def login_with_retries(
    creds: dict[str, str] | None = None,
    max_attempts: int = 8,
    delay_sec: int = 45,
) -> str:
    """Login with lock + retries (Zerodha can reject rapid / parallel logins)."""
    creds = creds or get_kite_credentials()
    api_key = creds["api_key"]
    last_err: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            with _login_lock():
                existing = read_access_token()
                if existing and token_is_valid(api_key, existing):
                    logger.info("Valid access token already present (skip login)")
                    return existing
                logger.info("Kite login attempt %s/%s", attempt, max_attempts)
                return login_and_get_access_token(creds)
        except (KiteLoginError, TokenException) as exc:
            last_err = exc
            logger.warning("Kite login attempt %s/%s failed: %s", attempt, max_attempts, exc)
            if attempt < max_attempts:
                time.sleep(delay_sec)

    raise KiteLoginError(
        f"Kite login failed after {max_attempts} attempts: {last_err}"
    )


def token_is_valid(api_key: str, access_token: str) -> bool:
    try:
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        kite.profile()
        return True
    except TokenException:
        return False
    except Exception as exc:
        logger.warning("Token validation error: %s", exc)
        return False


def ensure_access_token(force: bool = False, max_retries: int = 8) -> str:
    """
    Return a working access token — reuse access_token.txt when valid,
    otherwise run automated login (with retries + lock).
    """
    creds = get_kite_credentials()
    api_key = creds["api_key"]

    if not force:
        existing = read_access_token()
        if existing and token_is_valid(api_key, existing):
            logger.info("Reusing existing Kite access token")
            return existing

    logger.info("Generating fresh Kite access token...")
    return login_with_retries(creds, max_attempts=max_retries)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        token = ensure_access_token(force="--force" in sys.argv)
        print(f"Kite access token ready ({len(token)} chars) → {TOKEN_FILE}")
    except KiteLoginError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
