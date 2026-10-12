import test from "node:test";
import assert from "node:assert/strict";
import { createCallClock } from "../../examples/robot_intercom/web/call-clock.mjs";

test("duration starts on connection, survives recovery, and resets for another call", () => {
  let now = 10000;
  const clock = createCallClock(() => now);
  assert.equal(clock.update({ requestId: "a", state: "connecting" }), "—");
  assert.equal(clock.update({ requestId: "a", state: "running" }), "00:00");
  now += 65000;
  assert.equal(clock.update({ requestId: "a", state: "connecting" }), "01:05");
  assert.equal(clock.update({ requestId: "a", state: "running" }), "01:05");
  assert.equal(clock.update({ requestId: null, state: "idle" }), "—");
  assert.equal(clock.update({ requestId: "b", state: "running" }), "00:00");
});
