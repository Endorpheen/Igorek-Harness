const { access, lstat } = require('node:fs/promises');
const { constants } = require('node:fs');
const { join } = require('node:path');

async function installedImage(directory = '/usr/share/igorek-desktop-images') {
  const source = join(directory, 'base.qcow2');
  try {
    if (!(await lstat(directory)).isDirectory()) return null;
    for (const path of [source, source + '.manifest.json']) {
      if (!(await lstat(path)).isFile()) return null;
      await access(path, constants.R_OK);
    }
    return source;
  } catch { return null; }
}

module.exports = { installedImage };
