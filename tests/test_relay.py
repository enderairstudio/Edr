"""Tests for the EDR relay server: disk-backed rooms, hardening limits,
and room lifecycle (wait/request/upload/download/delete).

Run with: python -m unittest tests.test_relay -v
"""

import os
import socket
import sys
import time
import unittest
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import relay as r  # noqa: E402


def _free_port():
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class RelayRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.port = _free_port()
        self.server, self.base = r.start_relay_server(host="127.0.0.1", port=self.port)
        self.client = r.RelayClient(self.base)

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()

    def _wait_for(self, predicate, timeout=3.0):
        """The relay flips a room to "consumed" a few ms after the last byte
        left the socket, so state checks right after a download must poll."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def test_small_payload_roundtrip(self):
        room = r.generate_relay_id()
        payload = b"hello edr relay"
        self.client.upload(room, payload)
        status = self.client.room_status(room)
        self.assertTrue(status["ready"])
        self.assertEqual(status["bytes"], len(payload))
        self.assertEqual(self.client.download(room), payload)
        # Room is consumed on download.
        self.assertTrue(self._wait_for(lambda: not self.client.room_status(room)["ready"]))

    def test_multi_chunk_payload_roundtrip(self):
        room = r.generate_relay_id()
        payload = os.urandom(3 * r.CHUNK_SIZE + 777)  # forces several PUTs
        self.client.upload(room, payload)
        self.assertEqual(self.client.download(room), payload)

    def test_empty_payload_roundtrip(self):
        room = r.generate_relay_id()
        self.client.upload(room, b"")
        self.assertEqual(self.client.download(room), b"")

    def test_reset_room_on_resend(self):
        room = r.generate_relay_id()
        self.client.upload(room, b"first version")
        # Re-uploading (offset 0 again) should fully replace, not append.
        self.client.upload(room, b"second version, longer than first")
        self.assertEqual(self.client.download(room), b"second version, longer than first")

    def test_wait_request_lifecycle(self):
        room = r.generate_relay_id()
        self.client.register_waiting(room)
        status = self.client.room_status(room)
        self.assertTrue(status["waiting"])
        self.assertFalse(status["requested"])
        self.client.request_pull(room)
        status = self.client.room_status(room)
        self.assertTrue(status["requested"])

    def test_delete_room(self):
        room = r.generate_relay_id()
        self.client.upload(room, b"data to delete")
        self.client.delete_room(room)
        status = self.client.room_status(room)
        self.assertFalse(status["ready"])

    def test_spool_file_removed_after_consume(self):
        room = r.generate_relay_id()
        self.client.upload(room, os.urandom(1024))
        spool_path = r._STORE._spool_dir / f"{room}.part"
        self.assertTrue(spool_path.exists())
        self.client.download(room)
        self.assertTrue(self._wait_for(lambda: not spool_path.exists()))

    def test_spool_file_removed_after_delete(self):
        room = r.generate_relay_id()
        self.client.upload(room, os.urandom(1024))
        spool_path = r._STORE._spool_dir / f"{room}.part"
        self.assertTrue(spool_path.exists())
        self.client.delete_room(room)
        self.assertFalse(spool_path.exists())


class RelayHardeningTests(unittest.TestCase):
    def setUp(self):
        self.port = _free_port()
        self.server, self.base = r.start_relay_server(host="127.0.0.1", port=self.port)
        self.client = r.RelayClient(self.base)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_room_id_validator(self):
        self.assertTrue(r.is_valid_room_id("abc123"))
        self.assertFalse(r.is_valid_room_id(""))
        self.assertFalse(r.is_valid_room_id("ab"))  # too short
        self.assertFalse(r.is_valid_room_id("has spaces"))
        self.assertFalse(r.is_valid_room_id("UPPERCASE"))
        self.assertFalse(r.is_valid_room_id("../../etc/passwd"))
        self.assertFalse(r.is_valid_room_id("a" * 65))  # too long

    def test_rejects_invalid_room_id_over_http(self):
        import urllib.request
        import urllib.error

        # "a.b" survives as the final path segment but fails the room-id
        # pattern (dots aren't allowed), so this exercises the server-side
        # validator rather than relying on path-splitting to strip "..".
        req = urllib.request.Request(
            f"{self.base}/v1/rooms/a.b",
            method="PUT",
            data=b"x",
            headers={"Content-Length": "1"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)

    def test_oversized_chunk_rejected(self):
        with self.assertRaises(r.RoomRejected):
            r._STORE.put_chunk("validroomid", 0, r.MAX_CHUNK_BYTES + 1, b"x" * (r.MAX_CHUNK_BYTES + 1))

    def test_mismatched_total_rejected(self):
        room = r.generate_relay_id()
        r._STORE.reset_room(room)
        r._STORE.put_chunk(room, 0, 100, b"a" * 50)
        with self.assertRaises(r.RoomRejected):
            r._STORE.put_chunk(room, 50, 999, b"b" * 50)

    def test_idle_sweep_removes_stale_room(self):
        room = r.generate_relay_id()
        self.client.upload(room, b"will go stale")
        spool_path = r._STORE._spool_dir / f"{room}.part"
        # ready rooms are consumed on download, so simulate an abandoned
        # in-progress upload instead.
        room2 = r.generate_relay_id()
        r._STORE.reset_room(room2)
        r._STORE.put_chunk(room2, 0, 1000, b"a" * 10)  # incomplete, stays pending
        r._STORE._rooms[room2]["touched"] = time.time() - 999999
        stale = r._STORE.sweep_idle(idle_seconds=10)
        self.assertIn(room2, stale)
        self.assertFalse(r._STORE.status(room2)["waiting"])


if __name__ == "__main__":
    unittest.main()
