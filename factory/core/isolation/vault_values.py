"""Bounded host relay for append-only task-owned Vault records."""
import json

from .channel import MessageKind
from ..vault.agent_values import validate_request


class VaultStoreRelay:
    def __init__(self, writer, *, workdir, task_id, session_id, profile):
        self.writer = writer
        self.context = dict(workdir=workdir, task_id=task_id, session_id=session_id, profile=profile)
        self.replies = {}
        self.count = 0

    def handle_request(self, message):
        if message.kind is not MessageKind.VAULT_STORE_REQUEST:
            return None
        request_id, args = message.payload.get('request_id'), message.payload.get('args')
        if request_id in self.replies:
            return dict(self.replies[request_id])
        self.count += 1
        try:
            validate_request(args)
            if self.writer is None or self.count > 32:
                raise ValueError()
            result = self.writer.save(args, **self.context)
            if not isinstance(result, str) or len(result.encode()) > 4096:
                raise ValueError()
        except Exception:
            result = json.dumps({'ok': False, 'error': 'vault_store_refused'})
        reply = {'request_id': request_id, 'result': result}
        if self.count <= 32:
            self.replies[request_id] = reply
        return dict(reply)
