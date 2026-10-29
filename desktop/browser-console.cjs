'use strict';

const controllers = new WeakMap();
const ACTIONS = new Set(['console', 'console_messages', 'console_clear']);

function validConsole(fields) {
  if (!fields || typeof fields !== 'object' || Array.isArray(fields) || !ACTIONS.has(fields.action)) return false;
  const allowed = fields.action === 'console' ? ['action', 'expression', 'timeout_ms']
    : fields.action === 'console_messages' ? ['action', 'limit', 'after', 'level'] : ['action'];
  if (Object.keys(fields).some(key => !allowed.includes(key))) return false;
  if (fields.action === 'console') {
    return typeof fields.expression === 'string' && fields.expression.trim().length > 0
      && fields.expression.length <= 16000 && (!('timeout_ms' in fields)
        || Number.isInteger(fields.timeout_ms) && fields.timeout_ms >= 100 && fields.timeout_ms <= 5000);
  }
  if ('limit' in fields && (!Number.isInteger(fields.limit) || fields.limit < 1 || fields.limit > 200)) return false;
  if ('after' in fields && (!Number.isSafeInteger(fields.after) || fields.after < 0)) return false;
  if ('level' in fields && !['debug', 'info', 'warning', 'error'].includes(fields.level)) return false;
  return true;
}

// This function is serialized into the page. No Node or owner bridge is injected.
function captureValue(input) {
  const seen = new WeakSet();
  let remaining = 16000, nodes = 200;
  function text(value, limit = 4000) {
    const size = Math.max(0, Math.min(limit, remaining));
    const result = value.slice(0, size);
    remaining -= result.length;
    return result + (value.length > size ? '…' : '');
  }
  function visit(value, depth) {
    if (remaining <= 0 || nodes-- <= 0) return '[output limit]';
    if (value === null || typeof value === 'boolean') return value;
    if (typeof value === 'string') return text(value);
    if (typeof value === 'number') return Number.isFinite(value) ? value : String(value);
    if (typeof value === 'undefined') return '[undefined]';
    if (typeof value === 'bigint') return text(String(value) + 'n');
    if (typeof value === 'symbol' || typeof value === 'function') return text(String(value), 500);
    if (seen.has(value)) return '[circular]';
    if (depth >= 4) return '[depth limit]';
    seen.add(value);
    if (typeof Element !== 'undefined' && value instanceof Element) {
      return {node: text(value.tagName, 100), id: text(value.id, 500),
        text: text(value.textContent || '', 1000)};
    }
    if (typeof DOMRectReadOnly !== 'undefined' && value instanceof DOMRectReadOnly) {
      return {x: value.x, y: value.y, width: value.width, height: value.height};
    }
    const result = Array.isArray(value) ? [] : Object.create(null);
    let count = 0;
    for (const key in value) {
      if (!Object.prototype.hasOwnProperty.call(value, key)) continue;
      if (count++ >= 50 || remaining <= 0 || nodes <= 0) {
        if (Array.isArray(result)) result.push('[item limit]');
        else result['…'] = '[item limit]';
        break;
      }
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (!descriptor) continue;
      const name = text(key, 200);
      result[name] = 'value' in descriptor ? visit(descriptor.value, depth + 1) : '[accessor]';
    }
    return result;
  }
  return visit(input, 0);
}

function evaluationScript(expression) {
  return `(async()=>{const capture=${captureValue.toString()};try{const value=await(0,eval)(${JSON.stringify(expression)});return JSON.stringify({ok:true,value:capture(value)});}catch(error){return JSON.stringify({ok:false,exception:{name:String(error?.name||'Error').slice(0,200),message:String(error?.message||error).slice(0,4000)}});}})()`;
}

class BrowserConsole {
  constructor(contents, {Journal} = {}) {
    this.contents = contents;
    const Type = Journal || require('./browser-console-journal.cjs').ConsoleJournal;
    this.journal = new Type();
    this.epoch = 0;
    this.disposed = false;
    this.busy = false;
    this.serial = 0;
    this.onMessage = (...args) => {
      const [details, level, message, line, sourceId] = args;
      if (this.disposed) return;
      this.journal.push(details && typeof details.message === 'string' ? details
        : {level, message, lineNumber: line, sourceId});
    };
    this.onNavigation = (details, _url, _inPlace, isMainFrame) => {
      if (details?.isMainFrame === false || isMainFrame === false) return;
      this.epoch++;
      this.journal.clear();
    };
    this.onInPage = (details, _url, isMainFrame) => {
      if (details?.isMainFrame === false || isMainFrame === false) return;
      this.epoch++;
      this.journal.clear();
    };
    this.onDestroyed = () => this.dispose();
    contents.on('console-message', this.onMessage);
    contents.on('did-start-navigation', this.onNavigation);
    contents.on('did-navigate-in-page', this.onInPage);
    contents.once('destroyed', this.onDestroyed);
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.epoch++;
    this.journal.clear();
    for (const [event, listener] of [['console-message', this.onMessage],
      ['did-start-navigation', this.onNavigation], ['did-navigate-in-page', this.onInPage],
      ['destroyed', this.onDestroyed]]) this.contents.removeListener(event, listener);
  }

