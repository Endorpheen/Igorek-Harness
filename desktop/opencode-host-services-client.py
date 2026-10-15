"""MCP stdio transport to one explicitly delegated Unix capability."""
import http.client
import json
from pathlib import Path
import socket
import sys


class Connection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(100)
        self.sock.connect('/run/igorek-host-services.sock')


def main():
    token = Path('/run/igorek-host-services-token').read_text()
    for line in sys.stdin:
        identity = None
        try:
            if len(line) > 65536:
                raise ValueError('request_too_large')
            message = json.loads(line)
            identity = message.get('id')
            connection = Connection('localhost', timeout=100)
            try:
                connection.request('POST', '/mcp', line.encode(), {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
                response = connection.getresponse()
                raw = response.read(2 * 1024 * 1024 + 1)
                if response.status not in (200, 202) or len(raw) > 2 * 1024 * 1024:
                    raise ValueError('host_services_access_denied_or_unavailable')
                if identity is not None:
                    reply = json.loads(raw)
            finally:
                connection.close()
        except Exception:
            reply = {'jsonrpc': '2.0', 'id': identity, 'error': {'code': -32000, 'message': 'Host services unavailable or access revoked'}}
        if identity is not None:
            print(json.dumps(reply, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
