import io
import tempfile
import unittest
import zipfile
from pathlib import Path

import error as e
import share


class ShareSafetyTests(unittest.TestCase):
    def test_safe_extract_rejects_traversal_without_files(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive: archive.writestr("../escape.txt", "no")
        data.seek(0)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(e.CliError):
                share.safe_extract(data, Path(directory) / "out")
            self.assertFalse((Path(directory) / "escape.txt").exists())

    def test_fast_archive_uses_stored_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root / "a.bin").write_bytes(b"x" * 100)
            payload = share.bundle_to_memory(root, skip_guard=True, fast=True)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                self.assertEqual(archive.getinfo("a.bin").compress_type, zipfile.ZIP_STORED)
