const { isDeepStrictEqual } = require('node:util');

class SessionMcp {
  constructor({ request, trusted, settings }) {
    this.request = request; this.trusted = trusted; this.settings = settings;
    this.owner = null; this.taskId = null;
    this.pending = Promise.resolve();
  }
  dispatch(event, action, fields = {}) {
    const next = this.pending.then(() => this._dispatch(event, action, fields));
    this.pending = next.catch(() => {});
    return next;
  }
  async _dispatch(event, action, fields = {}) {
    if (!this.trusted(event)) throw new Error('Доступ запрещён.');
    const shape = { list: [], start: ['server', 'credential_id', 'message'], status: [], clear: [], settings: [], approve: ['request_id', 'decision'] };
    if (!Object.hasOwn(shape, action) || !fields || typeof fields !== 'object' || Array.isArray(fields)
        || Object.keys(fields).length !== shape[action].length || shape[action].some(key => typeof fields[key] !== 'string' || !fields[key].trim() || fields[key].length > 1024))
      return { error: 'invalid_request' };
    if (action === 'settings') { this.settings(); return { opened: true }; }
    const call = async (name, payload) => {
      const reply = await this.request(name, payload);
      if (!this.trusted(event)) throw new Error('Доступ запрещён.');
      return reply;
    };
    if (action === 'list') {
      const reply = await call('mcp_access');
      if (reply.error) return { error: reply.error };
      const value = reply.result.mcp_access;
      const gmail = await call('gmail_status');
      if (gmail.error) return { error: gmail.error };
      const entries = [];
      for (const server of value.servers) {
        if (server.disabled || (server.id === 'gmail' && !gmail.result.gmail.connected)) continue;
        for (const profile of value.profiles) {
          const grant = value.grants?.[server.id]?.[profile.credential_id];
          if (profile.status !== 'verified' || profile.config.api_format !== 'openai' || !grant
              || !isDeepStrictEqual(grant.config, profile.config) || !['allow', 'local_only'].includes(grant.access)) continue;
          let local = false;
          try { local = ['localhost', '127.0.0.1', '[::1]'].includes(new URL(profile.config.base_url).hostname); } catch {}
          if (grant.access === 'local_only' && !local) continue;
          entries.push({ server: server.id, credential_id: profile.credential_id, profile: profile.config.name,
            access: grant.access, local, readonly: ['gmail', 'openai_docs'].includes(server.id) });
        }
      }
      return { entries };
    }
    if (this.owner && this.owner !== event.sender) return { error: 'task_other_window' };
    if (action === 'start') {
      const reply = await call('private_mcp_start', fields);
      if (reply.error) return { error: reply.error };
      this.owner = event.sender; this.taskId = reply.result.private_mcp.task_id;
      return { job: reply.result.private_mcp };
    }
    if (!this.owner) return { job: { running: false, result: '', error: '' } };
    if (action === 'approve' && !['approve', 'reject'].includes(fields.decision)) return { error: 'invalid_request' };
    const reply = await call('private_mcp_status');
    if (reply.error) return { error: reply.error };
    if (reply.result.private_mcp.task_id !== this.taskId) {
      this.owner = this.taskId = null;
      return { error: 'task_replaced' };
    }
    if (action === 'approve') {
      const approved = await call('private_mcp_approve', fields);
      return approved.error ? { error: approved.error } : { job: approved.result.private_mcp };
    }
    if (action === 'clear') {
      const cleared = await call('private_mcp_clear');
      if (cleared.error) return { error: cleared.error };
      this.owner = this.taskId = null;
      return { job: { running: false, result: '', error: '' } };
    }
    return { job: reply.result.private_mcp };
  }
  release(sender) {
    if (this.owner !== sender) return;
    this.owner = this.taskId = null;
    this.request('private_mcp_clear').catch(() => {});
  }
}
module.exports = { SessionMcp };
