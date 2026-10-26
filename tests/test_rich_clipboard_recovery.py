"""Clipboard reader failures use fake commands and owned temp files only."""
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import cli_chat_rich_shared as c


@pytest.fixture(autouse=True)
def owned_clipboard(tmp_path,monkeypatch):
    for key in ['WAYLAND_DISPLAY','DISPLAY']:monkeypatch.delenv(key,raising=False)
    blocked=Mock(side_effect=AssertionError('real clipboard commands are forbidden'))
    monkeypatch.setattr(c.subprocess,'run',blocked);monkeypatch.setattr(c.subprocess,'Popen',blocked)
    original=c.tempfile.NamedTemporaryFile
    monkeypatch.setattr(c.tempfile,'NamedTemporaryFile',lambda **kwargs:original(dir=tmp_path,**kwargs))
    return tmp_path


@pytest.mark.parametrize('value',['empty','whitespace','missing','wrong-suffix','long-message','oversized'])
def test_non_image_clipboard_text_is_not_attached(owned_clipboard,value):
    root=owned_clipboard
    if value=='empty':text=''
    elif value=='whitespace':text='   '
    elif value=='missing':text=str(root/'missing.png')
    elif value=='wrong-suffix':p=root/'file.txt';p.write_text('fixture');text=str(p)
    elif value=='long-message':text='x'*10000
    else:
        p=root/'large.png'
        with p.open('wb') as f:f.truncate(c._MAX_ATTACH_BYTES+1)
        text=str(p)
    assert c._resolve_image_path_from_text(text) is None


@pytest.mark.parametrize('style',['uri','quoted','plain'])
def test_owned_image_paths_keep_spaces_and_uri_escapes(owned_clipboard,style):
    path=owned_clipboard/'fixture image.PNG';path.write_bytes(b'fixture')
    text=path.as_uri() if style=='uri' else '"'+str(path)+'"' if style=='quoted' else str(path)
    assert c._resolve_image_path_from_text(text)==str(path)


@pytest.mark.parametrize('mime,suffix',[('image/png','.png'),('image/jpeg','.jpg')])
def test_wayland_capture_preserves_only_fake_bytes_in_owned_file(monkeypatch,owned_clipboard,mime,suffix):
    data=b'fixture clipboard bytes'
    def command(argv,**kwargs):
        assert not kwargs.get('shell')
        if '--list-types' in argv:return SimpleNamespace(returncode=0,stdout=mime+'\n')
        kwargs['stdout'].write(data);return SimpleNamespace(returncode=0)
    monkeypatch.setattr(c.subprocess,'run',command)
    result=Path(c._clipboard_image_wl())
    assert result.parent==owned_clipboard and result.suffix==suffix and result.read_bytes()==data


@pytest.mark.parametrize('reader',[c._clipboard_image_wl,c._clipboard_image_x11],ids=['wayland','x11'])
@pytest.mark.parametrize('failure',['timeout','command-failed','empty'])
def test_capture_failure_removes_its_partial_temp_file(monkeypatch,owned_clipboard,reader,failure):
    def command(argv,**kwargs):
        if '--list-types' in argv:return SimpleNamespace(returncode=0,stdout='image/png\n')
        if '-version' in argv:return SimpleNamespace(returncode=0)
        if 'stdout' in kwargs:
            if failure=='timeout':raise subprocess.TimeoutExpired(argv,10)
            if failure=='command-failed':raise subprocess.CalledProcessError(1,argv)
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=1,stdout='')
    monkeypatch.setattr(c.subprocess,'run',command)
    if reader is c._clipboard_image_x11 and failure=='command-failed':
        # xclip failure is represented by its return code because check=False.
        original=command
        def command(argv,**kwargs):
            if 'stdout' in kwargs:return SimpleNamespace(returncode=1)
            return original(argv,**kwargs)
        monkeypatch.setattr(c.subprocess,'run',command)
    assert reader() is None
    assert list(owned_clipboard.glob('clip_*'))==[]


