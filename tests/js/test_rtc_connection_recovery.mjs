import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createConnectionRecovery } from '../../examples/sdk_media_lab/web/rtc-connection-recovery.mjs';

test('transient disconnect recovers without terminating conversation', () => {
  let timer; const failures = [];
  const policy = createConnectionRecovery(reason => failures.push(reason), {
    schedule: callback => { timer = callback; return 1; }, cancel: () => {},
  });
  policy.update('disconnected');
  assert.equal(failures.length, 0);
  policy.update('connected');
  timer();
  assert.equal(failures.length, 0);
});
test('persistent loss, failed connection and teardown are bounded', () => {
  let timer; const failures = [];
  const options = { schedule: callback => { timer = callback; return 1; }, cancel: () => {} };
  const policy = createConnectionRecovery(reason => failures.push(reason), options);
  policy.update('disconnected'); timer(); timer();
  assert.deepEqual(failures, ['WebRTC disconnect recovery timed out']);
  const stopped = createConnectionRecovery(reason => failures.push(reason), options);
  stopped.update('disconnected'); stopped.stop(); timer();
  assert.equal(failures.length, 1);
  const fatal = createConnectionRecovery(reason => failures.push(reason), options);
  fatal.update('failed');
  assert.equal(failures[1], 'WebRTC connection failed');
});

test('repeated disconnects share a deadline and an old timer cannot end a later recovery', () => {
  const timers = []; const cancelled = []; const failures = [];
  const policy = createConnectionRecovery(reason => failures.push(reason), {
    schedule: callback => { timers.push(callback); return timers.length; },
    cancel: timer => cancelled.push(timer),
  });
  policy.update('disconnected');
  policy.update('disconnected');
  assert.equal(timers.length, 1);
  policy.update('connected');
  assert.deepEqual(cancelled, [1]);
  policy.update('disconnected');
  timers[0]();
  assert.equal(failures.length, 0);
  timers[1]();
  assert.deepEqual(failures, ['WebRTC disconnect recovery timed out']);
});

test('closing during recovery cancels the timer and fails exactly once', () => {
  let timer; const failures = []; const cancelled = [];
  const policy = createConnectionRecovery(reason => failures.push(reason), {
    schedule: callback => { timer = callback; return 1; },
    cancel: value => cancelled.push(value),
  });
  policy.update('disconnected');
  policy.update('closed');
  timer();
  policy.update('failed');
  assert.deepEqual(failures, ['WebRTC connection closed']);
  assert.deepEqual(cancelled, [1]);
});

function peerEventsFixture() {
  const source = readFileSync(new URL('../../examples/sdk_media_lab/web/app.js', import.meta.url), 'utf8');
  const start = source.indexOf('function bindRtcPeerEvents(');
  const end = source.indexOf('\nfunction setLiveVideoState(', start);
  assert.ok(start >= 0 && end > start);
  const timers = []; const failures = []; const listeners = {};
  const peer = { connectionState: 'new', addEventListener: (name, callback) => { listeners[name] = callback; } };
  const state = { rtc: { generation: 1, peer, mode: 'video' } };
  const bind = vm.runInNewContext(`${source.slice(start, end)}\nbindRtcPeerEvents;`, {
    state,
    createConnectionRecovery: callback => createConnectionRecovery(callback, {
      schedule: timer => { timers.push(timer); return timers.length; }, cancel: () => {},
    }),
    isCurrentRtcGeneration: (current, expected) => current === expected,
    rtcModeHasAudio: () => false,
    failRtcSession: reason => failures.push(reason),
  });
  bind(peer, 1);
  const update = value => { peer.connectionState = value; listeners.connectionstatechange(); };
  return { state, timers, failures, update };
}

test('production peer handler recovers briefly, then terminates a persistent loss', () => {
  const { timers, failures, update } = peerEventsFixture();
  update('disconnected');
  assert.equal(failures.length, 0);
  update('connected');
  timers[0]();
  assert.equal(failures.length, 0);
  update('disconnected');
  timers[1]();
  assert.deepEqual(failures, ['WebRTC disconnect recovery timed out']);
});

test('production recovery timeout cannot terminate a replaced peer or generation', () => {
  for (const replacement of ['peer', 'generation']) {
    const { state, timers, failures, update } = peerEventsFixture();
    update('disconnected');
    if (replacement === 'peer') state.rtc.peer = {};
    else state.rtc.generation++;
    timers[0]();
    assert.deepEqual(failures, []);
  }
});
