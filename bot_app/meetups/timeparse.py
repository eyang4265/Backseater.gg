"""Lenient natural-time parsing for meetup candidate slots.

Meetup times are entered as free text ("fri 7pm", "tomorrow 18:30",
"2026-09-04 20:00") because a slash command cannot offer a usable date
picker.  Every parsed value is normalised to a UTC epoch immediately, so
the rest of the feature never handles a wall-clock time or a timezone;
rendering is done with Discord's own ``<t:epoch:style>`` markup, which
shows each viewer the slot in their own local timezone.

This module is deliberately pure: no Discord, no storage, no network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MAX_SLOTS = 20
"""Upper bound on candidate slots per axis, below Discord's 25-option cap."""


class TimeParseError(ValueError):
    """Raised when a candidate time cannot be understood."""


_AMPM_RE = re.compile(r"(?<![\d:])(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?(?![a-z])", re.I)
_HHMM_RE = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})(?!\d)")
_ISO_RE = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_SLASH_RE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?$")

_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


@dataclass(frozen=True)
class ParsedTime:
    """One candidate slot, already resolved to an absolute instant."""

    epoch: int
    source: str


def _extract_time(text: str) -> tuple[int, int, str]:
    """Pull an hour/minute out of ``text``, returning the leftover date part."""
    match = _AMPM_RE.search(text)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if not 1 <= hour <= 12:
            raise TimeParseError(f"{hour} is not a 12-hour clock hour")
        if match.group(3).lower() == "p":
            hour = hour if hour == 12 else hour + 12
        elif hour == 12:
            hour = 0
    else:
        match = _HHMM_RE.search(text)
        if not match:
            raise TimeParseError("no time of day found (try `7pm` or `19:30`)")
        hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise TimeParseError("that is not a valid time of day")
    remainder = (text[: match.start()] + " " + text[match.end() :]).strip()
    return hour, minute, remainder


def _next_weekday(today: date, weekday: int, *, allow_today: bool) -> date:
    """Return the next date falling on ``weekday``."""
    ahead = (weekday - today.weekday()) % 7
    if ahead == 0 and not allow_today:
        ahead = 7
    return today + timedelta(days=ahead)


def _strip_ordinal(token: str) -> str:
    """Drop an English ordinal suffix so ``3rd`` reads as ``3``."""
    return re.sub(r"^(\d{1,2})(st|nd|rd|th)$", r"\1", token)


def _extract_date(text: str, today: date) -> tuple[date, bool]:
    """Resolve the date part, reporting whether it was implicit.

    An implicit date ("", "7pm", "friday") may roll forward when the
    resulting instant has already passed; an explicit one may not.
    """
    cleaned = re.sub(r"[,@]", " ", text).strip().lower()
    cleaned = re.sub(r"\bon\b|\bat\b|\bnext\b|\bthis\b", " ", cleaned).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return today, True
    if cleaned == "today":
        # Explicit, so an already-passed "today 9am" is an error rather
        # than a silent jump to tomorrow.
        return today, False
    if cleaned in {"tomorrow", "tmr", "tmrw"}:
        return today + timedelta(days=1), False
    if cleaned in _WEEKDAYS:
        return _next_weekday(today, _WEEKDAYS[cleaned], allow_today=True), True

    iso = _ISO_RE.match(cleaned)
    if iso:
        return _build_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))), False

    slashed = _SLASH_RE.match(cleaned)
    if slashed:
        month, day = int(slashed.group(1)), int(slashed.group(2))
        raw_year = slashed.group(3)
        if raw_year:
            year = int(raw_year)
            year += 2000 if year < 100 else 0
            return _build_date(year, month, day), False
        candidate = _build_date(today.year, month, day)
        # A bare M/D that has already gone by this year means next year.
        return (candidate if candidate >= today else _build_date(today.year + 1, month, day)), False

    tokens = [_strip_ordinal(token) for token in cleaned.split()]
    if len(tokens) == 2:
        for month_token, day_token in (tokens, tokens[::-1]):
            month = _MONTHS.get(month_token)
            if month is not None and day_token.isdigit():
                day = int(day_token)
                candidate = _build_date(today.year, month, day)
                return (
                    candidate if candidate >= today else _build_date(today.year + 1, month, day)
                ), False
    raise TimeParseError(f"could not read a date from {text.strip()!r}")


