const test = require("node:test");
const assert = require("node:assert/strict");

const VectorPath = require("../web/vector-path.js");
const VectorEditorV2 = require("../web/vector-editor-v2.js");

test("smooth editor strokes compile into the existing V1 firmware budget", () => {
  const editorStrokes = [{
    width: 12,
    smooth: true,
    points: [
      { x: 32, y: 260 },
      { x: 120, y: 80 },
      { x: 260, y: 332 },
      { x: 380, y: 120 },
    ],
  }];

  const compiled = VectorEditorV2.compileToV1(editorStrokes);
  const encoded = VectorPath.encode(compiled);

  assert.ok(compiled[0].points.length > editorStrokes[0].points.length);
  assert.ok(VectorPath.pointCount(compiled) <= VectorPath.MAX_POINTS);
  assert.deepEqual(VectorPath.decode(encoded), compiled);
  assert.deepEqual(compiled[0].points[0], editorStrokes[0].points[0]);
  assert.deepEqual(compiled[0].points.at(-1), editorStrokes[0].points.at(-1));
});

test("editor normalization preserves bounded metadata and rejects invalid coordinates", () => {
  const normalized = VectorEditorV2.normalizeEditorStrokes([
    { width: 80, smooth: true, points: [{ x: -10, y: 500 }, { x: 100, y: 120 }] },
  ]);

  assert.equal(normalized[0].width, VectorPath.MAX_WIDTH);
  assert.equal(normalized[0].smooth, true);
  assert.deepEqual(normalized[0].points[0], { x: 0, y: 411 });
});

test("mirror, duplicate, and translation edit selected strokes without escaping the canvas", () => {
  const stroke = { width: 8, smooth: false, points: [{ x: 20, y: 40 }, { x: 100, y: 160 }] };

  assert.deepEqual(VectorEditorV2.mirrorStroke(stroke, "horizontal").points, [
    { x: 391, y: 40 },
    { x: 311, y: 160 },
  ]);
  assert.deepEqual(VectorEditorV2.mirrorStroke(stroke, "vertical").points, [
    { x: 20, y: 371 },
    { x: 100, y: 251 },
  ]);
  assert.deepEqual(
    VectorEditorV2.mirrorStroke(VectorEditorV2.mirrorStroke(stroke, "horizontal"), "horizontal"),
    stroke,
  );
  assert.deepEqual(VectorEditorV2.translateStroke(stroke, -100, 400).points, [
    { x: 0, y: 291 },
    { x: 80, y: 411 },
  ]);
  assert.deepEqual(VectorEditorV2.duplicateStroke(stroke).points, [
    { x: 32, y: 52 },
    { x: 112, y: 172 },
  ]);

  const edgeStroke = { width: 8, smooth: false, points: [{ x: 350, y: 350 }, { x: 411, y: 411 }] };
  assert.deepEqual(VectorEditorV2.duplicateStroke(edgeStroke).points, [
    { x: 338, y: 338 },
    { x: 399, y: 399 },
  ]);
});

test("point and stroke hit testing support direct manipulation", () => {
  const strokes = [
    { width: 8, smooth: false, points: [{ x: 20, y: 20 }, { x: 120, y: 20 }] },
    { width: 8, smooth: false, points: [{ x: 200, y: 200 }, { x: 260, y: 260 }] },
  ];

  assert.deepEqual(VectorEditorV2.hitTestPoint(strokes, { x: 202, y: 198 }, 10), {
    strokeIndex: 1,
    pointIndex: 0,
  });
  assert.equal(VectorEditorV2.hitTestStroke(strokes, { x: 80, y: 25 }, 12), 0);
  assert.equal(VectorEditorV2.hitTestStroke(strokes, { x: 400, y: 400 }, 12), -1);
});

test("stroke hit testing includes the visible half width of thick paths", () => {
  const strokes = [
    { width: 24, smooth: false, points: [{ x: 20, y: 100 }, { x: 220, y: 100 }] },
  ];

  assert.equal(VectorEditorV2.hitTestStroke(strokes, { x: 100, y: 117 }, 6), 0);
  assert.equal(VectorEditorV2.hitTestStroke(strokes, { x: 100, y: 120 }, 6), -1);
});

test("eraser removes the rendered path and leaves stable non-smoothed fragments", () => {
  const strokes = [
    { width: 8, smooth: false, points: [{ x: 20, y: 200 }, { x: 392, y: 200 }] },
  ];

  const erased = VectorEditorV2.eraseAt(strokes, { x: 206, y: 200 }, 18);

  assert.equal(erased.length, 2);
  assert.ok(erased.every((stroke) => stroke.smooth === false));
  assert.ok(erased[0].points.at(-1).x < 184);
  assert.ok(erased[1].points[0].x > 228);
});

test("eraser follows the same sampled geometry used to display smooth curves", () => {
  const stroke = {
    width: 10,
    smooth: true,
    points: [{ x: 30, y: 320 }, { x: 120, y: 60 }, { x: 300, y: 330 }, { x: 390, y: 80 }],
  };
  const rendered = VectorEditorV2.smoothPoints(stroke);
  const target = rendered[Math.floor(rendered.length * 0.45)];

  const erased = VectorEditorV2.eraseAt([stroke], target, 12);

  assert.ok(erased.length >= 1);
  assert.ok(erased.every((fragment) => fragment.smooth === false));
  assert.ok(erased.every((fragment) => fragment.points.every((point) =>
    VectorPath.pointDistance(point, target) > 17
  )));
});
