"""Behavioral tests for WorkspaceNamer text cleaning and name generation."""

import contextlib
import io
import sqlite3
import tempfile
import unittest

from factory.core.workspace_namer import WorkspaceNamer


class TestWorkspaceNamerBehavior(unittest.TestCase):

    def setUp(self):
        self.namer = WorkspaceNamer()

    def test_clean_text_removes_html_entities_and_special_characters(self):
        self.assertEqual(self.namer.clean_text('  HELLO&nbsp;  WORLD!!! '), 'hello world')

    def test_clean_text_replaces_separators_with_single_spaces(self):
        self.assertEqual(self.namer.clean_text('__Alpha???Beta--'), 'alpha beta')

    def test_clean_text_replaces_synonyms(self):
        self.assertEqual(self.namer.clean_text('Создай простой калькулятор'), 'создание простая калькулятор')

    def test_extract_keywords_returns_task_management_terms_without_duplicates(self):
        keywords = self.namer.extract_keywords(
            'управление задачами, JSON парсер, база данных, калькулятор, todo, анализ логов'
        )
        keyword_set = set(keywords)
        self.assertIn('туду_менеджер', keyword_set)
        self.assertIn('json_парсер', keyword_set)
        self.assertIn('база_данных', keyword_set)
        self.assertIn('калькулятор', keyword_set)
        self.assertIn('туду', keyword_set)
        self.assertIn('лог_анализ', keyword_set)
        self.assertEqual(len(keywords), len(keyword_set))

    def test_generate_name_with_empty_description_uses_task_prefix(self):
        name = self.namer.generate_name('', 'abcdef123456')
        self.assertRegex(name, r'^задача_\d{4}_\d{4}_abcdef$')

    def test_generate_name_truncates_to_max_length_and_appends_date(self):
        name = WorkspaceNamer(max_length=6).generate_name('elephant zebra fox yak')
        self.assertRegex(name, r'^elepha_\d{4}_\d{4}$')


    def _create_task_db(self, task_id, input_text, created_at, status):
        import os
        os.makedirs('./database', exist_ok=True)
        conn = sqlite3.connect('./database/factory.db')
        try:
            conn.execute(
                'CREATE TABLE IF NOT EXISTS tasks (task_id TEXT, input_text TEXT, created_at INTEGER, status TEXT)'
            )
            conn.execute(
                'INSERT INTO tasks (task_id, input_text, created_at, status) VALUES (?, ?, ?, ?)',
                (task_id, input_text, created_at, status),
            )
            conn.commit()
        finally:
            conn.close()

    def test_get_task_info_returns_stored_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with contextlib.chdir(tmp_dir):
                self._create_task_db('task-1', 'Реальная задача', 1700000000, 'done')
                info = self.namer.get_task_info('task-1')
        self.assertEqual(info['description'], 'Реальная задача')
        self.assertEqual(info['created_at'], 1700000000)
        self.assertEqual(info['status'], 'done')

    def test_get_task_info_returns_none_for_missing_task(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with contextlib.chdir(tmp_dir):
                self._create_task_db('task-1', 'Реальная задача', 1700000000, 'done')
                self.assertIsNone(self.namer.get_task_info('missing-task'))

    def test_get_task_info_sanitizes_sql_injection_task_id(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with contextlib.chdir(tmp_dir):
                self._create_task_db('known', 'Real Secret Task', 123, 'done')
                self.assertIsNone(self.namer.get_task_info("known' OR 1=1 --"))
                self.assertEqual(self.namer.get_task_info('known')['description'], 'Real Secret Task')

    def test_get_task_info_returns_none_when_database_missing(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with contextlib.chdir(tmp_dir), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertIsNone(self.namer.get_task_info('task-1'))
            self.assertIn('task-1', output.getvalue())


if __name__ == '__main__':
    unittest.main()
