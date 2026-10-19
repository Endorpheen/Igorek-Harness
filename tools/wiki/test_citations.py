"""Verify exported source references render as links rather than literal code."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

from bs4 import BeautifulSoup
import markdown

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('wiki_hooks', ROOT / 'docs/wiki/hooks.py')
hooks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hooks)


class WikiCitationTest(unittest.TestCase):
    def render(self, text, language='en'):
        source = ROOT / 'docs/wiki/content' / language / '4-Electron-Desktop-Application/4-Electron-Desktop-Application.md'
        page = SimpleNamespace(file=SimpleNamespace(abs_src_path=str(source)))
        result = hooks.on_page_markdown(text, page, None, None)
        return BeautifulSoup(markdown.markdown(result, extensions=['fenced_code']), 'html.parser')

    def test_export_citation_shapes_are_clickable(self):
        for text in ['`[desktop/main.cjs:5-8]()`', '`[desktop/main.cjs:5-8]`',
                     '[desktop/main.cjs:5-8]', '`desktop/main.cjs:5-8`', '`desktop/main.cjs:5-8()`',
                     '[`desktop/main.cjs:5-8]()', '[`desktop/main.cjs:5-8`]()']:
            with self.subTest(text=text):
                soup = self.render(text)
                self.assertEqual(len(soup.find_all('a')), 1)
                self.assertEqual(soup.a['href'], hooks.REPOSITORY + '/blob/571d14a96d44f46ae29c7f165024a8ef1f9ad1cd/desktop/main.cjs#L5-L8')
                self.assertIsNone(soup.find('code'))

    def test_grouped_citations_and_malformed_backticks(self):
        for text in ['`[desktop/main.cjs:5-8](), [desktop/runtime.cjs:68-109]()`',
                     '[`desktop/main.cjs:5-8](), [`desktop/runtime.cjs:68-109]()']:
            with self.subTest(text=text):
                soup = self.render(text)
                self.assertEqual(len(soup.find_all('a')), 2)
                self.assertIsNone(soup.find('code'))

    def test_source_file_without_lines_is_linked_only_in_source_list(self):
        soup = self.render('Sources: `factory/core/worker_presence.py`\n\nThe file `desktop/main.cjs` is an entry point.')
        self.assertEqual(len(soup.find_all('a')), 1)
        self.assertTrue(soup.a['href'].endswith('/factory/core/worker_presence.py'))
        self.assertEqual(soup.code.get_text(), 'desktop/main.cjs')

    def test_literal_examples_and_existing_links_remain_intact(self):
        soup = self.render('```text\n[desktop/main.cjs:5-8]()\n```\n\n`example [desktop/main.cjs:5-8]()`\n\n[GitHub](https://github.com/)')
        self.assertEqual(len(soup.find_all('a')), 1)
        self.assertEqual(soup.a['href'], 'https://github.com/')
        self.assertEqual(len(soup.find_all('code')), 2)
        self.assertIn('[desktop/main.cjs:5-8]()', soup.pre.get_text())

    def test_reported_page_in_both_languages(self):
        for language in ['en', 'ru']:
            with self.subTest(language=language):
                path = ROOT / 'docs/wiki/content' / language / '4-Electron-Desktop-Application/4-Electron-Desktop-Application.md'
                soup = self.render(path.read_text(), language)
                for ending in ['desktop/main.cjs#L1-L58', 'desktop/runtime.cjs#L68-L85',
                               'desktop/owner-preload.cjs#L1-L10', 'desktop/main.cjs#L73-L129',
                               'desktop/runtime.cjs#L68-L109', 'desktop/owner-preload.cjs#L29-L86',
                               'desktop/main.cjs#L5-L8']:
                    self.assertTrue(any(a['href'].endswith(ending) for a in soup.find_all('a')), ending)
                self.assertFalse(any('](' in c.get_text() for c in soup.find_all('code') if not c.find_parent('pre')))


if __name__ == '__main__':
    unittest.main()
