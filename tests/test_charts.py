"""Chart fallbacks that must work without matplotlib."""

import unittest
from unittest.mock import patch

from bot_app.charts import build_lp_chart


class LpChartTests(unittest.TestCase):
    def test_missing_matplotlib_returns_none(self) -> None:
        """Verify that missing matplotlib returns none."""
        with patch("bot_app.charts.MATPLOTLIB_AVAILABLE", False):
            self.assertIsNone(build_lp_chart([{"t": 1, "v": 100}], days=30))
