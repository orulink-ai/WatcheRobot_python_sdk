import assert from "node:assert/strict";
import test from "node:test";

import {
  admitVideoFrame,
  finishVideoFrameDecode,
  takePendingVideoFrame,
} from "../../examples/sdk_media_lab/web/video-frame-queue.mjs";

test("first frame owns the decoder", () => {
  const queue = { decodeBusy: false, pendingFrame: null };

  assert.deepEqual(admitVideoFrame(queue, { sequence: 1 }), {
    ownsDecoder: true,
    replacedPending: false,
  });
  assert.equal(queue.decodeBusy, true);
});

test("latest frame replaces only one pending frame", () => {
  const queue = { decodeBusy: true, pendingFrame: { sequence: 1 } };

  assert.deepEqual(admitVideoFrame(queue, { sequence: 2 }), {
    ownsDecoder: false,
    replacedPending: true,
  });
  assert.equal(queue.pendingFrame.sequence, 2);
  assert.equal(takePendingVideoFrame(queue).sequence, 2);
  assert.equal(queue.pendingFrame, null);
});

test("only the decoder owner clears the busy flag", () => {
  const queue = { decodeBusy: true, pendingFrame: null };

  finishVideoFrameDecode(queue, false);
  assert.equal(queue.decodeBusy, true);
  finishVideoFrameDecode(queue, true);
  assert.equal(queue.decodeBusy, false);
});
