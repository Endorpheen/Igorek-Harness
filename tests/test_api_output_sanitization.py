import pytest

from factory.api_server_core import output_utils as output

MODES = {'suggestion_mode': 'suggestion', 'tool_result_mode': 'tool_result'}


def test_sanitization_removes_hidden_reminder_without_losing_visible_answer():
    text = 'Visible answer.\n<system-reminder>Internal setup\nand controls</system-reminder>\nMore useful text.'
    assert output.sanitize_output(text) == 'Visible answer.\n\nMore useful text.'


def test_sanitization_removes_role_and_protocol_labels():
    text = '<|start|>\nassistant\n<|message|>\nA useful answer.\n<|end|>'
    assert output.sanitize_output(text) == 'A useful answer.'


def test_sanitization_removes_injected_help_banner_and_duplicate_paragraphs():
    text = 'The following skills are available for use with the Skill tool\n\nFirst answer.\n\nFirst answer.\n\nSecond answer.'
    assert output.sanitize_output(text) == 'First answer.\n\nSecond answer.'


def test_sanitization_preserves_non_ascii_text_and_code():
    text = 'Результат готов 🐙\n\n```python\nprint("hello")\n```'
    assert output.sanitize_output(text) == text


def test_normal_mode_hides_tool_protocol_without_dropping_surrounding_text():
    text = 'Before\n[tool_use id=1]\n<tool_call>internal payload</tool_call>\nAfter'
    assert output.sanitize_output_for_mode(text, 'normal', **MODES) == 'Before\n\nAfter'


def test_suggestion_is_one_line_and_limited_to_eight_words():
    text = 'one   two three four five six seven eight nine ten\nIgnore this second line'
    assert output.sanitize_output_for_mode(text, 'suggestion', **MODES) == 'one two three four five six seven eight'


def test_empty_suggestion_remains_empty_after_tool_metadata_is_removed():
    assert output.sanitize_output_for_mode('[tool_result id=1]', 'suggestion', **MODES) == ''


@pytest.mark.parametrize('text', [
    'Latest tool used: Bash\n\nTool result:\nbranch main\nversion 1.2.3',
    'latest TOOL used: Bash\r\n\r\nTOOL RESULT:\r\nbranch main\r\nversion 1.2.3',
    'Tool result: branch main\nversion 1.2.3',
])
def test_tool_result_mode_preserves_body_when_removing_leading_metadata(text):
    assert output.sanitize_output_for_mode(text, 'tool_result', **MODES) == 'branch main\nversion 1.2.3'


def test_tool_result_mode_preserves_unlabelled_answer_and_embedded_literal_labels():
    text = 'The file contains this example:\nLatest tool used: example\nTool result: sample'
    assert output.sanitize_output_for_mode(text, 'tool_result', **MODES) == text


def test_tool_preamble_is_normalized_and_bounded():
    assert output.sanitize_tool_preamble('  "Read\n the file"  ') == 'Read the file'
    long = output.sanitize_tool_preamble('x' * 130)
    assert long == 'x' * 120
    assert output.sanitize_tool_preamble('  ') == ''
