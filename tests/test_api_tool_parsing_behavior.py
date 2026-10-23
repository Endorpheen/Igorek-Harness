import copy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.api_server_core import tool_parsing as parsing


@pytest.mark.parametrize('text,expected', [
    ('prefix "input": {"a": {"b": 2}} trailing', {'a': {'b': 2}}),
    ('"other": {}', None), ('"input": nope', None),
    ('"input": {broken}', None),
])
def test_named_objects_preserve_nested_values_and_reject_missing_json(text, expected):
    assert parsing.extract_named_json_object(text, 'input') == expected


def test_object_scanner_recovers_after_corrupt_json_without_duplicating_nested_objects():
    assert parsing.extract_json_objects('before {bad} {"a": {"b": 1}} after {"c": 2}') == [
        {'a': {'b': 1}}, {'c': 2}]
    assert parsing.extract_json_objects('plain text') == []


@pytest.mark.parametrize('text,expected', [
    ('  {"ok": true}  ', {'ok': True}),
    ('```json\n{"a": 1}\n```', {'a': 1}),
    ('', None), ('null', None), ('[1, 2]', None),
    ('{broken}', None), ('{"a":1} {"b":2}', None),
])
def test_single_json_plan_requires_one_object(text, expected):
    assert parsing.parse_json_object(text) == expected


@pytest.mark.parametrize('payload,expected', [
    ({'tool': 'Read', 'input': {'file_path': 'a.py'}}, {'tool': 'Read', 'input': {'file_path': 'a.py'}}),
    ({'tool': 'Read'}, {'tool': 'Read', 'input': {}}),
    ({'tool': {'name': 'Read', 'input': {'file_path': 'a.py'}}}, {'tool': 'Read', 'input': {'file_path': 'a.py'}}),
    ({'tool': {'name': 'Read'}, 'input': {'limit': 1}}, {'tool': 'Read', 'input': {'limit': 1}}),
    ({'tool': 'Bash', 'input': 'pwd'}, {'tool': 'Bash', 'input': {'command': 'pwd'}}),
    (None, None), ([], None), ({}, None), ({'tool': 1}, None),
    ({'tool': {'name': None}}, None), ({'tool': 'Read', 'input': 'bad'}, None),
])
def test_plan_shapes_are_coerced_without_mutating_the_input(payload, expected):
    before = copy.deepcopy(payload)
    assert parsing.coerce_tool_plan_payload(payload) == expected
    assert payload == before


@pytest.mark.parametrize('text,expected', [
    ('"tool":"Read","input":{"file_path":"a.py"}', {'tool': 'Read', 'input': {'file_path': 'a.py'}}),
    ('"tool":"Write", "input":{"file_path":"out.txt","content":"unfinished', {'tool': 'Write', 'input': {'file_path': 'out.txt'}}),
    ('"tool":"Read","path":"a.py"', {'tool': 'Read', 'input': {'path': 'a.py'}}),
    ('"tool":"  "', None), ('nothing', None), ('"tool":"Read"', None),
])
def test_truncated_plans_keep_a_real_target_or_fail_closed(text, expected):
    assert parsing.salvage_tool_plan(text) == expected


def test_plan_parser_handles_nested_tool_records_after_unrelated_json():
    text = '{"metadata":1} {"tool":{"name":"Read","input":{"file_path":"a.py"}}}'
    assert parsing.parse_tool_plan(text) == {'tool': 'Read', 'input': {'file_path': 'a.py'}}
    assert parsing.parse_tool_plan('{"metadata":1}') is None
    assert parsing.parse_tool_plan('{"tool":"Bash","input":"pwd"}') == {'tool': 'Bash', 'input': {'command': 'pwd'}}


def textual(text):
    return parsing.parse_textual_tool_use(text, parse_json_object_fn=parsing.parse_json_object)


def test_captured_read_tool_call_remains_compatible():
    fixture = Path(__file__).parent / 'fixtures/tool_salvage/bracket_read_tool_use.txt'
    call = textual(fixture.read_text())
    assert call is not None
    assert call['name'] == 'Read'
    assert call['input']['file_path'].endswith('/Ledger.md')
    assert call['input']['offset'] == 1
    assert call['input']['limit'] == 80


