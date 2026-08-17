import assert from "node:assert/strict";

import {
  clampAnimationIntervalMs,
  createAnimationShuffleBag,
  normalizeAnimationCatalog,
  selectNextAnimation,
} from "../../examples/sdk_media_lab/web/animation-random.mjs";

assert.deepEqual(
  normalizeAnimationCatalog(["boot", "happy", "boot", "../unsafe", "UPPER", 7, "standby_little4"]),
  ["boot", "happy", "standby_little4"],
);
assert.deepEqual(normalizeAnimationCatalog(null), []);

assert.equal(selectNextAnimation(["boot"], "boot", () => 0.5), "boot");
assert.equal(selectNextAnimation(["boot", "happy", "smile"], "boot", () => 0), "happy");
assert.equal(selectNextAnimation(["boot", "happy", "smile"], "happy", () => 0.999), "smile");
assert.equal(selectNextAnimation([], null, () => 0.5), null);

const shuffledCycle = createAnimationShuffleBag(
  ["boot", "happy", "smile", "standby_little4"],
  "boot",
  () => 0,
);
assert.deepEqual([...shuffledCycle].sort(), ["boot", "happy", "smile", "standby_little4"]);
assert.equal(new Set(shuffledCycle).size, 4);
assert.notEqual(shuffledCycle[0], "boot");
assert.deepEqual(createAnimationShuffleBag(["boot"], "boot", () => 0.5), ["boot"]);
assert.deepEqual(createAnimationShuffleBag([], null, () => 0.5), []);

assert.equal(clampAnimationIntervalMs(1000), 3000);
assert.equal(clampAnimationIntervalMs(8000), 8000);
assert.equal(clampAnimationIntervalMs(300000), 120000);
assert.equal(clampAnimationIntervalMs(Number.NaN), 8000);

console.log("animation_random_tests: PASS");
