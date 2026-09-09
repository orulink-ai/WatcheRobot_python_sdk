import assert from "node:assert/strict";
import test from "node:test";
import { createMjpegTransport } from "../../examples/sdk_media_lab/web/mjpeg-transport.mjs";

function fixture() {
  let now = 0, next = 0, active = true;
  const timers = new Map(), sockets = [], packets = [], failures = [], acknowledgements = [];
  const transport = createMjpegTransport({
    url: "ws://device/ws/mjpeg", isActive: () => active,
    createSocket: () => {
      const listeners = new Map();
      const socket = { closed: false, sent: [],
        addEventListener: (name, cb) => listeners.set(name, cb),
        emit: (name, data) => listeners.get(name)?.({ data }),
        send: data => socket.sent.push(data), close: () => { socket.closed = true; },
      };
      sockets.push(socket); return socket;
    },
    setTimer: (cb, delay) => { timers.set(++next, { cb, at: now + delay }); return next; },
    clearTimer: id => timers.delete(id),
    onPacket: (data, ack) => { packets.push(data); acknowledgements.push(ack); }, onFailure: reason => failures.push(reason),
  });
  function advance(ms) {
    const end = now + ms;
    while (true) {
      const due = [...timers].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      now = due[1].at; timers.delete(due[0]); due[1].cb();
    }
    now = end;
  }
  return { transport, sockets, packets, failures, acknowledgements, timers, advance, deactivate: () => { active = false; } };
}

test("reconnects the transport without starting another product session", () => {
  const f = fixture(), first = f.sockets[0];
  first.emit("open"); assert.deepEqual(first.sent, ["ready"]);
  first.emit("close"); f.advance(250);
  assert.equal(f.sockets.length, 2);
  first.emit("message", "late");
  f.sockets[1].emit("open"); f.sockets[1].emit("message", "new JPEG");
  assert.deepEqual(f.packets, ["new JPEG"]);
  f.acknowledgements[0](); f.advance(6000);
  assert.deepEqual(f.failures, []);
});

test("handshake and repeated opens without decoded frames cannot extend recovery", () => {
  const f = fixture();
  f.sockets[0].emit("close"); f.advance(250);
  f.sockets[1].emit("open"); f.sockets[1].emit("message", "invalid JPEG");
  f.advance(5000);
  assert.equal(f.failures.length, 1);
  assert.equal(f.sockets[1].closed, true);
  f.advance(10000); assert.equal(f.failures.length, 1);
});

test("stop cancels pending retries and ignores late events", () => {
  const f = fixture();
  f.sockets[0].emit("close"); f.transport.stop(); f.advance(10000);
  f.sockets[0].emit("open"); f.sockets[0].emit("message", "late");
  assert.equal(f.sockets.length, 1); assert.deepEqual(f.packets, []);
  assert.deepEqual(f.failures, []); assert.equal(f.timers.size, 0);
});

test("superseded session cannot reconnect", () => {
  const f = fixture(); f.sockets[0].emit("close"); f.deactivate(); f.advance(10000);
  assert.equal(f.sockets.length, 1); assert.deepEqual(f.failures, []);
});

test("initial connection also has a bounded wait", () => {
  const f = fixture(); f.advance(5000);
  assert.equal(f.failures.length, 1); assert.equal(f.sockets[0].closed, true);
});

test("late decoding from an old socket cannot acknowledge the replacement", () => {
  const f = fixture(); f.sockets[0].emit("message", "old frame");
  f.sockets[0].emit("close"); f.advance(250); f.acknowledgements[0]();
  f.advance(5000); assert.equal(f.failures.length, 1);
});

test("a later interruption gets a new bounded recovery window", () => {
  const f = fixture(); f.sockets[0].emit("message", "frame"); f.acknowledgements[0]();
  f.advance(10000); f.sockets[0].emit("close"); f.advance(4999);
  assert.deepEqual(f.failures, []); f.advance(1); assert.equal(f.failures.length, 1);
});