def test_bracket_call_preserves_identity_nested_inputs_and_short_surrounding_text():
    block = '[tool_use id=toolu_fixture name=Write input={"file_path":"a","content":"hello","extra":{"x":1}}]'
    expected = {'id': 'toolu_fixture', 'name': 'Write', 'input': {'file_path': 'a', 'content': 'hello', 'extra': {'x': 1}}}
    assert textual('First\nSecond\nThird\n' + block + '\nDone\nEnd') == expected


@pytest.mark.parametrize('prefix,suffix', [
    ('a\nb\nc\nd\n', ''), ('[untrusted]\n', ''), ('', '\na\nb\nc'), ('', '\n[another]'),
])
def test_textual_call_rejects_ambiguous_surrounding_content(prefix, suffix):
    block = '[tool_use id=one name=Read input={"file_path":"a"}]'
    assert textual(prefix + block + suffix) is None


@pytest.mark.parametrize('text', [
    '', '[tool_use id=one name=Read input={broken}]',
    '[tool_use id=one name=Read input={}]\n[tool_use id=two name=Read input={}]',
    '[tool_use id=one name=Read input={}]<tool_call><function=Read><parameter=file_path>a</parameter></function></tool_call>',
    '<tool_call><function=Read></function></tool_call>',
    '<tool_call><function=Read><parameter=file_path>a</parameter></function></tool_call><tool_call></tool_call>',
    '<tool_call broken>',
])
def test_textual_calls_require_a_single_complete_unambiguous_call(text):
    assert textual(text) is None


def test_xml_call_is_case_insensitive_and_has_a_stable_generated_id():
    block = '<TOOL_CALL><FUNCTION=Read><PARAMETER=file_path> a.py </PARAMETER><PARAMETER=offset> 2 </PARAMETER></FUNCTION></TOOL_CALL>'
    one = textual(block)
    assert one == textual('Opening file\n' + block + '\nDone')
    assert one['name'] == 'Read'
    assert one['input'] == {'file_path': 'a.py', 'offset': '2'}
    assert one['id'].startswith('toolu_xml_')
    assert textual('1\n2\n3\n4\n' + block) is None
    assert textual('[other]\n' + block) is None
    assert textual(block + '\n1\n2\n3') is None
    assert textual(block + '\n[other]') is None


@pytest.mark.parametrize('schema,input_,expected', [
    (None, {}, (False, 'missing input_schema')),
    ({}, {}, (False, 'missing schema properties')),
    ({'properties': [], 'required': []}, {}, (False, 'missing schema properties')),
    ({'properties': {'path': {}}, 'required': ['path']}, {}, (False, 'missing required field: path')),
    ({'properties': {}, 'additionalProperties': False}, {'extra': 1}, (False, 'unexpected fields: extra')),
    ({'properties': {'path': {}}}, {'path': None}, (True, '')),
    ({'properties': {'path': None}}, {'path': 1}, (True, '')),
    ({'properties': {}, 'required': 'not-a-list'}, {}, (True, '')),
])
def test_schema_presence_required_fields_and_extra_keys(schema, input_, expected):
    assert parsing.validate_tool_input({'input_schema': schema}, input_) == expected


@pytest.mark.parametrize('kind,value,valid', [
    ('string', '', True), ('string', 2, False),
    ('boolean', False, True), ('boolean', 0, False),
    ('array', [], True), ('array', {}, False),
    ('object', {}, True), ('object', [], False),
    ('number', 0, True), ('number', 1.25, True), ('number', '2', False),
    ('number', True, False), ('number', False, False),
    ('number', float('nan'), False), ('number', float('inf'), False),
    ('integer', 0, True), ('integer', -2, True), ('integer', 2.0, True),
    ('integer', True, False), ('integer', float('nan'), False), ('integer', float('inf'), False),
    ('integer', False, False), ('integer', 1.25, False), ('integer', '2', False),
])
def test_schema_numeric_types_do_not_confuse_booleans_or_fractional_integers(kind, value, valid):
    tool = {'input_schema': {'properties': {'value': {'type': kind}}}}
    actual, error = parsing.validate_tool_input(tool, {'value': value})
    assert actual is valid
    assert (not error) is valid


