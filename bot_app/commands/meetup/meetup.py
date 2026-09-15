"""The ``/meetup`` command group: propose, confirm, list, and cancel."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ...config import get_settings
from ...meetups.render import build_meetup_embed, build_meetup_list_page, build_preview_embed
from ...meetups.store import (
    AXES,
    Meetup,
    STATE_CLOSED,
    STATE_POLLING,
    get_meetup_store,
)
from ...meetups.timeparse import (
    TimeParseError,
    parse_activities,
    parse_slots,
)
from ...meetups.views import (
    VIEW_KIND,
    build_meetup_view,
    may_manage,
    remember_view_state,
    send_confirmation,
    wind_down_thread,
)
from ...paginator import Paginator
from ...render import make_embed
from ..shared import GUILD_IDS, log_command

LOGGER = logging.getLogger(__name__)

THREAD_ARCHIVE_MINUTES = 10080
"""Seven days, the longest auto-archive Discord allows."""

_MEETUP_ID_DESCRIPTION = "Which meetup (defaults to this channel's latest)"

_INCLUDE_UNANSWERED = "Expected, not yet answered"
_INCLUDE_EXPECTED = "Everyone the poll expects"
_INCLUDE_VOTERS = "Everyone who voted at all"
_INCLUDE_CHOICES = (_INCLUDE_UNANSWERED, _INCLUDE_EXPECTED, _INCLUDE_VOTERS)


def _recipients(meetup: Meetup, include: str) -> list[int]:
    """Resolve who a confirmation ping should reach."""
    if include == _INCLUDE_VOTERS:
        everyone: set[int] = set()
        for axis in AXES:
            for voters in meetup.votes.get(axis, {}).values():
                everyone.update(voters)
        return sorted(everyone)
    if include == _INCLUDE_EXPECTED:
        return meetup.expected_attendees()
    return meetup.unanswered()


class SendPingView(discord.ui.View):
    """The organizer's last look before anyone is actually pinged.

    A wrong overlap silently pings the wrong twelve people, so the send is
    a separate, deliberate click rather than a side effect of running the
    command.
    """

    def __init__(self, meetup_id: int, recipients: list[int]) -> None:
        """Initialize the instance."""
        super().__init__(timeout=180)
        self.meetup_id = meetup_id
        self.recipients = recipients
        send = discord.ui.Button(
            label=f"Ping {len(recipients)}", emoji="📣", style=discord.ButtonStyle.primary
        )
        send.callback = self._send
        self.add_item(send)

    async def _send(self, interaction: discord.Interaction) -> None:
        """Post the ping message into the meetup's thread."""
        store = get_meetup_store()
        meetup = await asyncio.to_thread(store.get, self.meetup_id)
        if meetup is None:
            await interaction.response.edit_message(
                content="That meetup is gone.", embed=None, view=None
            )
            return
        await interaction.response.edit_message(
            content="Sending…", embed=None, view=None
        )
        message = await send_confirmation(interaction.client, meetup, self.recipients, store)
        await interaction.edit_original_response(
            content=(
                f"Pinged {len(self.recipients)}."
                if message is not None
                else "Could not post the ping — check the bot's channel permissions."
            )
        )


