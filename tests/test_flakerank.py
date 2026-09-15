"""Flake tier-list persistence and rendering."""

import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch
from unittest.mock import AsyncMock, Mock

from bot_app.commands import register_all
from bot_app.commands.command_directory import is_owner_only, public_commands
from bot_app.commands.info.commands import _COMMAND_LOOKUP
from bot_app.flake_ranks import build_flake_rank_embed
from bot_app.store import load_flake_ranks, set_flake_rank, set_flake_ranks


class FlakeRankTests(unittest.TestCase):
    """Keep assignments server-scoped and users in exactly one tier."""

    def test_assignment_persists_and_reassignment_moves_the_user(self) -> None:
        """A later assignment replaces, rather than duplicates, the old tier."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flake_ranks.json"
            with patch("bot_app.store.FLAKE_RANKS_PATH", path):
                set_flake_rank(10, 20, "Flaky Friend", "S")
                set_flake_rank(10, 20, "Flaky Friend", "B")
                set_flake_rank(11, 20, "Flaky Friend", "F")
                rankings = load_flake_ranks()
        self.assertEqual(rankings["10"]["20"]["tier"], "B")
        self.assertEqual(rankings["11"]["20"]["tier"], "F")

    def test_embed_orders_tiers_with_s_as_most_flaky(self) -> None:
        """The rendered legend and fields make the scale unambiguous."""
        embed = build_flake_rank_embed(
            {
                "2": {"display_name": "Bravo", "tier": "F"},
                "1": {"display_name": "Alpha", "tier": "S"},
            },
            guild_name="Friends",
        )
        self.assertEqual(embed.title, "Flake Tier List — Friends")
        self.assertIn("S is the most flaky", embed.description)
        self.assertEqual([field.name for field in embed.fields], [
            "S Tier", "A Tier", "B Tier", "C Tier", "D Tier", "F Tier",
            "Unknown Tier",
        ])
        self.assertEqual(embed.fields[0].value, "<@1>")
        self.assertEqual(embed.fields[-2].value, "<@2>")

    def test_unknown_is_persisted_as_the_bottom_tier(self) -> None:
        """Unknown remains title-cased and renders after every ranked tier."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flake_ranks.json"
            with patch("bot_app.store.FLAKE_RANKS_PATH", path):
                rankings = set_flake_rank(10, 20, "Mystery", "Unknown")
                persisted = load_flake_ranks()
        self.assertEqual(rankings["20"]["tier"], "Unknown")
        self.assertEqual(persisted["10"]["20"]["tier"], "Unknown")
        embed = build_flake_rank_embed(rankings, guild_name="Friends")
        self.assertEqual(embed.fields[-1].name, "Unknown Tier")
        self.assertEqual(embed.fields[-1].value, "<@20>")

    def test_multiple_users_are_saved_in_one_assignment(self) -> None:
        """A batch puts every selected member in the requested category."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flake_ranks.json"
            with patch("bot_app.store.FLAKE_RANKS_PATH", path):
                rankings = set_flake_ranks(
                    10, ((20, "First"), (30, "Second")), "F"
                )
        self.assertEqual(rankings["20"]["tier"], "F")
        self.assertEqual(rankings["30"]["tier"], "F")

    def test_embed_splits_large_tiers_only_between_members(self) -> None:
        """No field exceeds Discord's limit or cuts a member mention in half."""
        rankings = {
            str(user_id): {"display_name": str(user_id), "tier": "S"}
            for user_id in range(100000000000000000, 100000000000000060)
        }
        embed = build_flake_rank_embed(rankings, guild_name="Friends")
        s_fields = [field for field in embed.fields if field.name.startswith("S Tier")]
        self.assertGreater(len(s_fields), 1)
        self.assertTrue(all(len(field.value) <= 1024 for field in s_fields))
        self.assertEqual(sum(field.value.count("<@") for field in s_fields), 60)

    def test_command_is_owner_only_and_absent_from_public_directory(self) -> None:
        """Only the bot owner can invoke or discover the ranking command."""
        import discord

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            bot = discord.Bot()
            register_all(bot)
            command = next(
                command
                for command in bot.pending_application_commands
                if command.qualified_name == "flakerank"
            )
            self.assertTrue(is_owner_only(command))
            self.assertEqual([option.name for option in command.options], ["user", "category"])
            self.assertEqual(command.options[0].input_type, discord.SlashCommandOptionType.user)
            self.assertNotIn(command, public_commands(bot, tft=False))
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_flake_display_command_is_public_and_has_no_options(self) -> None:
        """The public read-only surface is registered separately from editing."""
        import discord

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            bot = discord.Bot()
            register_all(bot)
            command = next(
                command
                for command in bot.pending_application_commands
                if command.qualified_name == "flake"
            )
            self.assertFalse(is_owner_only(command))
            self.assertEqual(command.options, [])
            self.assertIn("flake", _COMMAND_LOOKUP)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_single_page_saves_before_one_ephemeral_response(self) -> None:
        """Local persistence completes before Discord's response can stall."""
        import discord

        from bot_app.commands.flakerank import FlakeRankCommands

        async def exercise() -> None:
            ctx = Mock()
            ctx.guild = Mock(id=10, name="Friends")
            ctx.author = Mock(id=99)
            ctx.respond = AsyncMock(return_value=Mock())
            member = Mock(
                spec=discord.Member, id=200000000000000020,
                name="friend", display_name="Flaky Friend"
            )
            ctx.guild.members = [member]
            ctx.guild.get_member.return_value = member
            cog = FlakeRankCommands(Mock())
            callback = FlakeRankCommands.flakerank.callback
            with patch(
                "bot_app.commands.flakerank.set_flake_rank",
                return_value={
                    "20": {"display_name": "Flaky Friend", "tier": "S"}
                },
            ):
                await callback(cog, ctx, member, "S")
            response_kwargs = ctx.respond.await_args.kwargs
            self.assertTrue(response_kwargs["ephemeral"])
            self.assertIn("Flake Tier List", response_kwargs["embed"].title)
            self.assertNotIn("view", response_kwargs)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