@pytest.mark.parametrize('old,new,valid', [
    ('old', 'new', True), ('old', '', True), ('', 'new', False),
    ('  ', 'new', False), ('same', 'same', False), (None, 'new', False), ('old', None, False),
])
def test_edit_contract_requires_a_real_change_but_allows_deletion(old, new, valid):
    tool = {'name': 'Edit', 'input_schema': {'properties': {}}}
    assert parsing.validate_tool_input(tool, {'old_string': old, 'new_string': new})[0] is valid


@pytest.mark.parametrize('name', ['Read', 'Write', 'Edit', 'Update', 'MultiEdit', 'NotebookEdit'])
def test_file_aliases_are_normalized_without_overwriting_explicit_targets(name):
    original = {'file_name': ' a.py ', 'filename': 'ignored.py'}
    assert parsing.normalize_tool_input({'name': name}, original) == {'file_path': 'a.py'}
    assert original == {'file_name': ' a.py ', 'filename': 'ignored.py'}
    assert parsing.normalize_tool_input({'name': name}, {'file_path': 'chosen.py', 'file_name': 'old.py'}) == {'file_path': 'chosen.py'}
    assert parsing.normalize_tool_input({'name': name}, {'file_name': None, 'filename': ' b.py '}) == {'file_path': 'b.py'}


def test_schema_defaults_preserve_explicit_falsy_values_and_unknown_tools():
    tool = {'name': 'Other', 'input_schema': {'properties': {'enabled': {'default': True}, 'limit': {'default': 10}, 'bad': None}}}
    assert parsing.normalize_tool_input(tool, {'enabled': False, 'limit': 0}) == {'enabled': False, 'limit': 0}
    assert parsing.normalize_tool_input(tool, {}) == {'enabled': True, 'limit': 10}
    assert parsing.normalize_tool_input({'name': 'Other'}, {'filename': 'a'}) == {'filename': 'a'}
    assert parsing.normalize_tool_input({'input_schema': {'properties': []}}, {}) == {}


def test_question_normalization_preserves_the_caller_and_repairs_incomplete_options():
    original = {'questions': ['  Continue?  ', ' ', 5, {'question': ''},
        {'question': 'Pick', 'header': '  Header longer than twelve  ', 'multiSelect': 'bad',
         'options': [{'label': ' A ', 'description': ' first '}, {'label': ' B ', 'description': ' second '},
                     {'label': '', 'description': 'bad'}, 'invalid', {'label': 'ignored', 'description': 'fifth'}]},
        {'question': 'Missing options', 'multiSelect': True},
        {'question': 'Invalid options', 'header': 3, 'options': [{'label': 'bad'}, None]}]}
    before = copy.deepcopy(original)
    result = parsing.normalize_tool_input({'name': 'AskUserQuestion'}, original)['questions']
    assert len(result) == 4
    assert result[0]['question'] == 'Continue?'
    assert result[0]['header'] == 'Question'
    assert len(result[0]['options']) == 2
    assert result[1]['header'] == 'Header longe'
    assert result[1]['options'] == [{'label': 'A', 'description': 'first'}, {'label': 'B', 'description': 'second'}]
    assert result[1]['multiSelect'] is False
    assert result[2]['multiSelect'] is True
    assert len(result[2]['options']) == 2
    assert result[3]['header'] == 'Question'
    assert len(result[3]['options']) == 2
    assert original == before


