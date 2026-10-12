import test from "node:test";
import assert from "node:assert/strict";
import { createCallController } from "../../examples/robot_intercom/web/call-controller.mjs";

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
};
function rig(overrides = {}) {
  const requests = [],
    states = [];
  const track = {
    kind: "audio",
    enabled: true,
    stops: 0,
    stop() {
      this.stops++;
    },
  };
  const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
  const peers = [];
  class Peer {
    constructor() {
      this.listeners = {};
      this.connectionState = "new";
      peers.push(this);
    }
    addTrack() {}
    addEventListener(name, callback) {
      this.listeners[name] = callback;
    }
    async createOffer() {
      return { sdp: "offer" };
    }
    async setLocalDescription() {}
    async getStats() {
      return new Map();
    }
    close() {
      this.closed = true;
    }
    getReceivers() {
      return [];
    }
  }
  const api = async (path, body) => {
    requests.push([path, body]);
    return path.endsWith("/stop")
      ? { state: "idle", matched: true }
      : { events: [] };
  };
  const controller = createCallController({
    api,
    getMicrophone: async () => stream,
    makePeer: () => new Peer(),
    makeId: () => `request-${peers.length + 1}`,
    schedule: () => 1,
    cancel: () => {},
    onState: (s) => states.push(s),
    ...overrides,
  });
  return { controller, requests, states, track, peers, stream };
}

test("cancelled microphone permission cannot allocate hardware or leak the late stream", async () => {
  const pending = deferred();
  const r = rig({ getMicrophone: () => pending.promise });
  const started = r.controller.start();
  await r.controller.stop();
  pending.resolve(r.stream);
  await started;
  assert.equal(r.track.stops, 1);
  assert.equal(r.requests.length, 0);
  assert.equal(r.controller.snapshot().requestId, null);
});
test("cancel during device start waits for acknowledgement before scoped release", async () => {
  const pending = deferred(),
    calls = [];
  const r = rig({
    api: async (path, body) => {
      calls.push([path, body]);
      if (path.endsWith("/start")) return pending.promise;
      return { state: "idle", matched: true };
    },
  });
  const started = r.controller.start();
  await new Promise((resolve) => setImmediate(resolve));
  const stopped = r.controller.stop();
  assert.equal(calls.length, 1);
  pending.resolve({});
  await Promise.all([started, stopped]);
  assert.deepEqual(
    calls.map((c) => c[0]),
    ["/api/call/start", "/api/call/stop"],
  );
  assert.equal(calls[0][1].request_id, calls[1][1].request_id);
  assert.equal(r.peers.length, 0);
});
test("unconfirmed release retains identity and a retry cannot start another call", async () => {
  let fail = true;
  const r = rig({
    api: async (path) => {
      if (path.endsWith("/stop") && fail) throw Error("offline");
      return { state: "idle", matched: true, events: [] };
    },
  });
  await r.controller.start();
  await assert.rejects(r.controller.stop(), /offline/);
  assert.equal(r.controller.snapshot().state, "cleanup_required");
  assert.equal(await r.controller.start(), false);
  fail = false;
  await r.controller.stop();
  assert.equal(r.controller.snapshot().requestId, null);
});
test("mute controls only local microphone and dispose releases all tracks", async () => {
  const r = rig();
  await r.controller.start();
  r.controller.setMuted(true);
  assert.equal(r.track.enabled, false);
  r.controller.setMuted(false);
  assert.equal(r.track.enabled, true);
  await r.controller.stop();
  assert.equal(r.track.stops, 1);
  assert.equal(r.peers[0].closed, true);
});

function deadlineRig(api) {
  const deadlines = new Map();
  let sequence = 0;
  const r = rig({
    api,
    requestTimeoutMs: 20,
    schedule: (fn, ms) => {
      const id = ++sequence;
      deadlines.set(id, { fn, ms });
      return id;
    },
    cancel: (id) => deadlines.delete(id),
  });
  return { ...r, expireRequest() {
    const entry = [...deadlines.entries()].find(([, timer]) => timer.ms === 20);
    assert.ok(entry, "request deadline must be armed");
    deadlines.delete(entry[0]);
    entry[1].fn();
  } };
}

test("a hung start times out, releases its identity, and ignores a late response", async () => {
  const pending = deferred(), calls = [];
  const r = deadlineRig(async (path, body) => {
    calls.push([path, body]);
    return path.endsWith("/start") ? pending.promise : { state: "idle", matched: true };
  });
  const started = r.controller.start();
  await new Promise((resolve) => setImmediate(resolve));
  r.expireRequest();
  assert.equal(await started, false);
  assert.equal(r.track.stops, 1);
  assert.equal(r.controller.snapshot().state, "idle");
  assert.equal(calls[0][1].request_id, calls[1][1].request_id);
  pending.resolve({});
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(r.peers.length, 0);
});

test("a hung offer times out and closes browser media before scoped release", async () => {
  const r = deadlineRig(async (path) => path.endsWith("/signal")
    ? new Promise(() => {}) : { state: "idle", matched: true });
  const started = r.controller.start();
  await new Promise((resolve) => setImmediate(resolve));
  r.expireRequest();
  assert.equal(await started, false);
  assert.equal(r.track.stops, 1);
  assert.equal(r.peers[0].closed, true);
});

test("a hung stop retains ownership for a retry without leaving the UI stopping", async () => {
  let hung = true;
  const r = deadlineRig(async (path) => path.endsWith("/stop") && hung
    ? new Promise(() => {}) : { state: "idle", matched: true, events: [] });
  await r.controller.start();
  const stopped = r.controller.stop();
  const rejected = assert.rejects(stopped, /超时/);
  await new Promise((resolve) => setImmediate(resolve));
  r.expireRequest();
  await rejected;
  assert.equal(r.controller.snapshot().state, "cleanup_required");
  assert.equal(await r.controller.start(), false);
  hung = false;
  await r.controller.stop();
  assert.equal(r.controller.snapshot().requestId, null);
});

test("concealment feedback converts codec samples into 20 ms frames", async () => {
  const timers = [];
  const reports = new Map([
    ["opus", { type: "codec", clockRate: 48000 }],
    ["audio", { type: "inbound-rtp", kind: "audio", codecId: "opus", concealedSamples: 1920 }],
  ]);
  const s = rig({ schedule: (fn, ms) => { timers.push({ fn, ms }); return timers.length; } });
  await s.controller.start();
  await new Promise((resolve) => setImmediate(resolve));
  s.peers[0].getStats = async () => reports;
  timers.find((timer) => timer.ms === 1000).fn();
  await new Promise((resolve) => setImmediate(resolve));
  const feedback = s.requests.filter(([path]) => path.endsWith("/feedback")).at(-1)[1];
  assert.equal(feedback.audio_concealed_frames, 2);
  await s.controller.stop();
});

test("the total connection deadline covers a pending microphone grant", async () => {
  const pending = deferred(), timers = [];
  const r = rig({
    getMicrophone: () => pending.promise,
    schedule: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
  });
  const started = r.controller.start();
  timers.find((timer) => timer.ms === 30000).fn();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(r.controller.snapshot().state, "idle");
  pending.resolve(r.stream);
  assert.equal(await started, false);
  assert.equal(r.track.stops, 1);
  assert.equal(r.requests.length, 0);
});
