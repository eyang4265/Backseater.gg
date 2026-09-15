"""Readable terminal logging with a detailed, plain-text file record."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import TextIO


_RESET = "\x1b[0m"
_DIM = "\x1b[2m"
_LEVEL_STYLES = {
    logging.DEBUG: ("DEBUG", "\x1b[36m"),
    logging.INFO: ("INFO ", "\x1b[32m"),
    logging.WARNING: ("WARN ", "\x1b[33m"),
    logging.ERROR: ("ERROR", "\x1b[31m"),
    logging.CRITICAL: ("FATAL", "\x1b[1;31m"),
}

_CATEGORY_BY_LOGGER = (
    ("bot_app.commands", "COMMAND"),
    ("bot_app.tracker", "TRACKER"),
    ("bot_app.announce", "ANNOUNCE"),
    ("bot_app.riot", "RIOT"),
    ("bot_app.http_debug", "RIOT"),
    ("bot_app.meetups", "MEETUP"),
    ("bot_app.store", "STORAGE"),
    ("bot_app.repositories", "STORAGE"),
    ("bot_app.match_cache", "STORAGE"),
    ("bot_app.ddragon", "METADATA"),
    ("bot_app.render", "RENDER"),
    ("bot_app.charts", "RENDER"),
    ("bot_app.config", "CONFIG"),
    ("discord", "DISCORD"),
)
_CATEGORY_COLORS = {
    "STARTUP": "\x1b[1;32m",
    "SHUTDOWN": "\x1b[1;31m",
    "COMMAND": "\x1b[1;34m",
    "TRACKER": "\x1b[36m",
    "ANNOUNCE": "\x1b[35m",
    "RIOT": "\x1b[1;35m",
    "MEETUP": "\x1b[33m",
    "STORAGE": "\x1b[34m",
    "RENDER": "\x1b[96m",
    "METADATA": "\x1b[95m",
    "CONFIG": "\x1b[90m",
    "DISCORD": "\x1b[94m",
    "APP": "\x1b[37m",
}


def _display_logger(name: str) -> str:
    """Shorten project logger names without obscuring third-party sources."""
    if name == "__main__":
        return "main"
    if name.startswith("bot_app."):
        return name.removeprefix("bot_app.")
    return name


def _record_category(record: logging.LogRecord) -> str:
    """Return an explicit category or infer one from the emitting subsystem."""
    explicit = getattr(record, "category", None)
    if explicit:
        return str(explicit).upper()[:9]
    for prefix, category in _CATEGORY_BY_LOGGER:
        if record.name == prefix or record.name.startswith(f"{prefix}."):
            return category
    return "APP"


class CategoryFilter(logging.Filter):
    """Attach a category so console and file handlers share the same label."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.category = _record_category(record)
        return True


class ConsoleFormatter(logging.Formatter):
    """Format records as compact, scannable terminal rows."""

    def __init__(self, *, use_color: bool) -> None:
        super().__init__(datefmt="%H:%M:%S")
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        """Render one record, indenting traceback continuation lines."""
        label, color = _LEVEL_STYLES.get(
            record.levelno, (record.levelname[:5].ljust(5), "")
        )
        timestamp = self.formatTime(record, self.datefmt)
        source = _display_logger(record.name)
        category_name = _record_category(record)
        category = category_name.ljust(9)
        category_color = _CATEGORY_COLORS.get(category_name, "\x1b[37m")
        message = record.getMessage()
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            message += "\n" + self.formatStack(record.stack_info)
        plain_prefix = f"{timestamp} │ {label} │ {category} │ {source} │ "
        continuation = " " * (len(plain_prefix) - 2) + "│ "
        message = message.replace("\n", f"\n{continuation}")
        if self.use_color:
            return (
                f"{_DIM}{timestamp}{_RESET} │ {color}{label}{_RESET} │ "
                f"{category_color}{category}{_RESET} │ "
                f"{_DIM}{source}{_RESET} │ {message}"
            )
        return plain_prefix + message


def _supports_color(stream: TextIO) -> bool:
    """Use ANSI styling only on interactive terminals that permit color."""
    return bool(getattr(stream, "isatty", lambda: False)()) and "NO_COLOR" not in os.environ


def configure_logging(level: str, *, log_path: str | Path = "bot.log") -> None:
    """Install distinct console and file handlers for the process."""
    console = logging.StreamHandler(sys.stderr)
    console.addFilter(CategoryFilter())
    console.setFormatter(ConsoleFormatter(use_color=_supports_color(sys.stderr)))

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.addFilter(CategoryFilter())
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s.%(msecs)03d %(levelname)-8s [%(category)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    logging.basicConfig(level=level, handlers=[console, file_handler], force=True)
