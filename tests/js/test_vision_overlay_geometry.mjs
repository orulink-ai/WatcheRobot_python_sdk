import assert from "node:assert/strict";
import test from "node:test";

import {
  deadZoneRect,
  scaleFace,
  targetPoint,
} from "../../examples/vision_debug_lab/web/overlay-geometry.mjs";

test("scales face coordinates independently on both axes", () => {
  assert.deepEqual(
    scaleFace({ x: 10, y: 20, width: 30, height: 40 }, 100, 200, 200, 100),
    { x: 20, y: 10, width: 60, height: 20 },
  );
});

test("centers the guide dead zone", () => {
  assert.deepEqual(deadZoneRect(1000, 500, 0.2), {
    x: 400,
    y: 200,
    width: 200,
    height: 100,
  });
});

test("selects the target face and returns its center", () => {
  const point = targetPoint(
    {
      width: 100,
      height: 100,
      faces: [
        { x: 0, y: 0, width: 10, height: 10, target: 0 },
        { x: 40, y: 20, width: 20, height: 40, target: 1 },
      ],
    },
    200,
    200,
  );
  assert.deepEqual(point, { x: 100, y: 80 });
});
