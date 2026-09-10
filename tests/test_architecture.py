"""Public compatibility imports and enforceable dependency boundaries."""

import ast
import asyncio
import unittest
from pathlib import Path

import discord

from bot_app.commands.command_directory import is_owner_only, public_commands
from bot_app.commands import register_all
from bot_app.domain.ranks import RankSnapshot
from bot_app.repositories.accounts import Account
from bot_app.services.riot_api import RiotClient


class ArchitectureBoundaryTests(unittest.TestCase):
    @staticmethod
    def _flatten(commands):
        """Yield every leaf slash command, descending into command groups."""
        for command in commands:
            subcommands = getattr(command, "subcommands", None)
            if subcommands:
                yield from ArchitectureBoundaryTests._flatten(subcommands)
            elif hasattr(command, "options"):
                yield command

    def test_slash_command_required_options_precede_optional_options(self) -> None:
        """Prevent Discord from rejecting the entire bulk command sync."""
        # Constructing a client needs a live event loop, and an earlier
        # asyncio.run() in the same process leaves the thread without one.
        asyncio.set_event_loop(asyncio.new_event_loop())
        bot = discord.Bot()
        register_all(bot)
        for command in self._flatten(bot.pending_application_commands):
            optional_seen = False
            for option in command.options:
                if not option.required:
                    optional_seen = True
                elif optional_seen:
                    self.fail(
                        f"/{command.qualified_name}: required option "
                        f"{option.name!r} follows an optional option"
                    )

    def test_player_specific_match_commands_offer_role_slots(self) -> None:
        """Keep the shared blue/red 1-10 player selector on each surface."""
        asyncio.set_event_loop(asyncio.new_event_loop())
        bot = discord.Bot()
        register_all(bot)
        commands_by_name = {
            command.qualified_name: command
            for command in self._flatten(bot.pending_application_commands)
        }
        for name in (
            "mastery",
            "match",
            "timeline",
            "laning",
            "matchhistory",
            "matchlist",
            "champstats",
            "counterstats",
            "duo",
        ):
            position = next(
                option
                for option in commands_by_name[name].options
                if option.name == "position"
            )
            self.assertFalse(position.required)
            self.assertEqual((position.min_value, position.max_value), (1, 10))

    def test_tft_commands_are_explicitly_prefixed(self) -> None:
        """TFT surfaces stay separate from the League command names."""
        asyncio.set_event_loop(asyncio.new_event_loop())
        bot = discord.Bot()
        register_all(bot)
        commands_by_name = {
            command.qualified_name: command
            for command in self._flatten(bot.pending_application_commands)
        }
        self.assertIn("tftmatch", commands_by_name)
        self.assertNotIn(
            "tftlivegame",
            commands_by_name,
            "TFT live games are disabled while Spectator-TFT-V5 returns 403",
        )
        self.assertIn("tftadd", commands_by_name)
        self.assertIn("tftupdate", commands_by_name)
        self.assertEqual(commands_by_name["tftupdate"].options, [])
        self.assertNotIn(
            "game", {option.name for option in commands_by_name["match"].options}
        )

    def test_game_command_directories_cover_every_public_command(self) -> None:
        """Keep both directories synchronized with live registration metadata."""
        asyncio.set_event_loop(asyncio.new_event_loop())
        bot = discord.Bot()
        register_all(bot)
        league = set(public_commands(bot, tft=False))
        tft = set(public_commands(bot, tft=True))
        public = {
            command
            for command in bot.application_commands
            if not is_owner_only(command)
        }
        self.assertEqual(league | tft, public)
        self.assertFalse(league & tft)
        self.assertTrue(
            all(not command.qualified_name.startswith("tft") for command in league)
        )
        self.assertTrue(
            all(command.qualified_name.startswith("tft") for command in tft)
        )

    def test_slash_command_options_resolved_to_real_types(self) -> None:
        """Catch options declared as annotations under postponed evaluation.

        Every module uses ``from __future__ import annotations``, so an
        option written as ``name: discord.Option(int, ...)`` reaches
        py-cord as the *source text* of that call: it silently registers
        as a string option and then raises ``TypeError`` at invoke time.
        Declaring options with ``@discord.option(...)`` avoids this.
        """
        asyncio.set_event_loop(asyncio.new_event_loop())
        bot = discord.Bot()
        register_all(bot)
        for command in self._flatten(bot.pending_application_commands):
            for option in command.options:
                self.assertNotIsInstance(
                    option._raw_type,
                    str,
                    f"/{command.qualified_name}: option {option.name!r} was declared as "
                    "an annotation; use a @discord.option decorator instead",
                )

    def test_public_boundaries_expose_their_primary_types(self) -> None:
        """Verify that public boundaries expose their primary types."""
        self.assertEqual(RankSnapshot.__name__, "RankSnapshot")
        self.assertEqual(Account.__name__, "Account")
        self.assertEqual(RiotClient.__name__, "RiotClient")

    def test_requests_is_owned_by_network_adapters(self) -> None:
        """Verify that requests is owned by network adapters."""
        root = Path(__file__).resolve().parents[1] / "bot_app"
        allowed = {
            root / "riot.py",
            root / "ddragon.py",
            root / "opgg.py",
            root / "coachless.py",
            root / "commands" / "ai.py",
        }
        offenders = []
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imports_requests = any(
                (
                    isinstance(node, ast.Import)
                    and any(alias.name == "requests" for alias in node.names)
                )
                or (isinstance(node, ast.ImportFrom) and node.module == "requests")
                for node in ast.walk(tree)
            )
            if imports_requests and path not in allowed:
                offenders.append(path.relative_to(root))
        self.assertEqual(offenders, [])

    def test_pure_modules_do_not_import_io_layers(self) -> None:
        """Verify that pure modules do not import io layers."""
        root = Path(__file__).resolve().parents[1] / "bot_app"
        pure = [
            "routing.py",
            "queues.py",
            "positions.py",
            "timeline.py",
            "history.py",
            "rating.py",
        ]
        forbidden = {"riot", "ddragon", "store", "discord", "requests"}
        for filename in pure:
            tree = ast.parse((root / filename).read_text(encoding="utf-8"))
            imported = {
                alias.name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
            }
            imported.update(
                (node.module or "").split(".")[-1]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom)
            )
            self.assertFalse(
                imported & forbidden, f"{filename}: {imported & forbidden}"
            )


if __name__ == "__main__":
    unittest.main()
