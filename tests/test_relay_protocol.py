"""Protocol conformance tests, run against BOTH relay implementations:

  * the Python relay (relay.py), in-process
  * the Rust relay (relay-rs), if the binary is built (skipped otherwise):
        cargo build --release --manifest-path relay-rs/Cargo.toml
    or point EDR_RELAY_BIN at a binary.

Run with: python -m unittest tests.test_relay_protocol -v
"""

import http.client
import json
import os
import socket
import subprocess
import sys
import time
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import relay as r  # noqa: E402

CHUNK_CAP = 1024 * 1024  # small enough to exercise the 413 paths cheaply


def _free_port():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def find_rust_relay():
    explicit = os.environ.get("EDR_RELAY_BIN")
    candidates = [explicit] if explicit else []
    name = "edr-relay.exe" if sys.platform == "win32" else "edr-relay"
    candidates.append(str(ROOT / "relay-rs" / "target" / "release" / name))
    candidates.append(str(ROOT / "relay-rs" / "target" / "debug" / name))
    return next((c for c in candidates if c and Path(c).is_file()), None)


class ProtocolSuite:
    """Mixin: subclasses provide self.port."""

    def http(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def put(self, room, offset, total, data):
        return self.http("PUT", f"/v1/rooms/{room}", data, {"X-EDR-Offset": str(offset), "X-EDR-Total": str(total)})

    def status(self, room):
        code, body = self.http("GET", f"/v1/rooms/{room}/status")
        self.assertEqual(code, 200)
        return json.loads(body)

    def wait_for(self, predicate, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def client(self):
        c = r.RelayClient(f"http://127.0.0.1:{self.port}")
        self.addCleanup(c.close)
        return c

    # -- basics ------------------------------------------------------------
    def test_health(self):
        code, body = self.http("GET", "/v1/health")
        self.assertEqual(code, 200)
        info = json.loads(body)
        self.assertIs(info["ok"], True)
        self.assertEqual(info["max_chunk_bytes"], CHUNK_CAP)

    def test_unknown_route_is_404(self):
        self.assertEqual(self.http("GET", "/nope")[0], 404)
        self.assertEqual(self.http("GET", "/v1/rooms/abcd1234/bogus")[0], 404)

    def test_invalid_room_ids_are_400_on_every_verb(self):
        for method, suffix in [("GET", ""), ("GET", "/status"), ("PUT", ""), ("DELETE", ""), ("POST", "/wait"), ("POST", "/request")]:
            code, _ = self.http(method, f"/v1/rooms/BAD_ID{suffix}", b"" if method in {"PUT", "POST"} else None)
            self.assertEqual(code, 400, f"{method} {suffix}")

    def test_status_shape_and_lifecycle(self):
        room = r.generate_relay_id()
        st = self.status(room)
        self.assertEqual(set(st), {"ready", "requested", "waiting", "consumed", "bytes"})
        self.assertFalse(any([st["ready"], st["requested"], st["waiting"], st["consumed"]]))
        self.assertEqual(self.http("POST", f"/v1/rooms/{room}/wait", b"")[0], 204)
        self.assertTrue(self.status(room)["waiting"])
        self.assertEqual(self.http("POST", f"/v1/rooms/{room}/request", b"")[0], 204)
        self.assertTrue(self.status(room)["requested"])

    # -- transfer ----------------------------------------------------------
    def test_multi_chunk_roundtrip_with_python_client(self):
        room = r.generate_relay_id()
        payload = os.urandom(3 * 1024 * 1024 + 123)  # several PUTs at the 1 MiB cap
        client = self.client()
        client.upload(room, payload)
        self.assertTrue(self.status(room)["ready"])
        self.assertEqual(client.download(room), payload)

    def test_empty_payload(self):
        room = r.generate_relay_id()
        client = self.client()
        client.upload(room, b"")
        self.assertEqual(client.download(room), b"")

    def test_reupload_resets_the_room(self):
        room = r.generate_relay_id()
        client = self.client()
        client.upload(room, b"first version")
        client.upload(room, b"v2")
        self.assertEqual(client.download(room), b"v2")

    def test_download_before_ready_is_404(self):
        room = r.generate_relay_id()
        self.put(room, 0, 10, b"12345")
        self.assertEqual(self.http("GET", f"/v1/rooms/{room}")[0], 404)

    def test_sender_sees_success_even_if_receiver_finishes_first(self):
        room = r.generate_relay_id()
        client = self.client()
        client.upload(room, b"tiny project")
        self.assertEqual(client.download(room), b"tiny project")
        started = time.time()
        client.wait_until_consumed(room, timeout=5)  # must not wait for a timeout
        self.assertLess(time.time() - started, 3)
        self.assertTrue(self.status(room)["consumed"])
        self.assertFalse(self.status(room)["ready"])

    def test_aborted_download_keeps_the_room_for_a_retry(self):
        room = r.generate_relay_id()
        payload = os.urandom(6 * 1024 * 1024)
        client = self.client()
        client.upload(room, payload)
        with closing(socket.create_connection(("127.0.0.1", self.port))) as sock:
            sock.sendall(f"GET /v1/rooms/{room} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
            sock.recv(65536)  # a few KiB, then the receiver dies
        time.sleep(0.4)
        st = self.status(room)
        self.assertTrue(st["ready"], "payload must survive an aborted download")
        self.assertFalse(st["consumed"], "an aborted download is not a finished transfer")
        self.assertEqual(client.download(room), payload)

    def test_delete(self):
        room = r.generate_relay_id()
        client = self.client()
        client.upload(room, b"to delete")
        self.assertEqual(self.http("DELETE", f"/v1/rooms/{room}")[0], 204)
        st = self.status(room)
        self.assertFalse(st["ready"])
        self.assertEqual(self.http("GET", f"/v1/rooms/{room}")[0], 404)

    # -- hardening ---------------------------------------------------------
    def test_chunk_outside_declared_total_is_rejected(self):
        room = r.generate_relay_id()
        self.assertEqual(self.put(room, 3_000_000_000, 10, b"Z")[0], 400)
        self.assertEqual(self.put(room, 5, 10, b"123456789")[0], 400)

    def test_total_cannot_change_mid_upload(self):
        room = r.generate_relay_id()
        self.assertEqual(self.put(room, 0, 100, b"a" * 50)[0], 204)
        self.assertEqual(self.put(room, 50, 999, b"b" * 50)[0], 409)

    def test_oversized_chunk_is_rejected(self):
        # The relay answers 413 and hangs up without reading an oversized body
        # (draining attacker-sized bodies would defeat the limit). Depending on
        # timing the client either reads the 413 or hits a broken pipe first;
        # both mean "rejected".
        room = r.generate_relay_id()
        try:
            code, _ = self.put(room, 0, CHUNK_CAP + 1, b"x" * (CHUNK_CAP + 1))
            self.assertEqual(code, 413)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        self.assertFalse(self.status(room)["ready"])

    def test_malformed_headers_are_400(self):
        room = r.generate_relay_id()
        code, _ = self.http("PUT", f"/v1/rooms/{room}", b"x", {"X-EDR-Offset": "abc"})
        self.assertEqual(code, 400)
        code, _ = self.http("PUT", f"/v1/rooms/{room}", b"x", {"X-EDR-Offset": "-1", "X-EDR-Total": "5"})
        self.assertEqual(code, 400)

    def test_connection_is_kept_alive(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.addCleanup(conn.close)
        for _ in range(3):
            conn.request("GET", "/v1/health")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            resp.read()
        self.assertIsNotNone(conn.sock, "server closed a keep-alive connection")


class PythonRelayProtocolTests(ProtocolSuite, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        cls._old_cap = r.MAX_CHUNK_BYTES
        r.MAX_CHUNK_BYTES = CHUNK_CAP
        cls.server, _ = r.start_relay_server(host="127.0.0.1", port=cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        r.MAX_CHUNK_BYTES = cls._old_cap


@unittest.skipUnless(find_rust_relay(), "Rust relay binary not built (cargo build --release in relay-rs/)")
class RustRelayProtocolTests(ProtocolSuite, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = _free_port()
        env = dict(os.environ, EDR_RELAY_MAX_CHUNK_BYTES=str(CHUNK_CAP))
        cls.proc = subprocess.Popen(
            [find_rust_relay(), "--host", "127.0.0.1", "--port", str(cls.port)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with closing(socket.create_connection(("127.0.0.1", cls.port), timeout=0.5)):
                    return
            except OSError:
                time.sleep(0.1)
        cls.proc.kill()
        raise RuntimeError("edr-relay did not start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.proc.kill()


if __name__ == "__main__":
    unittest.main()
