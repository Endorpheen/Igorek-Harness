import base64
import time
import xml.etree.ElementTree as ET
import pytest
from factory.core.mobile_bridge.qr import pairing_qr


def test_pairing_qr_is_local_svg_with_only_the_one_time_connection_data():
    value=pairing_qr('https://192.168.0.2:47822','1234-ABCD-5678','a'*64,int(time.time())+300)
    assert value['url']=='https://192.168.0.2:47822'
    svg=base64.b64decode(value['data_url'].split(',',1)[1]);root=ET.fromstring(svg)
    assert root.tag.endswith('svg') and any(node.tag.endswith('path') for node in root.iter())
    assert b'<script' not in svg and b'http://192.168' not in svg
    assert set(value)=={'url','expires_at','data_url'}


@pytest.mark.parametrize('url,code,pin',[
    ('http://192.168.0.2','1234-ABCD-5678','a'*64),
    ('https://user:pass@example.com','1234-ABCD-5678','a'*64),
    ('https://example.com/?token=x','1234-ABCD-5678','a'*64),
    ('https://example.com','invalid','a'*64),
    ('https://example.com','1234-ABCD-5678','invalid'),
])
def test_pairing_qr_rejects_unsafe_or_unpinned_targets(url,code,pin):
    with pytest.raises(ValueError):pairing_qr(url,code,pin,1)


def test_new_desktop_code_invalidates_only_previous_desktop_code(tmp_path):
    from factory.core.mobile_bridge.store import MobileStore
    store=MobileStore(str(tmp_path/'factory.db'))
    old=store.create_pairing_code(created_by='desktop-mobile-owner',now=1000)
    other=store.create_pairing_code(created_by='cli',now=1000)
    fresh=store.create_pairing_code(created_by='desktop-mobile-owner',replace_previous=True,now=1001)
    with pytest.raises(ValueError,match='already used'):store.complete_pairing(code=old['code'],name='old',now=1002)
    assert store.complete_pairing(code=other['code'],name='legacy',now=1002)['token']
    assert store.complete_pairing(code=fresh['code'],name='new',now=1002)['token']
    with pytest.raises(ValueError,match='already used'):store.complete_pairing(code=fresh['code'],name='replay',now=1003)
