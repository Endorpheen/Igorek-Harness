"""Public-client ChatGPT plan authentication; credentials stay in the host Vault."""
from __future__ import annotations

import base64
import hmac
import json
import math
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
JWKS = ISSUER + "/.well-known/jwks.json"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
PLAN_SCOPES = {"chatgpt.tokens.use.direct", "resource.invoke", "offline_access"}


class ChatGPTAuthError(ValueError):
    def __init__(self, code="chatgpt_auth_failed"):
        self.code = code
        super().__init__(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(url, *, form=None, token=None, empty=False):
    """Never redirect bearer credentials or include provider bodies in errors."""
    headers = {"Accept": "application/json", "User-Agent": "IgorekDesktop/ChatGPT"}
    data = None
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        with urllib.request.build_opener(NoRedirect).open(
            urllib.request.Request(url, data=data, headers=headers), timeout=20
        ) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ChatGPTAuthError("chatgpt_invalid_response")
        if empty and not raw:
            return {}
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except urllib.error.HTTPError as error:
        code = "chatgpt_auth_failed" if error.code in {400, 401, 403} else "chatgpt_network"
        try:
            value = json.loads(error.read(65536)).get("error")
            if isinstance(value, dict):
                value = value.get("code")
            if value == "invalid_grant":
                code = "chatgpt_login_required"
        except Exception:
            pass
        raise ChatGPTAuthError(code) from None
    except (OSError, urllib.error.URLError):
        raise ChatGPTAuthError("chatgpt_network") from None
    except (ValueError, TypeError, KeyError):
        raise ChatGPTAuthError("chatgpt_invalid_response") from None


def _decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_identity(token, client_id, nonce, *, fetch=None, now=None):
    """Validate signature and OIDC claims before trusting any account identity."""
    fetch = fetch or request
    now = time.time() if now is None else now
    try:
        head, body, signature = token.split(".")
        header, claims = json.loads(_decode(head)), json.loads(_decode(body))
        alg = header["alg"]
        if alg not in {"RS256", "ES256"}:
            raise ValueError()
        keys = fetch(JWKS)["keys"]
        candidates = [k for k in keys if k.get("kid") == header.get("kid")
                      and k.get("use", "sig") == "sig" and k.get("alg", alg) == alg]
        if len(candidates) != 1:
            raise ValueError()
        key = candidates[0]
        signed = (head + "." + body).encode("ascii")
        if alg == "RS256" and key["kty"] == "RSA":
            public = rsa.RSAPublicNumbers(int.from_bytes(_decode(key["e"])),
                                         int.from_bytes(_decode(key["n"]))).public_key()
            public.verify(_decode(signature), signed, padding.PKCS1v15(), hashes.SHA256())
        elif alg == "ES256" and key["kty"] == "EC" and key["crv"] == "P-256":
            raw = _decode(signature)
            if len(raw) != 64:
                raise ValueError()
            public = ec.EllipticCurvePublicNumbers(int.from_bytes(_decode(key["x"])),
                int.from_bytes(_decode(key["y"])), ec.SECP256R1()).public_key()
            public.verify(encode_dss_signature(int.from_bytes(raw[:32]), int.from_bytes(raw[32:])),
                          signed, ec.ECDSA(hashes.SHA256()))
        else:
            raise ValueError()
        aud = claims["aud"]
        if not (aud == client_id or isinstance(aud, list) and client_id in aud):
            raise ValueError()
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != client_id:
            raise ValueError()
        if claims.get("azp", client_id) != client_id or claims["iss"] != ISSUER:
            raise ValueError()
        if not isinstance(claims["exp"], (int, float)) or not math.isfinite(claims['exp']) or claims["exp"] <= now:
            raise ValueError()
        if claims.get("nbf", 0) > now + 30 or claims.get("iat", now) > now + 30:
            raise ValueError()
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise ValueError()
        if not isinstance(claims.get("nonce"), str) or not hmac.compare_digest(claims["nonce"], nonce):
            raise ValueError()
        return claims
    except ChatGPTAuthError:
        raise
    except Exception:
        raise ChatGPTAuthError("chatgpt_identity_invalid") from None


def token_set(response, previous=None, *, now=None):
    now = time.time() if now is None else now
    previous = previous or {}
    try:
        scopes = set(response["scope"].split())
        if not PLAN_SCOPES <= scopes:
            raise ChatGPTAuthError("chatgpt_plan_permission_missing")
        if str(response["token_type"]).lower() != "bearer":
            raise ValueError()
        for field in ("access_token", "refresh_token"):
            value = response[field]
            if not isinstance(value, str) or not value or any(ord(c) < 33 for c in value):
                raise ValueError()
        lifetime = int(response["expires_in"])
        if not 0 < lifetime <= 86400:
            raise ValueError()
        return {**previous, "access_token": response["access_token"],
            "refresh_token": response["refresh_token"],
            "id_token": response.get("id_token") or previous.get("id_token", ""),
            "scopes": sorted(scopes), "expires_at": now + lifetime,
            "earliest_refresh_at": float(response.get("earliest_refresh_at") or 0),
            "auth_error": ""}
    except ChatGPTAuthError:
        raise
    except Exception:
        raise ChatGPTAuthError("chatgpt_invalid_response") from None


def refresh(saved, *, fetch=None, now=None):
    fetch = fetch or request
    now = time.time() if now is None else now
    if not saved.get("access_token") or saved.get("auth_error"):
        raise ChatGPTAuthError(saved.get("auth_error") or "chatgpt_login_required")
    if saved.get("expires_at", 0) > now + 60:
        return saved
    if saved.get("earliest_refresh_at", 0) > now:
        if saved.get("expires_at", 0) <= now:
            raise ChatGPTAuthError("chatgpt_login_required")
        return saved
    response = fetch(TOKEN, form={"grant_type": "refresh_token", "client_id": saved["client_id"],
                                 "refresh_token": saved["refresh_token"], "resource": RESOURCE})
    return token_set(response, saved, now=now)


def revoke(saved, *, fetch=None):
    fetch = fetch or request
    if not saved.get("refresh_token"):
        return True
    discovery = fetch(ISSUER + "/.well-known/openid-configuration")
    endpoint = discovery.get("revocation_endpoint", "")
    url = urlsplit(endpoint)
    if (discovery.get("issuer") != ISSUER or url.scheme != "https" or url.netloc != "auth.openai.com"
            or url.query or url.fragment or not url.path.startswith("/")):
        raise ChatGPTAuthError("chatgpt_invalid_response")
    fetch(endpoint, form={"token": saved["refresh_token"], "token_type_hint": "refresh_token",
                         "client_id": saved["client_id"]}, empty=True)
    return True


def models(token, *, fetch=None):
    fetch = fetch or request
    result = fetch(RESOURCE + "/models", token=token)
    if not isinstance(result.get("models"), list):
        raise ChatGPTAuthError("chatgpt_invalid_response")
    items, seen = [], set()
    for item in result["models"]:
        if not isinstance(item, dict) or item.get("visibility") != "list":
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug or len(slug) > 256 or any(ord(c) < 33 for c in slug) or slug in seen:
            continue
        seen.add(slug)
        items.append({"id": slug, "name": str(item.get("display_name") or slug)[:256],
                      "api_format": "openai-responses", "free": False})
    if not items:
        raise ChatGPTAuthError("chatgpt_no_models")
    return {"models": items, "source": "live", "note": "Модели вашего аккаунта ChatGPT. Запросы используют общие лимиты подписки; доступ проверяется отдельно."}
