import assert from "node:assert/strict";
import test from "node:test";

import { evaluateAnimationConfirmation } from "../../examples/sdk_media_lab/web/animation-confirmation.mjs";

test("accepted animation request remains pending until a device frame is active", () => {
  assert.deepEqual(
    evaluateAnimationConfirmation({
      animationId: "standby_little4",
      requestedAtMs: 1_000,
      nowMs: 3_500,
      active: false,
    }),
    { state: "pending", animationId: "standby_little4" },
  );
});

test("device animation activity confirms the accepted request", () => {
  assert.deepEqual(
    evaluateAnimationConfirmation({
      animationId: "standby_little4",
      requestedAtMs: 1_000,
      nowMs: 1_100,
      active: true,
    }),
    { state: "confirmed", animationId: "standby_little4" },
  );
});

test("missing device activity fails after the prepare deadline", () => {
  assert.deepEqual(
    evaluateAnimationConfirmation({
      animationId: "standby_little4",
      requestedAtMs: 1_000,
      nowMs: 6_001,
      active: false,
    }),
    { state: "failed", animationId: "standby_little4" },
  );
});