  denied(check, epoch) {
    const reason = check();
    if (reason) return {error: reason};
    if (this.disposed || this.contents.isDestroyed() || epoch !== this.epoch) return {error: 'browser_changed'};
    return null;
  }

  async run(fields, check = () => null) {
    if (!validConsole(fields)) return {error: 'invalid_request'};
    const epoch = this.epoch;
    const denied = this.denied(check, epoch);
    if (denied) return denied;
    if (fields.action === 'console_messages') {
      const {action, ...options} = fields;
      return {...this.journal.read(options), document_epoch: epoch};
    }
    if (fields.action === 'console_clear') {
      this.journal.clear();
      return {cleared: true, document_epoch: epoch};
    }
    if (this.busy) return {error: 'console_busy'};
    const debug = this.contents.debugger;
    if (!debug || debug.isAttached()) return {error: 'console_debugger_busy'};
    if (this.contents.isLoadingMainFrame?.()) return {error: 'browser_loading'};
    this.busy = true;
    let owned = false, timer, timedOut = false;
    const started = Date.now(), timeout = fields.timeout_ms || 3000;
    const group = 'igorek-console-' + (++this.serial);
    const detached = () => { owned = false; };
    try {
      debug.attach('1.3'); owned = true;
      debug.on('detach', detached);
      const request = debug.sendCommand('Runtime.evaluate', {
        expression: evaluationScript(fields.expression), returnByValue: true,
        awaitPromise: true, timeout, userGesture: false, objectGroup: group,
      });
      const response = await Promise.race([request, new Promise((_, reject) => {
        timer = setTimeout(() => { timedOut = true; reject(Error('console_timeout')); }, timeout + 100);
      })]);
      const stale = this.denied(check, epoch);
      if (stale) return stale;
      if (response.exceptionDetails) {
        const description = response.exceptionDetails.exception?.description || response.exceptionDetails.text || 'JavaScript evaluation failed';
        return {error: /timed out|terminated/i.test(description) ? 'console_timeout' : 'console_evaluation_error',
          message: String(description).slice(0, 4000)};
      }
      const raw = response.result?.value;
      if (typeof raw !== 'string' || raw.length > 65536) return {error: 'console_result_unavailable'};
      const result = JSON.parse(raw);
      if (!result || typeof result.ok !== 'boolean') return {error: 'console_result_unavailable'};
      return result.ok ? {value: result.value, elapsed_ms: Date.now() - started, document_epoch: epoch}
        : {error: 'console_evaluation_error', exception: result.exception, document_epoch: epoch};
    } catch (error) {
      const stale = this.denied(check, epoch);
      if (stale) return stale;
      // Chromium may report its execution deadline as an internal inspector error.
      timedOut ||= /Internal error/i.test(String(error?.message)) && Date.now() - started >= timeout - 10;
      const timeoutFailure = timedOut || /timed out|terminated/i.test(String(error?.message));
      return {error: timeoutFailure ? 'console_timeout' : 'console_evaluation_failed'};
    } finally {
      clearTimeout(timer);
      if (owned && debug.isAttached()) {
        const cleanup = async (method, args) => {
          let deadline;
          try { await Promise.race([debug.sendCommand(method, args).catch(() => {}),
            new Promise(resolve => { deadline = setTimeout(resolve, 500); })]); } catch {}
          finally { clearTimeout(deadline); }
        };
        if (timedOut) await cleanup('Runtime.terminateExecution');
        await cleanup('Runtime.releaseObjectGroup', {objectGroup: group});
        try { debug.detach(); } catch {}
      }
      debug.removeListener('detach', detached);
      this.busy = false;
    }
  }
}

function consoleFor(contents) {
  let controller = controllers.get(contents);
  if (!controller) { controller = new BrowserConsole(contents); controllers.set(contents, controller); }
  return controller;
}

module.exports = {BrowserConsole, consoleFor, validConsole, evaluationScript, captureValue};