@pytest.mark.parametrize('reader',[c._clipboard_image_wl,c._clipboard_image_x11],ids=['wayland','x11'])
@pytest.mark.parametrize('kind',['uri','plain'])
def test_text_clipboard_fallback_skips_comments_and_uses_only_owned_image(monkeypatch,owned_clipboard,reader,kind):
    image=owned_clipboard/'fixture.png';image.write_bytes(b'fixture')
    def command(argv,**kwargs):
        if '--list-types' in argv:return SimpleNamespace(returncode=0,stdout='text/uri-list\ntext/plain;charset=utf-8\n')
        if '-version' in argv:return SimpleNamespace(returncode=0)
        if 'stdout' in kwargs:return SimpleNamespace(returncode=1)
        if 'text/uri-list' in argv:
            return SimpleNamespace(returncode=0 if kind=='uri' else 1,stdout='# fixture comment\n\n'+image.as_uri()+'\n')
        return SimpleNamespace(returncode=0,stdout=str(image))
    monkeypatch.setattr(c.subprocess,'run',command)
    assert reader()==str(image)
    assert list(owned_clipboard.glob('clip_*'))==[]


@pytest.mark.parametrize('reader',[c._clipboard_image_wl,c._clipboard_image_x11],ids=['wayland','x11'])
def test_uri_and_plain_probe_failure_degrades_to_no_image(monkeypatch,reader):
    def command(argv,**kwargs):
        if '--list-types' in argv:return SimpleNamespace(returncode=0,stdout='text/uri-list\ntext/plain\n')
        if '-version' in argv:return SimpleNamespace(returncode=0)
        if 'stdout' in kwargs:return SimpleNamespace(returncode=1)
        raise OSError('fixture provider unavailable')
    monkeypatch.setattr(c.subprocess,'run',command)
    assert reader() is None


@pytest.mark.parametrize('display',['wayland','x11','both','headless'])
@pytest.mark.parametrize('success',['first','last','none'])
def test_text_probe_order_is_bounded_and_preserves_unicode(monkeypatch,display,success):
    if display in {'wayland','both'}:monkeypatch.setenv('WAYLAND_DISPLAY','fixture')
    if display in {'x11','both'}:monkeypatch.setenv('DISPLAY','fixture')
    expected=['wl-paste'] if display=='wayland' else ['xclip','xsel'] if display=='x11' else ['wl-paste','xclip','xsel']
    calls=[]
    def command(argv,**kwargs):
        calls.append(argv[0]);assert kwargs['text'] is True and kwargs['timeout']==2
        works=success=='first' and len(calls)==1 or success=='last' and len(calls)==len(expected)
        if works:return SimpleNamespace(returncode=0,stdout='Привет 🐙')
        if len(calls)==1:raise OSError('fixture unavailable')
        return SimpleNamespace(returncode=1,stdout='')
    monkeypatch.setattr(c.subprocess,'run',command)
    result=c._clipboard_text()
    assert result==(None if success=='none' else 'Привет 🐙')
    assert calls==(expected[:1] if success=='first' else expected)


@pytest.mark.parametrize('scenario',['wayland','x11','none','failure'])
def test_public_image_probe_uses_display_family_without_real_clipboard(monkeypatch,scenario):
    monkeypatch.setenv('WAYLAND_DISPLAY','fixture');monkeypatch.setenv('DISPLAY','fixture')
    wl=Mock(side_effect=RuntimeError('fixture clipboard failure') if scenario=='failure' else None,return_value='/fixture/wl.png' if scenario=='wayland' else None)
    x11=Mock(return_value='/fixture/x11.png' if scenario=='x11' else None)
    monkeypatch.setattr(c,'_clipboard_image_wl',wl);monkeypatch.setattr(c,'_clipboard_image_x11',x11)
    assert c._clipboard_image_path()==('/fixture/wl.png' if scenario=='wayland' else '/fixture/x11.png' if scenario=='x11' else None)
    assert x11.called is (scenario in {'x11','none'})
