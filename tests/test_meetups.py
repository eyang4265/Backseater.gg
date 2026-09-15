"""Network-free tests for meetup parsing, storage, rendering, and closing."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from datetime import datetime
from zoneinfo import ZoneInfo

from bot_app.meetups.poller import poll_and_close_meetups
from bot_app.meetups.render import build_meetup_embed, build_preview_embed
from bot_app.meetups.store import (
    ANSWER_GOING,
    ANSWER_OUT,
    AXIS_ACTIVITY,
    AXIS_TIME,
    CLOSE_GRACE_SECONDS,
    MeetupStore,
    STATE_CLOSED,
    STATE_LOCKED,
    STATE_POLLING,
)
from bot_app.meetups.timeparse import (
    TimeParseError,
    format_slot_label,
    parse_activities,
    parse_slots,
    parse_when,
)

ZONE = "America/New_York"
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=ZoneInfo(ZONE))
PAST_START = 1_000_000_000
"""A start time long past its grace period however the clock reads."""


def _local(epoch: int) -> datetime:
    """Return an epoch as wall-clock time in the test timezone."""
    return datetime.fromtimestamp(epoch, ZoneInfo(ZONE))


class TimeParsingTests(unittest.TestCase):
    def test_understands_the_formats_people_actually_type(self) -> None:
        """Verify the accepted candidate-time spellings all resolve."""
        cases = {
            "fri 7pm": (2026, 9, 4, 19, 0),
            "tomorrow 18:30": (2026, 9, 1, 18, 30),
            "2026-09-04 20:00": (2026, 9, 4, 20, 0),
            "9/5 7:30pm": (2026, 9, 5, 19, 30),
            "sept 6 8pm": (2026, 9, 6, 20, 0),
            "6 sep 8pm": (2026, 9, 6, 20, 0),
            "today 9pm": (2026, 8, 31, 21, 0),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                moment = _local(parse_when(text, ZONE, now=NOW))
                self.assertEqual(
                    (moment.year, moment.month, moment.day, moment.hour, moment.minute),
                    expected,
                )

    def test_bare_time_rolls_to_tomorrow_once_it_has_passed(self) -> None:
        """A bare `9am` at noon means tomorrow morning, not this morning."""
        self.assertEqual(_local(parse_when("9am", ZONE, now=NOW)).day, 1)
        self.assertEqual(_local(parse_when("9pm", ZONE, now=NOW)).day, 31)

    def test_explicit_past_times_are_rejected(self) -> None:
        """An explicit date in the past is an error, never a silent shift."""
        for text in ("today 9am", "2026-08-30 20:00"):
            with self.subTest(text=text), self.assertRaises(TimeParseError):
                parse_when(text, ZONE, now=NOW)

    def test_unparseable_input_names_the_offending_entry(self) -> None:
        """A bad slot must be identifiable in a multi-slot list."""
        with self.assertRaises(TimeParseError) as caught:
            parse_slots("fri 7pm, whenever", ZONE, now=NOW)
        self.assertIn("whenever", str(caught.exception))

    def test_duplicate_slots_and_activities_collapse(self) -> None:
        """A repeated option must not split the vote in two."""
        self.assertEqual(len(parse_slots("fri 7pm, fri 19:00", ZONE, now=NOW)), 1)
        self.assertEqual(parse_activities("Bowling, bowling , Darts"), ["Bowling", "Darts"])

    def test_slot_labels_are_static_text(self) -> None:
        """Select labels cannot use Discord timestamp markup."""
        label = format_slot_label(parse_when("fri 7pm", ZONE, now=NOW), ZONE)
        self.assertEqual(label, "Fri 04 Sep, 7:00 PM")


class MeetupStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        """Create an in-memory store with one polling meetup."""
        self.store = MeetupStore(":memory:")
        self.slots = parse_slots("fri 7pm, sat 8pm", ZONE, now=NOW)
        self.meetup_id = self.store.create(
            guild_id=1,
            channel_id=2,
            organizer_id=3,
            title="Board games",
            location="Kev's place",
            timezone=ZONE,
            activities=["Bowling", "Board games"],
            times=[(slot.epoch, slot.source) for slot in self.slots],
        )

    def test_votes_are_replaced_not_accumulated(self) -> None:
        """Re-answering an axis must overwrite the previous selection."""
        self.store.set_votes(self.meetup_id, 7, AXIS_TIME, [str(self.slots[0].epoch)])
        self.store.set_votes(self.meetup_id, 7, AXIS_TIME, [str(self.slots[1].epoch)])
        meetup = self.store.get(self.meetup_id)
        self.assertEqual(meetup.selections(AXIS_TIME, 7), {str(self.slots[1].epoch)})

    def test_an_empty_selection_is_a_real_answer(self) -> None:
        """Ticking nothing must clear a member's previous votes."""
        self.store.set_votes(self.meetup_id, 7, AXIS_ACTIVITY, ["Bowling"])
        self.store.set_votes(self.meetup_id, 7, AXIS_ACTIVITY, [])
        self.assertEqual(self.store.get(self.meetup_id).selections(AXIS_ACTIVITY, 7), set())

    def test_expected_attendees_are_the_overlap_of_both_axes(self) -> None:
        """Only members matching both locked choices are expected."""
        both, activity_only, time_only = 7, 8, 9
        self.store.set_votes(self.meetup_id, both, AXIS_ACTIVITY, ["Bowling"])
        self.store.set_votes(self.meetup_id, both, AXIS_TIME, [str(self.slots[0].epoch)])
        self.store.set_votes(self.meetup_id, activity_only, AXIS_ACTIVITY, ["Bowling"])
        self.store.set_votes(self.meetup_id, time_only, AXIS_TIME, [str(self.slots[0].epoch)])
        self.store.lock(self.meetup_id, "Bowling", self.slots[0].epoch)
        meetup = self.store.get(self.meetup_id)
        self.assertEqual(meetup.state, STATE_LOCKED)
        self.assertEqual(meetup.expected_attendees(), [both])

    def test_answering_removes_a_member_from_the_chase_list(self) -> None:
        """``/meetup confirm`` must not re-ping someone who replied."""
        self.store.set_votes(self.meetup_id, 7, AXIS_ACTIVITY, ["Bowling"])
        self.store.set_votes(self.meetup_id, 7, AXIS_TIME, [str(self.slots[0].epoch)])
        self.store.set_votes(self.meetup_id, 8, AXIS_ACTIVITY, ["Bowling"])
        self.store.set_votes(self.meetup_id, 8, AXIS_TIME, [str(self.slots[0].epoch)])
        self.store.lock(self.meetup_id, "Bowling", self.slots[0].epoch)
        self.store.record_answer(self.meetup_id, 7, ANSWER_GOING)
        meetup = self.store.get(self.meetup_id)
        self.assertEqual(meetup.unanswered(), [8])
        self.assertEqual(meetup.by_answer(ANSWER_GOING), [7])

    def test_answers_are_replaced_per_member(self) -> None:
        """Changing your mind must not leave two answers behind."""
        self.store.record_answer(self.meetup_id, 7, ANSWER_GOING)
        self.store.record_answer(self.meetup_id, 7, ANSWER_OUT)
        meetup = self.store.get(self.meetup_id)
        self.assertEqual(meetup.by_answer(ANSWER_GOING), [])
        self.assertEqual(meetup.by_answer(ANSWER_OUT), [7])

    def test_only_meetups_past_their_grace_period_are_due(self) -> None:
        """A meetup stays open until well after it has started."""
        start = self.slots[0].epoch
        self.store.lock(self.meetup_id, "Bowling", start)
        self.assertEqual(self.store.due_for_close(now=start + 60), [])
        due = self.store.due_for_close(now=start + CLOSE_GRACE_SECONDS + 1)
        self.assertEqual([item.meetup_id for item in due], [self.meetup_id])

    def test_deleting_a_meetup_removes_its_votes(self) -> None:
        """Cancellation must not leave orphaned rows behind."""
        self.store.set_votes(self.meetup_id, 7, AXIS_ACTIVITY, ["Bowling"])
        self.store.delete(self.meetup_id)
        self.assertIsNone(self.store.get(self.meetup_id))
        with self.store._database() as db:
            remaining = db.execute("SELECT COUNT(*) FROM meetup_votes").fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_lookups_by_message_and_channel(self) -> None:
        """Both interaction paths must find the meetup again."""
        self.store.attach_message(self.meetup_id, 555, 666)
        self.assertEqual(self.store.for_message(555).thread_id, 666)
        self.assertEqual(self.store.latest_open_in_channel(2).meetup_id, self.meetup_id)
        self.store.set_state(self.meetup_id, STATE_CLOSED)
        self.assertIsNone(self.store.latest_open_in_channel(2))


class MeetupRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        """Create an in-memory store with one voted-on meetup."""
        self.store = MeetupStore(":memory:")
        self.slots = parse_slots("fri 7pm, sat 8pm", ZONE, now=NOW)
        self.meetup_id = self.store.create(
            guild_id=1, channel_id=2, organizer_id=3, title="Board games",
            location="Kev's place", timezone=ZONE,
            activities=["Bowling", "Board games"],
            times=[(slot.epoch, slot.source) for slot in self.slots],
        )

    def test_poll_embed_uses_aligned_inline_columns(self) -> None:
        """Tabular data follows the project's inline-column convention."""
        embed = build_meetup_embed(self.store.get(self.meetup_id))
        self.assertEqual(
            [field.name for field in embed.fields],
            ["Activity", "Votes", "Who", "When", "Votes", "Who"],
        )
        self.assertTrue(all(field.inline for field in embed.fields))

    def test_times_render_as_viewer_local_timestamps(self) -> None:
        """Every displayed time must be Discord markup, not wall clock."""
        embed = build_meetup_embed(self.store.get(self.meetup_id))
        when = next(field for field in embed.fields if field.name == "When")
        self.assertIn(f"<t:{self.slots[0].epoch}:f>", when.value)

    def test_locked_embed_switches_to_attendance_columns(self) -> None:
        """Locking replaces the poll layout with going/maybe/can't."""
        self.store.lock(self.meetup_id, "Bowling", self.slots[0].epoch)
        embed = build_meetup_embed(self.store.get(self.meetup_id))
        self.assertIn("**What:** Bowling", embed.description)
        self.assertIn(f"<t:{self.slots[0].epoch}:R>", embed.description)
        self.assertEqual(
            [field.name for field in embed.fields][:3],
            ["✅ Going", "🤔 Maybe", "❌ Can't"],
        )

    def test_every_field_value_fits_discord_limit(self) -> None:
        """A large lobby must not produce an unsendable embed."""
        for user_id in range(100000000000000000, 100000000000000060):
            self.store.set_votes(self.meetup_id, user_id, AXIS_ACTIVITY, ["Bowling"])
            self.store.set_votes(
                self.meetup_id, user_id, AXIS_TIME, [str(self.slots[0].epoch)]
            )
        self.store.lock(self.meetup_id, "Bowling", self.slots[0].epoch)
        meetup = self.store.get(self.meetup_id)
        embeds = [
            build_meetup_embed(meetup),
            build_preview_embed(meetup, meetup.expected_attendees()),
        ]
        for embed in embeds:
            for field in embed.fields:
                self.assertLessEqual(len(field.value), 1024, field.name)

    def test_closed_embed_drops_its_controls(self) -> None:
        """A closed meetup renders as a static summary."""
        from bot_app.meetups.views import build_meetup_view

        self.store.lock(self.meetup_id, "Bowling", self.slots[0].epoch)
        self.store.set_state(self.meetup_id, STATE_CLOSED)
        meetup = self.store.get(self.meetup_id)
        self.assertIsNone(build_meetup_view(meetup))
        self.assertIn("closed", build_meetup_embed(meetup).title)


