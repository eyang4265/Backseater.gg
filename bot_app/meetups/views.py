"""Component views and lifecycle transitions for meetups.

Every interaction here does only local SQLite work before replying, so
the response always lands inside Discord's interaction deadline without
needing a deferral.

The poll message lives in its parent channel, never inside the thread it
spawns: an archived thread cannot have its messages edited, so a meetup
proposed two weeks out would otherwise come back from auto-archive with
dead buttons.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Sequence

import discord

from ..store import remember_embed_button_state
from .render import build_confirmation_embed, build_meetup_embed
from .store import (
    ANSWER_GOING,
    ANSWER_MAYBE,
    ANSWER_OUT,
    AXIS_ACTIVITY,
    AXIS_TIME,
    Meetup,
    MeetupStore,
    STATE_CLOSED,
    STATE_CONFIRMING,
    STATE_LOCKED,
    STATE_POLLING,
    get_meetup_store,
)
from .timeparse import format_slot_label

LOGGER = logging.getLogger(__name__)

VIEW_KIND = "meetup"
CONFIRM_VIEW_KIND = "meetup_confirm"

async def remember_view_state(message: Any, kind: str, payload: dict[str, Any]) -> None:
    """Persist one meetup message's view state.

    A copy of the ``commands.shared`` helper rather than an import of it:
    this package must not depend on the command layer, which imports it
    back.  Only the meetup id is ever stored, so a restored view reflects
    current votes instead of a stale snapshot.
    """
    if (
        message is not None
        and isinstance(getattr(message, "id", None), int)
        and isinstance(getattr(getattr(message, "channel", None), "id", None), int)
    ):
        await asyncio.to_thread(
            remember_embed_button_state, message.id, message.channel.id, kind, payload
        )


_ANSWER_BUTTONS = (
    (ANSWER_GOING, "Going", "✅", discord.ButtonStyle.success),
    (ANSWER_MAYBE, "Maybe", "🤔", discord.ButtonStyle.secondary),
    (ANSWER_OUT, "Can't make it", "❌", discord.ButtonStyle.danger),
)


def may_manage(interaction: discord.Interaction, meetup: Meetup) -> bool:
    """Whether this member may lock, cancel, or close the meetup."""
    if interaction.user is not None and interaction.user.id == meetup.organizer_id:
        return True
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions is not None and permissions.manage_guild)


async def _deny(interaction: discord.Interaction) -> None:
    """Tell one member, privately, that a control is not theirs."""
    await interaction.response.send_message(
        "Only the organizer (or someone with Manage Server) can do that.",
        ephemeral=True,
    )


async def refresh_message(
    interaction: discord.Interaction, meetup: Meetup
) -> None:
    """Re-render the poll message in place and persist its view state."""
    await interaction.response.edit_message(
        embed=build_meetup_embed(meetup),
        view=build_meetup_view(meetup),
    )
    await remember_view_state(
        interaction.message, VIEW_KIND, {"meetup_id": meetup.meetup_id}
    )


class MeetupView(discord.ui.View):
    """The poll message's controls, rebuilt from the meetup's own state."""

    def __init__(self, meetup: Meetup, store: MeetupStore | None = None) -> None:
        """Initialize the instance."""
        super().__init__(timeout=None)
        self.meetup_id = meetup.meetup_id
        self._store = store or get_meetup_store()
        if meetup.state == STATE_POLLING:
            self._add_axis_select(meetup, AXIS_ACTIVITY, "What sounds good?")
            self._add_axis_select(meetup, AXIS_TIME, "When can you make it?")
            self._add_button("lock", "Lock it in", "🔒", discord.ButtonStyle.primary, self._lock)
            self._add_button("cancel", "Cancel", "🗑️", discord.ButtonStyle.danger, self._cancel)
        elif meetup.state in (STATE_LOCKED, STATE_CONFIRMING):
            for answer, label, emoji, style in _ANSWER_BUTTONS:
                self._add_button(answer, label, emoji, style, self._answer_callback(answer))
            self._add_button("close", "Close", "🔚", discord.ButtonStyle.secondary, self._close)

    # ----- construction -------------------------------------------------

    def _add_button(self, key: str, label: str, emoji: str, style, callback) -> None:
        """Attach one stable-custom_id button."""
        button = discord.ui.Button(
            label=label, emoji=emoji, style=style,
            custom_id=f"meetup:{self.meetup_id}:{key}",
        )
        button.callback = callback
        self.add_item(button)

    def _add_axis_select(self, meetup: Meetup, axis: str, placeholder: str) -> None:
        """Attach one multi-select covering every option on one axis."""
        options = meetup.axis_options(axis)
        if not options:
            return
        select = discord.ui.Select(
            custom_id=f"meetup:{self.meetup_id}:{axis}",
            placeholder=placeholder,
            min_values=0,
            max_values=len(options),
            options=[
                discord.SelectOption(
                    label=(
                        format_slot_label(int(option.key), meetup.timezone)
                        if axis == AXIS_TIME
                        else option.label
                    )[:100],
                    value=option.key,
                    description=meetup.timezone if axis == AXIS_TIME else None,
                )
                for option in options
            ],
        )
        select.callback = self._vote_callback(axis, select)
        self.add_item(select)

    def _reload(self) -> Meetup | None:
        """Re-read the meetup so concurrent votes are never overwritten."""
        return self._store.get(self.meetup_id)

    # ----- callbacks ----------------------------------------------------

    def _vote_callback(self, axis: str, select: discord.ui.Select):
        """Build the callback recording one member's votes on one axis."""

        async def vote(interaction: discord.Interaction) -> None:
            """Replace this member's selections on this axis."""
            meetup = self._reload()
            if meetup is None or meetup.state != STATE_POLLING:
                await interaction.response.send_message(
                    "This poll is no longer open.", ephemeral=True
                )
                return
            values = list(select.values or ())
            self._store.set_votes(meetup.meetup_id, interaction.user.id, axis, values)
            await refresh_message(interaction, self._reload() or meetup)

        return vote

    def _answer_callback(self, answer: str):
        """Build the callback recording one attendance answer."""

        async def respond(interaction: discord.Interaction) -> None:
            """Record going/maybe/out and re-render."""
            meetup = self._reload()
            if meetup is None or meetup.state == STATE_CLOSED:
                await interaction.response.send_message(
                    "This meetup is closed.", ephemeral=True
                )
                return
            self._store.record_answer(meetup.meetup_id, interaction.user.id, answer)
            await refresh_message(interaction, self._reload() or meetup)

        return respond

    async def _lock(self, interaction: discord.Interaction) -> None:
        """Open the organizer's private lock-in picker."""
        meetup = self._reload()
        if meetup is None:
            await interaction.response.send_message("This meetup is gone.", ephemeral=True)
            return
        if not may_manage(interaction, meetup):
            await _deny(interaction)
            return
        await interaction.response.send_message(
            embed=build_meetup_embed(meetup),
            view=LockView(meetup, self._store),
            ephemeral=True,
        )

    async def _cancel(self, interaction: discord.Interaction) -> None:
        """Delete the meetup outright, poll and all."""
        meetup = self._reload()
        if meetup is None:
            await interaction.response.send_message("This meetup is gone.", ephemeral=True)
            return
        if not may_manage(interaction, meetup):
            await _deny(interaction)
            return
        self._store.delete(meetup.meetup_id)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title=f"📅 {meetup.title} — cancelled",
                description=f"Cancelled by <@{interaction.user.id}>.",
                color=discord.Color.greyple(),
            ),
            view=None,
        )
        await wind_down_thread(interaction.client, meetup)

    async def _close(self, interaction: discord.Interaction) -> None:
        """Close the meetup from its own message."""
        meetup = self._reload()
        if meetup is None:
            await interaction.response.send_message("This meetup is gone.", ephemeral=True)
            return
        if not may_manage(interaction, meetup):
            await _deny(interaction)
            return
        self._store.set_state(meetup.meetup_id, STATE_CLOSED)
        closed = self._reload() or meetup
        await interaction.response.edit_message(
            embed=build_meetup_embed(closed), view=None
        )
        await wind_down_thread(interaction.client, closed)


