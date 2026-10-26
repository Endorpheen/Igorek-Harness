const { createHash } = require('node:crypto');
const fs = require('node:fs/promises');
const { join } = require('node:path');
const { fetch: updateFetch } = require('undici');

const REPOSITORY = 'Endorpheen/Igorek-Harness';
const RELEASES = `https://api.github.com/repos/${REPOSITORY}/releases/latest`;
const MAX_DOWNLOAD = 2 * 1024 * 1024 * 1024;
function version(value) {
  if (typeof value !== 'string' || !/^v?\d{1,6}\.\d{1,6}\.\d{1,6}$/.test(value)) throw Error('invalid_version');
  return value.replace(/^v/, '').split('.').map(Number);
}
function newer(candidate, current) {
  const a = version(candidate), b = version(current);
  for (let i = 0; i < 3; i++) { if (a[i] !== b[i]) return a[i] > b[i]; }
  return false;
}
function assetFor(release, platform, arch, portable = false, preview = false) {
  const tag = release.tag_name;
  const number = version(tag).join('.');
  if (release.draft || (release.prerelease && !preview) || arch !== 'x64') throw Error('unsupported_release');
  const name = platform === 'win32' ? `igorek-${number}-win-x64.exe`
    : platform === 'linux' ? portable ? `igorek-${number}-linux-x86_64.AppImage` : `igorek-${number}-linux-amd64.deb` : null;
  const assets = Array.isArray(release.assets) ? release.assets.filter(asset => asset.name === name) : [];
  if (assets.length !== 1) throw Error('platform_asset_missing');
  const asset = assets[0];
  const expected = `https://github.com/${REPOSITORY}/releases/download/${tag}/${name}`;
  if (asset.browser_download_url !== expected || !Number.isSafeInteger(asset.size)
      || asset.size < 1 || asset.size > MAX_DOWNLOAD || !/^sha256:[a-f0-9]{64}$/.test(asset.digest || ''))
    throw Error('unverified_release_asset');
  return Object.freeze({name, url: expected, size: asset.size, sha256: asset.digest.slice(7), portable,version:number});
}
function allowedRedirect(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && !url.username && !url.password && (!url.port || url.port === '443')
      && ['github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'].includes(url.hostname);
  } catch { return false; }
}
class ReleaseUpdates {
  constructor({current, cache, fetch = updateFetch, platform = process.platform, arch = process.arch, portable = false, preview = false}) {
    version(current); this.current = current; this.cache = cache; this.fetch = fetch;
    this.platform = platform; this.arch = arch; this.portable = portable; this.preview = preview; this.asset = null; this.busy = false;
  }
  async check() {
    if (this.busy) throw Error('update_busy');
    this.asset = null;
    const response = await this.fetch(this.preview?RELEASES.replace('/latest','?per_page=20'):RELEASES, {redirect:'error',credentials:'omit',signal:AbortSignal.timeout(20000),
      headers:{Accept:'application/vnd.github+json','User-Agent':'Igorek-Harness-updates','X-GitHub-Api-Version':'2022-11-28'}});
    if (response.status === 404) return {state:'no_release',current:this.current};
    if (response.status === 403 || response.status === 429) throw Error('release_check_rate_limited');
    if (!response.ok) throw Error('release_check_failed');
    const chunks = []; let length = 0;
    for await (const chunk of response.body) {
      const bytes = Buffer.from(chunk); length += bytes.length; chunks.push(bytes);
      if (length > 1024 * 1024) throw Error('release_metadata_too_large');
    }
    let release = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (this.preview) {
      if (!Array.isArray(release) || release.length > 20) throw Error('invalid_release_metadata');
      release = release.filter(item=>{try{version(item.tag_name);return !item.draft;}catch{return false;}})
        .sort((a,b)=>newer(a.tag_name,b.tag_name)?-1:newer(b.tag_name,a.tag_name)?1:Number(a.prerelease)-Number(b.prerelease))[0];
      if (!release) return {state:'no_release',current:this.current};
    }
    if (!newer(release.tag_name, this.current)) return {state:'current',current:this.current};
    this.asset = assetFor(release, this.platform, this.arch, this.portable, this.preview);
    return {state:'available',current:this.current,version:version(release.tag_name).join('.'),
      name:this.asset.name,size:this.asset.size,url:`https://github.com/${REPOSITORY}/releases/tag/${release.tag_name}`};
  }
  async download(progress = () => {}) {
    const asset = this.asset;
    if (!asset || this.busy) throw Error('update_not_ready');
    this.busy = true;
    let directory;
    try {
      await fs.mkdir(this.cache,{recursive:true,mode:0o700});
      const info = await fs.lstat(this.cache);
      if (!info.isDirectory() || info.isSymbolicLink()) throw Error('invalid_update_cache');
      directory = await fs.mkdtemp(join(this.cache,'release-'));
      const destination = join(directory,asset.name);
      let url = asset.url, response;
      const signal = AbortSignal.timeout(10 * 60 * 1000);
      for (let redirects = 0; ; redirects++) {
        response = await this.fetch(url,{redirect:'manual',credentials:'omit',signal,
          headers:{'User-Agent':'Igorek-Harness-updates'}});
        if (![301,302,303,307,308].includes(response.status)) break;
        const next = new URL(response.headers.get('location') || '',url).href;
        await response.body?.cancel();
        if (redirects >= 4 || !allowedRedirect(next)) throw Error('update_redirect_denied');
        url = next;
      }
      if (!response.ok || !response.body) throw Error('update_download_failed');
      const handle = await fs.open(destination,'wx',0o600), hash = createHash('sha256');
      let length = 0;
      try {
        for await (const chunk of response.body) {
          const bytes = Buffer.from(chunk); length += bytes.length;
          if (length > asset.size) throw Error('update_size_mismatch');
          hash.update(bytes); await handle.writeFile(bytes); progress(length / asset.size);
        }
        if (length !== asset.size || hash.digest('hex') !== asset.sha256) throw Error('update_checksum_mismatch');
        await handle.sync();
      } finally { await handle.close(); }
      if (asset.portable) await fs.chmod(destination,0o700);
      return {path:destination,portable:asset.portable,size:asset.size,sha256:asset.sha256,version:asset.version};
    } catch (error) {
      if (directory) await fs.rm(directory,{recursive:true,force:true});
      throw error;
    } finally { this.busy = false; }
  }
}
module.exports = {ReleaseUpdates, assetFor, newer, allowedRedirect, REPOSITORY};
