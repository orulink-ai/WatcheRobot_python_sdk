import test from "node:test";
import assert from "node:assert/strict";
import { createDisplayAudit } from "../../examples/sdk_media_lab/web/display-audit.mjs";

test("measures unique forward-sequence canvas draws", () => {
  const audit = createDisplayAudit();
  for (let i = 0; i <= 9600; i++) audit.record(i, i * 62.5, 640, 480);
  audit.record(9600, 600001, 640, 480);
  const result = audit.snapshot(600001);
  assert.equal(result.method, "unique_forward_canvas_draw");
  assert.equal(result.frames, 9601);
  assert.equal(result.duplicates, 1);
  assert.ok(Math.abs(result.fps - 16) < .001);
  assert.equal(result.gap_p95_ms, 62.5);
  assert.equal(result.dimensions_invalid, 0);
});

test("reports stalls, non-VGA images, backwards sequences and idle tail", () => {
  const audit = createDisplayAudit();
  audit.record(5, 100, 640, 480);
  audit.record(6, 2200, 416, 416);
  audit.record(4, 2300, 640, 480);
  const result = audit.snapshot(5500);
  assert.equal(result.frames, 2);
  assert.equal(result.sequence_errors, 1);
  assert.equal(result.dimensions_invalid, 1);
  assert.equal(result.gap_max_ms, 3300);
  assert.equal(result.stalls_over_1s, 2);
});

test("bounds evidence memory and flags incomplete statistics", () => {
  const audit = createDisplayAudit(2);
  for (let i = 0; i < 10; i++) audit.record(i, i * 20, 640, 480);
  assert.equal(audit.snapshot(200).truncated, true);
});
