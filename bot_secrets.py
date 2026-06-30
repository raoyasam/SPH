"""
Load all bot secrets from bot_secrets.env (single source of truth).

Legacy files kite_secrets.env and telegram_secrets.env are still read if
bot_secrets.env is missing, so existing setups keep working until you migrate.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SECRETS_FILE = ROOT / "bot_secrets.env"
LEGACY_KITE_FILE = ROOT / "kite_secrets.env"
LEGACY_TELEGRAM_FILE = ROOT / "telegram_secrets.env"

# Keys expected in bot_secrets.env
KITE_KEYS = (
    "KITE_API_KEY",
    "KITE_API_SECRET",
    "KITE_USER_ID",
    "KITE_PASSWORD",
    "KITE_TOTP_SECRET",
)
TELEGRAM_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)
OPTIONAL_KEYS = (
    "BOT_DASHBOARD_TOKEN",
)
ALL_SECRET_KEYS = KITE_KEYS + TELEGRAM_KEYS + OPTIONAL_KEYS


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            values[key] = value
    return values


def _apply_to_environ(values: dict[str, str], overwrite: bool = False) -> None:
    for key, value in values.items():
        if overwrite or key not in os.environ:
            os.environ[key] = value


def load_secrets() -> None:
    """Load secrets into os.environ. Call once at process startup."""
    if SECRETS_FILE.exists():
        _apply_to_environ(_parse_env_file(SECRETS_FILE), overwrite=True)
        return

    # Legacy: merge kite + telegram env files
    _apply_to_environ(_parse_env_file(LEGACY_KITE_FILE))
    _apply_to_environ(_parse_env_file(LEGACY_TELEGRAM_FILE))


def require_env(name: str) -> str:
    load_secrets()
    value = os.environ.get(name, "").strip()
    if not value or "your-" in value.lower() or "REPLACE" in value.upper():
        raise RuntimeError(
            f"{name} is not set. Copy bot_secrets.env.example to bot_secrets.env and fill it in."
        )
    return value


def get_kite_api_key() -> str:
    return require_env("KITE_API_KEY")


def get_telegram_config() -> tuple[str, str]:
    return require_env("TELEGRAM_BOT_TOKEN"), require_env("TELEGRAM_CHAT_ID")


def migrate_legacy_secrets() -> bool:
    """
    Create bot_secrets.env from kite_secrets.env + telegram_secrets.env if missing.
    Returns True if a new file was written.
    """
    if SECRETS_FILE.exists():
        return False

    merged: dict[str, str] = {}
    merged.update(_parse_env_file(LEGACY_KITE_FILE))
    merged.update(_parse_env_file(LEGACY_TELEGRAM_FILE))
    if not merged:
        return False

    lines = [
        "# Auto-migrated from legacy env files. Add any missing Kite keys.\n",
        "# See bot_secrets.env.example for descriptions.\n\n",
    ]
    for key in ALL_SECRET_KEYS:
        if key in merged and merged[key]:
            val = merged[key].replace("'", "'\\''")
            lines.append(f"{key}='{val}'\n")
        else:
            lines.append(f"# {key}=''\n")

    SECRETS_FILE.write_text("".join(lines), encoding="utf-8")
    return True
