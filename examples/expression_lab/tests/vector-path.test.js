const test = require("node:test");
const assert = require("node:assert/strict");

const VectorPath = require("../web/vector-path.js");

test("normalization always stays inside the firmware point budget", () => {
  const strokes = Array.from({ length: 12 }, (_, strokeIndex) => ({
    width: 8,
    points: Array.from({ length: 48 }, (_, pointIndex) => ({
      x: pointIndex * 8,
      y: strokeIndex * 20 + (pointIndex % 2) * 8,
    })),
  }));

  const normalized = VectorPath.normalize(strokes);

  assert.equal(normalized.length, 12);
  assert.ok(VectorPath.pointCount(normalized) <= VectorPath.MAX_POINTS);
  assert.ok(normalized.every((stroke) => stroke.points.length >= 1));
});

test("encoding and decoding preserve a bounded vector path", () => {
  const source = [
    { width: 8, points: [{ x: 12, y: 34 }, { x: 210, y: 300 }] },
    { width: 16, points: [{ x: 411, y: 0 }] },
  ];

  const encoded = VectorPath.encode(source);

  assert.deepEqual(VectorPath.decode(encoded), source);
  assert.equal(VectorPath.decode(`${encoded}00`), null);
  assert.equal(VectorPath.decode("0200"), null);
});

test("decoding rejects coordinates and widths outside the firmware contract", () => {
  assert.equal(VectorPath.decode("01013101019c0000"), null);
  assert.equal(VectorPath.decode("01010801019c0000"), null);
});