class _FakeMessage:
    """A poll message that records what the closer did to it."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.pinned = True
        self.unpinned = False
        self.edits: list[dict] = []

    async def edit(self, **kwargs) -> None:
        """Record an edit."""
        self.edits.append(kwargs)

    async def unpin(self) -> None:
        """Record an unpin."""
        self.unpinned = True


class _FakeThread:
    """A discussion thread that records its archive state."""

    def __init__(self) -> None:
        """Initialize the instance."""
        self.archived = False
        self.locked = False
        self.deleted = False

    async def edit(self, **kwargs) -> None:
        """Record an archive/lock edit."""
        self.archived = kwargs.get("archived", self.archived)
        self.locked = kwargs.get("locked", self.locked)


class _FakeBot:
    """Minimal client exposing one channel, message, and thread."""

    def __init__(self, message: _FakeMessage, thread: _FakeThread) -> None:
        """Initialize the instance."""
        self.message = message
        self.thread = thread

        class _Channel:
            async def fetch_message(_self, _id):
                """Return the single fake message."""
                return message

        self._channel = _Channel()

    def get_channel(self, channel_id: int):
        """Return the thread for its id and the channel otherwise."""
        return self.thread if channel_id == 666 else self._channel


class MeetupClosingTests(unittest.TestCase):
    def test_the_poller_closes_archives_and_unpins(self) -> None:
        """Closing must strip controls and wind the thread down, not delete it."""
        store = MeetupStore(":memory:")
        slots = parse_slots("fri 7pm", ZONE, now=NOW)
        meetup_id = store.create(
            guild_id=1, channel_id=2, organizer_id=3, title="Board games",
            location="", timezone=ZONE, activities=["Bowling"],
            times=[(slot.epoch, slot.source) for slot in slots],
        )
        store.attach_message(meetup_id, 555, 666)
        store.lock(meetup_id, "Bowling", PAST_START)
        message, thread = _FakeMessage(), _FakeThread()
        bot = _FakeBot(message, thread)

        closed = asyncio.run(poll_and_close_meetups(bot, store))

        self.assertEqual(closed, 1)
        self.assertEqual(store.get(meetup_id).state, STATE_CLOSED)
        self.assertIsNone(message.edits[-1]["view"])
        self.assertTrue(message.unpinned)
        self.assertTrue(thread.archived and thread.locked)
        self.assertFalse(thread.deleted)

    def test_closing_is_idempotent(self) -> None:
        """A second pass must not re-close what is already closed."""
        store = MeetupStore(":memory:")
        slots = parse_slots("fri 7pm", ZONE, now=NOW)
        meetup_id = store.create(
            guild_id=1, channel_id=2, organizer_id=3, title="Board games",
            location="", timezone=ZONE, activities=["Bowling"],
            times=[(slot.epoch, slot.source) for slot in slots],
        )
        store.attach_message(meetup_id, 555, 666)
        store.lock(meetup_id, "Bowling", PAST_START)
        bot = _FakeBot(_FakeMessage(), _FakeThread())
        asyncio.run(poll_and_close_meetups(bot, store))
        self.assertEqual(asyncio.run(poll_and_close_meetups(bot, store)), 0)

    def test_a_still_polling_meetup_is_left_alone(self) -> None:
        """An unlocked poll only ages out after the stale window."""
        store = MeetupStore(":memory:")
        slots = parse_slots("fri 7pm", ZONE, now=NOW)
        meetup_id = store.create(
            guild_id=1, channel_id=2, organizer_id=3, title="Board games",
            location="", timezone=ZONE, activities=["Bowling"],
            times=[(slot.epoch, slot.source) for slot in slots],
        )
        store.attach_message(meetup_id, 555, 666)
        bot = _FakeBot(_FakeMessage(), _FakeThread())
        self.assertEqual(asyncio.run(poll_and_close_meetups(bot, store)), 0)
        self.assertEqual(store.get(meetup_id).state, STATE_POLLING)


class MeetupRegressionTests(unittest.IsolatedAsyncioTestCase):
    """Exercise server isolation, large lists, and stale organizer pickers."""

    def setUp(self) -> None:
        self.store = MeetupStore(":memory:")
        self.meetup_id = self.create_meetup()

    def create_meetup(self) -> int:
        return self.store.create(
            guild_id=1, channel_id=2, organizer_id=3, title="x" * 100,
            location="", timezone=ZONE, activities=["Bowling", "Games"],
            times=[(2_000_000_000, "future")],
        )

    async def test_explicit_ids_cannot_escape_the_invoking_server(self) -> None:
        from bot_app.commands.meetup.meetup import MeetupCommands

        cog = SimpleNamespace(store=self.store)
        for guild_id in (999, None):
            ctx = SimpleNamespace(guild_id=guild_id, channel=SimpleNamespace(id=2))
            self.assertIsNone(await MeetupCommands._resolve(cog, ctx, self.meetup_id))
        ctx = SimpleNamespace(guild_id=1, channel=SimpleNamespace(id=999))
        self.assertEqual(
            (await MeetupCommands._resolve(cog, ctx, self.meetup_id)).meetup_id,
            self.meetup_id,
        )

    async def test_cross_server_manager_cannot_cancel_or_preview_pings(self) -> None:
        from bot_app.commands.meetup.meetup import MeetupCommands

        with patch("bot_app.commands.meetup.meetup.get_meetup_store", return_value=self.store):
            cog = MeetupCommands(SimpleNamespace())
        self.store.lock(self.meetup_id, "Bowling", 2_000_000_000)
        ctx = SimpleNamespace(
            guild_id=999, channel=SimpleNamespace(id=888), respond=AsyncMock(),
            interaction=SimpleNamespace(user=SimpleNamespace(
                id=444, guild_permissions=SimpleNamespace(manage_guild=True)
            )),
        )
        with patch("bot_app.commands.meetup.meetup.log_command"):
            await MeetupCommands.cancel.callback(cog, ctx, self.meetup_id)
            await MeetupCommands.confirm.callback(cog, ctx, self.meetup_id, None)
        self.assertEqual(self.store.get(self.meetup_id).state, STATE_LOCKED)
        self.assertEqual(ctx.respond.await_count, 2)
        self.assertTrue(all("view" not in call.kwargs for call in ctx.respond.call_args_list))

    async def test_large_list_keeps_every_row_in_safe_aligned_pages(self) -> None:
        from bot_app.commands.meetup.meetup import MeetupCommands

        ids = [self.meetup_id] + [self.create_meetup() for _ in range(100)]
        ctx = SimpleNamespace(guild_id=1, author=SimpleNamespace(id=3), respond=AsyncMock())
        with patch("bot_app.commands.meetup.meetup.log_command"):
            await MeetupCommands.list_meetups.callback(SimpleNamespace(store=self.store), ctx)
        paginator = ctx.respond.call_args.kwargs["view"]
        seen = []
        for page in range(paginator.max_page + 1):
            paginator.page = page
            embed = paginator.render()
            self.assertEqual([f.name for f in embed.fields], ["Meetup", "State", "When"])
            lengths = [len(f.value.splitlines()) for f in embed.fields]
            self.assertEqual(len(set(lengths)), 1)
            self.assertTrue(all(f.inline and len(f.value) <= 1024 for f in embed.fields))
            self.assertLessEqual(len(embed), 6000)
            seen.extend(int(row.split("`")[1][1:]) for row in embed.fields[0].value.splitlines())
        self.assertCountEqual(seen, ids)
        paginator.stop()

    async def test_stale_picker_cannot_reopen_or_replace_a_locked_plan(self) -> None:
        from bot_app.meetups.views import LockView

        for state in (STATE_CLOSED, STATE_LOCKED):
            with self.subTest(state=state):
                meetup_id = self.create_meetup()
                picker = LockView(self.store.get(meetup_id), self.store)
                self.store.lock(meetup_id, "Games", 2_000_000_000)
                if state == STATE_CLOSED:
                    self.store.set_state(meetup_id, STATE_CLOSED)
                original = self.store.get(meetup_id)
                interaction = SimpleNamespace(
                    response=SimpleNamespace(defer=AsyncMock()),
                    edit_original_response=AsyncMock(), client=SimpleNamespace(),
                )
                with patch("bot_app.meetups.views._edit_meetup_message", new_callable=AsyncMock) as edit:
                    await picker._confirm(interaction)
                current = self.store.get(meetup_id)
                self.assertEqual((current.state, current.locked_activity, current.closed_at),
                                 (original.state, original.locked_activity, original.closed_at))
                interaction.response.defer.assert_awaited_once()
                self.assertIn("no longer open", interaction.edit_original_response.call_args.kwargs["content"])
                edit.assert_not_awaited()
                picker.stop()

    async def test_fresh_picker_acknowledges_before_locking_and_updates_message(self) -> None:
        from bot_app.meetups.views import LockView

        picker = LockView(self.store.get(self.meetup_id), self.store)
        interaction = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(), client=SimpleNamespace(),
        )
        original_lock = self.store.lock

        def lock(*args):
            interaction.response.defer.assert_awaited_once()
            return original_lock(*args)

        with patch.object(self.store, "lock", side_effect=lock), patch(
            "bot_app.meetups.views._edit_meetup_message", new_callable=AsyncMock
        ) as edit:
            await picker._confirm(interaction)
        self.assertEqual(self.store.get(self.meetup_id).state, STATE_LOCKED)
        edit.assert_awaited_once()
        picker.stop()


if __name__ == "__main__":
    unittest.main()
