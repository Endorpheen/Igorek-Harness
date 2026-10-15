(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.IgorekToolGroups = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  const task = item => String(item.task_id || item.taskId || '');
  const call = item => String(item.tool_use_id || item.toolUseId || '');
  const key = (owner, id) => owner && id ? owner + '\0' + id : '';
  const technical = item => item.type === 'status' && ['task.paused', 'task.requeued_approval'].includes(item.key);
  const failed = item => ['error', 'fail', 'failed', 'exit'].includes(item.status) || item.is_error === true;
  function project(items) {
    const calls = new Map(), decisions = new Map(), attached = new Set();
    for (const item of items) if (item.type === 'tool') {
      const id = key(task(item), call(item));
      if (id) calls.set(id, calls.has(id) ? null : item);
    }
    for (const item of items) if (item.type === 'approval' && item.resolved && item.kind !== 'user_input') {
      const id = key(task(item), String(item.tool_use_id || item.request_id || ''));
      if (!id || !calls.get(id)) continue;
      if (!decisions.has(id)) decisions.set(id, []);
      decisions.get(id).push(item); attached.add(item.id);
    }
    const result = []; let group = null;
    const flush = () => {
      if (!group) return;
      if (group.rows.every(row => row.item.type === 'status')) result.push(...group.rows.map(row => row.item));
      else result.push(group);
      group = null;
    };
    for (const item of items) {
      if (attached.has(item.id)) continue;
      const eligible = item.type === 'tool' || technical(item) || (item.type === 'approval' && item.resolved && item.kind !== 'user_input');
      if (!eligible) { flush(); result.push(item); continue; }
      const owner = task(item);
      if (group && owner !== group.task_id && (owner || group.task_id)) flush();
      if (!group) group = { id: 'activity-' + item.id, type: 'tool_group', task_id: owner, rows: [] };
      if (!group.task_id && owner) group.task_id = owner;
      group.rows.push({ id: item.id, item, approvals: decisions.get(key(owner, call(item))) || [] });
    }
    flush(); return result;
  }
  function stats(group) {
    const tools = group.rows.filter(row => row.item.type === 'tool');
    const approvals = group.rows.flatMap(row => row.item.type === 'approval' ? [row.item] : row.approvals);
    const running = tools.filter(row => ['running', 'pending', 'queued'].includes(row.item.status));
    const rejected = approvals.filter(item => ['reject', 'cancel'].includes(item.resolved));
    const errors = group.rows.filter(row => failed(row.item) ||
      (row.item.type === 'approval' && ['reject', 'cancel'].includes(row.item.resolved)) ||
      row.approvals.some(item => ['reject', 'cancel'].includes(item.resolved)));
    const stopped = tools.filter(row => ['stopped', 'cancelled'].includes(row.item.status));
    const done = tools.filter(row => ['done', 'ok', 'success', 'completed'].includes(row.item.status || 'done') && !failed(row.item) && !row.approvals.some(item => ['reject', 'cancel'].includes(item.resolved)));
    const unknown = tools.filter(row => !done.includes(row) && !errors.includes(row) && !running.includes(row) && !stopped.includes(row));
    return { count: tools.length || approvals.length, tools: tools.length, permissions: approvals.length, running: running.length,
      errors: errors.length, successful: done.length, unknown: unknown.length, failed: tools.filter(row => failed(row.item)).length, stopped: stopped.length,
      done: tools.length > 0 && done.length === tools.length && !rejected.length,
      preview: (errors[0] || running[running.length - 1])?.item || null };
  }
  return { project, stats, task, call };
});
