"""Real file-tool results drive the tour; model prose alone never passes it."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from factory.core.cli_chat import _append_entry, _read_session_meta, _session_path, _write_session_meta
from factory.core.cc_backend_tools import execute_tool
from factory.core.mobile_bridge import demo
from factory.core.mobile_bridge.service import MobileConflict, MobileInvalid, MobileService
from factory.core.prompt_assembly import DEMO_CONTENT
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database


class DemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {'IGOREK_FACTORY_HOME': self.tmp.name,
                                     'FACTORY_DATA_DIR': self.tmp.name})
        env.start(); self.addCleanup(env.stop)
        initialize_database(get_db_path())
        self.service = MobileService(profile='fixture', workdir=self.tmp.name, data_dir=self.tmp.name)
        backend = patch.object(self.service, '_profile_backend', return_value='cc-backend')
        backend.start(); self.addCleanup(backend.stop)
        self.source = 'chat-20261015-120000-abc123'
        _write_session_meta(_session_path(self.source), session_id=self.source, profile='fixture', cwd=self.tmp.name)
        _append_entry(_session_path(self.source), role='user', text='private source history')

    def start(self, language='ru'):
        return self.service.send_message(self.source, '/demo', idempotency_key='start-fixture', demo_language=language)

    def task(self, task_id):
        return self.service._task_row(task_id)

    def finish(self, task, status='ok', text='Shown using real tools'):
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute('UPDATE tasks SET status=?, output_text=? WHERE task_id=?', (status, text, task['task_id']))
        self.service.store.append_event(kind='task.finish', payload={'status': status},
            session_id=task['metadata']['session'], task_id=task['task_id'])
        self.service.finalize_turns()

    def tool(self, task, name, args):
        output = execute_tool(name, args, task['metadata']['cwd'], 'unrestricted',
                              delegate_metadata=task['metadata'])
        self.service.store.append_event(kind='tool.result', payload={
            'tool_name': name, 'tool_input': args, 'result_preview': output, 'status': 'done'},
            session_id=task['metadata']['session'], task_id=task['task_id'])
        return output

    def test_start_isolated_idempotent_and_no_source_history(self):
        result = self.start()
        task = self.task(result['task_id'])
        self.assertNotEqual(result['session_id'], self.source)
        self.assertTrue(Path(task['metadata']['cwd']).is_relative_to(Path(self.tmp.name) / 'demos'))
        self.assertNotIn('private source history', task['input_text'])
        self.assertEqual(task['metadata']['history_messages'], [])
        self.assertTrue(task['metadata']['demo_workspace'])
        self.assertEqual(self.start()['task_id'], task['task_id'])
        self.assertFalse((_read_session_meta(_session_path(self.source)) or {}).get('demo'))

    def test_desktop_ui_hint_is_turn_metadata_and_not_an_implicit_permission(self):
        result = self.service.send_message(self.source, 'Where are notifications?', desktop_ui=True)
        metadata = self.task(result['task_id'])['metadata']
        self.assertTrue(metadata['desktop_ui'])
        self.assertEqual(metadata['platform'], 'desktop')
        self.assertNotIn('auto_approve', metadata)
        self.assertNotIn('restrict_tools_to', metadata)

    def test_full_tour_uses_real_tools_and_leaves_a_useful_note(self):
        result = self.start()
        session = result['session_id']
        for step, tool in enumerate(demo.TOOLS):
            state = _read_session_meta(_session_path(session))['demo']
            self.assertEqual(state['step'], step)
            task = self.task(state['task_id'])
            if tool == 'Write':
                args = {'file_path': 'demo-note.txt', 'content': DEMO_CONTENT['ru']}
            elif tool == 'Read':
                args = {'file_path': 'demo-note.txt'}
            elif tool == 'Edit':
                args = {'file_path': 'demo-note.txt', 'old_string': 'Давайте попробуем вместе.', 'new_string': 'Теперь можно дать мне свою задачу.'}
            elif tool == 'Glob':
                args = {'pattern': '*.txt', 'path': '.'}
            elif tool == 'Grep':
                args = {'pattern': 'файлами', 'path': 'demo-note.txt'}
            elif tool == 'update_plan':
                statuses = ['completed'] * 3 if step == 7 else ['in_progress', 'pending', 'pending']
                args = {'plan': [{'step': name, 'status': status} for name, status in zip(['Create', 'Read and edit', 'Find'], statuses)]}
            if tool:
                self.tool(task, tool, args)
            self.finish(task)
            self.service.finalize_turns()
        state = _read_session_meta(_session_path(session))['demo']
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(len(state['results']), 8)
        self.assertTrue(all(item['passed'] for item in state['results']))
        messages = self.service.get_session(session)['messages']
        self.assertTrue(any('Никаких отчётов' in message['text'] for message in messages))
        self.assertFalse(any('private source history' in message['text'] for message in messages))
        self.assertTrue(any('3/8' in message['text'] for message in messages))

    def test_prose_without_tool_does_not_pass_and_does_not_continue(self):
        result = self.start()
        self.finish(self.task(result['task_id']))
        state = _read_session_meta(_session_path(result['session_id']))['demo']
        self.finish(self.task(state['task_id']), text='I created everything successfully!')
        stopped = _read_session_meta(_session_path(result['session_id']))['demo']
        self.assertEqual(stopped['status'], 'stopped')
        self.assertFalse(stopped['results'][-1]['passed'])
        self.assertEqual(stopped['step'], 1)

    def test_cancel_prevents_following_turns_and_manual_input_is_not_steered(self):
        result = self.start()
        with self.assertRaises(MobileConflict):
            self.service.send_message(result['session_id'], 'another task')
        self.service.cancel_session(result['session_id'])
        self.service.finalize_turns()
        state = _read_session_meta(_session_path(result['session_id']))['demo']
        self.assertEqual(state['status'], 'stopped')
        self.assertEqual(state['step'], 0)

    def test_restarted_service_continues_from_persisted_state(self):
        result = self.start('en')
        self.service = MobileService(profile='fixture', workdir=self.tmp.name, data_dir=self.tmp.name)
        self.finish(self.task(result['task_id']))
        state = _read_session_meta(_session_path(result['session_id']))['demo']
        self.assertEqual(state['step'], 1)
        self.assertEqual(state['language'], 'en')

    def test_commands_network_and_external_reads_are_blocked_even_with_unrestricted_mode(self):
        result = self.start()
        task = self.task(result['task_id'])
        outside = Path(self.tmp.name) / 'outside.txt'; outside.write_text('private')
        for name in ['Bash', 'web_search', 'delegate_task', 'VaultStore', 'browser', 'memory_forget']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.tool(task, name, {})
        with self.assertRaises(ValueError):
            self.tool(task, 'Read', {'file_path': str(outside)})

    def test_requires_supported_profile_and_separate_command_without_attachments(self):
        with patch.object(self.service, '_profile_backend', return_value='codex'):
            with self.assertRaises(MobileInvalid):
                self.start()
        with self.assertRaises(MobileInvalid):
            self.service.send_message(self.source, '/demo', upload_ids=['upload'])
        self.service.available_profiles = lambda: []
        with self.assertRaises(MobileInvalid):
            self.start()

    def test_demo_scope_remains_for_manual_followup(self):
        result = self.start()
        self.service.cancel_session(result['session_id'])
        manual = self.service.send_message(result['session_id'], 'Read our note', cwd=self.tmp.name)
        metadata = self.task(manual['task_id'])['metadata']
        self.assertTrue(metadata['demo_workspace'])
        self.assertNotEqual(metadata['cwd'], self.tmp.name)
        self.assertFalse(metadata.get('demo_run'))

    def test_written_note_accepts_optional_final_newline_but_rejects_other_content(self):
        result = self.start()
        self.finish(self.task(result['task_id']))
        state = _read_session_meta(_session_path(result['session_id']))['demo']
        task = self.task(state['task_id'])
        plan = {'plan': [{'step': str(index), 'status': status} for index, status in enumerate(['in_progress', 'pending', 'pending'])]}
        self.tool(task, 'update_plan', plan); self.finish(task)
        state = _read_session_meta(_session_path(result['session_id']))['demo']
        task = self.task(state['task_id'])
        self.tool(task, 'Write', {'file_path': 'demo-note.txt', 'content': DEMO_CONTENT['ru'].rstrip('\n')})
        self.finish(task)
        state = _read_session_meta(_session_path(result['session_id']))['demo']
        self.assertEqual(state['step'], 3)
