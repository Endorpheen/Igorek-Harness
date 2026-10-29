'use strict';

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { ConsoleJournal } = require('../browser-console-journal.cjs');

const LONG_MESSAGE = 'abcdef0123456789abcdef0123456789';

function journalWithSize(maxEntries, maxMessageLength) {
  return new ConsoleJournal({ maxEntries, maxMessageLength });
}

test('levels are normalized on push', () => {
  const journal = journalWithSize();
  assert.strictEqual(journal.push({ level: 'warn', message: 'a' }).level, 'warning');
  assert.strictEqual(journal.push({ level: 'log', message: 'b' }).level, 'info');
  assert.strictEqual(journal.push({ level: 'debug', message: 'c' }).level, 'debug');
  assert.strictEqual(journal.push({ level: 'error', message: 'd' }).level, 'error');
  assert.strictEqual(journal.push({ level: 'unknown', message: 'e' }).level, 'info');
  assert.strictEqual(journal.push({ message: 'f' }).level, 'info');
  assert.strictEqual(journal.read().entries.length, 6);
});

test('Electron numeric levels are mapped', () => {
  const journal = journalWithSize();
  assert.strictEqual(journal.push({ level: 0, message: 'a' }).level, 'debug');
  assert.strictEqual(journal.push({ level: 1, message: 'b' }).level, 'info');
  assert.strictEqual(journal.push({ level: 2, message: 'c' }).level, 'warning');
  assert.strictEqual(journal.push({ level: 3, message: 'd' }).level, 'error');
});

test('push does not add records with non-string messages', () => {
  const journal = journalWithSize();
  assert.strictEqual(journal.push({ level: 'info', message: 'ok' }).seq, 1);
  assert.strictEqual(journal.push({ level: 'info', message: 123 }), null);
  assert.strictEqual(journal.push({ level: 'info' }), null);
  assert.strictEqual(journal.push(null), null);
  assert.strictEqual(journal.read().entries.length, 1);
  assert.strictEqual(journal.read().last_seq, 1);
});

test('long messages are truncated and marked as truncated', () => {
  const journal = journalWithSize(200, 10);
  const record = journal.push({ level: 'info', message: LONG_MESSAGE });
  assert.strictEqual(record.truncated, true);
  assert.strictEqual(record.message, LONG_MESSAGE.slice(0, 10));
  assert.strictEqual(journal.read().entries[0].truncated, true);
});

test('oldest records are evicted when the journal is full', () => {
  const journal = journalWithSize(3, 4000);
  for (let i = 1; i <= 5; i++) {
    journal.push({ level: 'info', message: `msg${i}` });
  }
  const { entries, dropped } = journal.read();
  assert.strictEqual(entries.length, 3);
  assert.strictEqual(entries[0].message, 'msg3');
  assert.strictEqual(entries[2].seq, 5);
  assert.strictEqual(dropped, 2);
});

test('read accepts limit, after and level filters', () => {
  const journal = journalWithSize(10, 4000);
  for (let i = 1; i <= 10; i++) {
    journal.push({
      level: i % 2 === 1 ? 'warning' : 'info',
      message: `msg${i}`,
      lineNumber: i,
    });
  }
  let result = journal.read({ limit: 3 });
  assert.strictEqual(result.entries.length, 3);
  assert.strictEqual(result.entries[0].seq, 1);

  result = journal.read({ after: 3 });
  assert.strictEqual(result.entries.length, 7);
  assert.strictEqual(result.entries[0].seq, 4);

  result = journal.read({ level: 'warning' });
  assert.strictEqual(result.entries.length, 5);
  assert.strictEqual(result.entries[0].message, 'msg1');

  result = journal.read({ limit: 1, after: 8, level: 'warning' });
  assert.strictEqual(result.entries.length, 1);
  assert.strictEqual(result.entries[0].seq, 9);
});

test('read rejects invalid limit, after and level values', () => {
  const journal = journalWithSize();
  const invalidReads = [
    { limit: 0 },
    { limit: 201 },
    { limit: '10' },
    { limit: 1.5 },
    { after: -1 },
    { after: '5' },
    { level: 'warn' },
    { level: 'bogus' },
  ];
  for (const args of invalidReads) {
    assert.throws(() => journal.read(args), TypeError);
  }
});

test('constructor rejects invalid sizes', () => {
  assert.throws(() => new ConsoleJournal({ maxEntries: 0 }), TypeError);
  assert.throws(() => new ConsoleJournal({ maxEntries: 1001 }), TypeError);
  assert.throws(() => new ConsoleJournal({ maxEntries: 'abc' }), TypeError);
  assert.throws(() => new ConsoleJournal({ maxMessageLength: 0 }), TypeError);
  assert.throws(() => new ConsoleJournal({ maxMessageLength: 16001 }), TypeError);
  assert.throws(() => new ConsoleJournal({ maxMessageLength: true }), TypeError);
});

test('sourceId keeps origin and pathname only', () => {
  const journal = journalWithSize();
  const cases = [
    ['http://user:pass@example.com/path?query=1#hash', 'http://example.com/path'],
    ['http://example.com/path', 'http://example.com/path'],
    ['https://example.com:8080/path', 'https://example.com:8080/path'],
    ['http://user@example.com', 'http://example.com/'],
    ['ftp://example.com/path', ''],
    ['not-a-url', ''],
  ];
  for (const [sourceId, expected] of cases) {
    const record = journal.push({ level: 'info', message: sourceId, sourceId });
    assert.strictEqual(record.sourceId, expected, sourceId);
  }
});

test('push and read return copies', () => {
  const journal = journalWithSize();
  const record = journal.push({ level: 'info', message: 'copy' });
  record.message = 'mutated';
  assert.strictEqual(journal.read().entries[0].message, 'copy');

  const first = journal.read().entries[0];
  first.level = 'error';
  assert.strictEqual(journal.read().entries[0].level, 'info');
});

test('clear resets entries and dropped but keeps seq', () => {
  const journal = journalWithSize(2, 4000);
  journal.push({ level: 'info', message: 'a' });
  journal.push({ level: 'info', message: 'b' });
  journal.push({ level: 'info', message: 'c' });
  assert.strictEqual(journal.read().dropped, 1);

  journal.clear();
  const record = journal.push({ level: 'info', message: 'd' });
  assert.strictEqual(record.seq, 4);
  assert.deepStrictEqual(journal.read().entries, [{ ...record }]);
  assert.strictEqual(journal.read().dropped, 0);
});

test('two journals are independent', () => {
  const first = journalWithSize();
  const second = journalWithSize();
  first.push({ level: 'info', message: 'first' });
  second.push({ level: 'error', message: 'second' });
  assert.strictEqual(first.read().entries.length, 1);
  assert.strictEqual(second.read().entries.length, 1);
  assert.strictEqual(first.read().entries[0].level, 'info');
  assert.strictEqual(second.read().entries[0].level, 'error');
});
