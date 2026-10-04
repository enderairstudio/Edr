"""Tests for project selection, bundling, atomic extraction and transfers.

Run with: python -m unittest tests.test_share -v
"""

import contextlib
import io
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import error as e  # noqa: E402
import relay as r  # noqa: E402
import share as s  # noqa: E402
from test_relay_protocol import find_rust_relay  # noqa: E402


def _free_port():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def quiet():
    """Silence EDR's progress output (stdout) and error output (stderr)."""
    stack = contextlib.ExitStack()
    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
    return stack


def make_tree(root, files):
    for rel, data in files.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class ProjectSelectionTests(TempDirCase):
    def names(self, root, **kwargs):
        return sorted(rel.as_posix() for _, rel in s.iter_project_files(root, **kwargs))

    def test_normal_projects_keep_files_named_like_edr_modules(self):
        files = ["app.py", "handler.py", "error.py", "print.py", "src/handler.py", "utils/error.py", "src/command.py"]
        make_tree(self.tmp, {name: "x" for name in files})
        self.assertEqual(self.names(self.tmp), sorted(files))

    def test_edr_cli_folder_still_hides_its_own_modules_unless_asked(self):
        cli = {name: "x" for name in s.CLI_FILES}
        make_tree(self.tmp, {**cli, "README.md": "doc", "docs/handler.py": "user file at depth"})
        self.assertEqual(self.names(self.tmp), ["README.md", "docs/handler.py"])
        self.assertIn("handler.py", self.names(self.tmp, include_cli=True))

    def test_python_and_launcher_dirs_are_only_ignored_at_the_root(self):
        make_tree(self.tmp, {
            "python/embedded.txt": "x", "launcher/a.cs": "x",
            "bindings/python/x.py": "x", "src/launcher/a.py": "x",
        })
        self.assertEqual(self.names(self.tmp), ["bindings/python/x.py", "src/launcher/a.py"])

    def test_generic_ignored_dirs_apply_at_any_depth(self):
        make_tree(self.tmp, {"keep.txt": "x", "a/node_modules/m.js": "x", "a/__pycache__/m.pyc": "x", ".git/config": "x", "project_payload.zip": "x"})
        self.assertEqual(self.names(self.tmp), ["keep.txt"])

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_symlinks_that_leave_the_project_are_not_followed(self):
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "id_rsa"
            secret.write_text("TOP SECRET")
            make_tree(self.tmp, {"ok.txt": "ok", "inside.txt": "inside"})
            try:
                os.symlink(secret, self.tmp / "innocent.txt")
                os.symlink(self.tmp / "inside.txt", self.tmp / "alias.txt")
            except OSError:
                self.skipTest("symlink creation not permitted")
            names = self.names(self.tmp)
            self.assertNotIn("innocent.txt", names)
            self.assertIn("alias.txt", names)  # links that stay inside are fine

    def test_status_summary_lists_root_only_dirs_too(self):
        summary = s.project_summary(self.tmp)
        self.assertIn("launcher", summary["ignored_dirs"])
        self.assertIn("node_modules", summary["ignored_dirs"])


class BundlingTests(TempDirCase):
    def test_file_with_pre_1980_mtime_is_bundled_not_fatal(self):
        make_tree(self.tmp, {"old.txt": "hi"})
        os.utime(self.tmp / "old.txt", (0, 0))
        with quiet():
            data = s.bundle_to_memory(self.tmp, skip_guard=True)
        self.assertEqual(zipfile.ZipFile(io.BytesIO(data)).read("old.txt"), b"hi")

    def test_fast_mode_stores_without_compression(self):
        make_tree(self.tmp, {"a.txt": "a" * 5000})
        with quiet():
            fast = zipfile.ZipFile(io.BytesIO(s.bundle_to_memory(self.tmp, skip_guard=True, fast=True)))
            normal = zipfile.ZipFile(io.BytesIO(s.bundle_to_memory(self.tmp, skip_guard=True)))
        self.assertEqual(fast.infolist()[0].compress_type, zipfile.ZIP_STORED)
        self.assertEqual(normal.infolist()[0].compress_type, zipfile.ZIP_DEFLATED)

    def test_file_deleted_mid_bundle_is_skipped(self):
        make_tree(self.tmp, {"a.txt": "a", "gone.txt": "g", "z.txt": "z"})
        real_write = zipfile.ZipFile.write

        def flaky(self_zip, filename, arcname=None, *args, **kwargs):
            if str(filename).endswith("gone.txt"):
                raise FileNotFoundError(filename)
            return real_write(self_zip, filename, arcname, *args, **kwargs)

        with quiet(), mock.patch.object(zipfile.ZipFile, "write", flaky):
            data = s.bundle_to_memory(self.tmp, skip_guard=True)
        self.assertEqual(sorted(zipfile.ZipFile(io.BytesIO(data)).namelist()), ["a.txt", "z.txt"])

    def test_pack_streams_to_disk_and_leaves_no_partial_file(self):
        make_tree(self.tmp, {"a.txt": "a"})
        out = self.tmp / "out" / "bundle.zip"
        out.parent.mkdir()
        with quiet():
            target, size = s.bundle_to_file(out, self.tmp, skip_guard=True)
        self.assertEqual(size, target.stat().st_size)
        self.assertFalse(out.with_name("bundle.zip.part").exists())
        with quiet(), self.assertRaises(SystemExit):
            s.bundle_to_file(out, self.tmp, skip_guard=True)  # exists, no --force


