"""Tests for `edr update`: version parsing, release lookup, safe install, rollback.

Run with: python -m unittest tests.test_updater -v

A tiny local HTTP server plays GitHub. Updates are installed for real into a
temp folder laid out like an EDR install (<root>/app/*.py + launcher).
"""

import contextlib
import hashlib
import http.server
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import error as e  # noqa: E402
import handler as h  # noqa: E402
import print as p  # noqa: E402
import share as s  # noqa: E402
import updater as u  # noqa: E402

NEW_VERSION = "9.9.9"


def app_sources(version):
    """name -> bytes of every shipped module, with print.VERSION rewritten."""
    files = {}
    for name in sorted(s.CLI_FILES):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        if name == "print.py":
            text = re.sub(r'^VERSION = ".*"', f'VERSION = "{version}"', text, count=1, flags=re.MULTILINE)
        files[name] = text.encode("utf-8")
    files["newmod.py"] = b"MARK = 1\n"
    return files


def build_unix_script():
    text = (REPO_ROOT / "build-unix.sh").read_text(encoding="utf-8")
    patched = re.sub(r"^(APP_FILES=\([^)]*)\)", r"\1 newmod.py)", text, count=1, flags=re.MULTILINE)
    assert patched != text
    return patched.encode("utf-8")


def source_zip(version=NEW_VERSION, overrides=None, extra=None):
    files = app_sources(version)
    files.update(overrides or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        prefix = "enderairstudio-Edr-abc1234/"
        archive.writestr(prefix + "build-unix.sh", build_unix_script())
        archive.writestr(prefix + "setup_installer.py", b"raise SystemExit('installer tooling must not be installed')\n")
        for name, data in files.items():
            archive.writestr(prefix + name, data)
        for name, data in (extra or {}).items():
            archive.writestr(name, data)
    return buffer.getvalue()


def windows_zip(version=NEW_VERSION, overrides=None):
    files = app_sources(version)
    files.update(overrides or {})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        # Windows PowerShell 5.1's Compress-Archive writes backslash separators.
        archive.writestr("edr\\edr.exe", b"NEW-LAUNCHER")
        for name, data in files.items():
            archive.writestr(f"edr\\app\\{name}", data)
    return buffer.getvalue()


class FakeGitHub:
    def __init__(self):
        self.release = None
        self.files = {}
        self.status = 200
        self.hits = []
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def do_GET(self):
                fake.hits.append(self.path)
                if self.path.endswith("/releases/latest"):
                    if fake.status != 200:
                        self.send_error(fake.status)
                        return
                    body = json.dumps(fake.release).encode()
                elif self.path in fake.files:
                    body = fake.files[self.path]
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/repo"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def publish(self, tag, source=None, windows=None, digest=None):
        assets = []
        if windows is not None:
            self.files["/repo/download/EDR-win64.zip"] = windows
            asset = {"name": u.WINDOWS_ASSET, "browser_download_url": f"{self.base}/download/EDR-win64.zip"}
            if digest is not None:
                asset["digest"] = digest
            assets.append(asset)
        if source is not None:
            self.files["/repo/zipball"] = source
        self.release = {"tag_name": tag, "assets": assets, "zipball_url": f"{self.base}/zipball"}


class UpdaterCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.root = tmp / "install"
        self.app = self.root / "app"
        self.app.mkdir(parents=True)
        for name in s.CLI_FILES:
            (self.app / name).write_bytes((REPO_ROOT / name).read_bytes())
        (self.app / "qrcode").mkdir()
        (self.app / "qrcode" / "__init__.py").write_text("VENDORED = True\n")
        (self.root / "edr").write_text("#!/bin/sh\nOLD-LAUNCHER\n")
        (self.root / "edr").chmod(0o755)

        self.github = FakeGitHub()
        self.addCleanup(self.github.close)
        for patcher in (
            mock.patch.dict(os.environ, {"EDR_UPDATE_API": self.github.base}),
            mock.patch.object(u, "locate_install", return_value=u.Install(self.root, self.app)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = h.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def installed_version(self):
        text = (self.app / "print.py").read_text(encoding="utf-8")
        return re.search(r'^VERSION = "(.*)"', text, re.MULTILINE).group(1)

    def leftovers(self):
        return [path.name for path in self.root.rglob("*") if path.name.endswith((u.BACKUP_SUFFIX, u.NEW_SUFFIX))]


class VersionTests(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(u.parse_version("v0.5.17"), (0, 5, 17))
        self.assertEqual(u.parse_version("0.5.17.0"), (0, 5, 17))
        self.assertEqual(u.parse_version("V1.0"), u.parse_version("1.0.0"))
        self.assertEqual(u.parse_version("v2.0.0-rc1"), (2,))
        self.assertIsNone(u.parse_version("nightly"))
        self.assertIsNone(u.parse_version(""))
        self.assertIsNone(u.parse_version(None))

    def test_ordering_is_numeric_not_textual(self):
        self.assertGreater(u.parse_version("v0.5.18"), u.parse_version("v0.5.17"))
        self.assertGreater(u.parse_version("v0.10.0"), u.parse_version("v0.9.9"))
        self.assertGreater(u.parse_version("v0.5.17.1"), u.parse_version("v0.5.17"))

    def test_plain_http_is_refused_without_the_test_override(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EDR_UPDATE_API", None)
            with self.assertRaises(e.CliError):
                u._require_safe_url("http://example.com/x.zip")
            u._require_safe_url("https://example.com/x.zip")


class CheckTests(UpdaterCase):
    def test_up_to_date_downloads_nothing(self):
        self.github.publish(f"v{p.VERSION}")
        code, out, _ = self.run_cli("update", "--latest")
        self.assertEqual(code, 0)
        self.assertIn("up to date", out)
        self.assertEqual(self.github.hits, ["/repo/releases/latest"])

    def test_check_reports_a_newer_release_and_installs_nothing(self):
        self.github.publish("v9.9.9", source=source_zip())
        code, out, _ = self.run_cli("update", "--check")
        self.assertEqual(code, 0)
        self.assertIn(f"{p.VERSION} -> v9.9.9", out)
        self.assertEqual(self.github.hits, ["/repo/releases/latest"])
        self.assertEqual(self.installed_version(), p.VERSION)

    def test_a_dev_build_ahead_of_the_release_is_left_alone(self):
        self.github.publish("v0.0.1")
        code, out, _ = self.run_cli("update", "--latest")
        self.assertEqual(code, 0)
        self.assertIn("newer than the latest release", out)
        self.assertEqual(self.github.hits, ["/repo/releases/latest"])

    def test_http_errors_become_clean_messages(self):
        self.github.status = 404
        code, _, err = self.run_cli("update", "--check")
        self.assertEqual(code, 1)
        self.assertIn("No published release", err)
        self.github.status = 403
        code, _, err = self.run_cli("update", "--check")
        self.assertEqual(code, 1)
        self.assertIn("rate limit", err)

    def test_unreachable_github_is_a_clean_message(self):
        self.github.close()
        code, _, err = self.run_cli("update", "--check")
        self.assertEqual(code, 1)
        self.assertIn("Could not reach GitHub", err)

    def test_non_numeric_tag_is_rejected(self):
        self.github.publish("nightly")
        code, _, err = self.run_cli("update", "--check")
        self.assertEqual(code, 1)
        self.assertIn("not a version number", err)


class InstallTests(UpdaterCase):
    def test_source_update_replaces_files_and_keeps_everything_else(self):
        self.github.publish("v9.9.9", source=source_zip())
        code, out, err = self.run_cli("update", "--latest")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(self.installed_version(), NEW_VERSION)
        self.assertTrue((self.app / "newmod.py").is_file(), "files listed in the release's APP_FILES must be installed")
        self.assertFalse((self.app / "setup_installer.py").exists())
        self.assertEqual((self.app / "qrcode" / "__init__.py").read_text(), "VENDORED = True\n")
        self.assertIn("OLD-LAUNCHER", (self.root / "edr").read_text())
        self.assertEqual(self.leftovers(), [])
        self.assertIn(f"Updated EDR {p.VERSION} -> {NEW_VERSION}", out)

    def test_update_without_the_latest_flag_does_the_same(self):
        self.github.publish("v9.9.9", source=source_zip())
        self.assertEqual(self.run_cli("update")[0], 0)
        self.assertEqual(self.installed_version(), NEW_VERSION)

    def test_windows_update_swaps_the_launcher_and_normalises_backslash_paths(self):
        archive = windows_zip()
        self.github.publish("v9.9.9", windows=archive, digest="sha256:" + hashlib.sha256(archive).hexdigest())
        with mock.patch.object(u, "_is_windows", return_value=True):
            code, out, err = self.run_cli("update", "--latest")
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(self.installed_version(), NEW_VERSION)
        self.assertEqual((self.root / "edr.exe").read_bytes(), b"NEW-LAUNCHER")
        self.assertEqual(self.leftovers(), [])

    def test_force_reinstalls_the_same_version(self):
        self.github.publish(f"v{p.VERSION}", source=source_zip(version=p.VERSION))
        code, out, _ = self.run_cli("update", "--latest", "--force")
        self.assertEqual(code, 0, out)
        self.assertIn("Updated EDR", out)

    def test_checksum_mismatch_installs_nothing(self):
        self.github.publish("v9.9.9", windows=windows_zip(), digest="sha256:" + "0" * 64)
        with mock.patch.object(u, "_is_windows", return_value=True):
            code, _, err = self.run_cli("update", "--latest")
        self.assertEqual(code, 1)
        self.assertIn("checksum", err)
        self.assertEqual(self.installed_version(), p.VERSION)
        self.assertFalse((self.root / "edr.exe").exists())

    def test_a_release_that_fails_its_self_test_installs_nothing(self):
        broken = {"command.py": b"import sys\nsys.exit(3)\n"}
        self.github.publish("v9.9.9", source=source_zip(overrides=broken))
        code, _, err = self.run_cli("update", "--latest")
        self.assertEqual(code, 1)
        self.assertIn("self-test", err)
        self.assertEqual(self.installed_version(), p.VERSION)
        self.assertFalse((self.app / "newmod.py").exists())

    def test_a_tag_whose_contents_are_not_newer_installs_nothing(self):
        self.github.publish("v9.9.9", source=source_zip(version=p.VERSION))
        code, _, err = self.run_cli("update", "--latest")
        self.assertEqual(code, 1)
        self.assertIn("not newer", err)
        self.assertEqual(self.installed_version(), p.VERSION)

    def test_path_traversal_in_the_archive_is_rejected(self):
        evil = source_zip(extra={"enderairstudio-Edr-abc1234/../../evil.py": b"print('pwned')\n"})
        self.github.publish("v9.9.9", source=evil)
        code, _, err = self.run_cli("update", "--latest")
        self.assertEqual(code, 1)
        self.assertIn("unsafe path", err)
        self.assertFalse((self.root.parent / "evil.py").exists())
        self.assertEqual(self.installed_version(), p.VERSION)

    def test_garbage_download_is_a_clean_error(self):
        self.github.publish("v9.9.9", source=b"this is not a zip")
        code, _, err = self.run_cli("update", "--latest")
        self.assertEqual(code, 1)
        self.assertIn("not a valid zip", err)

    def test_missing_windows_asset_is_a_clean_error(self):
        self.github.publish("v9.9.9", source=source_zip())
        with mock.patch.object(u, "_is_windows", return_value=True):
            code, _, err = self.run_cli("update", "--latest")
        self.assertEqual(code, 1)
        self.assertIn(u.WINDOWS_ASSET, err)

    def test_temp_files_are_cleaned_up(self):
        before = {path.name for path in Path(tempfile.gettempdir()).glob("edr-update-*")}
        self.github.publish("v9.9.9", source=source_zip())
        self.run_cli("update", "--latest")
        after = {path.name for path in Path(tempfile.gettempdir()).glob("edr-update-*")}
        self.assertEqual(after - before, set())


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_failure_midway_restores_every_file(self):
        stage, root = self.tmp / "stage", self.tmp / "root"
        (stage / "app").mkdir(parents=True)
        (root / "app").mkdir(parents=True)
        (stage / "app" / "a.py").write_text("new a")
        (stage / "app" / "b.py").write_text("new b")
        (stage / "app" / "c.py").write_text("new c")
        (root / "app" / "a.py").write_text("old a")
        (root / "app" / "c.py").mkdir()  # c.py cannot be replaced: it is a folder
        with self.assertRaises(e.CliError):
            u.apply_files(stage, root)
        self.assertEqual((root / "app" / "a.py").read_text(), "old a")
        self.assertFalse((root / "app" / "b.py").exists())
        self.assertTrue((root / "app" / "c.py").is_dir())
        self.assertEqual([path.name for path in root.rglob("*") if path.name.endswith((u.BACKUP_SUFFIX, u.NEW_SUFFIX))], [])

    def test_a_replaced_file_keeps_its_permissions(self):
        stage, root = self.tmp / "stage", self.tmp / "root"
        stage.mkdir()
        root.mkdir()
        (stage / "tool").write_text("new")
        (root / "tool").write_text("old")
        (root / "tool").chmod(0o755)
        u.apply_files(stage, root)
        self.assertEqual((root / "tool").read_text(), "new")
        if os.name != "nt":
            self.assertTrue(os.access(root / "tool", os.X_OK))

    def test_stale_backups_are_cleaned_before_a_run(self):
        root = self.tmp / "root"
        (root / "app").mkdir(parents=True)
        (root / f"edr.exe{u.BACKUP_SUFFIX}").write_text("stale")
        (root / "app" / f"print.py{u.BACKUP_SUFFIX}").write_text("stale")
        (root / "app" / "print.py").write_text("keep")
        u.clean_stale_backups(u.Install(root, root / "app"))
        self.assertEqual(sorted(path.name for path in root.rglob("*") if path.is_file()), ["print.py"])


class InstallLocationTests(unittest.TestCase):
    def test_a_source_checkout_is_not_updatable(self):
        with self.assertRaises(e.CliError) as ctx:
            u.locate_install()  # the repo folder is not called "app"
        self.assertIn("git pull", str(ctx.exception))


class HelpTests(unittest.TestCase):
    def render(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            p.help_menu()
        return out.getvalue()

    def test_help_lists_update_and_stays_compact_and_ascii(self):
        text = self.render()
        self.assertIn("update --latest", text)
        self.assertIn("update --check", text)
        text.encode("ascii")
        self.assertLessEqual(max(len(line) for line in text.splitlines()), 78)

    def test_every_command_in_the_help_exists_in_the_parser(self):
        parser = h.build_parser()
        argv = {
            "relay": ["relay", "start"],
            "rm": ["rm", "share", "--id", "x"],
            "set-dir": ["set-dir", "x", "."],
        }
        for command in ("start", "push", "share", "pull", "relay", "create", "list", "edit", "rm", "dir",
                        "set-dir", "status", "scan", "pack", "ip", "doctor", "update", "uninstall", "version"):
            parser.parse_args(argv.get(command, [command]))


if __name__ == "__main__":
    unittest.main()
