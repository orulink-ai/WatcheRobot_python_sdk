import test from "node:test";
import assert from "node:assert/strict";
import { createCallController } from "../../examples/robot_intercom/web/call-controller.mjs";
import { connectCallPresence } from "../../examples/robot_intercom/web/call-presence.mjs";

const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
};

test('cancel during microphone processing releases late graph without starting device', async () => {
  const pending = deferred(); let disposed = 0;
  const r = rig({ prepareMicrophone: () => pending.promise });
  const started = r.controller.start();
  await new Promise(resolve => setImmediate(resolve));
  await r.controller.stop();
  pending.resolve({ stream: r.stream, dispose: () => disposed++ });
  await started;
  assert.equal(disposed, 1); assert.equal(r.track.stops, 1);
  assert.equal(r.requests.length, 0);
});
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
    addTrack(track) { this.sentTrack = track; }
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

test("visuals receive the existing microphone and release it with the call", async () => {
  const streams = [];
  const r = rig({ onLocal: (stream) => streams.push(stream) });
  await r.controller.start();
  assert.equal(r.peers[0].sentTrack, r.track);
  assert.equal(streams[0], r.stream);
  assert.equal(r.track.stops, 0);
  await r.controller.stop();
  assert.equal(streams.at(-1), null);
  assert.equal(r.track.stops, 1);
});

test("a cancelled late microphone never reaches the visual layer", async () => {
  const pending = deferred(),
    streams = [];
  const r = rig({
    getMicrophone: () => pending.promise,
    onLocal: (s) => streams.push(s),
  });
  const started = r.controller.start();
  await r.controller.stop();
  pending.resolve(r.stream);
  await started;
  assert.ok(!streams.includes(r.stream));
  assert.equal(r.track.stops, 1);
});

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

test("page exit releases tracks and reports page_closed for the owned call", async () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const beacons = [];
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { sendBeacon: (path, body) => beacons.push([path, body]) },
  });
  try {
    const r = rig();
    await r.controller.start();
    const id = r.controller.snapshot().requestId;
    r.controller.leave();
    assert.equal(r.track.stops, 1);
    assert.equal(r.peers[0].closed, true);
    assert.equal(beacons[0][0], "/api/call/stop");
    assert.deepEqual(JSON.parse(await beacons[0][1].text()), {
      request_id: id,
      reason: "page_closed",
    });
  } finally {
    if (original) Object.defineProperty(globalThis, "navigator", original);
    else delete globalThis.navigator;
  }
});

test("cancelled presence handshake closes its late socket without reviving a call", async () => {
  const pending = deferred();
  let closed = 0;
  const r = rig({ connectPresence: () => pending.promise });
  const started = r.controller.start();
  await new Promise((resolve) => setImmediate(resolve));
  await r.controller.stop();
  pending.resolve({
    close() {
      closed++;
    },
  });
  await started;
  assert.equal(closed, 1);
  assert.equal(r.peers.length, 0);
  assert.equal(r.controller.snapshot().requestId, null);
});

test("presence remains open until scoped stop completes and its old close cannot end a new call", async () => {
  const sockets = [],
    release = deferred();
  let blocking = true;
  const r = rig({
    api: async (path) =>
      path.endsWith("/stop") && blocking
        ? release.promise
        : { state: "idle", events: [] },
    connectPresence: async (id, onClosed) => {
      const socket = {
        id,
        onClosed,
        closed: false,
        close() {
          this.closed = true;
          this.onClosed();
        },
      };
      sockets.push(socket);
      return socket;
    },
  });
  await r.controller.start();
  const stop = r.controller.stop();
  assert.equal(sockets[0].closed, false);
  release.resolve({ state: "idle" });
  await stop;
  assert.equal(sockets[0].closed, true);
  blocking = false;
  await r.controller.start();
  sockets[0].onClosed();
  assert.notEqual(r.controller.snapshot().state, "idle");
  await r.controller.stop();
});

function presenceRig() {
  let socket,
    timeout,
    failures = 0;
  const details = [];
  class Socket {
    constructor(url) {
      this.url = url;
      this.listeners = {};
      this.closes = 0;
      socket = this;
    }
    addEventListener(name, callback) {
      this.listeners[name] = callback;
    }
    close() {
      this.closes++;
      this.listeners.close?.();
    }
  }
  const promise = connectCallPresence("request-a", (detail) => {
    failures++;
    details.push(detail);
  }, {
    WebSocketClass: Socket,
    baseUrl: "http://127.0.0.1:12345/",
    schedule: (callback) => {
      timeout = callback;
      return 1;
    },
    cancel: () => {},
  });
  return {
    promise,
    socket,
    timeout: () => timeout(),
    failures: () => failures,
    details,
  };
}

test("presence requires server acknowledgement and intentional close is not a failure", async () => {
  const r = presenceRig();
  assert.equal(
    new URL(r.socket.url).searchParams.get("request_id"),
    "request-a",
  );
  r.socket.listeners.message({ data: '{ "ready": true }' });
  const connection = await r.promise;
  connection.close();
  assert.equal(r.failures(), 0);
});

test("presence timeout releases the pending connection and later socket errors fail once", async () => {
  const pending = presenceRig();
  const rejected = assert.rejects(pending.promise, /未建立/);
  pending.timeout();
  await rejected;
  assert.equal(pending.socket.closes, 1);
  const active = presenceRig();
  active.socket.listeners.message({ data: '{"ready":true}' });
  await active.promise;
  active.socket.listeners.error();
  active.socket.listeners.close();
  assert.equal(active.failures(), 1);
});

