import http.client
import json
import socket
import ssl
import time
from urllib.parse import urlsplit

MAX_RESPONSE = 1024 * 1024

class ProbeFailure(Exception):
    def __init__(self, reason, http_status=None):
        self.reason = reason
        self.http_status = http_status

def post_json(url, payload, headers):
    target = urlsplit(url)
    connection = (http.client.HTTPSConnection(target.hostname, target.port, timeout=10,
                  context=ssl.create_default_context()) if target.scheme == "https"
                  else http.client.HTTPConnection(target.hostname, target.port, timeout=10))
    response = None
    try:
        connection.request("POST", target.path or "/", json.dumps(payload).encode(),
                           {"Content-Type": "application/json", **headers})
        response = connection.getresponse()
        if response.status != 200:
            code = response.status
            reason = "authentication" if code in {401, 403} else "rate_limit" if code == 429 else "http_error"
            raise ProbeFailure(reason, code)
        deadline = time.monotonic() + 10
        chunks, length = [], 0
        while True:
            if time.monotonic() >= deadline:
                raise ProbeFailure("timeout")
            chunk = response.read1(min(65536, MAX_RESPONSE + 1 - length))
            if not chunk:
                break
            chunks.append(chunk); length += len(chunk)
            if length > MAX_RESPONSE:
                raise ProbeFailure("invalid_response")
        result = json.loads(b"".join(chunks))
        if not isinstance(result, dict):
            raise ProbeFailure("invalid_response")
        return result
    except (socket.timeout, TimeoutError):
        raise ProbeFailure("timeout") from None
    except (ssl.SSLError, OSError, http.client.HTTPException):
        raise ProbeFailure("network") from None
    except (ValueError, UnicodeError):
        raise ProbeFailure("invalid_response") from None
    finally:
        if response is not None:
            response.close()
        connection.close()


