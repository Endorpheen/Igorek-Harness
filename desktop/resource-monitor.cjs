const os = require('node:os');
const fs = require('node:fs');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const {execFile} = require('node:child_process');
const {promisify} = require('node:util');
const run = promisify(execFile);
const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
const percent = value => number(value) === null ? null : Math.min(100, value);
function parseGPUs(text) {
  return String(text).split(/\r?\n/).filter(line => line.trim()).slice(0, 8).flatMap(line => {
    const columns = line.match(/(?:"[^"]*"|[^,])+/g)?.map(value => value.trim().replace(/^"|"$/g, ''));
    if (columns?.length !== 7) return [];
    const [name, ...values] = columns;
    const [used, total, load, temperature, power, limit] = values.map(value => value !== '' ? number(Number(value)) : null);
    return [{name: name.slice(0, 160), used: used === null ? null : used * 1048576,
      total: total === null || total === 0 ? null : total * 1048576,
      load: percent(load), temperature, power, limit}];
  });
}
async function readGPUs() {
  try {
    const {stdout} = await run('nvidia-smi', ['--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw,power.limit', '--format=csv,noheader,nounits'],
      {timeout: 1500, maxBuffer: 65536, windowsHide: true});
    return parseGPUs(stdout);
  } catch { return []; }
}
function cpuTimes(cpus) {
  let idle = 0, total = 0;
  for (const cpu of cpus) { idle += cpu.times.idle; total += Object.values(cpu.times).reduce((a, b) => a + b, 0); }
  return {idle, total};
}
class ResourceSampler {
  constructor({system = os, read = fs.readFileSync, gpu = readGPUs, clock = Date.now} = {}) {
    this.system = system; this.read = read; this.gpu = gpu; this.clock = clock;
    this.previous = cpuTimes(system.cpus()); this.cached = null; this.pending = null;
  }
  async snapshot() {
    if (this.pending) return this.pending;
    if (this.cached && this.clock() - this.cached.at < 1500) return this.cached;
    this.pending = this.collect();
    try { this.cached = await this.pending; return this.cached; } finally { this.pending = null; }
  }
  async collect() {
    const current = cpuTimes(this.system.cpus()), delta = current.total - this.previous.total;
    const cpu = delta > 0 ? percent(100 * (1 - (current.idle - this.previous.idle) / delta)) : null;
    this.previous = current;
    const total = number(this.system.totalmem()); let available = number(this.system.freemem());
    if (this.system.platform() === 'linux') {
      try {
        const value = this.read('/proc/meminfo', 'utf8').match(/^MemAvailable:\s+(\d+)\s+kB$/m);
        if (value) available = Number(value[1]) * 1024;
      } catch { /* Other platforms and restricted hosts use OS free-memory data. */ }
    }
    return {at: this.clock(), cpu, ram: {total, used: total === null || available === null ? null : Math.max(0, total - Math.min(total, available))}, gpus: await this.gpu().catch(() => [])};
  }
}
class ResourceMonitorSettings {
  constructor(file) {
    this.file = file; this.enabled = false;
    try {
      const stat = fs.lstatSync(file);
      if (!stat.isFile() || stat.isSymbolicLink() || stat.size > 1024) return;
      const value = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (value.version === 1 && typeof value.enabled === 'boolean') this.enabled = value.enabled;
    } catch { /* Missing or invalid preferences leave the monitor disabled. */ }
  }
  state() { return {enabled: this.enabled}; }
  set(enabled) {
    if (typeof enabled !== 'boolean') throw Error('invalid_monitor_setting');
    const directory = path.dirname(this.file), temporary = path.join(directory, '.resource-monitor-' + randomUUID());
    fs.mkdirSync(directory, {recursive: true, mode: 0o700});
    try {
      fs.writeFileSync(temporary, JSON.stringify({version: 1, enabled}), {mode: 0o600, flag: 'wx'});
      fs.renameSync(temporary, this.file);
    } finally { try { fs.unlinkSync(temporary); } catch {} }
    this.enabled = enabled; return this.state();
  }
}
module.exports = {ResourceSampler, ResourceMonitorSettings, parseGPUs};
