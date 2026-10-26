import hashlib
import tempfile
import unittest
from pathlib import Path

from test_release_install import update

verified_file = update.verified_file


class SwappedPath:
    def __init__(self, first: Path, second: Path) -> None:
        self.first = first
        self.second = second

    def lstat(self):
        return self.first.lstat()

    def is_symlink(self):
        return False

    def open(self, mode):
        return self.second.open(mode)


class UpdateFileContractsTest(unittest.TestCase):

    def test_update_checksum_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "a.bin"
            path.write_bytes(b"abc")
            digest = hashlib.sha256(b"abc").hexdigest()
            self.assertIsNone(verified_file(path, 3, digest))
            path.write_bytes(b"xyz")
            with self.assertRaises(ValueError) as ctx:
                verified_file(path, 3, digest)
            self.assertEqual(str(ctx.exception), "update_checksum_mismatch")

    def test_update_file_changed_on_swapped_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            a = tmp_path / "a.bin"
            b = tmp_path / "b.bin"
            a.write_bytes(b"abc")
            b.write_bytes(b"abc")
            digest = hashlib.sha256(b"abc").hexdigest()
            swapped = SwappedPath(a, b)
            with self.assertRaises(ValueError) as ctx:
                verified_file(swapped, 3, digest)
            self.assertEqual(str(ctx.exception), "update_file_changed")
