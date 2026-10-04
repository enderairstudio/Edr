"""CI helper: run the whole suite with a hang watchdog.

If the suite is still running after EDR_TEST_WATCHDOG seconds (default 300),
every thread's stack is dumped to hang-traceback.txt and the process exits
non-zero, so a hanging test is diagnosable instead of burning the 6 hour
GitHub job limit. Test output goes to test-output.txt (verbose, flushed per
test, so the last line names the test that was running).
"""

import faulthandler
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    os.chdir(ROOT)
    watchdog = int(os.environ.get("EDR_TEST_WATCHDOG", "300"))
    hang_file = open("hang-traceback.txt", "w")
    faulthandler.dump_traceback_later(watchdog, exit=True, file=hang_file)
    with open("test-output.txt", "w", buffering=1, encoding="utf-8") as out:
        runner = unittest.TextTestRunner(stream=out, verbosity=2)
        suite = unittest.defaultTestLoader.discover("tests")
        result = runner.run(suite)
    faulthandler.cancel_dump_traceback_later()
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
