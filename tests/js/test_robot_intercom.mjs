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
