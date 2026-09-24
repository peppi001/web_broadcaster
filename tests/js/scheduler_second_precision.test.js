'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '..', '..', 'html', 'static', 'scheduler.js'), 'utf8');
const names = [
  'normalizeSchedulerTime', 'parseRunWhen', 'computeRunWhen',
  'parseLocalDateTime', 'nextDateFromWeekdayTime', 'computeNextRunDateFromRunWhen'
];
function extract(name) {
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start >= 0, `Missing ${name}`);
  const end = source.indexOf('\n  function ', start + 2);
  assert.ok(end >= 0, `Missing terminator for ${name}`);
  return source.slice(start, end);
}

let fields = {};
const context = {
  Date,
  form: {},
  qs: (selector) => fields[selector] || null,
};
vm.createContext(context);
vm.runInContext(names.map(extract).join('\n'), context);

assert.equal(context.normalizeSchedulerTime('20:00'), '20:00:00');
assert.equal(context.normalizeSchedulerTime('20:00:15'), '20:00:15');
assert.equal(context.normalizeSchedulerTime('20:00:60'), '');
assert.equal(context.normalizeSchedulerTime('24:00:00'), '');
for (const value of ['2026-09-19 20:00:15', 'Everyday 20:00:15', 'Monday 20:00:15']) {
  const parsed = context.parseRunWhen(value);
  assert.equal(parsed.time, '20:00:15', value);
}
assert.equal(context.parseRunWhen('Everyday 20:00').time, '20:00');
fields = {
  '#recurring_event': { checked: false },
  '#run_date': { value: '2026-09-19' },
  '#run_weekday': { value: 'Everyday' },
  '#run_time': { value: '20:00:15' },
};
assert.equal(context.computeRunWhen(), '2026-09-19 20:00:15');
fields['#recurring_event'].checked = true;
assert.equal(context.computeRunWhen(), 'Everyday 20:00:15');
fields['#run_weekday'].value = 'Monday';
assert.equal(context.computeRunWhen(), 'Monday 20:00:15');
fields['#run_time'].value = '20:00';
assert.equal(context.computeRunWhen(), 'Monday 20:00:00');
assert.equal(context.parseLocalDateTime('2026-09-19 20:00:15').getSeconds(), 15);
assert.equal(context.parseLocalDateTime('2026-09-19 20:00').getSeconds(), 0);
assert.equal(context.parseLocalDateTime('2026-09-19 20:00:60'), null);
assert.equal(context.parseLocalDateTime('2026-02-30 20:00:15'), null);

const now = new Date();
const futureDay = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 2, 20, 0, 15);
const dateText = `${futureDay.getFullYear()}-${String(futureDay.getMonth() + 1).padStart(2, '0')}-${String(futureDay.getDate()).padStart(2, '0')}`;
assert.equal(context.computeNextRunDateFromRunWhen(`${dateText} 20:00:15`).getSeconds(), 15);
assert.equal(context.computeNextRunDateFromRunWhen('Everyday 20:00:15').getSeconds(), 15);
assert.equal(context.computeNextRunDateFromRunWhen('Monday 20:00:15').getSeconds(), 15);
assert.equal(context.computeNextRunDateFromRunWhen('Everyday 20:00').getSeconds(), 0);
assert.equal(context.computeNextRunDateFromRunWhen('Monday 20:00').getSeconds(), 0);
assert.equal(context.computeNextRunDateFromRunWhen('Everyday 20:00:60'), null);
console.log('Scheduler second precision JS tests passed');
