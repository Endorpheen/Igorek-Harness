'use strict';

/*
 * browser-console-journal — in-RAM console journal for the Apex embedded
 * browser. Records are kept only in memory and exposed as copies, so the
 * journal state can never be modified from the outside.
 */

const DEFAULT_MAX_ENTRIES = 200;
const DEFAULT_MAX_MESSAGE_LENGTH = 4000;
const MAX_ENTRIES_MIN = 1;
const MAX_ENTRIES_MAX = 1000;
const MAX_MESSAGE_LENGTH_MIN = 1;
const MAX_MESSAGE_LENGTH_MAX = 16000;
const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;

const DEBUG = 'debug';
const INFO = 'info';
const WARNING = 'warning';
const ERROR = 'error';
const VALID_LEVELS = new Set([DEBUG, INFO, WARNING, ERROR]);

/* console API aliases: warn -> warning, log -> info */
const LEVEL_ALIASES = new Map([
  ['warn', WARNING],
  ['log', INFO],
]);

/* Electron 0/1/2/3 numeric levels */
const ELECTRON_LEVELS = new Map([
  [0, DEBUG],
  [1, INFO],
  [2, WARNING],
  [3, ERROR],
]);

function isInteger(value) {
  return typeof value === 'number' && Number.isInteger(value);
}

function normalizeLevel(rawLevel) {
  if (rawLevel === null || rawLevel === undefined) return INFO;
  if (typeof rawLevel === 'string') {
    const normalized = LEVEL_ALIASES.get(rawLevel) ?? rawLevel;
    return VALID_LEVELS.has(normalized) ? normalized : INFO;
  }
  if (typeof rawLevel === 'number') {
    return ELECTRON_LEVELS.get(rawLevel) ?? INFO;
  }
  return INFO;
}

function normalizeLineNumber(lineNumber) {
  if (isInteger(lineNumber) && lineNumber >= 0) return lineNumber;
  return 0;
}

/* http(s) URLs keep origin + pathname only: no login/password, search or hash */
function normalizeSourceId(sourceId) {
  if (typeof sourceId !== 'string') return '';
  if (!sourceId.startsWith('http://') && !sourceId.startsWith('https://')) return '';
  try {
    const url = new URL(sourceId);
    url.username = '';
    url.password = '';
    return url.origin + url.pathname;
  } catch {
    return '';
  }
}

function isTruncated(message, maxMessageLength) {
  return message.length > maxMessageLength;
}

function truncateMessage(message, maxMessageLength) {
  return message.length > maxMessageLength ? message.slice(0, maxMessageLength) : message;
}

function validateMaxEntries(maxEntries) {
  if (
    typeof maxEntries !== 'number' ||
    !isInteger(maxEntries) ||
    maxEntries < MAX_ENTRIES_MIN ||
    maxEntries > MAX_ENTRIES_MAX
  ) {
    throw new TypeError(
      `maxEntries must be an integer in ${MAX_ENTRIES_MIN}..${MAX_ENTRIES_MAX}, got ${maxEntries}`
    );
  }
  return maxEntries;
}

function validateMaxMessageLength(maxMessageLength) {
  if (
    typeof maxMessageLength !== 'number' ||
    !isInteger(maxMessageLength) ||
    maxMessageLength < MAX_MESSAGE_LENGTH_MIN ||
    maxMessageLength > MAX_MESSAGE_LENGTH_MAX
  ) {
    throw new TypeError(
      `maxMessageLength must be an integer in ${MAX_MESSAGE_LENGTH_MIN}..${MAX_MESSAGE_LENGTH_MAX}, got ${maxMessageLength}`
    );
  }
  return maxMessageLength;
}

function assertLimit(limit) {
  if (!isInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
    throw new TypeError(`limit must be an integer in 1..${MAX_LIMIT}, got ${limit}`);
  }
}

function assertAfter(after) {
  if (!isInteger(after) || after < 0) {
    throw new TypeError(`after must be a non-negative integer, got ${after}`);
  }
}

function assertLevel(level) {
  if (level !== undefined && !VALID_LEVELS.has(level)) {
    throw new TypeError(`level must be one of ${[...VALID_LEVELS].join(', ')}, got ${level}`);
  }
}

/**
 * @typedef {Object} ConsoleRecord
 * @property {number} seq - monotonic sequence number, starts at 1
 * @property {string} level - one of debug, info, warning, error
 * @property {string} message - message, truncated to maxMessageLength when pushed
 * @property {number} lineNumber - non-negative integer
 * @property {string} sourceId - normalized source identifier
 * @property {boolean} truncated - true when the message was truncated
 */

class ConsoleJournal {
  constructor({
    maxEntries = DEFAULT_MAX_ENTRIES,
    maxMessageLength = DEFAULT_MAX_MESSAGE_LENGTH,
  } = {}) {
    this.maxEntries = validateMaxEntries(maxEntries);
    this.maxMessageLength = validateMaxMessageLength(maxMessageLength);
    this.records = [];
    this.nextSeq = 0;
    this.dropped = 0;
  }

  /*
   * Append a console record. Returns a copy of the stored record, or null
   * when the record was rejected (e.g. a non-string message).
   */
  push(details) {
    if (details === null || typeof details !== 'object') return null;
    const { level, message, lineNumber, sourceId } = details;

    if (typeof message !== 'string') return null;

    this.nextSeq++;
    const record = {
      seq: this.nextSeq,
      level: normalizeLevel(level),
      message: truncateMessage(message, this.maxMessageLength),
      lineNumber: normalizeLineNumber(lineNumber),
      sourceId: normalizeSourceId(sourceId),
      truncated: isTruncated(message, this.maxMessageLength),
    };

    this.records.push(record);
    if (this.records.length > this.maxEntries) {
      this.records.shift();
      this.dropped++;
    }

    return { ...record };
  }

  /*
   * Read records. Returns the first `limit` records with seq after `after`
   * and matching `level`, in seq order, as copies.
   */
  read({ limit = DEFAULT_LIMIT, after = 0, level } = {}) {
    assertLimit(limit);
    assertAfter(after);
    assertLevel(level);

    const entries = this.records
      .filter((record) => record.seq > after && (level === undefined || record.level === level))
      .slice(0, limit)
      .map((record) => ({ ...record }));

    return { entries, last_seq: this.nextSeq, dropped: this.dropped };
  }

  /* Clear records and the dropped counter; seq numbers keep growing. */
  clear() {
    this.records = [];
    this.dropped = 0;
  }
}

module.exports = { ConsoleJournal };
