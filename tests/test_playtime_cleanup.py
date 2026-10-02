from __future__ import annotations

import subprocess
import sys
import unittest

from core.playtime_tracker import terminate_game_process


class PlaytimeCleanupTests(unittest.TestCase):
    def test_cleanup_terminates_owned_process_session(self):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            start_new_session=True,
        )
        try:
            self.assertTrue(terminate_game_process(process, graceful_timeout=0.1))
            self.assertIsNotNone(process.poll())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


if __name__ == "__main__":
    unittest.main()
