"""Public architecture boundaries remain importable and dependency-light."""

import unittest

from bot_app.domain.ranks import RankSnapshot
from bot_app.repositories.accounts import Account
from bot_app.services.riot_api import RiotClient


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_public_boundaries_expose_their_primary_types(self) -> None:
        self.assertEqual(RankSnapshot.__name__, "RankSnapshot")
        self.assertEqual(Account.__name__, "Account")
        self.assertEqual(RiotClient.__name__, "RiotClient")


if __name__ == "__main__":
    unittest.main()
