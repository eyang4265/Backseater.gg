"""Announcement presentation helpers."""

import unittest

from bot_app.announce import gold_embed


class GoldEmbedTests(unittest.TestCase):
    def test_shows_blue_red_and_per_row_gold_difference(self) -> None:
        """Verify that shows blue red and per row gold difference."""
        embed = gold_embed(
            {
                "info": {
                    "participants": [
                        {"participantId": 1, "teamId": 100, "goldEarned": 22_459},
                        {"participantId": 2, "teamId": 100, "goldEarned": 16_830},
                        {"participantId": 6, "teamId": 200, "goldEarned": 13_202},
                        {"participantId": 7, "teamId": 200, "goldEarned": 17_832},
                    ]
                }
            }
        )

        self.assertEqual(
            [field.name for field in embed.fields], ["Blue Team", "Red Team", "Diff"]
        )
        self.assertEqual(embed.fields[0].value, "22,459 gold\n16,830 gold")
        self.assertEqual(embed.fields[1].value, "13,202 gold\n17,832 gold")
        self.assertEqual(embed.fields[2].value, "+9,257 gold\n-1,002 gold")


if __name__ == "__main__":
    unittest.main()