test("presence error waits for the real close metadata and repeated notifications fail once", async () => {
  const r = presenceRig();
  r.socket.listeners.message({ data: '{"ready":true}' });
  await r.promise;
  r.socket.listeners.error();
  assert.equal(r.failures(), 0);
  assert.equal(r.socket.closes, 0);
  r.socket.listeners.close({ code: 1011, wasClean: false, reason: "keepalive ping timeout" });
  r.socket.listeners.error();
  r.socket.listeners.close({ code: 1006, wasClean: false, reason: "" });
  assert.equal(r.failures(), 1);
  assert.deepEqual(r.details, [{
    code: 1011, wasClean: false, errorSeen: true, reason: "keepalive ping timeout",
  }]);
});

test("presence close without an error retains its evidence and redacts untrusted reasons", async () => {
  const r = presenceRig();
  r.socket.listeners.message({ data: '{"ready":true}' });
  await r.promise;
  r.socket.listeners.close({ code: 1006, wasClean: false, reason: "token=private-secret".repeat(20) });
  assert.equal(r.failures(), 1);
  assert.deepEqual(r.details, [{
    code: 1006, wasClean: false, errorSeen: false, reason: "[redacted]",
  }]);
});

test("controller reports presence evidence once and ignores a retired socket", async () => {
  const callbacks = [], reports = [];
  const r = rig({
    report: (value) => reports.push(value),
    connectPresence: async (id, onClosed) => {
      callbacks.push(onClosed);
      return { close() {} };
    },
  });
  await r.controller.start();
  const detail = { code: 1006, wasClean: false, errorSeen: true, reason: "" };
  callbacks[0](detail);
  callbacks[0](detail);
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(reports.filter((value) => value.kind === "presence_lost"), [{
    kind: "presence_lost", close_code: 1006, was_clean: false, error_seen: true, close_reason: "",
  }]);
  assert.equal(r.requests.filter(([path]) => path.endsWith("/stop")).length, 1);
  await r.controller.start();
  callbacks[0](detail);
  assert.notEqual(r.controller.snapshot().state, "idle");
  assert.equal(reports.filter((value) => value.kind === "presence_lost").length, 1);
  await r.controller.stop();
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
  return {
    ...r,
    expireRequest() {
      const entry = [...deadlines.entries()].find(
        ([, timer]) => timer.ms === 20,
      );
      assert.ok(entry, "request deadline must be armed");
      deadlines.delete(entry[0]);
      entry[1].fn();
    },
  };
}

test("a hung start times out, releases its identity, and ignores a late response", async () => {
  const pending = deferred(),
    calls = [];
  const r = deadlineRig(async (path, body) => {
    calls.push([path, body]);
    return path.endsWith("/start")
      ? pending.promise
      : { state: "idle", matched: true };
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
  const r = deadlineRig(async (path) =>
    path.endsWith("/signal")
      ? new Promise(() => {})
      : { state: "idle", matched: true },
  );
  const started = r.controller.start();
  await new Promise((resolve) => setImmediate(resolve));
  r.expireRequest();
  assert.equal(await started, false);
  assert.equal(r.track.stops, 1);
  assert.equal(r.peers[0].closed, true);
});

test("a hung stop retains ownership for a retry without leaving the UI stopping", async () => {
  let hung = true;
  const r = deadlineRig(async (path) =>
    path.endsWith("/stop") && hung
      ? new Promise(() => {})
      : { state: "idle", matched: true, events: [] },
  );
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
    [
      "audio",
      {
        type: "inbound-rtp",
        kind: "audio",
        codecId: "opus",
        concealedSamples: 1920,
      },
    ],
  ]);
  const s = rig({
    schedule: (fn, ms) => {
      timers.push({ fn, ms });
      return timers.length;
    },
  });
  await s.controller.start();
  await new Promise((resolve) => setImmediate(resolve));
  s.peers[0].getStats = async () => reports;
  timers.find((timer) => timer.ms === 1000).fn();
  await new Promise((resolve) => setImmediate(resolve));
  const feedback = s.requests
    .filter(([path]) => path.endsWith("/feedback"))
    .at(-1)[1];
  assert.equal(feedback.audio_concealed_frames, 2);
  await s.controller.stop();
});

test("the total connection deadline covers a pending microphone grant", async () => {
  const pending = deferred(),
    timers = [];
  const r = rig({
    getMicrophone: () => pending.promise,
    schedule: (fn, ms) => {
      timers.push({ fn, ms });
      return timers.length;
    },
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

test("a connected call retires its setup deadline before connection recovery", async () => {
  const timers = new Map();
  let sequence = 0;
  const r = rig({
    schedule: (fn, ms) => {
      const id = ++sequence;
      timers.set(id, { fn, ms });
      return id;
    },
    cancel: (id) => timers.delete(id),
  });
  await r.controller.start();
  const peer = r.peers[0];
  peer.connectionState = "connected";
  peer.listeners.connectionstatechange();
  assert.equal(
    [...timers.values()].some((timer) => timer.ms === 30000),
    false,
  );
  peer.connectionState = "disconnected";
  peer.listeners.connectionstatechange();
  assert.equal(r.controller.snapshot().state, "connecting");
  assert.equal(
    [...timers.values()].some((timer) => timer.ms === 3000),
    true,
  );
  await r.controller.stop();
});

test("heartbeat starts after allocation while offer signalling is still pending", async () => {
  const pending = deferred(),
    calls = [];
  const r = rig({
    api: async (path) => {
      calls.push(path);
      return path.endsWith("/signal")
        ? pending.promise
        : { state: "idle", matched: true, events: [] };
    },
  });
  const started = r.controller.start();
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(calls.includes("/api/call/heartbeat"));
  pending.resolve({});
  await started;
  await r.controller.stop();
});
