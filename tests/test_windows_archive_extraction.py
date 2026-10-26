import io
import tarfile
import tempfile
import unittest
from pathlib import Path

import importlib.util

_spec = importlib.util.spec_from_file_location(
    "windows_archive_subject", Path(__file__).resolve().parents[1] / "desktop/opencode-windows.py"
)
subject = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(subject)


class TestWindowsArchiveExtraction(unittest.TestCase):
    def test_extract_writes_fixture_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "package.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo("package/bin/opencode.exe")
                info.size = len(b"fixture-exe")
                tar.addfile(info, io.BytesIO(b"fixture-exe"))

            output = Path(tmp) / "output"
            subject.extract(archive, output)

            self.assertEqual((output / "opencode.exe").read_bytes(), b"fixture-exe")

    def test_extract_rejects_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "package.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                info = tarfile.TarInfo("package/bin/opencode.exe")
                info.size = 0
                tar.addfile(info, io.BytesIO(b""))

            output = Path(tmp) / "output"
            with self.assertRaises(ValueError) as ctx:
                subject.extract(archive, output)

            self.assertEqual(str(ctx.exception), "invalid_opencode_windows_archive")
            self.assertFalse((output / "opencode.exe").exists())


if __name__ == "__main__":
    unittest.main()
