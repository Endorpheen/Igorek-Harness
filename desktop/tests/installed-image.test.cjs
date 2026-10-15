const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { join } = require('node:path');
const { tmpdir } = require('node:os');
const { installedImage } = require('../installed-image.cjs');

test('installed package is selected only with regular image and manifest', async () => {
  const root = await fs.mkdtemp(join(tmpdir(), 'igorek-installed-image-'));
  const image = join(root, 'base.qcow2');
  try {
    assert.equal(await installedImage(root), null);
    await fs.writeFile(image, 'fixture');
    assert.equal(await installedImage(root), null);
    await fs.writeFile(image + '.manifest.json', '{}');
    assert.equal(await installedImage(root), image);
    await fs.rename(image, image + '.real');
    await fs.symlink(image + '.real', image);
    assert.equal(await installedImage(root), null);
    await fs.unlink(image);
    await fs.rename(image + '.real', image);
    await fs.unlink(image + '.manifest.json');
    await fs.symlink(image, image + '.manifest.json');
    assert.equal(await installedImage(root), null);
    await fs.symlink(root, root + '-link');
    assert.equal(await installedImage(root + '-link'), null);
  } finally {
    await fs.rm(root + '-link', { force: true });
    await fs.rm(root, { recursive: true, force: true });
  }
});
