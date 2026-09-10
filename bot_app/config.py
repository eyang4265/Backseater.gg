"""Process configuration.

Secrets are read from the environment first, then from ``json/secrets.json``
(which is gitignored) so a local checkout keeps working without exporting
anything. Nothing is hardcoded in source.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOGGER = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
JSON_DIR = REPO_ROOT / "json"
SECRETS_PATH = JSON_DIR / "secrets.json"


class ConfigError(RuntimeError):
    """A required setting is missing."""


@dataclass(frozen=True)
class Settings:
    discord_token: str
    riot_api_key: str
    discord_owner_id: int
    guild_ids: tuple[int, ...]
    announcement_channel_id: int
    openai_api_key: str = ""
    openai_model: str = "gpt-5-mini"
    poll_interval_seconds: int = 120
    max_tracked_accounts: int = 50
    timezone: str = "UTC"
    match_cache_enabled: bool = True
    tft_api_key: str = ""
    sync_commands_enabled: bool = True

    riot_rate_limits: tuple[tuple[int, float], ...] = ((20, 1.0), (100, 120.0))
    request_timeout_seconds: float = 20.0
    max_retries: int = 4
    log_level: str = "INFO"
    _source: str = field(default="env", repr=False)


def _load_secrets_file() -> dict[str, object]:
    """Load secrets file."""
    try:
        with SECRETS_PATH.open(encoding="utf-8") as secrets_file:
            data = json.load(secrets_file)
    except FileNotFoundError:
        LOGGER.debug("No secrets file found at %s; using environment only", SECRETS_PATH)
        return {}
    except (json.JSONDecodeError, OSError) as error:
        LOGGER.warning("Ignoring unreadable %s: %s", SECRETS_PATH, error)
        return {}
    LOGGER.debug("Loaded %d secret keys from %s", len(data) if isinstance(data, dict) else 0, SECRETS_PATH)
    return data if isinstance(data, dict) else {}


def _get(file_values: dict[str, object], key: str, default: object = None) -> object:
    """Environment wins over the secrets file, which wins over the default."""
    env_value = os.environ.get(key.upper())
    if env_value not in (None, ""):
        return env_value
    if key in file_values:
        return file_values[key]
    return default


def _require(file_values: dict[str, object], key: str) -> str:
    """Handle require."""
    value = _get(file_values, key)
    if value is None or not str(value).strip():
        raise ConfigError(
            f"Missing required setting {key!r}. Set the {key.upper()} environment "
            f"variable or add a {key!r} entry to {SECRETS_PATH}."
        )
    return str(value).strip()


def _int_list(value: object) -> tuple[int, ...]:
    """Handle list."""
    if value is None or value == "":
        return ()
    values = value.replace(",", " ").split() if isinstance(value, str) else value
    if not isinstance(values, (list, tuple)):
        values = (values,)
    try:
        return tuple(int(item) for item in values)
    except (TypeError, ValueError) as error:
        raise ConfigError(
            "guild_ids must be a comma-separated list of numeric Discord IDs."
        ) from error


def _positive_int(file_values: dict[str, object], key: str, default: int) -> int:
    """Handle int."""
    try:
        value = int(str(_get(file_values, key, default)))
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{key} must be an integer.") from error
    if value <= 0:
        raise ConfigError(f"{key} must be greater than zero.")
    return value


def _positive_float(file_values: dict[str, object], key: str, default: float) -> float:
    """Handle float."""
    try:
        value = float(str(_get(file_values, key, default)))
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{key} must be a number.") from error
    if value <= 0:
        raise ConfigError(f"{key} must be greater than zero.")
    return value


def _boolean(file_values: dict[str, object], key: str, default: bool) -> bool:
    """Handle boolean."""
    value = _get(file_values, key, default)
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} must be true or false.")


def _timezone(file_values: dict[str, object]) -> str:
    """Handle timezone."""
    name = str(_get(file_values, "timezone", "UTC") or "UTC")
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(
            f"timezone must be a valid IANA timezone (got {name!r})."
        ) from error
    return name


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Resolve settings once per process."""
    LOGGER.debug("Resolving process settings")
    file_values = _load_secrets_file()
    settings = Settings(
        discord_token=_require(file_values, "discord_token"),
        riot_api_key=_require(file_values, "riot_api_key"),
        tft_api_key=str(_get(file_values, "tft_api_key", "") or ""),
        discord_owner_id=_positive_int(file_values, "discord_owner_id", 0),
        guild_ids=_int_list(_get(file_values, "guild_ids", ())),
        announcement_channel_id=_positive_int(
            file_values, "announcement_channel_id", 0
        ),
        openai_api_key=str(_get(file_values, "openai_api_key", "") or ""),
        openai_model=str(_get(file_values, "openai_model", "gpt-5-mini")),
        poll_interval_seconds=_positive_int(file_values, "poll_interval_seconds", 120),
        max_tracked_accounts=_positive_int(file_values, "max_tracked_accounts", 50),
        timezone=_timezone(file_values),
        match_cache_enabled=_boolean(file_values, "match_cache_enabled", True),
        sync_commands_enabled=_boolean(file_values, "sync_commands_enabled", True),
        request_timeout_seconds=_positive_float(
            file_values, "request_timeout_seconds", 20.0
        ),
        max_retries=_positive_int(file_values, "max_retries", 4),
        log_level=str(_get(file_values, "log_level", "INFO")),
        _source="env+file" if file_values else "env",
    )
    LOGGER.info(
        "Loaded settings from %s (poll_interval=%ss, log_level=%s)",
        settings._source, settings.poll_interval_seconds, settings.log_level,
    )
    return settings