class LockView(discord.ui.View):
    """The organizer's ephemeral picker for the winning activity and time."""

    def __init__(self, meetup: Meetup, store: MeetupStore) -> None:
        """Initialize the instance."""
        super().__init__(timeout=300)
        self.meetup_id = meetup.meetup_id
        self._store = store
        self.activity: str | None = _leader(meetup, AXIS_ACTIVITY)
        self.moment: str | None = _leader(meetup, AXIS_TIME)
        self._add_select(meetup, AXIS_ACTIVITY, "Winning activity")
        self._add_select(meetup, AXIS_TIME, "Winning time")
        confirm = discord.ui.Button(
            label="Lock it in", emoji="🔒", style=discord.ButtonStyle.primary
        )
        confirm.callback = self._confirm
        self.add_item(confirm)

    def _add_select(self, meetup: Meetup, axis: str, placeholder: str) -> None:
        """Attach a single-choice select pre-pointed at the current leader."""
        options = meetup.axis_options(axis)
        select = discord.ui.Select(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=(
                        format_slot_label(int(option.key), meetup.timezone)
                        if axis == AXIS_TIME
                        else option.label
                    )[:100],
                    value=option.key,
                    description=f"{len(meetup.voters(axis, option.key))} vote(s)",
                    default=option.key
                    == (self.moment if axis == AXIS_TIME else self.activity),
                )
                for option in options
            ],
        )

        async def choose(interaction: discord.Interaction, chosen=select, which=axis) -> None:
            """Remember the organizer's pick without closing the picker."""
            value = chosen.values[0]
            if which == AXIS_TIME:
                self.moment = value
            else:
                self.activity = value
            await interaction.response.defer()

        select.callback = choose
        self.add_item(select)

    async def _confirm(self, interaction: discord.Interaction) -> None:
        """Apply the lock and re-render the public poll message."""
        meetup = self._store.get(self.meetup_id)
        if meetup is None:
            await interaction.response.edit_message(
                content="This meetup is gone.", embed=None, view=None
            )
            return
        if self.activity is None or self.moment is None:
            await interaction.response.send_message(
                "Pick both an activity and a time first.", ephemeral=True
            )
            return
        self._store.lock(meetup.meetup_id, self.activity, int(self.moment))
        locked = self._store.get(meetup.meetup_id) or meetup
        await interaction.response.edit_message(
            content=(
                f"Locked in. **{len(locked.expected_attendees())}** "
                "poll respondents match both choices — run `/meetup confirm` to ping them."
            ),
            embed=None,
            view=None,
        )
        await _edit_meetup_message(interaction.client, locked)


