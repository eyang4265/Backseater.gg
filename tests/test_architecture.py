"""Public compatibility imports and enforceable dependency boundaries."""

import ast
import unittest
from pathlib import Path

import discord

from bot_app.commands import register_all
from bot_app.domain.ranks import RankSnapshot
from bot_app.repositories.accounts import Account
from bot_app.services.riot_api import RiotClient


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_slash_command_required_options_precede_optional_options(self) -> None:
        """Prevent Discord from rejecting the entire bulk command sync."""
        bot = discord.Bot()
        register_all(bot)
        for command in bot.pending_application_commands:
            optional_seen = False
            for option in command.options:
                if not option.required:
                    optional_seen = True
                elif optional_seen:
                    self.fail(
                        f"/{command.qualified_name}: required option "
                        f"{option.name!r} follows an optional option"
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
