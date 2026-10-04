"""CI helper: run each test module in its own process with a timeout.

A hanging test cannot burn the 6 hour GitHub job limit, and the result of
every module is emitted as a GitHub annotation (readable through the API
without downloading logs). Locally it is just a summary:

    python tests/ci_runner.py            # EDR_TEST_TIMEOUT=seconds per module (default 150)
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = int(os.environ.get("EDR_TEST_TIMEOUT", "150"))
IN_CI = os.environ.get("GITHUB_ACTIONS") == "true"


def as_text(value):
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value


def annotate(level, title, message):
    if IN_CI:
        message = message[:6000].replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::{level} title={title}::{message}", flush=True)


def run_module(name):
    command = [sys.executable, "-X", "faulthandler", "-m", "unittest", f"tests.{name}", "-v"]
    try:
        proc = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=TIMEOUT)
        output = (as_text(proc.stdout) + as_text(proc.stderr)).replace("\r", "\n")
        return ("ok" if proc.returncode == 0 else "FAILED"), output
    except subprocess.TimeoutExpired as exc:
        output = (as_text(exc.stdout) + as_text(exc.stderr)).replace("\r", "\n")
        return "HANG", output


def main():
    modules = sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, "tests")) if f.startswith("test_") and f.endswith(".py"))
    failed = []
    for name in modules:
        status, output = run_module(name)
        ran = re.search(r"^Ran (\d+) tests? in ([\d.]+)s", output, re.M)
        detail = f"{ran.group(1)} tests in {ran.group(2)}s" if ran else "no result"
        print(f"{name:24s} {status:7s} {detail}", flush=True)
        if status == "ok":
            annotate("notice", name, f"ok: {detail}")
            continue
        failed.append(name)
        if status == "HANG":
            tail = "\n".join(line for line in output.split("\n") if line.strip())[-3500:]
            annotate("error", f"{name} HANG", f"no result after {TIMEOUT}s. Last output (the last test listed is the one that hung):\n{tail}")
        else:
            starts = [m.start() for m in re.finditer(r"^(ERROR|FAIL): ", output, re.M)]
            body = output[starts[0]:] if starts else output[-3500:]
            annotate("error", f"{name} FAILED", body)
        print(output[-2500:], flush=True)
    print("\nFAILED modules: " + ", ".join(failed) if failed else "\nAll test modules passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