class ConfirmationView(discord.ui.View):
    """Buttons on the standalone ping message.

    They write into the same attendance store the locked poll message
    reads, so a member answering here updates both surfaces.
    """

    def __init__(self, meetup_id: int, store: MeetupStore | None = None) -> None:
        """Initialize the instance."""
        super().__init__(timeout=None)
        self.meetup_id = meetup_id
        self._store = store or get_meetup_store()
        for answer, label, emoji, style in _ANSWER_BUTTONS:
            button = discord.ui.Button(
                label=label, emoji=emoji, style=style,
                custom_id=f"meetup:{meetup_id}:confirm:{answer}",
            )
            button.callback = self._callback(answer)
            self.add_item(button)

    def _callback(self, answer: str):
        """Build the callback recording one confirmation answer."""

        async def respond(interaction: discord.Interaction) -> None:
            """Record the answer and refresh both messages."""
            meetup = self._store.get(self.meetup_id)
            if meetup is None or meetup.state == STATE_CLOSED:
                await interaction.response.send_message(
                    "This meetup is closed.", ephemeral=True
                )
                return
            self._store.record_answer(meetup.meetup_id, interaction.user.id, answer)
            updated = self._store.get(meetup.meetup_id) or meetup
            await interaction.response.edit_message(
                embed=build_confirmation_embed(updated), view=self
            )
            await _edit_meetup_message(interaction.client, updated)

        return respond


def _leader(meetup: Meetup, axis: str) -> str | None:
    """Return the most-voted option key on one axis, ties broken by order."""
    options = meetup.axis_options(axis)
    if not options:
        return None
    return max(
        options,
        key=lambda option: (len(meetup.voters(axis, option.key)), -option.position),
    ).key


