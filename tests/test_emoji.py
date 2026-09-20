"""Custom-emoji lookup tests."""

import unittest
from unittest.mock import patch

from bot_app.emoji import item_emoji, rune_emoji, summoner_spell_emoji


class ItemEmojiTests(unittest.TestCase):
    """Historical match ids can use the configured current item asset."""

    def test_old_stormrazor_id_uses_3097_icon(self) -> None:
        """Final items with id 3095 render the existing 3097 emoji."""
        current_icon = object()
        with patch("bot_app.emoji._emoji_index", return_value={"3097": current_icon}):
            self.assertIs(item_emoji("Stormrazor", item_id=3095), current_icon)



class RuneEmojiTests(unittest.TestCase):
    """Verify OP.GG shard labels select the configured shard assets."""

    def test_opgg_shards_use_configured_assets(self) -> None:
        """Map OP.GG's shard labels to the server's short asset names."""
        aliases = {
            "Adaptive Force": "AdaptiveForce",
            "Adaptive Force Scaling": "AdaptiveForceScaling",
            "Attack Speed": "AttackSpeed",
            "Armor": "Armor",
            "Ability Haste": "CDRScaling",
            "Cooldown Reduction": "CDRScaling",
            "Health": "HealthPlus",
            "Health Scaling": "HealthScaling",
            "Magic Resist": "MagicRes",
            "Move Speed": "MovementSpeed",
            "Movement Speed": "MovementSpeed",
            "Nimbus Cloak": "NimbusCloak",
            "Tenacity": "Tenacity",
        }
        emoji_by_name = {name.lower(): object() for name in set(aliases.values())}
        with patch(
            "bot_app.emoji._emoji_index",
            return_value=emoji_by_name,
        ):
            for label, emoji_name in aliases.items():
                self.assertIs(rune_emoji(label), emoji_by_name[emoji_name.lower()])

    def test_summoner_spell_ids_use_the_supplied_asset_names(self) -> None:
        """Resolve Coachless spell IDs to the filenames used as emoji names."""
        flash = object()
        ignite = object()
        with patch(
            "bot_app.emoji._emoji_index",
            return_value={"summonerflash": flash, "summonerdot": ignite},
        ):
            self.assertIs(summoner_spell_emoji(4), flash)
            self.assertIs(summoner_spell_emoji("14"), ignite)


if __name__ == "__main__":
    unittest.main()
