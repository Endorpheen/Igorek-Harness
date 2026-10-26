"""HTML rendering must preserve literal code and protect block placeholders."""
from html.parser import HTMLParser
from unittest.mock import Mock

import pytest

from factory.core import telegram_bridge as t


class Content(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.stack=[];self.code=[];self.tags=[];self.nested=[];self.text=[]
    def handle_starttag(self,tag,attrs):
        if 'code' in self.stack:self.nested.append(tag)
        self.stack.append(tag);self.tags.append(tag)
        if tag=='code':self.code.append('')
    def handle_endtag(self,tag):
        if tag in self.stack:self.stack=self.stack[:len(self.stack)-1-self.stack[::-1].index(tag)]
    def handle_data(self,data):
        self.text.append(data)
        if 'code' in self.stack:self.code[-1]+=data


def parsed(value):
    reader=Content();reader.feed(value);reader.close();return reader


@pytest.mark.parametrize('code',[
    'a < b & c > d','**literal bold markers**','*literal italic markers*',
    '[label](https://example.invalid/path)','<think>literal tag</think>',
    '<think/>literal marker','print("a & b")','__literal underline__',
])
def test_inline_code_is_escaped_once_and_never_interpreted_as_markdown(code):
    result=parsed(t._convert_markdown_to_telegram_html('Before `'+code+'` after'))
    assert result.code==[code]
    assert result.nested==[]


@pytest.mark.parametrize('code',['<think>literal thought tag</think>','<think/>literal marker','a < b & c > d'])
def test_fenced_code_keeps_think_tags_literal(code):
    rendered=t._convert_markdown_to_telegram_html('```python\n'+code+'\n```')
    result=parsed(rendered)
    assert result.code==[code] and 'blockquote' not in result.tags


@pytest.mark.parametrize('literal',['@@CODEBLOCK_0@@','@@THINKBLOCK_0@@','@@IGOREK_0_CODEBLOCK_0@@'])
def test_original_placeholder_like_text_is_not_replaced_by_other_content(literal):
    text=literal+'\n```python\nprint(1)\n```\n<think>fixture reasoning</think>'
    rendered=t._convert_markdown_to_telegram_html(text)
    assert literal in ''.join(parsed(rendered).text)
    assert parsed(rendered).code==['print(1)']


def test_thinking_with_code_markers_stays_plain_text_inside_its_quote():
    text='<think>literal **markers** and `code`\n```python\nvalue\n```</think>\nFinal.'
    result=parsed(t._convert_markdown_to_telegram_html(text))
    assert 'blockquote' in result.tags and result.code==[]
    assert 'literal **markers** and `code`' in ''.join(result.text)


def test_plain_message_markup_still_surrounds_inline_code():
    rendered=t._convert_markdown_to_telegram_html('**Before `value` after** and *italic* and __underline__ [link](https://example.invalid)')
    result=parsed(rendered)
    assert all(tag in result.tags for tag in ['b','code','i','u','a'])
    assert result.code==['value']


def test_actual_bot_payload_preserves_inline_command_without_live_delivery(monkeypatch):
    monkeypatch.setenv('FACTORY_TELEGRAM_RICH_MESSAGES','0')
    monkeypatch.setattr(t,'_get_custom_emoji_map',lambda:{})
    bot=t.TelegramBotApi('synthetic-token')
    transport=Mock(return_value={'ok':True,'result':{'message_id':1}})
    monkeypatch.setattr(bot,'_request_json',transport)
    assert bot.send_message('fixture-chat','Run `a < b & c`',reply_to_message_id=7)
    transport.assert_called_once()
    method,payload=transport.call_args.args
    assert method=='sendMessage' and payload['parse_mode']=='HTML'
    assert payload['reply_to_message_id']==7
    assert parsed(payload['text']).code==['a < b & c']


@pytest.mark.parametrize('fenced',[False,True])
def test_bot_does_not_extract_literal_long_think_tag_from_code(monkeypatch,fenced):
    monkeypatch.setenv('FACTORY_TELEGRAM_RICH_MESSAGES','0')
    monkeypatch.setattr(t,'_get_custom_emoji_map',lambda:{})
    code='<think>'+'x'*2500+'</think>'
    message='```python\n'+code+'\n```' if fenced else '`'+code+'`'
    bot=t.TelegramBotApi('synthetic-token');transport=Mock(return_value={'ok':True})
    document=Mock();monkeypatch.setattr(bot,'_request_json',transport);monkeypatch.setattr(bot,'send_document',document)
    bot.send_message('fixture-chat',message)
    document.assert_not_called()
    assert parsed(transport.call_args.args[1]['text']).code==[code]


def test_actual_long_reasoning_still_uses_an_owned_document_fallback(monkeypatch):
    monkeypatch.setenv('FACTORY_TELEGRAM_RICH_MESSAGES','0')
    monkeypatch.setattr(t,'_get_custom_emoji_map',lambda:{})
    bot=t.TelegramBotApi('synthetic-token');transport=Mock(return_value={'ok':True});captured=[]
    monkeypatch.setattr(bot,'_request_json',transport)
    monkeypatch.setattr(bot,'send_document',lambda chat_id,path,**kwargs:captured.append(path.read_text()))
    bot.send_message('fixture-chat','<think>'+'x'*2500+'</think>\nFinal answer.')
    assert captured==['x'*2500]
    assert 'Final answer.' in transport.call_args.args[1]['text']
