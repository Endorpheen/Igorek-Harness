const { test } = require('node:test');
const assert = require('node:assert/strict');
const { parseLink } = require('../../factory/web_ui/static/github-reviews.js');

test('normalizes pull request files and commits links to pull request url', (t) => {
  assert.equal(parseLink('https://github.com/example/project/pull/7/files'), 'https://github.com/example/project/pull/7');
  assert.equal(parseLink('https://github.com/example/project/pull/7/commits'), 'https://github.com/example/project/pull/7');
  assert.equal(parseLink('https://github.com/example/project/pull/7/commits/'), 'https://github.com/example/project/pull/7');
  assert.equal(parseLink('https://github.com/example/project/pull/7/files/'), 'https://github.com/example/project/pull/7');
});

test('removes query and hash from pull request url', (t) => {
  assert.equal(parseLink('https://github.com/example/project/pull/7?tab=files#diff'), 'https://github.com/example/project/pull/7');
});

test('returns empty string for unsafe or unsupported urls', (t) => {
  assert.equal(parseLink('http://github.com/example/project/pull/7'), '');
  assert.equal(parseLink('https://github.com.evil.test/example/project/pull/7'), '');
  assert.equal(parseLink('https://user:pass@github.com/example/project/pull/7'), '');
});

test('returns empty string for invalid or unsupported pull request links', (t) => {
  assert.equal(parseLink(''), '');
  assert.equal(parseLink('not a URL'), '');
  assert.equal(parseLink('https://github.com/example/project/issues/7'), '');
  assert.equal(parseLink('https://github.com/example/project/pull/0'), '');
  assert.equal(parseLink('https://github.com/example/project/pull/7/unknown'), '');
});