class SafeExtractTests(TempDirCase):
    def zip_bytes(self, members):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        return buffer.getvalue()

    def extract(self, data, dest, force=False):
        with quiet():
            return s.safe_extract(io.BytesIO(data), dest, force)

    def corrupt_member(self, data, marker):
        raw = bytearray(data)
        raw[raw.find(marker)] ^= 0xFF  # CRC error when that member is read
        return bytes(raw)

    def test_happy_path_extracts_files_and_empty_dirs(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("a.txt", "A")
            archive.writestr("sub/b.txt", "B")
            archive.writestr("emptydir/", "")
        dest = self.tmp / "dest"
        self.assertEqual(self.extract(buffer.getvalue(), dest), 2)
        self.assertEqual(snapshot(dest), {"a.txt": b"A", "sub/b.txt": b"B"})
        self.assertTrue((dest / "emptydir").is_dir())
        self.assertEqual([p.name for p in dest.iterdir() if p.name.startswith(".edr-staging")], [])

    def test_path_traversal_is_blocked_and_nothing_is_written(self):
        for name in ("../evil.txt", "a/../../evil.txt", "/abs/evil.txt"):
            dest = self.tmp / "dest"
            with self.assertRaises(SystemExit, msg=name):
                self.extract(self.zip_bytes({"ok.txt": "ok", name: "x"}), dest)
            self.assertFalse(dest.exists(), f"{name}: destination should not even be created")
        self.assertFalse((self.tmp / "evil.txt").exists())

    def test_existing_file_without_force_aborts_before_writing_anything(self):
        dest = self.tmp / "dest"
        make_tree(dest, {"a.txt": "ORIGINAL"})
        with self.assertRaises(SystemExit):
            self.extract(self.zip_bytes({"new.txt": "N", "a.txt": "X"}), dest)
        self.assertEqual(snapshot(dest), {"a.txt": b"ORIGINAL"})

    def test_force_overwrite_failure_restores_the_original_file(self):
        dest = self.tmp / "dest"
        make_tree(dest, {"a.txt": "ORIGINAL IMPORTANT DATA", "keep.txt": "keep"})
        data = self.corrupt_member(self.zip_bytes({"a.txt": "new content", "b.txt": "second file"}), b"second file")
        with self.assertRaises(Exception):
            self.extract(data, dest, force=True)
        self.assertEqual(snapshot(dest), {"a.txt": b"ORIGINAL IMPORTANT DATA", "keep.txt": b"keep"})

    def test_failure_into_a_new_folder_removes_the_folder(self):
        dest = self.tmp / "fresh"
        data = self.corrupt_member(self.zip_bytes({"a.txt": "aaa", "b.txt": "second file"}), b"second file")
        with self.assertRaises(Exception):
            self.extract(data, dest)
        self.assertFalse(dest.exists())

    def test_interrupt_during_the_move_phase_rolls_everything_back(self):
        dest = self.tmp / "dest"
        make_tree(dest, {"a.txt": "ORIGINAL"})
        real_replace = os.replace
        calls = {"n": 0}

        def interrupting(src, dst):
            # Count only the staging -> destination moves (their source lives in
            # the staging folder); compare path *parts*, not string prefixes,
            # because Windows temp dirs can have 8.3 short-name aliases.
            if ".edr-staging" in str(src) and "new" in Path(src).parts:
                calls["n"] += 1
                if calls["n"] == 3:  # a few files already moved
                    raise KeyboardInterrupt
            return real_replace(src, dst)

        data = self.zip_bytes({"a.txt": "NEW", "b.txt": "B", "sub/c.txt": "C", "d.txt": "D"})
        with mock.patch.object(os, "replace", interrupting), self.assertRaises(KeyboardInterrupt):
            self.extract(data, dest, force=True)
        self.assertEqual(snapshot(dest), {"a.txt": b"ORIGINAL"})
        self.assertFalse((dest / "sub").exists())

    def test_folder_vs_file_conflicts_are_rejected(self):
        dest = self.tmp / "dest"
        (dest / "a.txt").mkdir(parents=True)
        (dest / "a.txt" / "keep").write_text("k")
        with self.assertRaises(SystemExit):
            self.extract(self.zip_bytes({"a.txt": "file"}), dest, force=True)
        self.assertTrue((dest / "a.txt" / "keep").exists())

    def test_zip_bomb_is_rejected_before_extracting(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("zeros.bin", bytes(12 * 1024 * 1024))  # ratio >> 300
        dest = self.tmp / "dest"
        with self.assertRaises(SystemExit):
            self.extract(buffer.getvalue(), dest)
        self.assertFalse(dest.exists())

    def test_not_enough_disk_space_fails_early(self):
        usage = shutil.disk_usage(self.tmp)._replace(free=10)
        dest = self.tmp / "dest"
        with mock.patch.object(shutil, "disk_usage", return_value=usage), self.assertRaises(SystemExit):
            self.extract(self.zip_bytes({"a.txt": "x" * 1000}), dest)
        self.assertFalse(dest.exists())


class PayloadParsingTests(unittest.TestCase):
    def receive(self, raw, **kwargs):
        with quiet():
            return s.receive_payload_to_tempfile(io.BytesIO(raw), **kwargs)

    def payload(self, manifest, zip_bytes=b"zipbytes"):
        return s._encode_header(manifest) + zip_bytes

    def test_roundtrip_stages_the_zip_part_only(self):
        manifest, path = self.receive(self.payload({"file_count": 1, "root_name": "x"}))
        try:
            self.assertEqual(manifest["root_name"], "x")
            self.assertEqual(path.read_bytes(), b"zipbytes")
        finally:
            path.unlink()

    def test_bad_magic_is_rejected(self):
        with self.assertRaises(e.CliError):
            self.receive(b"NOPE" + bytes(20))

    def test_oversized_header_is_rejected(self):
        with self.assertRaises(e.CliError):
            self.receive(s.PROTOCOL_MAGIC + struct.pack("!Q", 64 * 1024 * 1024))

    def test_truncated_header_is_a_connection_error(self):
        with self.assertRaises(ConnectionError):
            self.receive(s.PROTOCOL_MAGIC + struct.pack("!Q", 100) + b"{}")

    def test_missing_archive_data_is_detected(self):
        with self.assertRaises(ConnectionError):
            self.receive(self.payload({"file_count": 3}, b""))

    def test_download_size_cap_aborts_and_cleans_up(self):
        before = set(Path(tempfile.gettempdir()).glob("edr-pull-*"))
        with mock.patch.object(s, "MAX_EXTRACT_BYTES", 10), self.assertRaises(e.CliError):
            self.receive(self.payload({"file_count": 1}, b"x" * 100))
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("edr-pull-*")), before)


