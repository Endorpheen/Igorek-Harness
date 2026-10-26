"""Adaptive table rendering must keep every cell and complete ANSI sequences."""
import re

import pytest

from factory.core.tui.markdown_render import render_markdown_text_with_width_and_cwd


ANSI=re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)')


def plain(lines):
    return '\n'.join(ANSI.sub('',line) for line in lines)


@pytest.mark.parametrize('width',[1,8,16,30,80])
@pytest.mark.parametrize('content',[
    '<div>TokenHTML</div>',
    '<!-- TokenHTML -->',
    '<section class="fixture">TokenHTML</section>',
])
def test_first_column_html_spillover_is_preserved_in_both_table_and_record_layouts(width,content):
    markdown='| Label | Value |\n|---|---|\n| '+content+' | |\n| TokenRow | TokenValue |'
    lines=render_markdown_text_with_width_and_cwd(markdown,width,None)
    normalized=re.sub(r'\s+','',plain(lines))
    for token in ['TokenHTML','TokenRow','TokenValue']:
        assert token in normalized


@pytest.mark.parametrize('width',[1,4,8,12,16,30])
def test_record_headers_never_split_ansi_control_sequences_across_lines(width):
    lines=render_markdown_text_with_width_and_cwd('| Name | Value |\n|---|---|\n| TokenAlpha | TokenBeta |',width,None)
    assert lines
    assert all('\x1b' not in ANSI.sub('',line) for line in lines)


@pytest.mark.parametrize('width',[1,4,8,12,20,60])
@pytest.mark.parametrize('value',['**TokenBold**','*TokenEmphasis*','`TokenCode`'])
def test_styled_record_values_retain_text_and_complete_color_codes(width,value):
    markdown='| Name | Value |\n|---|---|\n| TokenAlpha | '+value+' |'
    lines=render_markdown_text_with_width_and_cwd(markdown,width,None)
    assert all('\x1b' not in ANSI.sub('',line) for line in lines)
    normalized=re.sub(r'\s+','',plain(lines))
    assert 'TokenAlpha' in normalized
    assert value.strip('*`') in normalized


@pytest.mark.parametrize('width',[1,5,12,30,80])
def test_header_only_fallback_preserves_all_header_text(width):
    lines=render_markdown_text_with_width_and_cwd('| HeaderAlpha | HeaderBeta |\n|:---|---:|',width,None)
    normalized=re.sub(r'\s+','',plain(lines))
    assert 'HeaderAlpha' in normalized and 'HeaderBeta' in normalized
    assert all('\x1b' not in ANSI.sub('',line) for line in lines)


@pytest.mark.parametrize('width',[8,16,30,80])
def test_html_literal_with_another_nonempty_cell_stays_in_the_table(width):
    markdown='| Name | Value |\n|---|---|\n| <b>X</b> | TokenValue |'
    lines=render_markdown_text_with_width_and_cwd(markdown,width,None)
    normalized=re.sub(r'\s+','',plain(lines))
    assert '<b>X</b>' in normalized and 'TokenValue' in normalized


@pytest.mark.parametrize('width',[1,6,20,45,80,120,None])
@pytest.mark.parametrize('cells',[
    ['a'*50,'b'*70,'c'*9],
    ['a aa aaa aaaa aaaaa aaaaaa','b bb bbb bbbb bbbbb bbbbbb','c'],
    ['1'*45,'2'*65,'3'*8],
    ['я'*30,'ж'*40,'ю'*10],
])
def test_grid_and_record_transitions_preserve_all_cell_characters(width,cells):
    from collections import Counter
    document='| FieldX | FieldY | FieldZ |\n|:---|:---:|---:|\n'
    document+='\n'.join('| '+' | '.join(cells)+' |' for _ in range(2))
    lines=render_markdown_text_with_width_and_cwd(document,width,None)
    visible=plain(lines)
    assert all('\x1b' not in ANSI.sub('',line) for line in lines)
    expected=Counter(''.join(cells)*2)
    for character,count in expected.items():
        if not character.isspace():assert visible.count(character)==count
