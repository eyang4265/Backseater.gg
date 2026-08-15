"""Every team must come out with exactly one player per role."""

import unittest

from bot_app.positions import (
    ROLE_ORDER,
    SMITE_SPELL_ID,
    assign_team_positions,
    role_sort_key,
)

_TAGS = {
    "Garen": ("Fighter",),
    "LeeSin": ("Fighter", "Assassin"),
    "Orianna": ("Mage",),
    "Jinx": ("Marksman",),
    "Lulu": ("Support", "Mage"),
    "Xerath": ("Mage", "Support"),
    "Nilah": ("Assassin", "Fighter"),
    "Nobody": (),
}


def _team(*champion_ids, smite_index=None):
    """Handle team."""
    participants = []
    for index, _ in enumerate(champion_ids):
        spells = {"spell1Id": 4, "spell2Id": 7}
        if index == smite_index:
            spells["spell2Id"] = SMITE_SPELL_ID
        participants.append(spells)
    return participants, list(champion_ids)


class AssignTeamPositionsTests(unittest.TestCase):
    def test_full_team_gets_each_role_exactly_once(self) -> None:
        """Verify that full team gets each role exactly once."""
        participants, champions = _team(
            "Garen", "LeeSin", "Orianna", "Jinx", "Lulu", smite_index=1
        )
        assigned = assign_team_positions(participants, champions, _TAGS)
        self.assertEqual(sorted(assigned), sorted(ROLE_ORDER))

    def test_smite_wins_the_jungle_slot(self) -> None:
        """Verify that smite wins the jungle slot."""
        participants, champions = _team(
            "Garen", "Orianna", "Jinx", "Lulu", "LeeSin", smite_index=2
        )
        assigned = assign_team_positions(participants, champions, _TAGS)
        self.assertEqual(assigned[2], "Jungle")

    def test_match_v5_summoner_spell_fields_detect_smite(self) -> None:
        """Verify that match v5 summoner spell fields detect smite."""
        participants, champions = _team("LeeSin", "Garen", "Orianna", "Jinx", "Lulu")
        participants[0] = {"summoner1Id": 4, "summoner2Id": SMITE_SPELL_ID}
        assigned = assign_team_positions(
            participants,
            champions,
            _TAGS,
            ["Top", "Jungle", "Mid", "Bottom", "Support"],
        )
        self.assertEqual(assigned[0], "Jungle")
        self.assertEqual(len(set(assigned)), 5)

    def test_reported_positions_are_honoured(self) -> None:
        """Verify that reported positions are honoured."""
        participants, champions = _team("Garen", "LeeSin", "Orianna", "Jinx", "Lulu")
        assigned = assign_team_positions(
            participants, champions, _TAGS, ["Support", None, None, None, None]
        )
        self.assertEqual(assigned[0], "Support")
        self.assertEqual(sorted(assigned), sorted(ROLE_ORDER))

    def test_duplicate_reported_positions_still_fill_every_slot(self) -> None:
        """Verify that duplicate reported positions still fill every slot."""
        participants, champions = _team("Garen", "LeeSin", "Orianna", "Jinx", "Lulu")
        assigned = assign_team_positions(
            participants, champions, _TAGS, ["Mid", "Mid", "Mid", None, None]
        )
        self.assertEqual(len(assigned), 5)
        self.assertTrue(all(role in ROLE_ORDER for role in assigned))

    def test_tag_order_separates_mid_mages_from_support_mages(self) -> None:
        """Verify that tag order separates mid mages from support mages."""
        participants, champions = _team(
            "Garen", "LeeSin", "Xerath", "Jinx", "Lulu", smite_index=1
        )
        assigned = assign_team_positions(participants, champions, _TAGS)
        self.assertEqual(assigned[2], "Mid")
        self.assertEqual(assigned[4], "Support")

    def test_champion_override_beats_its_tags(self) -> None:
        """Verify that champion override beats its tags."""
        participants, champions = _team(
            "Garen", "LeeSin", "Orianna", "Nilah", "Lulu", smite_index=1
        )
        assigned = assign_team_positions(participants, champions, _TAGS)
        self.assertEqual(assigned[3], "Bottom")

    def test_untagged_champion_still_gets_a_role(self) -> None:
        """Verify that untagged champion still gets a role."""
        participants, champions = _team(
            "Nobody", "LeeSin", "Orianna", "Jinx", "Lulu", smite_index=1
        )
        assigned = assign_team_positions(participants, champions, _TAGS)
        self.assertEqual(sorted(assigned), sorted(ROLE_ORDER))

    def test_empty_team(self) -> None:
        """Verify that empty team."""
        self.assertEqual(assign_team_positions([], [], _TAGS), [])


class RoleSortKeyTests(unittest.TestCase):
    def test_orders_rows_top_to_support(self) -> None:
        """Verify that orders rows top to support."""
        shuffled = ["Support", "Top", "Bottom", "Jungle", "Mid"]
        self.assertEqual(sorted(shuffled, key=role_sort_key), list(ROLE_ORDER))

    def test_unknown_roles_sort_last(self) -> None:
        """Verify that unknown roles sort last."""
        self.assertEqual(sorted(["???", "Top"], key=role_sort_key), ["Top", "???"])


if __name__ == "__main__":
    unittest.main()
