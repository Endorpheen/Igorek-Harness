import tempfile
import unittest
from pathlib import Path

from factory.core.mobile_bridge.uploads import (
    classify_upload_kind,
    format_attachments_block,
    resolve_session_uploads,
    sanitize_upload_name,
    save_session_upload,
    session_upload_dir,
)


class UploadsModuleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name)
        self.session_id = "chat-20260916-120000-up001"

    def tearDown(self):
        self._tmp.cleanup()

    def test_sanitize_keeps_unicode_and_strips_paths(self):
        self.assertEqual(sanitize_upload_name("../../etc/смета 2026.xlsx"), "смета 2026.xlsx")
        self.assertEqual(sanitize_upload_name(""), "upload")

    def test_kind_classification(self):
        self.assertEqual(classify_upload_kind("photo.HEIC", "application/octet-stream"), "image")
        self.assertEqual(classify_upload_kind("doc.pdf", "application/pdf"), "file")
        self.assertEqual(classify_upload_kind("blob", "image/png"), "image")

    def test_upload_rejects_a_symlinked_session_directory(self):
        outside = self.cwd / 'outside'
        outside.mkdir()
        directory = session_upload_dir(self.cwd, self.session_id)
        directory.parent.mkdir()
        directory.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            save_session_upload(self.cwd, self.session_id, filename='note.txt',
                                mime_type='text/plain', data=b'fixture')
        self.assertEqual(list(outside.iterdir()), [])

    def test_index_symlink_cannot_overwrite_another_file(self):
        outside = self.cwd / 'outside.json'
        outside.write_text('{"fixture":"unchanged"}')
        directory = session_upload_dir(self.cwd, self.session_id)
        directory.mkdir(parents=True)
        (directory / 'index.json').symlink_to(outside)
        with self.assertRaises((OSError, ValueError)):
            save_session_upload(self.cwd, self.session_id, filename='note.txt',
                                mime_type='text/plain', data=b'fixture')
        self.assertEqual(outside.read_text(), '{"fixture":"unchanged"}')

    def test_fixed_temporary_index_symlink_is_not_used(self):
        outside = self.cwd / 'outside.txt'
        outside.write_text('unchanged fixture')
        directory = session_upload_dir(self.cwd, self.session_id)
        directory.mkdir(parents=True)
        (directory / 'index.json.tmp').symlink_to(outside)
        descriptor = save_session_upload(self.cwd, self.session_id, filename='note.txt',
                                         mime_type='text/plain', data=b'fixture')
        self.assertEqual(outside.read_text(), 'unchanged fixture')
        self.assertEqual(len(resolve_session_uploads(self.cwd, self.session_id, [descriptor['upload_id']])), 1)

    def test_save_and_resolve_roundtrip(self):
        descriptor = save_session_upload(
            self.cwd,
            self.session_id,
            filename="отчёт.pdf",
            mime_type="application/pdf",
            data=b"%PDF-1.4",
        )

        resolved = resolve_session_uploads(self.cwd, self.session_id, [descriptor["upload_id"]])

        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["name"], "отчёт.pdf")
        self.assertEqual(resolved[0]["path"], descriptor["path"])
        self.assertTrue(Path(descriptor["path"]).is_relative_to(session_upload_dir(self.cwd, self.session_id)))

    def test_resolve_ignores_unknown_and_outside_paths(self):
        outside = self.cwd / "secret.txt"
        outside.write_text("secret")
        directory = session_upload_dir(self.cwd, self.session_id)
        directory.mkdir(parents=True)
        (directory / "index.json").write_text(
            '{"evil": {"name": "secret.txt", "path": "%s"}}' % outside, encoding="utf-8"
        )

        self.assertEqual(resolve_session_uploads(self.cwd, self.session_id, ["evil", "missing"]), [])
        self.assertEqual(resolve_session_uploads(self.cwd, self.session_id, None), [])

    def test_format_attachments_block_mentions_paths(self):
        attachment = {
            "name": "фото.jpg",
            "kind": "image",
            "path": "/tmp/фото.jpg",
        }

        block = format_attachments_block([attachment])

        self.assertIn("фото.jpg", block)
        self.assertIn("/tmp/фото.jpg", block)
        self.assertEqual(format_attachments_block([]), "")


if __name__ == "__main__":
    unittest.main()
