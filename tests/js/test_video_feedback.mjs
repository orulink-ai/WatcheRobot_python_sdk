import assert from "node:assert/strict";
import test from "node:test";

import {
  createVideoCongestionFeedback,
  deviceVideoCongestionLevel,
  updateVideoCongestionFeedback,
} from "../../examples/sdk_media_lab/web/video-feedback.mjs";

test("one historical drop does not latch congestion forever", () => {
  let feedback = createVideoCongestionFeedback();
  feedback = updateVideoCongestionFeedback(feedback, {
    droppedDelta: 1,
    displayFps: 9,
    targetFps: 10,
    frameAgeMs: 80,
  });
  assert.equal(feedback.level, 1);

  for (let index = 0; index < 3; index += 1) {
    feedback = updateVideoCongestionFeedback(feedback, {
      droppedDelta: 0,
      displayFps: 10,
      targetFps: 10,
      frameAgeMs: 40,
    });
  }
  assert.equal(feedback.level, 0);
});

test("device receives only current pressure instead of the browser clear latch", () => {
  assert.equal(deviceVideoCongestionLevel({ level: 1, rawLevel: 0, clearWindows: 2 }), 0);
  assert.equal(deviceVideoCongestionLevel({ level: 2, rawLevel: 2, clearWindows: 0 }), 2);
});

test("sustained drops become severe congestion", () => {
  let feedback = createVideoCongestionFeedback();
  feedback = updateVideoCongestionFeedback(feedback, {
    droppedDelta: 4,
    displayFps: 5,
    targetFps: 10,
    frameAgeMs: 250,
  });

  assert.equal(feedback.level, 2);
});

test("slow display without drops reports moderate congestion", () => {
  const feedback = updateVideoCongestionFeedback(createVideoCongestionFeedback(), {
    droppedDelta: 0,
    displayFps: 7,
    targetFps: 10,
    frameAgeMs: 150,
  });

  assert.equal(feedback.level, 1);
});

test("session startup without a first frame is not reported as congestion", () => {
  const feedback = updateVideoCongestionFeedback(createVideoCongestionFeedback(), {
    receivedDelta: 0,
    droppedDelta: 0,
    displayFps: 0,
    targetFps: 10,
    frameAgeMs: 0,
  });

  assert.equal(feedback.level, 0);
});

test("a slow device source is not mistaken for browser congestion", () => {
  const feedback = updateVideoCongestionFeedback(createVideoCongestionFeedback(), {
    receivedDelta: 5,
    droppedDelta: 0,
    displayFps: 5,
    sentFps: 5,
    targetFps: 12,
    frameAgeMs: 200,
  });

  assert.equal(feedback.level, 0);
});
