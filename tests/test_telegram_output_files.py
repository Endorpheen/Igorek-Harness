import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from factory.core.telegram_bridge import (
    ChatRuntimeState,
    TelegramBridge,
    _build_workspace_context_entry,
    _check_output_file_path,
    _collect_output_file_paths,
    _output_file_roots,
)


class TelegramOutputFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / 'chat'
        self.base.mkdir()
        self.workspace = self.root / 'workspace'
        self.task_id = '12345678-1234-1234-1234-123456789012'
        self.env = patch.dict(os.environ, {'FACTORY_DATA_DIR': str(self.root), 'IGOREK_FACTORY_HOME': str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.bridge = TelegramBridge.__new__(TelegramBridge)
        self.bridge.workdir = str(self.base)
        self.bridge.profile = 'cc-openrouter'
        self.bridge.memory_manager = SimpleNamespace(
            review_turn=Mock(return_value=[]), format_memory_update_message=Mock(return_value=''),
        )
        self.bridge.telegram = SimpleNamespace(send_document=Mock())
        self.bridge._safe_send = Mock()
        self.session = ChatRuntimeState(chat_id='42', user_id='1', chat_session='files-test', workspace_task_id=self.task_id)
        self.bridge._session_for_chat = Mock(return_value=self.session)
        self.bridge._effective_profile_name = Mock(return_value='cc-openrouter')

    def file(self, path, data=b'content'):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def send(self, transport, text, files=None):
        if transport == 'app':
            self.bridge._send_turn_outcome('42', {'status': 'ok', 'messages': [text], 'files': files or []})
        else:
            self.bridge._send_task_outcome(self.session, 'Создай файл', {
                'task_id': self.task_id, 'status': 'ok', 'result_text': text, 'workspace_files': files or [],
            })

    def test_bare_filename_delivered_by_both_transports(self):
        report = self.file(self.base / 'report.xlsx')
        for transport in ('app', 'task'):
            with self.subTest(transport=transport):
                self.session.delivered_files.clear()
                self.bridge.telegram.send_document.reset_mock()
                self.send(transport, 'Вот файл: report.xlsx')
                self.bridge.telegram.send_document.assert_called_once_with('42', report)

    def test_explicit_absolute_bare_deduplicate_after_resolution(self):
        report = self.file(self.base / 'report.xlsx')
        alias = self.base / 'alias.xlsx'
        alias.symlink_to(report)
        self.assertEqual(_collect_output_file_paths(self.base, [f'{report} report.xlsx alias.xlsx'], ['report.xlsx', str(report)]), [str(report)])

    def test_candidate_stage_order_and_relative_subpath(self):
        first = self.file(self.base / 'first.txt')
        second = self.file(self.base / 'second.txt')
        third = self.file(self.base / 'reports' / 'third.xlsx')
        self.assertEqual(_collect_output_file_paths(self.base, [f'reports/third.xlsx {second}'], [str(first)]), list(map(str, (first, second, third))))

    def test_absolute_filename_delivered_by_both_transports(self):
        report = self.file(self.base / 'report.csv')
        for transport in ('app', 'task'):
            with self.subTest(transport=transport):
                self.session.delivered_files.clear()
                self.bridge.telegram.send_document.reset_mock()
                self.send(transport, f'Файл: `{report}`')
                self.bridge.telegram.send_document.assert_called_once_with('42', report)

    def test_task_workspace_outside_chat_delivered_and_deduplicated(self):
        for relative, path in (
            ('report.xlsx', self.workspace / self.task_id / 'results' / 'report.xlsx'),
            ('temp/report.xlsx', self.workspace / f'temp_execution_{self.task_id[:8]}' / 'report.xlsx'),
        ):
            with self.subTest(relative=relative):
                self.bridge.telegram.send_document.reset_mock()
                report = self.file(path)
                self.send('task', f'Вот файл: {report}', [relative])
                self.bridge.telegram.send_document.assert_called_once_with('42', report)

    def test_app_workspace_outside_chat_delivered(self):
        report = self.file(self.workspace / f'temp_execution_{self.task_id[:8]}' / 'report.xlsx')
        self.send('app', 'Готово', [str(report)])
        self.bridge.telegram.send_document.assert_called_once_with('42', report)

    def test_foreign_explicit_and_absolute_paths_rejected_by_both_transports(self):
        report = self.file(self.root / 'private' / 'report.xlsx')
        self.file(self.base / 'report.xlsx')
        for transport in ('app', 'task'):
            with self.subTest(transport=transport):
                self.send(transport, str(report), [str(report)])
        self.bridge.telegram.send_document.assert_not_called()

    def test_traversal_and_link_labels_do_not_fall_back_to_basename(self):
        outside = self.file(self.root / 'report.xlsx')
        self.file(self.base / 'report.xlsx')
        (self.base / 'sub').mkdir()
        for text in ('../report.xlsx', 'sub/../../report.xlsx', f'[report.xlsx]({outside})', f'https://example.test{outside}'):
            with self.subTest(text=text):
                self.assertEqual(_collect_output_file_paths(self.base, [text], []), [])
        self.assertEqual(_check_output_file_path(self.base, '../report.xlsx')[1], 'outside_allowed_roots')

    def test_symlink_escape_rejected(self):
        outside = self.file(self.root / 'private.xlsx')
        alias = self.base / 'report.xlsx'
        alias.symlink_to(outside)
        self.assertEqual(_collect_output_file_paths(self.base, ['report.xlsx'], [str(alias)]), [])

    def test_service_empty_missing_and_directory_filtered(self):
        files = [self.file(self.base / name) for name in (
            '.tool_results/report.txt', '.checkpoints/report.txt', '.codex/report.txt',
            'input.json', 'result.json', 'copy_metadata.json', 'cc_backend_trace.jsonl',
        )]
        files += [self.file(self.base / 'empty.txt', b''), self.base / 'missing.txt', self.base]
        self.assertEqual(_collect_output_file_paths(self.base, [], list(map(str, files))), [])

    def test_sender_revalidates_and_distinguishes_failures(self):
        inside = self.file(self.base / 'report.xlsx')
        outside = self.file(self.root / 'private.xlsx')
        collected = _collect_output_file_paths(self.base, [], [str(inside)])
        inside.unlink()
        inside.symlink_to(outside)
        self.bridge._send_output_files('42', collected)
        self.bridge.telegram.send_document.assert_not_called()
        self.assertIn('путь вне разрешённых папок', self.bridge._safe_send.call_args.args[1])
        self.assertEqual(_check_output_file_path(self.base, 'missing.xlsx')[1], 'missing')
        self.assertEqual(_check_output_file_path(self.base, '.')[1], 'not_file')
        self.assertEqual(_check_output_file_path(self.base, '\x00')[1], 'invalid_path')

    def test_success_deduplicated_across_turns_in_both_transports(self):
        self.file(self.base / 'report.xlsx')
        for transport in ('app', 'task'):
            with self.subTest(transport=transport):
                self.session.delivered_files.clear()
                self.bridge.telegram.send_document.reset_mock()
                self.send(transport, 'report.xlsx')
                self.send(transport, 'report.xlsx')
                self.assertEqual(self.bridge.telegram.send_document.call_count, 1)

    def test_failed_delivery_can_be_retried_in_both_transports(self):
        report = self.file(self.base / 'report.xlsx')
        for transport in ('app', 'task'):
            with self.subTest(transport=transport):
                self.session.delivered_files.clear()
                self.bridge.telegram.send_document.reset_mock()
                self.bridge.telegram.send_document.side_effect = [RuntimeError('offline'), None]
                self.send(transport, 'report.xlsx')
                self.assertEqual(self.session.delivered_files, {})
                self.send(transport, 'report.xlsx')
                self.assertEqual(list(self.session.delivered_files), [str(report)])
                self.assertNotEqual(self.session.delivered_files[str(report)], "")
                self.assertEqual(self.bridge.telegram.send_document.call_count, 2)

    def test_changed_file_is_redelivered_by_freshness_signature(self):
        # A mutable output file (spreadsheet updated during the day) must be
        # re-delivered whenever its content changes; path-only dedup would
        # silently drop every update after the first.
        report = self.file(self.base / 'weight_calories.xlsx', b'v1')
        for transport in ('app', 'task'):
            with self.subTest(transport=transport):
                self.session.delivered_files.clear()
                self.bridge.telegram.send_document.reset_mock()
                self.send(transport, 'weight_calories.xlsx')
                report.write_bytes(b'v2-updated-and-longer')
                self.send(transport, 'weight_calories.xlsx')
                self.assertEqual(self.bridge.telegram.send_document.call_count, 2)

    def test_unchanged_file_is_not_redelivered(self):
        self.file(self.base / 'report.xlsx', b'stable')
        for transport in ('app', 'task'):
            with self.subTest(transport=transport):
                self.session.delivered_files.clear()
                self.bridge.telegram.send_document.reset_mock()
                self.send(transport, 'report.xlsx')
                self.send(transport, 'report.xlsx')
                self.assertEqual(self.bridge.telegram.send_document.call_count, 1)

    def test_roots_reject_invalid_task_id_and_escaped_workspace_symlink(self):
        self.assertEqual(_output_file_roots(self.base, '../../private'), (self.base,))
        self.workspace.mkdir()
        outside = self.root / 'private'
        outside.mkdir()
        (self.workspace / f'temp_execution_{self.task_id[:8]}').symlink_to(outside)
        self.assertNotIn(outside, _output_file_roots(self.base, self.task_id))

    def test_workspace_symlink_to_another_task_does_not_grant_access(self):
        foreign = self.file(self.workspace / 'foreign-task' / 'results' / 'report.xlsx')
        (self.workspace / f'temp_execution_{self.task_id[:8]}').symlink_to(foreign.parent)
        self.send('task', str(foreign), ['temp/report.xlsx'])
        self.bridge.telegram.send_document.assert_not_called()

    def test_followup_uses_metadata_workspace_instead_of_current_task(self):
        report = self.file(self.workspace / f'temp_execution_{self.task_id[:8]}' / 'report.xlsx')
        self.bridge._send_task_outcome(self.session, 'Продолжи', {
            'task_id': 'new-task', 'status': 'ok', 'result_text': 'Готово',
            'metadata': {'workspace_task_id': self.task_id}, 'workspace_files': ['temp/report.xlsx'],
        })
        self.bridge.telegram.send_document.assert_called_once_with('42', report)

    def test_markdown_and_punctuation_keep_valid_paths(self):
        report = self.file(self.base / 'report.xlsx')
        for text in ('**`report.xlsx`**', '(report.xlsx).', '«report.xlsx»', f'[скачать]({report})'):
            with self.subTest(text=text):
                self.assertEqual(_collect_output_file_paths(self.base, [text], []), [str(report)])

    def test_quoted_audio_paths_with_spaces_are_discovered(self):
        audio = self.file(self.base / 'sPeeX - Back To Hard Klubb (2k17 Edit).mp3')
        for text in (f'Файл: `{audio}`', f'Файл: "{audio}"', f'[скачать]({audio})'):
            with self.subTest(text=text):
                self.assertEqual(_collect_output_file_paths(self.base, [text], []), [str(audio)])

    def test_relative_audio_filename_is_discovered(self):
        audio = self.file(self.base / 'track.mp3')
        self.assertEqual(_collect_output_file_paths(self.base, ['Готово: track.mp3'], []), [str(audio)])

    def test_redundant_quote_pair_around_path_is_delivered(self):
        report = self.file(self.base / 'report.xlsx')
        for text in (f"Файл: `'{report}'`", f'Файл: "\'{report}\'"', f'Файл: `"{report}"`'):
            with self.subTest(text=text):
                self.assertEqual(_collect_output_file_paths(self.base, [text], []), [str(report)])

    def test_redundant_quotes_do_not_grant_outside_access(self):
        outside = self.file(self.root / 'private.xlsx')
        for text in (f"`'{outside}'`", f'"{outside}"'):
            with self.subTest(text=text):
                self.assertEqual(_collect_output_file_paths(self.base, [text], []), [])

    def test_quoted_command_and_outside_path_are_not_discovered(self):
        audio = self.file(self.base / 'track.mp3')
        outside = self.file(self.root / 'private song.mp3')
        text = f'Команда: `cp "{audio}" /tmp/track.mp3`; внешний файл: `{outside}`'
        self.assertEqual(_collect_output_file_paths(self.base, [text], []), [])

    def test_explicit_path_with_spaces_and_normalized_base(self):
        report = self.file(self.base / 'my report.xlsx')
        self.assertEqual(_collect_output_file_paths(self.base / '..' / 'chat', [], [str(report)]), [str(report)])
        self.assertEqual(_collect_output_file_paths(self.base, [], [str(report)], allowed_roots=()), [])

    def test_history_preserves_last_mention_order_and_limit(self):
        first = self.file(self.base / 'first.txt')
        second = self.file(self.base / 'second.txt')
        third = self.file(self.base / 'third.txt')
        outside = self.file(self.root / 'private.txt')
        entries = [{'role': 'assistant', 'text': str(path)} for path in (first, second, third, first, outside)]
        result = _build_workspace_context_entry(entries=entries, workspace_task_id=self.task_id, base_dir=self.base, limit=2)
        self.assertEqual([line for line in result['text'].splitlines() if line.startswith('- ')], [f'- {third}', f'- {first}'])

    def test_history_resolves_shared_workspace_and_filters_service_files(self):
        report = self.file(self.workspace / f'temp_execution_{self.task_id[:8]}' / 'report.xlsx')
        self.file(report.parent / '.codex' / 'secret.txt')
        result = _build_workspace_context_entry(entries=[{
            'role': 'assistant', 'task_id': 'different-task', 'workspace_task_id': self.task_id,
            'workspace_files': ['temp/report.xlsx', 'temp/.codex/secret.txt'], 'text': '',
        }], workspace_task_id=self.task_id, base_dir=self.base)
        self.assertIn(str(report), result['text'])
        self.assertNotIn('secret.txt', result['text'])


if __name__ == '__main__':
    unittest.main()
