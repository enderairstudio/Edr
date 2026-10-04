"""Tests for the CLI layer: config store, validation, error handling.

Run with: python -m unittest tests.test_handler -v
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import error as e  # noqa: E402
import handler as h  # noqa: E402


class IsolatedHome(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir()
        patcher = mock.patch.object(Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = h.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class StoreTests(IsolatedHome):
    def test_save_is_atomic_and_survives_a_failed_write(self):
        h.save_store({"a": {"id": "a"}})
        original = h.store_path().read_text()
        real_replace = os.replace
        with mock.patch.object(os, "replace", side_effect=KeyboardInterrupt), self.assertRaises(KeyboardInterrupt):
            h.save_store({"b": {"id": "b"}})
        self.assertEqual(h.store_path().read_text(), original)
        self.assertEqual(h.load_store(), {"a": {"id": "a"}})
        os.replace = real_replace

    def test_config_is_not_imported_from_an_untrusted_working_folder(self):
        evil = Path(self._tmp.name) / "received-project"
        (evil / ".edr").mkdir(parents=True)
        (evil / ".edr" / "sharers.json").write_text(json.dumps({"x": {"id": "x", "path": "/etc", "skip_guard": True}}))
        cwd = os.getcwd()
        os.chdir(evil)
        try:
            self.assertEqual(h.load_store(), {})
        finally:
            os.chdir(cwd)
        self.assertFalse(h.store_path().exists(), "nothing may be copied into the home config")


class CreateValidationTests(IsolatedHome):
    def make_project(self):
        folder = Path(self._tmp.name) / "proj"
        folder.mkdir()
        (folder / "a.txt").write_text("a")
        return folder

    def test_relay_id_must_be_a_valid_room_id(self):
        folder = self.make_project()
        for bad in ("MyProject", "ab", "with space", "a/b", "x" * 80):
            code, _, err = self.run_cli("create", "sharer", str(folder), "--non-network", "--id", bad, "--name", "n")
            self.assertEqual(code, 1, bad)
            self.assertIn("Invalid relay id", err)
        self.assertEqual(h.load_store(), {})

    def test_valid_and_prefixed_relay_ids_are_accepted(self):
        folder = self.make_project()
        code, _, _ = self.run_cli("create", "sharer", str(folder), "--non-network", "--id", "Edrnko_myproject1", "--name", "n")
        self.assertEqual(code, 0)
        self.assertIn("myproject1", h.load_store())

    def test_create_does_not_stall_without_a_terminal(self):
        folder = self.make_project()
        with mock.patch.object(sys, "stdin", io.StringIO("")), mock.patch("time.sleep", side_effect=AssertionError("slept")):
            code, _, _ = self.run_cli("create", "sharer", str(folder), "--id", "plainlan")
        self.assertEqual(code, 0)


class ErrorHandlingTests(IsolatedHome):
    def test_runtime_errors_become_one_line_messages(self):
        with mock.patch.object(h.s, "get_local_ip", side_effect=RuntimeError("Cannot reach relay at http://x")):
            code, _, err = self.run_cli("ip")
        self.assertEqual(code, 1)
        self.assertIn("Cannot reach relay", err)
        self.assertNotIn("Traceback", err)

    def test_edr_debug_restores_the_traceback(self):
        with mock.patch.object(h.s, "get_local_ip", side_effect=RuntimeError("boom")), \
                mock.patch.dict(os.environ, {"EDR_DEBUG": "1"}):
            with self.assertRaises(RuntimeError):
                self.run_cli("ip")


class RelayStartTests(IsolatedHome):
    def test_busy_port_is_a_friendly_error(self):
        import socket

        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            port = busy.getsockname()[1]
            code, _, err = self.run_cli("relay", "start", "--engine", "python", "--host", "127.0.0.1", "--port", str(port))
        self.assertEqual(code, 1)
        self.assertIn("Cannot start the relay", err)

    def test_engine_rust_without_binary_explains_how_to_build_it(self):
        with mock.patch.object(h, "find_rust_relay", return_value=None):
            code, _, err = self.run_cli("relay", "start", "--engine", "rust")
        self.assertEqual(code, 1)
        self.assertIn("cargo build --release", err)

    def test_auto_engine_uses_the_rust_binary_when_present(self):
        with mock.patch.object(h, "find_rust_relay", return_value="/opt/edr-relay"), \
                mock.patch.object(h.subprocess, "call", return_value=0) as call:
            code, _, _ = self.run_cli("relay", "start", "--port", "9999")
        self.assertEqual(code, 0)
        self.assertEqual(call.call_args[0][0], ["/opt/edr-relay", "--host", "0.0.0.0", "--port", "9999"])

    def test_python_engine_ignores_an_installed_binary(self):
        with mock.patch.object(h, "find_rust_relay", return_value="/opt/edr-relay"), \
                mock.patch.object(h.subprocess, "call") as call, \
                mock.patch.object(h.r, "start_relay_server", side_effect=OSError("stop here")):
            code, _, _ = self.run_cli("relay", "start", "--engine", "python")
        self.assertEqual(code, 1)
        call.assert_not_called()

    def test_find_rust_relay_honours_env_var(self):
        with tempfile.NamedTemporaryFile() as fake:
            with mock.patch.dict(os.environ, {"EDR_RELAY_BIN": fake.name}):
                self.assertEqual(h.find_rust_relay(), fake.name)


class PullCommandTests(unittest.TestCase):
    def test_custom_relay_url_is_part_of_the_pull_command(self):
        import qrterm as q

        self.assertEqual(q.pull_command_text("Edrnko_abc123"), "edr pull Edrnko_abc123")
        self.assertEqual(q.pull_command_text("Edrnko_abc123", relay_url="http://127.0.0.1:8765/"), "edr pull Edrnko_abc123")
        self.assertEqual(
            q.pull_command_text("Edrnko_abc123", relay_url="https://relay.example.com/"),
            "edr pull Edrnko_abc123 --relay-url https://relay.example.com",
        )


if __name__ == "__main__":
    unittest.main()
