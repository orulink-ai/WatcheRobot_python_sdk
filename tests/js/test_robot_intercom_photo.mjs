import test from "node:test";
import assert from "node:assert/strict";
import { createPhotoController } from "../../examples/robot_intercom/web/photo-controller.mjs";

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
}
const flush = () => new Promise((resolve) => setImmediate(resolve));
function rig(overrides = {}) {
  const requests = [],
    progress = [],
    photos = [],
    errors = [],
    timers = new Map();
  let now = 0,
    sequence = 0,
    idle = 0;
  const controller = createPhotoController({
    clock: () => now,
    schedule: (callback, delay) => {
      timers.set(++sequence, { callback, at: now + delay });
      return sequence;
    },
    cancel: (id) => timers.delete(id),
    capture: async (id, signal) => {
      requests.push({ id, signal });
      return { url: "/photo.jpg" };
    },
    load: async () => ({ naturalWidth: 640, naturalHeight: 480 }),
    onProgress: (state) => progress.push(state),
    onPhoto: (photo, image, elapsedMs) =>
      photos.push({ photo, image, elapsedMs }),
    onError: (error) => errors.push(error.message),
    onIdle: () => idle++,
    ...overrides,
  });
  function advance(ms) {
    const target = now + ms;
    while (true) {
      const item = [...timers.entries()]
        .filter(([, t]) => t.at <= target)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (!item) break;
      timers.delete(item[0]);
      now = item[1].at;
      item[1].callback();
    }
    now = target;
  }
  return {
    controller,
    requests,
    progress,
    photos,
    errors,
    timers,
    advance,
    idle: () => idle,
  };
}

test("photo starts immediately with a two second estimate and blocks duplicate clicks", async () => {
  const pending = deferred();
  const requests = [];
  const r = rig({
    capture: (id, signal) => {
      requests.push({ id, signal });
      return pending.promise;
    },
  });
  const work = r.controller.take("call-a");
  assert.equal(requests.length, 1);
  assert.equal(requests[0].id, "call-a");
  assert.equal(requests[0].signal.aborted, false);
  assert.equal(r.controller.snapshot().busy, true);
  assert.equal(r.progress[0].phase, "capturing");
  assert.equal(r.progress[0].remainingSeconds, 2);
  assert.equal(await r.controller.take("call-b"), false);
  assert.equal(requests.length, 1);
  r.advance(1000);
  assert.equal(r.progress.at(-1).remainingSeconds, 1);
  pending.resolve({ url: "/photo.jpg" });
  assert.equal(await work, true);
  assert.equal(r.photos.length, 1);
  assert.equal(r.timers.size, 0);
});

test("an elapsed estimate remains waiting and never reports photo success", async () => {
  const pending = deferred();
  const r = rig({ capture: () => pending.promise });
  const work = r.controller.take("call-a");
  r.advance(5000);
  assert.equal(r.progress.at(-1).remainingSeconds, 0);
  assert.equal(r.progress.at(-1).elapsedSeconds, 5);
  assert.equal(r.progress.at(-1).phase, "capturing");
  assert.equal(r.photos.length, 0);
  pending.resolve({ url: "/photo.jpg" });
  await work;
});

test("capture acknowledgement changes phase but success waits for decoded pixels", async () => {
  const image = deferred();
  const r = rig({ load: () => image.promise });
  const work = r.controller.take("call-a");
  await flush();
  assert.equal(r.progress.at(-1).phase, "loading");
  assert.equal(r.photos.length, 0);
  image.resolve({ naturalWidth: 640, naturalHeight: 480 });
  await work;
  assert.equal(r.photos[0].image.naturalWidth, 640);
  assert.equal(r.controller.snapshot().busy, false);
});

test("camera and image failures release timers and permit a retry", async () => {
  let fail = true;
  const r = rig({
    capture: async () => {
      if (fail) throw Error("camera busy");
      return { url: "/photo.jpg" };
    },
  });
  assert.equal(await r.controller.take("call-a"), false);
  assert.deepEqual(r.errors, ["camera busy"]);
  assert.equal(r.timers.size, 0);
  fail = false;
  assert.equal(await r.controller.take("call-a"), true);
  const imageFailure = rig({
    load: async () => {
      throw Error("bad jpeg");
    },
  });
  assert.equal(await imageFailure.controller.take("call-a"), false);
  assert.equal(imageFailure.photos.length, 0);
  assert.equal(imageFailure.timers.size, 0);
});

test("timeout aborts the HTTP request and a late photo cannot revive a finished operation", async () => {
  const pending = deferred();
  let signal;
  const r = rig({
    capture: (id, value) => {
      signal = value;
      return pending.promise;
    },
  });
  const work = r.controller.take("call-a");
  r.advance(15000);
  assert.equal(await work, false);
  assert.equal(signal.aborted, true);
  assert.match(r.errors[0], /超时/);
  pending.resolve({ url: "/late.jpg" });
  await flush();
  assert.equal(r.photos.length, 0);
  assert.equal(r.timers.size, 0);
});

test("page exit cancels an old decode without clearing or overwriting a newer photo", async () => {
  const old = deferred();
  let index = 0;
  const r = rig({
    load: () =>
      ++index === 1 ? old.promise : Promise.resolve({ naturalWidth: 640 }),
  });
  const first = r.controller.take("call-a");
  await flush();
  r.controller.cancel();
  assert.equal(await first, false);
  assert.equal(await r.controller.take("call-b"), true);
  old.resolve({ naturalWidth: 1 });
  await flush();
  assert.equal(r.photos.length, 1);
  assert.equal(r.photos[0].image.naturalWidth, 640);
  assert.equal(r.errors.length, 0);
  assert.equal(r.timers.size, 0);
});

test("completed captures update the next estimate within a bounded range", async () => {
  const pending = deferred();
  let first = true;
  const r = rig({
    capture: () => (first ? pending.promise : new Promise(() => {})),
  });
  const work = r.controller.take("call-a");
  r.advance(4500);
  pending.resolve({ url: "/photo.jpg" });
  await work;
  first = false;
  const next = r.controller.take("call-b");
  assert.equal(r.progress.at(-1).remainingSeconds, 5);
  r.controller.cancel();
  await next;
});
