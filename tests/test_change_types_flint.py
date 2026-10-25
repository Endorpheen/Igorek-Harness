"""Contract tests for non-text file change classification."""

import unittest

from factory.core.change_file_types import non_text_change_kind


class TestChangeTypesFlint(unittest.TestCase):
    """Six contract areas for non_text_change_kind."""

    def test_spreadsheet_kinds(self):
        spreadsheet_extensions = [
            "xlsx",
            "xls",
            "xlsm",
            "xlsb",
            "xlt",
            "xltx",
            "xltm",
            "ods",
            "ots",
            "numbers",
        ]
        for ext in spreadsheet_extensions:
            with self.subTest(extension=ext):
                self.assertEqual(
                    non_text_change_kind(f"report.{ext}"),
                    "spreadsheet",
                )

    def test_other_media_kinds(self):
        video_extensions = [
            "mp4",
            "m4v",
            "mov",
            "webm",
            "mkv",
            "avi",
            "mpeg",
            "mpg",
            "3gp",
            "ogv",
        ]
        for ext in video_extensions:
            with self.subTest(extension=ext):
                self.assertEqual(non_text_change_kind(f"clip.{ext}"), "video")
        audio_extensions = [
            "mp3",
            "wav",
            "flac",
            "ogg",
            "m4a",
            "aac",
            "opus",
            "aiff",
            "wma",
        ]
        for ext in audio_extensions:
            with self.subTest(extension=ext):
                self.assertEqual(non_text_change_kind(f"sound.{ext}"), "audio")
        image_extensions = [
            "jpg",
            "jpeg",
            "png",
            "gif",
            "webp",
            "bmp",
            "tif",
            "tiff",
            "heic",
            "heif",
            "avif",
            "ico",
        ]
        for ext in image_extensions:
            with self.subTest(extension=ext):
                self.assertEqual(non_text_change_kind(f"image.{ext}"), "image")

    def test_text_formats_are_none(self):
        for ext in ["csv", "tsv", "py", "md", "json", "svg"]:
            with self.subTest(extension=ext):
                self.assertIsNone(non_text_change_kind(f"file.{ext}"))

    def test_case_and_unicode_names(self):
        with self.subTest(path="LIBRARY.XLSX"):
            self.assertEqual(non_text_change_kind("LIBRARY.XLSX"), "spreadsheet")
        with self.subTest(path="Книга.Ods"):
            self.assertEqual(non_text_change_kind("Книга.Ods"), "spreadsheet")
        with self.subTest(path="Фильм.MP4"):
            self.assertEqual(non_text_change_kind("Фильм.MP4"), "video")
        with self.subTest(path="Текст.TXT"):
            self.assertIsNone(non_text_change_kind("Текст.TXT"))

    def test_double_extensions_and_folders(self):
        with self.subTest(path="report.xlsx.py"):
            self.assertIsNone(non_text_change_kind("report.xlsx.py"))
        with self.subTest(path="my.xlsx/data.csv"):
            self.assertIsNone(non_text_change_kind("my.xlsx/data.csv"))
        with self.subTest(path="docs/ods.xlsx"):
            self.assertEqual(non_text_change_kind("docs/ods.xlsx"), "spreadsheet")

    def test_windows_paths(self):
        with self.subTest(path="C:\\Users\\Игорь\\docs\\spreadsheet.xlsx"):
            self.assertEqual(
                non_text_change_kind("C:\\Users\\Игорь\\docs\\spreadsheet.xlsx"),
                "spreadsheet",
            )
        with self.subTest(path="C:\\Папка\\Книга.MP4"):
            self.assertEqual(
                non_text_change_kind("C:\\Папка\\Книга.MP4"),
                "video",
            )
        with self.subTest(path="C:\\Temp\\notes.txt"):
            self.assertIsNone(non_text_change_kind("C:\\Temp\\notes.txt"))


if __name__ == "__main__":
    unittest.main()
