import test from 'node:test';
import assert from 'node:assert/strict';
import {percentages, resourceOptions, readSession, saveRequest, confirmSave, PreviewSelection} from '../src/watcherobot/idle_studio/web/state.mjs';

test('grouped sessions preserve version and display weights within each pool', () => {
  const rows = [{group: 'standby', weight: 1}, {group: 'standby', weight: 3}, {group: 'feature', weight: 1}];
  assert.deepEqual(percentages(rows), [25, 75, 100]);
  const session = readSession({device_id: 'a', revision: 0, editable: true, profile: {schema: 2, behaviors: rows}});
  assert.equal(saveRequest(session, rows).profile.schema, 2);
});

test('preview survives unrelated rendering but rejects stale device and asset results', () => {
  const preview = new PreviewSelection();
  const device = {device_id: 'a'};
  const token = preview.select(device, 'blink', '眨眼');
  assert.equal(preview.select(device, 'blink', '新的名字'), token);
  assert.equal(preview.accept(token, 'blob:first'), true);
  assert.equal(preview.url, 'blob:first');
  preview.select(device, 'sleep', '睡觉');
  assert.equal(preview.accept(token, 'blob:late'), false);
  assert.equal(preview.url, null);
  const second = preview.token;
  preview.select({device_id: 'b'}, 'sleep', '睡觉');
  assert.equal(preview.accept(second, 'blob:wrong-device'), false);
});

test('save confirmation accepts reordered object keys but rejects changed content and device', () => {
  const request = {device_id: 'a', revision: 2, profile: {schema: 1, behaviors: [{id: 'b', weight: 7}]}};
  assert.doesNotThrow(() => confirmSave(request, {device_id: 'a', revision: 3, profile: {behaviors: [{weight: 7, id: 'b'}], schema: 1}}));
  assert.throws(() => confirmSave(request, {...request, revision: 3, device_id: 'other'}));
  assert.throws(() => confirmSave(request, {...request, revision: 3, profile: {schema: 1, behaviors: []}}));
  assert.throws(() => confirmSave(request, request));
});

test('save binds the current draft to device identity and revision', () => {
  const rows = [{id: 'a', weight: 10}];
  const session = readSession({port: 'COM6', device_id: 'machine-a', revision: 4, editable: true});
  const request = saveRequest(session, rows);
  assert.deepEqual(request, {port: 'COM6', device_id: 'machine-a', revision: 4, profile: {schema: 1, behaviors: rows}});
  rows[0].weight = 99;
  assert.equal(request.profile.behaviors[0].weight, 10);
  assert.throws(() => saveRequest(null, rows));
  assert.throws(() => saveRequest({...session, editable: false}, rows));
});

test('percentages sum to 100 with deterministic rounding and zero weights', () => {
  assert.deepEqual(percentages([1, 1, 1, 0].map(weight => ({weight}))), [33.34, 33.33, 33.33, 0]);
  assert.deepEqual(percentages([{weight: 0}]), [0]);
});
test('catalog offers only tracks actually present on the connected device', () => {
  const catalog = {expressions: [
    {id: 'a', display_name: '表情 A', assets: {animation: {}, sound: {}}},
    {id: 'b', assets: {animation: {}, action: {}}},
  ]};
  assert.deepEqual(resourceOptions(catalog, 'sound'), [{id: 'a', name: '表情 A'}]);
  assert.deepEqual(resourceOptions(catalog, 'motion'), [{id: 'b', name: 'b'}]);
});
test('changing devices clears the prior configuration and keeps identity', () => {
  const old = readSession({device_id: 'a', profile: {behaviors: [{id: 'old'}]}});
  const fresh = readSession({device_id: 'b', profile: null, editable: true, revision: 0});
  assert.deepEqual(fresh.rows, []);
  assert.equal(fresh.device_id, 'b');
  assert.equal(old.rows.length, 1);
});
