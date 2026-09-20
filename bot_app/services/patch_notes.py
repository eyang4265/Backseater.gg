"""Read the maintained, user-facing bot patch notes file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PATCH_NOTES_PATH = Path(__file__).resolve().parents[2] / "PATCH_NOTES.md"
_DATE_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class BotChange:
    """One dated, user-facing bot change."""

    date: str
    summary: str


class PatchNotesUnavailable(Exception):
    """The maintained patch notes cannot be read or contain no entries."""


def parse_patch_notes(content: str, limit: int = 5) -> tuple[BotChange, ...]:
    """Take the newest dated bullets from the maintained notes file."""
    date = None
    changes = []
    for line in content.splitlines():
        heading = _DATE_HEADING.fullmatch(line.strip())
        if heading:
            date = heading.group(1)
        elif date and line.startswith("- ") and line[2:].strip():
            changes.append(BotChange(date, line[2:].strip()))
            if len(changes) == limit:
                break
    if not changes:
        raise PatchNotesUnavailable("No bot updates are recorded yet.")
    return tuple(changes)


def recent_bot_changes(limit: int = 5) -> tuple[BotChange, ...]:
    """Read current bot notes on demand, including changes not yet committed."""
    try:
        content = PATCH_NOTES_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PatchNotesUnavailable("Bot patch notes are unavailable.") from error
    return parse_patch_notes(content, limit)
