import test from "node:test";
import assert from "node:assert/strict";
import { createDeviceConnector } from "../../examples/robot_intercom/web/device-connector.mjs";

test("accepted pairing is not success until Daemon reports the same robot online", async () => {
  const device = createDeviceConnector({
    api: async () => ({
      connection: { request_id: "pair-a", state: "discovering" },
    }),
  });
  await device.connect("123456");
  assert.equal(device.snapshot().phase, "waiting");
  device.update({ online: true, request_id: "other" });
  assert.equal(device.snapshot().phase, "error");
  await device.connect("123456");
  device.update({ online: true, request_id: "pair-a" });
  assert.equal(device.snapshot().phase, "connected");
});
test("invalid codes and repeated submission do not issue requests", async () => {
  let requests = 0,
    resolve;
  const device = createDeviceConnector({
    api: () => {
      requests++;
      return new Promise((r) => (resolve = r));
    },
  });
  await device.connect("12345");
  assert.equal(requests, 0);
  const pending = device.connect("123456");
  await device.connect("654321");
  assert.equal(requests, 1);
  resolve({ connection: { request_id: "a" } });
  await pending;
});
test("failed discovery explains retry and waiting has a deadline", async () => {
  let now = 0;
  const device = createDeviceConnector({
    now: () => now,
    api: async () => ({ connection: { request_id: "a" } }),
  });
  await device.connect("123456");
  device.update({ state: "idle", last_error: "discovery_timeout" });
  assert.equal(device.snapshot().phase, "error");
  await device.connect("123456");
  now = 31000;
  device.update({ state: "discovering" });
  assert.equal(device.snapshot().phase, "error");
});
