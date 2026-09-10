"""Embed rendering for every meetup surface.

One builder serves the poll message, the locked plan, the confirmation
ping, and the closed summary, so those four never drift apart: each state
is a branch on the meetup's own ``state`` field rather than a separate
embed builder per caller.

Columns follow the project's tabular convention — separate inline fields
per column, so Discord aligns the values — and every field value is
clamped to Discord's 1,024-character limit, which a list of forty
mentions comfortably exceeds.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import discord

from ..render import make_embed
from .store import (
    ANSWER_GOING,
    ANSWER_MAYBE,
    ANSWER_OUT,
    AXIS_ACTIVITY,
    AXIS_TIME,
    Meetup,
    STATE_CLOSED,
    STATE_CONFIRMING,
    STATE_LOCKED,
    STATE_POLLING,
)
from .timeparse import discord_timestamp

FIELD_LIMIT = 1024
_EMPTY = "—"

ANSWER_LABELS = {
    ANSWER_GOING: "✅ Going",
    ANSWER_MAYBE: "🤔 Maybe",
    ANSWER_OUT: "❌ Can't",
}


def _mentions(user_ids: Sequence[int], *, limit: int = FIELD_LIMIT) -> str:
    """Render a member list as mentions, trimmed to fit one embed field.

    Mentions inside an embed never ping; only the confirmation message's
    own content does.
    """
    if not user_ids:
        return _EMPTY
    rendered: list[str] = []
    length = 0
    for index, user_id in enumerate(user_ids):
        mention = f"<@{user_id}>"
        remaining = len(user_ids) - index
        overflow = f" +{remaining} more"
        if length + len(mention) + len(overflow) > limit:
            rendered.append(f"+{remaining} more")
            break
        rendered.append(mention)
        length += len(mention) + 1
    return " ".join(rendered)


def _column(values: Iterable[str]) -> str:
    """Render one inline column, clamped to the embed field limit."""
    text = "\n".join(values) or _EMPTY
    return text if len(text) <= FIELD_LIMIT else text[: FIELD_LIMIT - 1] + "…"


def _header(meetup: Meetup) -> str:
    """Return the shared description block for every meetup state."""
    lines = [f"Organized by <@{meetup.organizer_id}>"]
    if meetup.location:
        lines.append(f"📍 {meetup.location}")
    return "\n".join(lines)


def _add_poll_axis(
    embed: discord.Embed, meetup: Meetup, axis: str, heading: str
) -> None:
    """Add one axis' label/votes/voters column trio."""
    options = meetup.axis_options(axis)
    if not options:
        return
    labels: list[str] = []
    counts: list[str] = []
    voters: list[str] = []
    for option in options:
        who = meetup.voters(axis, option.key)
        display = (
            discord_timestamp(int(option.key), "f") if axis == AXIS_TIME else option.label
        )
        labels.append(display)
        counts.append(str(len(who)))
        voters.append(_mentions(who, limit=200))
    embed.add_field(name=heading, value=_column(labels), inline=True)
    embed.add_field(name="Votes", value=_column(counts), inline=True)
    embed.add_field(name="Who", value=_column(voters), inline=True)


def _locked_description(meetup: Meetup) -> str:
    """Describe the locked-in plan with viewer-local timestamps."""
    lines = [_header(meetup)]
    if meetup.locked_activity:
        lines.append(f"**What:** {meetup.locked_activity}")
    if meetup.locked_time is not None:
        lines.append(
            f"**When:** {discord_timestamp(meetup.locked_time)}"
            f" ({discord_timestamp(meetup.locked_time, 'R')})"
        )
    return "\n".join(lines)


def build_meetup_embed(meetup: Meetup) -> discord.Embed:
    """Render the meetup for whichever state it is currently in."""
    if meetup.state == STATE_POLLING:
        embed = make_embed(
            f"{_header(meetup)}\n\nPick every option that works for you.",
            title=f"📅 {meetup.title}",
        )
        _add_poll_axis(embed, meetup, AXIS_ACTIVITY, "Activity")
        _add_poll_axis(embed, meetup, AXIS_TIME, "When")
        embed.set_footer(text=f"Meetup #{meetup.meetup_id} · times shown in your local timezone")
        return embed

    if meetup.state == STATE_CLOSED:
        embed = make_embed(
            _locked_description(meetup) if meetup.locked_time else _header(meetup),
            title=f"📅 {meetup.title} — closed",
            color=discord.Color.greyple(),
        )
        going = meetup.by_answer(ANSWER_GOING)
        if going:
            embed.add_field(name="✅ Went", value=_mentions(going), inline=False)
        embed.set_footer(text=f"Meetup #{meetup.meetup_id} · closed")
        return embed

    embed = make_embed(
        _locked_description(meetup),
        title=f"📅 {meetup.title}",
        color=discord.Color.blue(),
    )
    for answer in (ANSWER_GOING, ANSWER_MAYBE, ANSWER_OUT):
        embed.add_field(
            name=ANSWER_LABELS[answer],
            value=_mentions(meetup.by_answer(answer), limit=340),
            inline=True,
        )
    waiting = meetup.unanswered()
    if waiting:
        embed.add_field(
            name="⏳ Expected, not yet confirmed",
            value=_mentions(waiting),
            inline=False,
        )
    footer = f"Meetup #{meetup.meetup_id}"
    if meetup.state == STATE_CONFIRMING:
        footer += " · confirmation requested"
    else:
        footer += " · locked in — use /meetup confirm to ping everyone"
    embed.set_footer(text=footer)
    return embed


def build_confirmation_embed(meetup: Meetup) -> discord.Embed:
    """Render the standalone message that actually pings attendees."""
    embed = make_embed(
        f"{_locked_description(meetup)}\n\nStill good for you?",
        title=f"📣 Double-checking: {meetup.title}",
        color=discord.Color.blue(),
    )
    embed.set_footer(text=f"Meetup #{meetup.meetup_id}")
    return embed


def build_preview_embed(meetup: Meetup, recipients: Sequence[int]) -> discord.Embed:
    """Render the organizer-only preview shown before any ping is sent."""
    embed = make_embed(
        f"{_locked_description(meetup)}\n\n"
        f"**{len(recipients)}** {'person' if len(recipients) == 1 else 'people'}"
        " will be pinged.",
        title=f"Confirm ping: {meetup.title}",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Recipients", value=_mentions(recipients), inline=False)
    embed.set_footer(text="Nothing has been sent yet.")
    return embed
