"""Terminal and file logging presentation."""

import io
import logging
import tempfile
import unittest
from pathlib import Path

from bot_app.logging_config import ConsoleFormatter, configure_logging


class ConsoleFormatterTests(unittest.TestCase):
    def test_console_record_is_compact_and_shortens_project_logger(self) -> None:
        """Terminal rows expose time, severity, category, source, and message."""
        record = logging.LogRecord(
            "bot_app.commands.shared", logging.WARNING, __file__, 1, "Slow %s", ("call",), None
        )
        rendered = ConsoleFormatter(use_color=False).format(record)
        self.assertRegex(
            rendered,
            r"^\d\d:\d\d:\d\d │ WARN  │ COMMAND   │ commands\.shared │ Slow call$",
        )

    def test_explicit_lifecycle_category_overrides_logger_inference(self) -> None:
        """Startup and shutdown events can be distinguished from other main logs."""
        record = logging.LogRecord("main", logging.INFO, __file__, 1, "Bot ready", (), None)
        record.category = "startup"
        rendered = ConsoleFormatter(use_color=False).format(record)
        self.assertIn("│ STARTUP   │ main │ Bot ready", rendered)

    def test_color_console_styles_the_category_and_resets_before_source(self) -> None:
        """Category color is terminal-only and cannot bleed into later columns."""
        record = logging.LogRecord(
            "bot_app.commands.shared", logging.INFO, __file__, 1, "Called", (), None
        )
        rendered = ConsoleFormatter(use_color=True).format(record)
        self.assertIn("\x1b[1;34mCOMMAND  \x1b[0m │", rendered)
        self.assertTrue(rendered.endswith("│ Called"))

    def test_console_traceback_continuation_is_visually_grouped(self) -> None:
        """Multiline details remain attached to their leading log row."""
        record = logging.LogRecord("worker", logging.ERROR, __file__, 1, "first\nsecond", (), None)
        rendered = ConsoleFormatter(use_color=False).format(record)
        lines = rendered.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith("│ second"))
        self.assertEqual(lines[1].index("│ second"), lines[0].index("│ first"))


class ConfigureLoggingTests(unittest.TestCase):
    def tearDown(self) -> None:
        """Close temporary handlers and restore a neutral root logger."""
        root = logging.getLogger()
        for handler in root.handlers[:]:
            handler.close()
            root.removeHandler(handler)

    def test_file_log_is_detailed_and_contains_no_terminal_escapes(self) -> None:
        """The durable log keeps full dates, milliseconds, and full logger names."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.log"
            configure_logging("INFO", log_path=path)
            logging.getLogger("bot_app.tracker").warning("poll delayed")
            for handler in logging.getLogger().handlers:
                handler.flush()
            contents = path.read_text(encoding="utf-8")
        self.assertRegex(
            contents,
            r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} WARNING  \[TRACKER\] bot_app\.tracker: poll delayed\n$",
        )
        self.assertNotIn("\x1b", contents)
