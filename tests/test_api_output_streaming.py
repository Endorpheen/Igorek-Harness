import pytest

from factory.api_server_core import output_utils as output


def test_ordinary_output_without_stop_sequences_is_preserved():
    assert output.split_stop_sequence('Useful answer', None) == ('Useful answer', None, False)
    assert output.split_stop_sequence('Useful answer', []) == ('Useful answer', None, False)
    assert output.split_stop_sequence('', ['STOP']) == ('', None, False)


def test_empty_and_non_text_stop_entries_do_not_stop_output():
    assert output.split_stop_sequence('Useful answer', ['', None, 0]) == ('Useful answer', None, False)


def test_earliest_stop_wins_regardless_of_configuration_order():
    assert output.split_stop_sequence('first STOP later END', ['END', 'STOP']) == ('first ', 'STOP', True)


@pytest.mark.parametrize('sequences', [['STOP', 'STOP_LONG'], ['STOP_LONG', 'STOP']])
def test_longest_stop_wins_when_both_start_at_same_position(sequences):
    assert output.split_stop_sequence('answer STOP_LONG ignored', sequences) == ('answer ', 'STOP_LONG', True)


@pytest.mark.parametrize(('raw', 'expected'), [
    ('answer<', ('answer', None, False)),
    ('answer<ST', ('answer', None, False)),
    ('answer<STOP', ('answer', None, False)),
    ('answer<STOP>', ('answer', '<STOP>', True)),
    ('answer<STOP>ignored', ('answer', '<STOP>', True)),
])
def test_partial_stop_is_withheld_until_it_is_completed(raw, expected):
    assert output.split_stop_sequence(raw, ['<STOP>']) == expected


def test_failed_partial_stop_is_released_without_losing_text():
    assert output.split_stop_sequence('answer<ST', ['<STOP>']) == ('answer', None, False)
    assert output.split_stop_sequence('answer<STx', ['<STOP>']) == ('answer<STx', None, False)


def test_unmatched_stop_does_not_trim_regular_output():
    assert output.split_stop_sequence('ordinary output', ['<STOP>', 'END']) == ('ordinary output', None, False)


@pytest.mark.parametrize(('raw', 'emitted', 'expected'), [
    ('Hello', '', ('Hello', 'Hello')),
    ('Hello world', 'Hello', ('Hello world', ' world')),
    ('Hello world', 'Hello world', ('Hello world', '')),
])
def test_streaming_delta_contains_only_new_visible_text(raw, emitted, expected):
    assert output.extract_visible_delta(raw, emitted, 'normal', suggestion_mode='suggestion', tool_result_mode='tool_result') == expected


def test_rewritten_visible_text_is_reported_as_a_replacement():
    assert output.extract_visible_delta('Corrected answer', 'Previous answer', 'normal',
                                        suggestion_mode='suggestion', tool_result_mode='tool_result') == ('Corrected answer', 'Corrected answer')


@pytest.mark.parametrize(('count', 'expected'), [(9, 'end_turn'), (10, 'max_tokens'), (11, 'max_tokens')])
def test_stop_reason_changes_at_the_output_token_limit(count, expected):
    assert output.infer_stop_reason(count, 10) == expected
