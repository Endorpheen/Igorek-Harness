"""Local, short-lived desktop pairing QR with a pinned HTTPS identity."""
from io import BytesIO
import base64
import json
import re
from urllib.parse import urlsplit


def pairing_qr(url, code, fingerprint, expires_at):
    parsed=urlsplit(url)
    if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('','/')
            or not re.fullmatch(r'[A-F0-9]{4}(?:-[A-F0-9]{4}){2}',code)
            or not re.fullmatch(r'[A-Fa-f0-9]{64}',fingerprint) or type(expires_at) is not int):
        raise ValueError('Invalid pairing data')
    payload={'type':'igorek.desktop.pair','version':1,'url':url.rstrip('/'),'code':code,
        'fingerprint':fingerprint,'expires_at':expires_at,'name':'Десктоп'}
    text=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
    import qrcode
    from qrcode.image.svg import SvgPathFillImage
    qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,border=4,box_size=8)
    qr.add_data(text);qr.make(fit=True);stream=BytesIO();qr.make_image(image_factory=SvgPathFillImage).save(stream)
    return {'data_url':'data:image/svg+xml;base64,'+base64.b64encode(stream.getvalue()).decode(),
            'expires_at':expires_at,'url':payload['url']}
