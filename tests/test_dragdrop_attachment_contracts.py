"""Terminal drag/drop separates actual owned image paths from ordinary text."""
import io
import shlex

import pytest
from rich.console import Console

from factory.core import cli_chat_rich as c


@pytest.mark.parametrize('suffix',['.png','.JPG','.jpeg','.gif','.webp'])
def test_quoted_image_path_preserves_spaces_and_prompt_text(tmp_path,suffix):
    image=tmp_path/('fixture image'+suffix);image.write_bytes(b'fixture')
    text='Inspect '+shlex.quote(str(image))+' please'
    prompt,files=c._extract_dragdrop_images(text)
    assert prompt=='Inspect please' and files==[str(image)]


@pytest.mark.parametrize('ending',[',',';'])
def test_terminal_punctuation_does_not_become_part_of_attachment_path(tmp_path,ending):
    image=tmp_path/'fixture.png';image.write_bytes(b'fixture')
    assert c._extract_dragdrop_images(str(image)+ending)==('',[str(image)])


def test_multiple_images_keep_order_and_leave_unrelated_filename_as_text(tmp_path):
    first=tmp_path/'one.png';second=tmp_path/'two.png'
    for image in [first,second]:image.write_bytes(b'fixture')
    prompt,files=c._extract_dragdrop_images('Compare '+str(first)+' report.txt '+str(second))
    assert prompt=='Compare report.txt' and files==[str(first),str(second)]


@pytest.mark.parametrize('kind',['missing','directory','unsupported','oversized'])
def test_non_attachable_path_remains_in_user_prompt(tmp_path,kind):
    path=tmp_path/('fixture.pdf' if kind=='unsupported' else 'fixture.png')
    if kind=='directory':path.mkdir()
    elif kind=='oversized':
        with path.open('wb') as stream:stream.truncate(c._MAX_ATTACH_BYTES+1)
    elif kind=='unsupported':path.write_bytes(b'fixture')
    assert c._extract_dragdrop_images('Inspect '+str(path))==('Inspect '+str(path),[])


def test_home_shorthand_is_resolved_only_inside_owned_home(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path));image=tmp_path/'fixture.png';image.write_bytes(b'fixture')
    assert c._extract_dragdrop_images('~/fixture.png')==('',[str(image)])


def test_malformed_quotes_do_not_crash_or_attach_unrelated_bare_name():
    text="Explain 'unclosed quotation fixture.png"
    assert c._extract_dragdrop_images(text)==(text,[])
    assert c._extract_dragdrop_images('')==('',[])


def test_attachment_summary_uses_bounded_metadata_and_survives_deleted_file(tmp_path):
    output=io.StringIO();console=Console(file=output,width=120)
    image=tmp_path/'fixture.png';image.write_bytes(b'fixture')
    c._print_attached_files(console,[str(image),str(tmp_path/'missing.png')])
    text=output.getvalue()
    assert 'fixture.png' in text and 'missing.png' in text and 'image/png' in text
    assert '?' in text
    assert image.read_bytes()==b'fixture'
