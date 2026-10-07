import assert from "node:assert/strict";
import test from "node:test";
import { createCombinedSceneLifecycle } from "../../examples/sdk_media_lab/web/combined-scene.mjs";

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function fixture(overrides = {}) {
  const calls = [];
  const lifecycle = createCombinedSceneLifecycle({
    isProceduralRunning: () => false,
    startProcedural: async () => { calls.push("animation:start"); return { state: "running" }; },
    stopProcedural: async () => { calls.push("animation:stop"); return { state: "idle" }; },
    startAudio: async () => { calls.push("audio:start"); return true; },
    stopAudio: async () => { calls.push("audio:stop"); },
    ...overrides,
  });
  return { calls, lifecycle };
}

test("combined start confirms the default animation before audio and ignores double clicks", async () => {
  const animation = deferred();
  const { calls, lifecycle } = fixture({ startProcedural: () => { calls.push("animation:start"); return animation.promise; } });
  const started = lifecycle.start();
  assert.equal(lifecycle.snapshot().busy, true);
  assert.equal(await lifecycle.start(), false);
  assert.deepEqual(calls, ["animation:start"]);
  animation.resolve({ state: "running" });
  assert.equal(await started, true);
  assert.deepEqual(calls, ["animation:start", "audio:start"]);
  assert.equal(lifecycle.snapshot().ownsProcedural, true);
  await lifecycle.end();
  assert.deepEqual(calls, ["animation:start", "audio:start", "audio:stop", "animation:stop"]);
});

test("an independently enabled animation is reused and preserved on combined end or failure", async () => {
  const { calls, lifecycle } = fixture({ isProceduralRunning: () => true });
  await lifecycle.start();
  await lifecycle.end();
  assert.deepEqual(calls, ["audio:start", "audio:stop"]);
  const failed = fixture({ isProceduralRunning: () => true, startAudio: async () => false });
  await assert.rejects(failed.lifecycle.start(), /call did not start/);
  assert.equal(failed.calls.includes("animation:stop"), false);
});

test("a missing animation acknowledgement prevents audio startup", async () => {
  for (const reply of [null, { state: "idle" }]) {
    const { calls, lifecycle } = fixture({ startProcedural: async () => reply });
    await assert.rejects(lifecycle.start(), /animation did not start/);
    assert.equal(calls.includes("audio:start"), false);
    assert.equal(lifecycle.snapshot().active, false);
  }
});

test("microphone failure releases an animation acquired by this combined start", async () => {
  const { calls, lifecycle } = fixture({ startAudio: async () => { throw new Error("microphone denied"); } });
  await assert.rejects(lifecycle.start(), /microphone denied/);
  assert.equal(calls.includes("animation:stop"), true);
  assert.equal(lifecycle.snapshot().ownsProcedural, false);
});

test("cancellation during a pending animation acknowledgement releases its late acquisition", async () => {
  const animation = deferred();
  const { calls, lifecycle } = fixture({ startProcedural: () => animation.promise });
  const started = lifecycle.start();
  await lifecycle.end();
  animation.resolve({ state: "running" });
  assert.equal(await started, false);
  // A late successful start may follow the first stop on the server; confirm stop again.
  assert.deepEqual(calls, ["audio:stop", "animation:stop", "animation:stop"]);
});

test("late audio startup after cancellation cannot leave audio or animation running", async () => {
  const audio = deferred();
  const { calls, lifecycle } = fixture({ startAudio: () => { calls.push("audio:start"); return audio.promise; } });
  const started = lifecycle.start();
  await Promise.resolve();
  await lifecycle.end();
  audio.resolve(true);
  assert.equal(await started, false);
  assert.equal(calls.filter(value => value === "animation:stop").length, 1);
  assert.equal(calls.at(-1), "audio:stop");
  assert.equal(lifecycle.snapshot().active, false);
});

test("page exit invalidates pending combined startup while the page lifecycle handles beacons", async () => {
  const animation = deferred();
  const { calls, lifecycle } = fixture({ startProcedural: () => animation.promise });
  const started = lifecycle.start();
  lifecycle.cancelForPageExit();
  animation.resolve({ state: "running" });
  assert.equal(await started, false);
  assert.deepEqual(calls, ["animation:stop"]);
});

test("asynchronous call failure releases only a combined-owned animation", async () => {
  const { calls, lifecycle } = fixture();
  await lifecycle.start();
  await lifecycle.audioStopped();
  assert.equal(calls.at(-1), "animation:stop");
  const borrowed = fixture({ isProceduralRunning: () => true });
  await borrowed.lifecycle.start();
  await borrowed.lifecycle.audioStopped();
  assert.deepEqual(borrowed.calls, ["audio:start"]);
});

test("an unconfirmed animation stop retains ownership for a safe retry", async () => {
  let fail = true;
  const { lifecycle } = fixture({ stopProcedural: async () => { if (fail) throw new Error("stop unconfirmed"); return { state: "idle" }; } });
  await lifecycle.start();
  await assert.rejects(lifecycle.end(), /stop unconfirmed/);
  assert.equal(lifecycle.snapshot().ownsProcedural, true);
  fail = false;
  await lifecycle.end();
  assert.equal(lifecycle.snapshot().ownsProcedural, false);
});

test("cancelling a hung microphone request permits a new scene and its late result cannot stop the new scene", async () => {
  const firstAudio = deferred();
  let audioStarts = 0;
  const { calls, lifecycle } = fixture({ startAudio: () => {
    calls.push("audio:start");
    return ++audioStarts === 1 ? firstAudio.promise : Promise.resolve(true);
  } });
  const oldStart = lifecycle.start();
  await Promise.resolve();
  await lifecycle.end();
  assert.equal(lifecycle.snapshot().busy, false);
  assert.equal(await lifecycle.start(), true);
  const stopsBeforeLateReply = calls.filter(value => value.endsWith(":stop")).length;
  firstAudio.resolve(true);
  assert.equal(await oldStart, false);
  assert.equal(lifecycle.snapshot().active, true);
  assert.equal(lifecycle.snapshot().ownsProcedural, true);
  assert.equal(calls.filter(value => value.endsWith(":stop")).length, stopsBeforeLateReply);
});

test("a lost animation acknowledgement is cleaned up and an empty stop acknowledgement stays unconfirmed", async () => {
  const lost = fixture({ startProcedural: async () => { throw new Error("ack lost"); } });
  await assert.rejects(lost.lifecycle.start(), /ack lost/);
  assert.deepEqual(lost.calls, ["animation:stop"]);
  const unconfirmed = fixture({ stopProcedural: async () => null });
  await unconfirmed.lifecycle.start();
  await assert.rejects(unconfirmed.lifecycle.end(), /stop is unconfirmed/);
  assert.equal(unconfirmed.lifecycle.snapshot().ownsProcedural, true);
});
