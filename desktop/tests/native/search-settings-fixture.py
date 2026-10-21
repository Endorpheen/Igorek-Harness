"""Private synthetic owner process for native search settings acceptance."""
import io
import json
import sys
from pathlib import Path

bundle, home = map(Path, sys.argv[1:3])
sys.path[:0] = [str(bundle), str(bundle / 'packages')]
from vault_owner import OwnerVault
from web_tools import DesktopWebTools
import web_tools
from factory.core.cc_backend_tools import execute_tool, get_tool_schemas
from factory.core.web_tools_runtime import scoped_web_tools

KEY = 'test-only-search-key'
PASSWORD = 'test-only-password'
requests = []
class Opener:
    def open(self, request, timeout):
        assert any(value in {KEY, 'Bearer ' + KEY} for value in request.headers.values())
        assert KEY not in request.data.decode()
        requests.append(request.full_url)
        result = {'organic': [{'title': 'Native fixture', 'link': 'https://example.com/', 'snippet': 'fixture'}]} if 'serper.dev' in request.full_url else {'results': [{'title': 'Native fixture', 'url': 'https://example.com/', 'content': 'fixture', 'highlights': ['fixture']}]}
        return io.BytesIO(json.dumps(result).encode())
web_tools.urllib.request.build_opener = lambda *_: Opener()
owner = OwnerVault(home)
owner.service.create(PASSWORD)
profile = owner.providers.save('native-search', 'fixture-model', 'http://127.0.0.1:8081/v1', 'openai', '')
owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status': 'verified', 'reason': 'ok'})
try:
    for line in sys.stdin:
        request = json.loads(line)
        if request.get('action') == 'fixture_inspect':
            provider = DesktopWebTools(owner.lifecycle, profile['credential_id'], profile['config'])
            with scoped_web_tools(provider):
                names = {tool['name'] for tool in get_tool_schemas()}
                output = execute_tool('web_search', {'query': 'fixture'}, str(home))
            assert KEY not in output
            reply = {'id': request['id'], 'result': {'selection': owner.search_setup.status()['provider'],
                'search': 'web_search' in names, 'extract': 'web_extract' in names,
                'request_count': len(requests), 'result_ok': 'Native fixture' in output}}
        else:
            reply = owner.dispatch(request)
        print(json.dumps(reply), flush=True)
finally:
    owner.close()