def test_named_calls_validate_normalized_values_then_policy_and_keep_original_identity():
    tool = {'name': 'Read', 'input_schema': {'properties': {'file_path': {'type': 'string'}, 'limit': {'type': 'integer', 'default': 10}}, 'required': ['file_path']}}
    req, state = SimpleNamespace(), object()
    policy = Mock(return_value=(True, ''))
    kwargs = {'tool_lookup': lambda _: {'Read': tool}, 'normalize_tool_input_fn': parsing.normalize_tool_input,
              'validate_tool_input_fn': parsing.validate_tool_input, 'validate_tool_policy_fn': policy}
    source = {'id': 'call-1', 'name': 'Read', 'input': {'filename': ' a.py '}}
    assert parsing.validated_named_tool_use(req, state, source, **kwargs) == {
        'id': 'call-1', 'name': 'Read', 'input': {'file_path': 'a.py', 'limit': 10}}
    policy.assert_called_once_with(state, 'Read', {'file_path': 'a.py', 'limit': 10})
    assert source['input'] == {'filename': ' a.py '}
    policy.reset_mock()
    for call in [None, {'name': 'Unknown', 'input': {}}, {'name': 'Read', 'input': {'file_path': 3}}]:
        assert parsing.validated_named_tool_use(req, state, call, **kwargs) is None
    policy.assert_not_called()
    policy.return_value = (False, 'outside workspace')
    assert parsing.validated_named_tool_use(req, state, source, **kwargs) is None
    policy.reset_mock()
    assert parsing.validated_named_tool_use(req, None, source, **kwargs)['id'] == 'call-1'
    policy.assert_not_called()


@pytest.mark.parametrize('kind,raw,expected,valid', [
    ('integer', '2', 2, True), ('integer', '0', 0, True),
    ('integer', 2.0, 2, True), ('integer', '2.0', 2, True),
    ('number', '1.25', 1.25, True), ('number', '2e1', 20.0, True),
    ('integer', '2.5', '2.5', False), ('integer', 'false', 'false', False),
    ('integer', False, False, False), ('number', 'NaN', 'NaN', False),
    ('number', 'null', 'null', False), ('integer', '[]', '[]', False),
    ('integer', 'not a number', 'not a number', False),
])
def test_numeric_normalization_keeps_legacy_transport_values_and_never_turns_bool_into_a_number(kind, raw, expected, valid):
    tool = {'name': 'Read', 'input_schema': {'properties': {'value': {'type': kind}}}}
    original = {'value': raw}
    normalized = parsing.normalize_tool_input(tool, original)
    assert normalized == {'value': expected}
    assert original == {'value': raw}
    assert parsing.validate_tool_input(tool, normalized)[0] is valid
    if kind == 'integer' and valid:
        assert type(normalized['value']) is int


def test_xml_numeric_parameters_survive_the_actual_normalize_validate_pipeline():
    fixture = Path(__file__).parent / 'fixtures/tool_salvage/api_xml_read_numeric_parameters.txt'
    call = textual(fixture.read_text())
    tool = {'name': 'Read', 'input_schema': {'properties': {
        'file_path': {'type': 'string'}, 'offset': {'type': 'integer'}, 'limit': {'type': 'integer'}},
        'required': ['file_path'], 'additionalProperties': False}}
    policy = Mock(return_value=(True, ''))
    validated = parsing.validated_named_tool_use(SimpleNamespace(), object(), call,
        tool_lookup=lambda _: {'Read': tool}, normalize_tool_input_fn=parsing.normalize_tool_input,
        validate_tool_input_fn=parsing.validate_tool_input, validate_tool_policy_fn=policy)
    assert validated is not None
    assert validated['input'] == {'file_path': 'src/main.py', 'offset': 2, 'limit': 20}
    assert call['input'] == {'file_path': 'src/main.py', 'offset': '2', 'limit': '20'}
    policy.assert_called_once()


@pytest.mark.parametrize('schema_type', [['integer', 'null'], {'unsupported': 'shape'}, None, 'string'])
def test_unsupported_numeric_schema_shapes_preserve_existing_behavior(schema_type):
    tool = {'input_schema': {'properties': {'value': {'type': schema_type}}}}
    assert parsing.normalize_tool_input(tool, {'value': '2'}) == {'value': '2'}