class SocketWriterTests(unittest.TestCase):
    def test_vanished_receiver_raises_transfer_aborted(self):
        left, right = socket.socketpair()
        right.close()
        writer = s._ProgressSocketWriter(left, 1000, buffer_size=16)
        with quiet(), self.assertRaises(s.TransferAborted):
            for _ in range(2000):  # enough to hit EPIPE / ECONNRESET
                writer.write(b"x" * 4096)
            writer.flush()
        left.close()

    def test_small_writes_are_batched_into_few_sends(self):
        # A recording stand-in for the socket: a real socketpair nobody reads
        # from would block in sendall() once the kernel buffer fills (only ~8 KiB
        # on macOS).
        sends = []
        sink = mock.Mock(sendall=lambda data: sends.append(len(data)))
        writer = s._ProgressSocketWriter(sink, 100_000, buffer_size=10_000)
        with quiet():
            for _ in range(50):
                writer.write(b"y" * 1000)
            writer.flush()
        self.assertLessEqual(len(sends), 6)
        self.assertEqual(sum(sends), 50_000)


class EndToEndTransferTests(TempDirCase):
    """Real sender -> receiver transfers over loopback."""

    def project(self):
        root = self.tmp / "proj"
        make_tree(root, {
            "README.md": "# demo",
            "src/main.js": "console.log('hi')",
            "src/handler.py": "def handler(): pass",
            "docs/blob.bin": os.urandom(2 * 1024 * 1024),
            "empty.txt": b"",
        })
        return root

    def serve_lan(self, root, forever=False, fast=False):
        port = _free_port()
        thread = threading.Thread(
            target=lambda: s._serve_via_tcp(root, port, False, "t", forever, skip_guard=True, fast=fast),
            daemon=True,
        )
        with quiet():
            thread.start()
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                with closing(socket.create_connection(("127.0.0.1", port), timeout=0.2)):
                    pass
            except OSError:
                time.sleep(0.05)
                continue
            break
        return port, thread

    def test_lan_roundtrip_is_byte_identical(self):
        root = self.project()
        # The probe connection in serve_lan consumed the first accept in
        # non-forever mode, so use forever=True and many pulls.
        port, _ = self.serve_lan(root, forever=True)
        dest = self.tmp / "copy"
        with quiet():
            extracted, folder = s.receive_project("127.0.0.1", port=port, target_dir=dest)
        self.assertEqual(Path(folder), dest.resolve())
        self.assertEqual(extracted, 5)
        self.assertEqual(snapshot(dest), snapshot(root))

    def test_lan_fast_mode_roundtrip(self):
        root = self.project()
        port, _ = self.serve_lan(root, forever=True, fast=True)
        dest = self.tmp / "copy"
        with quiet():
            s.receive_project("127.0.0.1", port=port, target_dir=dest)
        self.assertEqual(snapshot(dest), snapshot(root))

    @unittest.skipIf(sys.platform == "win32", "SO_LINGER layout differs on Windows")
    def test_auto_server_survives_a_receiver_that_aborts(self):
        root = self.project()
        port, thread = self.serve_lan(root, forever=True)
        sock = socket.create_connection(("127.0.0.1", port))
        sock.recv(100)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        sock.close()  # RST, like Ctrl+C on the receiver
        time.sleep(0.5)
        self.assertTrue(thread.is_alive(), "sender died because one receiver aborted")
        dest = self.tmp / "copy"
        with quiet():
            s.receive_project("127.0.0.1", port=port, target_dir=dest)
        self.assertEqual(snapshot(dest), snapshot(root))

    def test_guard_blocks_a_dangerous_sender_payload_on_the_receiver(self):
        root = self.tmp / "bad"
        make_tree(root, {"ok.txt": "ok", "payload.exe": "MZ not really"})
        port = _free_port()
        threading.Thread(
            target=lambda: s._serve_via_tcp(root, port, False, "t", True, skip_guard=True), daemon=True
        ).start()
        time.sleep(0.3)
        dest = self.tmp / "copy"
        with quiet(), self.assertRaises(e.CliError):
            s.receive_project("127.0.0.1", port=port, target_dir=dest)
        self.assertFalse(dest.exists())

    def relay_roundtrip(self, relay_url):
        root = self.project()
        relay_id = r.generate_relay_id()
        errors = []

        def sender():
            try:
                with quiet():
                    s._serve_via_relay(root, False, "t", relay_id, False, relay_url, skip_guard=True)
            except BaseException as err:  # surfaced in the main thread
                errors.append(err)

        thread = threading.Thread(target=sender, daemon=True)
        thread.start()
        time.sleep(0.5)
        dest = self.tmp / "copy"
        with quiet():
            s.receive_project(r.relay_code(relay_id), target_dir=dest, relay_url=relay_url)
        thread.join(timeout=20)
        self.assertFalse(thread.is_alive(), "sender never saw the transfer finish")
        self.assertEqual(errors, [])
        self.assertEqual(snapshot(dest), snapshot(root))
        leftovers = [p for p in Path(tempfile.gettempdir()).glob("edr-push-*")]
        self.assertEqual(leftovers, [], "sender temp zip was not cleaned up")

    def test_relay_roundtrip_python_relay(self):
        port = _free_port()
        server, base = r.start_relay_server(host="127.0.0.1", port=port)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.relay_roundtrip(base)

    @unittest.skipUnless(find_rust_relay(), "Rust relay binary not built")
    def test_relay_roundtrip_rust_relay(self):
        import subprocess

        port = _free_port()
        proc = subprocess.Popen([find_rust_relay(), "--host", "127.0.0.1", "--port", str(port)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait)
        self.addCleanup(proc.terminate)
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with closing(socket.create_connection(("127.0.0.1", port), timeout=0.2)):
                    break
            except OSError:
                time.sleep(0.1)
        self.relay_roundtrip(f"http://127.0.0.1:{port}")


if __name__ == "__main__":
    unittest.main()
