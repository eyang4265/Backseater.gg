"""Single-instance startup guard."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bot_app.singleton as singleton


class SingletonLockTests(unittest.TestCase):
    def setUp(self) -> None:
        # Use a throwaway lock file so this test never contends with (or is
        # broken by) a real bot process holding the repo's actual bot.lock.
        self._tmpdir = tempfile.TemporaryDirectory()
        self._lock_patch = patch.object(
            singleton, "_LOCK_PATH", Path(self._tmpdir.name) / "bot.lock"
        )
        self._lock_patch.start()
        if singleton._lock_file is not None:
            singleton._lock_file.close()
            singleton._lock_file = None

    def tearDown(self) -> None:
        if singleton._lock_file is not None:
            singleton._lock_file.close()
            singleton._lock_file = None
        self._lock_patch.stop()
        self._tmpdir.cleanup()

    def test_second_acquire_in_same_process_exits(self) -> None:
        """Verify that a second lock attempt while one is held exits the process."""
        singleton.acquire_singleton_lock()
        held_lock = singleton._lock_file
        try:
            with self.assertRaises(SystemExit):
                singleton.acquire_singleton_lock()
        finally:
            held_lock.close()


if __name__ == "__main__":
    unittest.main()
