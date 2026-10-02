import os
import tempfile
import unittest
from pathlib import Path

import relay


class RelayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, _ = relay.start_relay_server("127.0.0.1", 0)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_disk_backed_upload_download_and_consumption(self):
        room = "abc123"
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "source", Path(directory) / "target"
            source.write_bytes(b"EDR" * 400_000)
            relay.register_waiting_room(room, self.url)
            relay.upload_file(room, source, base_url=self.url)
            self.assertTrue(relay.RelayClient(self.url).room_status(room)["ready"])
            relay.download_to_file(room, target, base_url=self.url)
            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertFalse(relay.RelayClient(self.url).room_status(room)["ready"])

    def test_rejects_bad_ids_and_noncontiguous_uploads(self):
        self.assertEqual(relay.parse_relay_remote("Edrnko_BAD!"), (None, None))
        with self.assertRaises(ValueError):
            relay._STORE.put_chunk("abc123", 2, 3, b"x")