class MeetupCommands(commands.Cog):
    """Meetup planning commands."""

    meetup = discord.SlashCommandGroup(
        "meetup", "Plan a meetup with the group", guild_ids=GUILD_IDS
    )

    def __init__(self, bot: discord.Bot) -> None:
        """Initialize the instance."""
        self.bot = bot
        self.store = get_meetup_store()

    async def _resolve(self, ctx, meetup_id: int | None) -> Meetup | None:
        """Resolve a meetup only within the invoking server."""
        if ctx.guild_id is None:
            return None
        if meetup_id is not None:
            meetup = await asyncio.to_thread(self.store.get, meetup_id)
        else:
            meetup = await asyncio.to_thread(self.store.latest_open_in_channel, ctx.channel.id)
        return meetup if meetup is not None and meetup.guild_id == ctx.guild_id else None

    @meetup.command(name="propose", description="Poll the group on what to do and when")
    @discord.option("title", description="What to call this meetup", required=True)
    @discord.option(
        "activities",
        description="Comma-separated options, e.g. bowling, board games",
        required=True,
    )
    @discord.option(
        "times",
        description="Comma-separated times, e.g. fri 7pm, sat 8pm, 9/6 19:30",
        required=True,
    )
    @discord.option("location", description="Where it happens", required=False)
    @discord.option(
        "timezone",
        description="IANA timezone the times are written in",
        required=False,
    )
    async def propose(self, ctx, title, activities, times, location, timezone) -> None:
        """Post an activity/time poll with its own discussion thread."""
        log_command(ctx, title=title, activities=activities, times=times, location=location, timezone=timezone)
        if ctx.guild_id is None:
            await ctx.respond(
                embed=make_embed("Meetups can only be planned in a server."),
                ephemeral=True,
            )
            return
        zone = timezone or get_settings().timezone
        try:
            chosen_activities = parse_activities(activities)
            slots = parse_slots(times, zone)
        except TimeParseError as error:
            await ctx.respond(embed=make_embed(f"❌ {error}"), ephemeral=True)
            return

        await ctx.defer()
        meetup_id = await asyncio.to_thread(
            self.store.create,
            guild_id=ctx.guild_id,
            channel_id=ctx.channel.id,
            organizer_id=ctx.author.id,
            title=title[:100],
            location=(location or "")[:200],
            timezone=zone,
            activities=chosen_activities,
            times=[(slot.epoch, slot.source) for slot in slots],
        )
        meetup = await asyncio.to_thread(self.store.get, meetup_id)
        sent = await ctx.respond(
            embed=build_meetup_embed(meetup),
            view=build_meetup_view(meetup),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        message = await self._materialize(ctx, sent)
        if message is None:
            LOGGER.warning("Meetup %d posted without a resolvable message", meetup_id)
            return
        thread_id = await self._open_thread(ctx, message, title)
        await asyncio.to_thread(
            self.store.attach_message, meetup_id, message.id, thread_id
        )
        await remember_view_state(message, VIEW_KIND, {"meetup_id": meetup_id})
        await self._pin(message)

    async def _materialize(self, ctx, sent) -> discord.Message | None:
        """Resolve the posted response into a real channel message.

        Threads and pins need a Message in the parent channel; an
        interaction response can come back as an ``Interaction`` or a
        webhook message whose channel is only partially resolved.
        """
        try:
            if isinstance(sent, discord.Interaction):
                sent = await sent.original_response()
            return await ctx.channel.fetch_message(sent.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError) as error:
            LOGGER.warning("Could not resolve the meetup message: %s", error)
            return None

    async def _open_thread(self, ctx, message: discord.Message, title: str) -> int | None:
        """Create the discussion thread, degrading quietly when disallowed.

        Discord has no nested threads, so a meetup proposed inside one
        simply uses the thread it is already in.
        """
        if isinstance(ctx.channel, discord.Thread):
            return None
        try:
            thread = await message.create_thread(
                name=f"{title} — planning"[:100],
                auto_archive_duration=THREAD_ARCHIVE_MINUTES,
            )
        except (discord.Forbidden, discord.HTTPException) as error:
            LOGGER.warning("Could not create a meetup thread: %s", error)
            return None
        return thread.id

    async def _pin(self, message: discord.Message) -> None:
        """Pin the poll so it stays findable, if the bot may pin."""
        try:
            await message.pin()
        except (discord.Forbidden, discord.HTTPException) as error:
            LOGGER.debug("Could not pin the meetup message: %s", error)

    @meetup.command(name="confirm", description="Ping everyone to double-check they are coming")
    @discord.option("meetup_id", int, description=_MEETUP_ID_DESCRIPTION, required=False)
    @discord.option(
        "include", description="Who to ping", choices=list(_INCLUDE_CHOICES), required=False
    )
    async def confirm(self, ctx, meetup_id, include) -> None:
        """Show the organizer who would be pinged, then send on confirmation."""
        log_command(ctx, meetup_id=meetup_id, include=include)
        meetup = await self._resolve(ctx, meetup_id)
        if meetup is None or meetup.state == STATE_CLOSED:
            await ctx.respond(
                embed=make_embed("No open meetup found here. Use `/meetup propose` first."),
                ephemeral=True,
            )
            return
        if meetup.state == STATE_POLLING:
            await ctx.respond(
                embed=make_embed(
                    "That meetup is still polling — lock in an activity and time first."
                ),
                ephemeral=True,
            )
            return
        if not may_manage(ctx.interaction, meetup):
            await ctx.respond(
                embed=make_embed("Only the organizer can send the confirmation ping."),
                ephemeral=True,
            )
            return
        recipients = _recipients(meetup, include or _INCLUDE_UNANSWERED)
        if not recipients:
            await ctx.respond(
                embed=make_embed("Nobody to ping — everyone expected has already answered."),
                ephemeral=True,
            )
            return
        await ctx.respond(
            embed=build_preview_embed(meetup, recipients),
            view=SendPingView(meetup.meetup_id, recipients),
            ephemeral=True,
        )

    @meetup.command(name="list", description="Show this server's open meetups")
    async def list_meetups(self, ctx) -> None:
        """Page through this server's open meetups in aligned, bounded columns."""
        log_command(ctx)
        if ctx.guild_id is None:
            await ctx.respond(
                embed=make_embed("Meetups can only be planned in a server."), ephemeral=True
            )
            return
        meetups = await asyncio.to_thread(self.store.open_in_guild, ctx.guild_id)
        if not meetups:
            await ctx.respond(
                embed=make_embed("No open meetups. Use `/meetup propose` to start one."),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        paginator = Paginator(
            meetups,
            author_id=ctx.author.id,
            render_page=build_meetup_list_page,
            page_size=5,
        )
        paginator.message = await ctx.respond(
            embed=paginator.render(),
            view=paginator if paginator.max_page else None,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @meetup.command(name="cancel", description="Cancel a meetup and archive its thread")
    @discord.option("meetup_id", int, description=_MEETUP_ID_DESCRIPTION, required=False)
    async def cancel(self, ctx, meetup_id) -> None:
        """Delete a meetup outright and wind down its thread."""
        log_command(ctx, meetup_id=meetup_id)
        meetup = await self._resolve(ctx, meetup_id)
        if meetup is None:
            await ctx.respond(embed=make_embed("No open meetup found here."), ephemeral=True)
            return
        if not may_manage(ctx.interaction, meetup):
            await ctx.respond(
                embed=make_embed("Only the organizer can cancel this meetup."), ephemeral=True
            )
            return
        await asyncio.to_thread(self.store.delete, meetup.meetup_id)
        message = None
        try:
            message = await ctx.channel.fetch_message(meetup.message_id) if meetup.message_id else None
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            message = None
        if message is not None:
            await message.edit(
                embed=make_embed(
                    f"Cancelled by <@{ctx.author.id}>.",
                    title=f"📅 {meetup.title} — cancelled",
                    color=discord.Color.greyple(),
                ),
                view=None,
            )
        await wind_down_thread(self.bot, meetup)
        await ctx.respond(
            embed=make_embed(f"Cancelled meetup `#{meetup.meetup_id}`."), ephemeral=True
        )


def setup(bot: discord.Bot) -> None:
    """Register this command module with the bot."""
    bot.add_cog(MeetupCommands(bot))
