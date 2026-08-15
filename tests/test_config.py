"""Configuration parsing and validation."""

import unittest
from unittest.mock import patch

from bot_app.config import ConfigError, get_settings


VALID_SETTINGS = {
    "discord_token": "discord-token",
    "riot_api_key": "riot-key",
    "discord_owner_id": "123",
    "announcement_channel_id": "456",
}


class SettingsTests(unittest.TestCase):
    def _settings(self, values: dict[str, object]):
        """Handle settings."""
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        with (
            patch("bot_app.config._load_secrets_file", return_value=values),
            patch.dict("os.environ", {}, clear=True),
        ):
            return get_settings()

    def test_environment_values_override_the_secrets_file(self) -> None:
        """Verify that environment values override the secrets file."""
        get_settings.cache_clear()
        self.addCleanup(get_settings.cache_clear)
        with (
            patch("bot_app.config._load_secrets_file", return_value=VALID_SETTINGS),
            patch.dict(
                "os.environ", {"DISCORD_TOKEN": "environment-token"}, clear=True
            ),
        ):
            self.assertEqual(get_settings().discord_token, "environment-token")

    def test_parses_guild_ids_and_positive_runtime_values(self) -> None:
        """Verify that parses guild ids and positive runtime values."""
        settings = self._settings(
            {
                **VALID_SETTINGS,
                "guild_ids": "100, 200 300",
                "poll_interval_seconds": "45",
                "request_timeout_seconds": "2.5",
                "max_retries": "3",
            }
        )
        self.assertEqual(settings.guild_ids, (100, 200, 300))
        self.assertEqual(settings.poll_interval_seconds, 45)
        self.assertEqual(settings.request_timeout_seconds, 2.5)
        self.assertEqual(settings.max_retries, 3)

    def test_rejects_invalid_numeric_configuration(self) -> None:
        """Verify that rejects invalid numeric configuration."""
        for key, value in (
            ("poll_interval_seconds", "0"),
            ("max_retries", "nope"),
            ("announcement_channel_id", "-1"),
        ):
            with self.subTest(key=key):
                with self.assertRaises(ConfigError):
                    self._settings({**VALID_SETTINGS, key: value})

    def test_parses_feature_settings_and_validates_timezone(self) -> None:
        """Verify that parses feature settings and validates timezone."""
        settings = self._settings(
            {
                **VALID_SETTINGS,
                "timezone": "America/Los_Angeles",
                "max_tracked_accounts": "30",
                "match_cache_enabled": "false",
            }
        )
        self.assertEqual(settings.timezone, "America/Los_Angeles")
        self.assertEqual(settings.max_tracked_accounts, 30)
        self.assertFalse(settings.match_cache_enabled)
        with self.assertRaises(ConfigError):
            self._settings({**VALID_SETTINGS, "timezone": "Moon/SeaOfTranquility"})
        with self.assertRaises(ConfigError):
            self._settings({**VALID_SETTINGS, "timezone": "/absolute/path"})


if __name__ == "__main__":
    unittest.main()
