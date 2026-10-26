"""Public Markdown normalization preserves source while exposing real pipe tables."""
import re

import pytest

from factory.core.tui import markdown as m,table_detect as t
from factory.core.tui.theme import preview_theme


TABLE='| Name | Value |\n| --- | ---: |\n| fixture | 7 |\n'


@pytest.mark.parametrize('marker',['```','````','~~~'])
@pytest.mark.parametrize('language',['md','markdown','MARKDOWN'])
def test_closed_markdown_table_fence_exposes_only_table_body(marker,language):
    source='before\n'+marker+language+'\n'+TABLE+marker+'\nafter\n'
    assert m.unwrap_markdown_fences(source)=='before\n'+TABLE+'after\n'


@pytest.mark.parametrize('language',['python','sh','text',''])
def test_other_fence_languages_preserve_literal_pipes_and_original_bytes(language):
    source='```'+language+'\n'+TABLE+'```\n'
    assert m.unwrap_markdown_fences(source)==source


@pytest.mark.parametrize('body',['ordinary prose\n','| A | B |\n\n| --- | --- |\n','| --- | --- |\n| --- | --- |\n','Привет 🐙\n'])
def test_markdown_without_real_adjacent_table_rows_remains_code(body):
    source='```md\n'+body+'```\n'
    assert m.unwrap_markdown_fences(source)==source


def test_every_unclosed_stream_prefix_preserves_all_received_text():
    source='```markdown\n'+TABLE+'данные 🐙\n'
    for end in range(len(source)+1):
        prefix=source[:end]
        assert m.unwrap_markdown_fences(prefix)==prefix


@pytest.mark.parametrize('indent',['    ','\t',' \t'])
def test_indented_code_does_not_open_markdown_fence(indent):
    source=indent+'```md\n'+TABLE+indent+'```\n'
    assert m.unwrap_markdown_fences(source)==source


@pytest.mark.parametrize('indent',['',' ','   '])
def test_supported_fence_indentation_does_not_change_table_contents(indent):
    source=indent+'```md\n'+TABLE+indent+'```\n'
    assert m.unwrap_markdown_fences(source)==TABLE


def test_blockquote_table_keeps_quote_prefix_after_outer_fence_removal():
    body=''.join('> '+line+'\n' for line in TABLE.splitlines())
    source='> ```md\n'+body+'> ```\n'
    assert m.unwrap_markdown_fences(source)==body


@pytest.mark.parametrize('fake_close',['~~~','``','``` trailing-info','    ```'])
def test_mismatched_closing_line_cannot_discard_partial_markdown(fake_close):
    source='```md\n'+TABLE+fake_close+'\n'
    assert m.unwrap_markdown_fences(source)==source


def test_longer_closing_marker_is_valid_and_crlf_body_is_preserved():
    body=TABLE.replace('\n','\r\n')
    assert m.unwrap_markdown_fences('```md\r\n'+body+'````\r\n')==body


@pytest.mark.parametrize('line,expected',[
    ('',None),('plain',None),('a | b',['a','b']),('| a | b |',['a','b']),
    (r'| escaped\|pipe | value |',[r'escaped\|pipe','value']),
    (r'a\|b',None),('| a |',['a']),
])
def test_pipe_structure_respects_escaped_delimiters(line,expected):
    assert t.parse_table_segments(line)==expected


@pytest.mark.parametrize('line,valid',[('| --- | :--- | ---: | :---: |',True),('--- | ----',True),('| -- | --- |',False),('| --- | |',False),('| a--- | --- |',False),('',False)])
def test_alignment_delimiter_requires_real_dash_segments(line,valid):
    assert t.is_table_delimiter_line(line) is valid


@pytest.mark.parametrize('opening,kind',[('```md',t.FenceKind.Markdown),('~~~MARKDOWN',t.FenceKind.Markdown),('```python',t.FenceKind.Other),('````sh',t.FenceKind.Other)])
def test_fence_tracker_ignores_short_or_foreign_closers(opening,kind):
    tracker=t.FenceTracker();assert tracker.kind()==t.FenceKind.Outside
    tracker.advance(opening);assert tracker.kind()==kind
    tracker.advance('ordinary line');tracker.advance('``');assert tracker.kind()==kind
    closer=opening[0]*6
    tracker.advance(closer+' extra');assert tracker.kind()==kind
    tracker.advance(closer);assert tracker.kind()==t.FenceKind.Outside


def test_fence_tracker_handles_blockquotes_and_leaves_indented_examples_literal():
    tracker=t.FenceTracker();tracker.advance('    ```md');assert tracker.kind()==t.FenceKind.Outside
    tracker.advance('> > ~~~md');assert tracker.kind()==t.FenceKind.Markdown
    tracker.advance('> > ~~~');assert tracker.kind()==t.FenceKind.Outside
    assert t.strip_blockquote_prefix(' > >  body')=='body'


def plain(lines):return '\n'.join(re.sub(r'\x1b\[[0-9;]*m','',line) for line in lines)


def test_agent_rendering_appends_without_losing_prior_lines_or_table_cells(tmp_path):
    lines=['preserved previous output']
    with preview_theme('dark'):
        m.append_markdown_agent('```md\n'+TABLE+'```\n',60,lines)
        m.append_markdown('Привет fixture',60,str(tmp_path),lines)
    text=plain(lines)
    assert lines[0]=='preserved previous output'
    assert all(value in text for value in ['Name','Value','fixture','7','Привет'])
    assert '```' not in text


def test_agent_link_rendering_preserves_visible_label_without_network(tmp_path,monkeypatch):
    import urllib.request
    def forbidden(*args,**kwargs):raise AssertionError("Rendering must not fetch remote links")
    monkeypatch.setattr(urllib.request,"urlopen",forbidden)
    with preview_theme('dark'):
        lines=m.render_markdown_agent_with_links_and_cwd('[Fixture](https://example.test/report)',60,str(tmp_path))
    assert 'Fixture' in plain([line.line for line in lines])
    assert lines and 'https://example.test/report' not in plain([line.line for line in lines])
