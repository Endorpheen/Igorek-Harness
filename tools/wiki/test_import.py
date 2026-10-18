"""Exercise additive import, local revisions and hostile archive boundaries."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location('wiki_import', Path(__file__).with_name('import_zip.py'))
importer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(importer)


class WikiImportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.site = self.root / 'site'

    def archive(self, name, files):
        target = self.root / name
        with zipfile.ZipFile(target, 'w') as archive:
            for path, content in files.items():
                archive.writestr(path, content)
        return target

    def test_partial_update_preserves_missing_pages_and_other_language(self):
        first = self.archive('first.zip', {'1-One.md': '# One', '2-Two.md': '# Two'})
        ru = self.archive('ru.zip', {'ru_md/1-One.md': '# Один'})
        importer.import_archives({'en': first, 'ru': ru}, self.site)
        partial = self.archive('partial.zip', {'1-One.md': '# Updated'})
        importer.import_archives({'en': partial}, self.site)
        self.assertEqual((self.site / 'content/en/1-One.md').read_text(), '# Updated')
        self.assertEqual((self.site / 'content/en/2-Two.md').read_text(), '# Two')
        self.assertEqual((self.site / 'content/ru/1-One.md').read_text(), '# Один')

    def test_same_upstream_preserves_local_revision(self):
        archive = self.archive('first.zip', {'1-One.md': '# Original'})
        importer.import_archives({'en': archive}, self.site)
        page = self.site / 'content/en/1-One.md'
        page.write_text('# Maintained')
        importer.import_archives({'en': archive}, self.site)
        self.assertEqual(page.read_text(), '# Maintained')

    def test_partial_update_keeps_retained_source_revision(self):
        def metadata(commit):
            return json.dumps({'wiki': {'wikis': {'en': {'metadata': {'full_commit_sha': commit}}}}})
        first = self.archive('first.zip', {'1-One.md': '# Original', '2-Two.md': '# Retained',
            '_raw/full_wiki.json': metadata('a' * 40)})
        importer.import_archives({'en': first}, self.site)
        partial = self.archive('partial.zip', {'1-One.md': '# Updated',
            '_raw/full_wiki.json': metadata('b' * 40)})
        importer.import_archives({'en': partial}, self.site)
        records = json.loads((self.site / 'import-manifest.json').read_text())['languages']['en']['files']
        self.assertEqual(records['1-One.md']['source_commit'], 'b' * 40)
        self.assertEqual(records['2-Two.md']['source_commit'], 'a' * 40)

    def test_conflict_has_no_partial_writes(self):
        first = self.archive('first.zip', {'1-One.md': '# Original'})
        importer.import_archives({'en': first}, self.site)
        page = self.site / 'content/en/1-One.md'
        page.write_text('# Maintained')
        manifest = (self.site / 'import-manifest.json').read_bytes()
        changed = self.archive('changed.zip', {'2-Two.md': '# New', '1-One.md': '# Upstream'})
        with self.assertRaisesRegex(ValueError, 'no files changed'):
            importer.import_archives({'en': changed}, self.site)
        self.assertFalse((self.site / 'content/en/2-Two.md').exists())
        self.assertEqual((self.site / 'import-manifest.json').read_bytes(), manifest)

    def test_service_metadata_excluded_and_diagrams_preserved(self):
        text = '# One\n\n```mermaid\ngraph LR\n A-->B\n```\n'
        archive = self.archive('first.zip', {'README.md': 'private export wrapper',
            '_raw/repo_note_raw.json': '{"private":"metadata"}', '1-One.md': text})
        report = importer.import_archives({'en': archive}, self.site)
        self.assertEqual((self.site / 'content/en/1-One.md').read_bytes(), text.encode())
        self.assertEqual(report[0]['mermaid'], 1)
        self.assertFalse((self.site / 'content/en/_raw').exists())
        self.assertEqual(len(json.loads((self.site / 'import-manifest.json').read_text())['languages']['en']['files']), 1)

    def test_traversal_and_existing_symlink_refused(self):
        archive = self.archive('bad.zip', {'../escape.md': '# Bad'})
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            importer.import_archives({'en': archive}, self.site)
        self.assertFalse((self.root / 'escape.md').exists())
        outside = self.root / 'outside'; outside.mkdir()
        (self.site / 'content').mkdir(parents=True)
        (self.site / 'content/en').symlink_to(outside)
        archive = self.archive('safe.zip', {'1-One.md': '# One'})
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            importer.import_archives({'en': archive}, self.site)
        self.assertFalse((outside / '1-One.md').exists())


if __name__ == '__main__':
    unittest.main()
