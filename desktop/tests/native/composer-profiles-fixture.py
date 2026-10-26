"""Serve the real chat UI against disposable profiles without model requests."""
import json
import os
from pathlib import Path
import ssl
import sys
import threading

repo = Path(__file__).resolve().parents[3]
root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=True)
os.environ.update(IGOREK_FACTORY_HOME=str(root), FACTORY_DATA_DIR=str(root), HOME=str(root / 'home'))
sys.path[:0] = [str(repo), str(repo / 'desktop')]

from vault_owner import OwnerVault
from factory.core.cli_chat import _append_entry, _session_path
from factory.core.mobile_bridge.server import BridgeConfig, MobileBridge, build_server
from factory.core.mobile_bridge.service import MobileService
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database
from factory.web_ui.server import _Server, _certificate_paths

initialize_database(get_db_path())
owner = OwnerVault(root)
owner.service.create('composer-fixture-password')
saved = {}
for name in ['cloud', 'local']:
    saved[name] = owner.providers.save(name, name + '-fixture-model', 'http://127.0.0.1:9/v1', 'openai', '')
    owner.service.check_provider_profile(saved[name]['credential_id'], lambda *_: {'status': 'verified', 'reason': 'fixture'})
service = MobileService(profile='cloud', workdir=str(root))


class Agent:
    control_lock = threading.RLock()
    active = 'cloud'

    def status(self):
        return {'profile': self.active, 'running': True}

    def start(self, identity):
        profile = next(p for p in owner.providers.list_profiles() if p['credential_id'] == identity)
        self.active = service.profile = profile['config']['name']
        folder = root / 'profiles'
        folder.mkdir(exist_ok=True)
        (folder / (self.active + '.json')).write_text(json.dumps(profile['config']))
        return self.status()

    def close(self):
        pass


agent = Agent()
owner.agent = agent
owner.browser_service = service
service.available_profiles = lambda: [agent.active]
def fixture_profile_control(operation, name):
    result = owner.mobile_profile_control(operation, name)
    if operation == 'catalog':
        for profile in result.get('profiles', []):
            profile['models'] = ['gpt-6.1-sol', 'gpt-6.1-terra'] if profile['name'] == 'cloud' else []
    return result


service.profile_control = fixture_profile_control
agent.start(saved['cloud']['credential_id'])
sid = service.create_session(profile='cloud', cwd=str(root))['session_id']
_append_entry(_session_path(sid), 'user', 'Retained fixture history')
_append_entry(_session_path(sid), 'assistant', 'Retained fixture response')
bridge = MobileBridge(config=BridgeConfig(profile='cloud', workdir=str(root)))
bridge.service = service
mobile = build_server(bridge, host='127.0.0.1', port=0)
web = _Server(0, f'http://127.0.0.1:{mobile.server_address[1]}')
cert, key, _ = _certificate_paths()
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain(str(cert), str(key))
web.enable_tls(context)
threads = []
for server in [mobile, web]:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    threads.append(thread)
print(json.dumps({'origin': f'https://127.0.0.1:{web.server_address[1]}', 'cert': str(cert),
    'session': sid, 'pairing': bridge.store.create_pairing_code()['code']}), flush=True)
try:
    sys.stdin.readline()
finally:
    for server in [web, mobile]:
        server.shutdown()
        server.server_close()
    owner.close()
