"""Progress output must reflect real work and never sleep by default.

Run with: python -m unittest tests.test_print -v
"""

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import print as p  # noqa: E402


class ProgressTests(unittest.TestCase):
    def test_progress_never_sleeps_by_default(self):
        self.assertFalse(p.PACED_PROGRESS)
        p.configure_workload(files=5000, bytes_=10 * 1024 ** 3)  # a "huge" project
        out = io.StringIO()
        with mock.patch("time.sleep", side_effect=AssertionError("progress slept")), contextlib.redirect_stdout(out):
            for label in ("scanning", "copying files", "sending payload"):
                for percent in (0, 10, 50, 99, 100):
                    p.progress(label, percent)
        text = out.getvalue()
        self.assertIn("done", text)
        self.assertIn("copying files", text)

    def test_progress_shows_the_real_percentage_immediately(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            p.progress("unit-test stage", 0)
            p.progress("unit-test stage", 77)
            p.progress("unit-test stage", 100)
        self.assertIn("unit-test stage.... 77", out.getvalue())

    def test_name_prompt_is_skipped_when_stdin_is_not_a_terminal(self):
        with mock.patch.object(sys, "stdin", io.StringIO("")), mock.patch("time.sleep", side_effect=AssertionError("slept")):
            self.assertIsNone(p.prompt_name_countdown(3))


if __name__ == "__main__":
    unittest.main()