def build_meetup_view(meetup: Meetup) -> discord.ui.View | None:
    """Return the controls for a meetup, or ``None`` once it is closed."""
    if meetup.state == STATE_CLOSED:
        return None
    return MeetupView(meetup)


async def _fetch_message(bot: Any, meetup: Meetup) -> discord.Message | None:
    """Fetch a meetup's poll message, tolerating every ordinary failure."""
    if meetup.message_id is None:
        return None
    try:
        channel = bot.get_channel(meetup.channel_id) or await bot.fetch_channel(
            meetup.channel_id
        )
        return await channel.fetch_message(meetup.message_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError) as error:
        LOGGER.warning("Could not fetch meetup %d's message: %s", meetup.meetup_id, error)
        return None


async def _edit_meetup_message(bot: Any, meetup: Meetup) -> None:
    """Re-render a meetup's poll message from outside its own interaction."""
    message = await _fetch_message(bot, meetup)
    if message is None:
        return
    try:
        await message.edit(embed=build_meetup_embed(meetup), view=build_meetup_view(meetup))
    except (discord.Forbidden, discord.HTTPException) as error:
        LOGGER.warning("Could not edit meetup %d's message: %s", meetup.meetup_id, error)


async def wind_down_thread(bot: Any, meetup: Meetup) -> None:
    """Unpin the poll message and archive/lock the discussion thread.

    The thread is archived rather than deleted: it exists to keep the
    planning conversation, and that is worth more than a tidy channel.
    """
    message = await _fetch_message(bot, meetup)
    if message is not None and message.pinned:
        try:
            await message.unpin()
        except (discord.Forbidden, discord.HTTPException) as error:
            LOGGER.debug("Could not unpin meetup %d: %s", meetup.meetup_id, error)
    if meetup.thread_id is None:
        return
    try:
        thread = bot.get_channel(meetup.thread_id) or await bot.fetch_channel(
            meetup.thread_id
        )
        await thread.edit(archived=True, locked=True)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError) as error:
        LOGGER.debug("Could not archive meetup %d's thread: %s", meetup.meetup_id, error)


async def close_meetup(bot: Any, meetup: Meetup, store: MeetupStore | None = None) -> None:
    """Close one meetup: static summary, no controls, archived thread."""
    active = store or get_meetup_store()
    active.set_state(meetup.meetup_id, STATE_CLOSED)
    closed = active.get(meetup.meetup_id) or meetup
    closed.state = STATE_CLOSED
    await _edit_meetup_message(bot, closed)
    await wind_down_thread(bot, closed)
    LOGGER.info("Closed meetup %d (%s)", closed.meetup_id, closed.title)


async def send_confirmation(
    bot: Any,
    meetup: Meetup,
    recipients: Sequence[int],
    store: MeetupStore | None = None,
) -> discord.Message | None:
    """Post the one message that actually pings people.

    Editing a message never notifies anyone, so the double-check has to be
    a new message.  It goes into the meetup's thread when there is one —
    mentioning a member there both pings them and adds them to the thread,
    keeping the whole plan in one place.
    """
    active = store or get_meetup_store()
    destination_id = meetup.thread_id or meetup.channel_id
    try:
        channel = bot.get_channel(destination_id) or await bot.fetch_channel(destination_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException) as error:
        LOGGER.warning("Could not resolve meetup %d's channel: %s", meetup.meetup_id, error)
        return None
    content = " ".join(f"<@{user_id}>" for user_id in recipients)
    try:
        message = await channel.send(
            content=content or None,
            embed=build_confirmation_embed(meetup),
            view=ConfirmationView(meetup.meetup_id, active),
            # The one place in this bot that deliberately pings: every other
            # surface suppresses mentions.
            allowed_mentions=discord.AllowedMentions(users=True),
        )
    except (discord.Forbidden, discord.HTTPException) as error:
        LOGGER.warning("Could not send meetup %d's confirmation: %s", meetup.meetup_id, error)
        return None
    if meetup.state == STATE_LOCKED:
        active.set_state(meetup.meetup_id, STATE_CONFIRMING)
        await _edit_meetup_message(bot, active.get(meetup.meetup_id) or meetup)
    await remember_view_state(message, CONFIRM_VIEW_KIND, {"meetup_id": meetup.meetup_id})
    return message