def _build_date(year: int, month: int, day: int) -> date:
    """Construct a date, reporting impossible calendar values plainly."""
    try:
        return date(year, month, day)
    except ValueError as error:
        raise TimeParseError(f"{year:04d}-{month:02d}-{day:02d} is not a real date") from error


def parse_when(text: str, timezone: str, *, now: datetime | None = None) -> int:
    """Parse one candidate time into a UTC epoch.

    ``timezone`` is the IANA zone the text is written in; ``now`` is the
    reference instant used to resolve relative phrasing, defaulting to the
    current time in that zone.
    """
    try:
        zone = ZoneInfo(timezone)
    except Exception as error:  # ZoneInfoNotFoundError and friends
        raise TimeParseError(f"{timezone!r} is not a known timezone") from error
    reference = (now or datetime.now(zone)).astimezone(zone)
    hour, minute, remainder = _extract_time(text.strip())
    day, implicit = _extract_date(remainder, reference.date())
    moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    if moment <= reference:
        if not implicit:
            raise TimeParseError(f"{text.strip()!r} is in the past")
        # "7pm" after 7pm means tomorrow; "friday 7pm" on Friday evening
        # means next Friday.
        moment += timedelta(days=1 if not remainder.strip() else 7)
    return int(moment.timestamp())


def parse_slots(raw: str, timezone: str, *, now: datetime | None = None) -> list[ParsedTime]:
    """Parse a comma-separated candidate-time list, preserving order.

    Duplicate instants collapse to one slot so a repeated entry cannot
    split the vote between two identical options.
    """
    entries = [part.strip() for part in raw.split(",") if part.strip()]
    if not entries:
        raise TimeParseError("no candidate times were supplied")
    if len(entries) > MAX_SLOTS:
        raise TimeParseError(f"at most {MAX_SLOTS} candidate times are supported")
    parsed: list[ParsedTime] = []
    seen: set[int] = set()
    for entry in entries:
        try:
            epoch = parse_when(entry, timezone, now=now)
        except TimeParseError as error:
            raise TimeParseError(f"{entry!r}: {error}") from error
        if epoch in seen:
            continue
        seen.add(epoch)
        parsed.append(ParsedTime(epoch=epoch, source=entry))
    return parsed


def parse_activities(raw: str) -> list[str]:
    """Split and de-duplicate the comma-separated activity list."""
    entries = [part.strip() for part in raw.split(",") if part.strip()]
    if not entries:
        raise TimeParseError("no activities were supplied")
    if len(entries) > MAX_SLOTS:
        raise TimeParseError(f"at most {MAX_SLOTS} activities are supported")
    unique: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        folded = entry.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        unique.append(entry[:100])
    return unique


def discord_timestamp(epoch: int, style: str = "F") -> str:
    """Render an epoch as Discord markup, localised per viewer."""
    return f"<t:{int(epoch)}:{style}>"


def format_slot_label(epoch: int, timezone: str) -> str:
    """Render a slot as static text for a Discord select option.

    Select labels are plain strings, so ``<t:...>`` markup cannot be used
    there; this is the one place a meetup shows a wall-clock time, and it
    is always paired with the zone it is written in.
    """
    try:
        zone = ZoneInfo(timezone)
    except Exception:  # pragma: no cover - validated upstream
        zone = ZoneInfo("UTC")
    moment = datetime.fromtimestamp(int(epoch), zone)
    hour = moment.hour % 12 or 12
    suffix = "AM" if moment.hour < 12 else "PM"
    return f"{moment:%a %d %b}, {hour}:{moment:%M} {suffix}"
