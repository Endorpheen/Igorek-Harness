"""Public compact-record rendering and mode-selection contracts."""
import copy
import re

import pytest

from factory.core.tui.markdown_render import TableCell,TableColumnKind,TableColumnMetrics
from factory.core.tui.markdown_render.table_key_value import render_records,should_render_records
from factory.core.tui.render.highlight import Style


ANSI=re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def metrics(kind):
    return TableColumnMetrics(max_width=40,header_token_width=6,body_token_width=30,kind=kind)


@pytest.mark.parametrize('width',[None,1,8,20,80])
@pytest.mark.parametrize('kind',[TableColumnKind.Compact,TableColumnKind.Narrative,TableColumnKind.TokenHeavy])
@pytest.mark.parametrize('styled',[False,True])
def test_records_preserve_every_field_and_keep_color_codes_whole(width,kind,styled):
    headers=[TableCell(['FieldA']),TableCell(['FieldB'])]
    rows=[[TableCell(['ValueA0']),TableCell(['ValueB0 more fixture words'])],
          [TableCell(['ValueA1']),TableCell(['ValueB1 final fixture words'])]]
    original=copy.deepcopy((headers,rows))
    lines=render_records(headers,rows,[metrics(kind),metrics(kind)],width,Style(bold=styled),Style())
    assert lines and all('\x1b' not in ANSI.sub('',line.line) for line in lines)
    visible=re.sub(r'\s+','',''.join(ANSI.sub('',line.line) for line in lines))
    for token in ['FieldA','FieldB','ValueA0','ValueA1','ValueB0','ValueB1']:
        assert token in visible
    assert visible.count('FieldA')==visible.count('FieldB')==2
    assert (headers,rows)==original
    if styled:assert any('\x1b[1m' in line.line for line in lines)


@pytest.mark.parametrize('width',[None,1,8,80])
def test_empty_values_and_empty_rows_do_not_remove_their_labels(width):
    headers=[TableCell(['EmptyField'])]
    assert render_records(headers,[],[metrics(TableColumnKind.Compact)],width,Style(),Style())==[]
    lines=render_records(headers,[[TableCell([])]],[metrics(TableColumnKind.Compact)],width,Style(),Style())
    visible=re.sub(r'\s+','',''.join(line.line for line in lines))
    assert 'EmptyField' in visible


@pytest.mark.parametrize('rows,width,kind,expected',[
    ([],8,TableColumnKind.Compact,False),
    ([['OK']],8,TableColumnKind.Compact,False),
    ([['LongUnbrokenToken']],3,TableColumnKind.Compact,True),
    ([['LongUnbrokenToken'],['OK'],['OK'],['OK'],['OK'],['OK']],3,TableColumnKind.Compact,False),
    ([['LongUnbrokenToken'],['LongUnbrokenToken'],['OK'],['OK'],['OK'],['OK']],3,TableColumnKind.Compact,True),
    ([['LongUnbrokenToken']],6,TableColumnKind.TokenHeavy,True),
    ([['LongUnbrokenToken']],12,TableColumnKind.TokenHeavy,False),
    ([['one two three four five six seven eight nine ten']],2,TableColumnKind.Narrative,True),
])
def test_record_mode_responds_to_lost_readability_without_flipping_for_one_outlier(rows,width,kind,expected):
    cells=[[TableCell([value]) for value in row] for row in rows]
    assert should_render_records(cells,[width],[metrics(kind)]) is expected


def test_two_starved_narrative_columns_trigger_a_readable_record_layout():
    long='one two three four five six seven eight nine ten eleven twelve thirteen fourteen'
    rows=[[TableCell([long]),TableCell([long])]]
    assert should_render_records(rows,[12,12],[metrics(TableColumnKind.Narrative),metrics(TableColumnKind.Narrative)])
