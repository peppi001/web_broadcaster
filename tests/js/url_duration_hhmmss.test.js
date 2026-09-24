'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', '..', 'html', 'static', 'scheduler.js'), 'utf8');
function extract(name) {
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start >= 0, `Missing function ${name}`);
  const end = name === 'commitUrlFromModal'
    ? source.indexOf('\n  // Wire URL modal events once', start)
    : source.indexOf('\n  function ', start + 2);
  assert.ok(end >= 0, `Missing terminator for ${name}`);
  return source.slice(start, end);
}
const input = { value: '', validity: '', validityReports: 0, focused: 0,
  setCustomValidity(message) { this.validity = message; },
  reportValidity() { ++this.validityReports; },
  focus() { ++this.focused; },
};
const values = { value: 'https://radio.example/live' };
const fixedTitle = { value: 'Radio - Live Show' };
const context = {
  urlInput: values, urlDurationInput: input, urlCustomInput: fixedTitle,
  urlCustomRow: { hidden: false }, urlModalExternalResolver: null,
  getActionLines: () => [], setActionLines: lines => { context.savedLines = lines; },
  closeUrlModal: () => { ++context.closed; }, closed: 0,
};
vm.createContext(context);
vm.runInContext(['formatUrlDuration', 'parseUrlDuration', 'readUrlModalValues',
  'commitUrlFromModal'].map(extract).join('\n'), context);

for (const [value, expected] of [
  ['', -1], ['  ', -1], ['00:00:01', 1], ['00:01:00', 60],
  ['00:01:30', 90], ['01:02:03', 3723], ['20:00:15', 72015],
  ['99:59:59', 359999],
  ['00:00:00', null], ['00:00:60', null], ['00:60:00', null],
  ['1:00:00', null], ['00:01', null], ['60', null], ['00:01:30x', null],
  ['00:01:-1', null], ['99999999999999999999:00:00', null]
]) assert.equal(context.parseUrlDuration(value), expected, value);
assert.equal(context.formatUrlDuration(60), '00:01:00');
assert.equal(context.formatUrlDuration(3723), '01:02:03');
assert.equal(context.formatUrlDuration(-1), '');
assert.equal(context.formatUrlDuration(undefined), '');

input.value = '';
assert.equal(context.readUrlModalValues().duration, -1);
context.commitUrlFromModal();
assert.equal(context.savedLines[0], 'URL:-1:https://radio.example/live');
assert.equal(context.closed, 1);
input.value = '20:00:15';
assert.equal(context.readUrlModalValues().duration, 72015);
context.commitUrlFromModal();
assert.equal(context.savedLines[0], 'URL:72015:https://radio.example/live');
assert.equal(context.readUrlModalValues().custom_metadata, 'Radio - Live Show');
input.value = '01:90:00';
const prior = context.closed;
assert.equal(context.readUrlModalValues(), null);
context.commitUrlFromModal();
assert.equal(context.closed, prior, 'Invalid duration must not commit or close dialog');
assert.equal(input.validityReports, 2);
assert.match(input.validity, /HH:MM:SS/);
input.value = '';
assert.equal(context.readUrlModalValues().duration, -1);
assert.equal(input.validity, '');
console.log('URL HH:MM:SS/infinite modal JS regression tests passed');
