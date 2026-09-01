(function exposeVectorPath(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.VectorPath = api;
}(typeof globalThis === "object" ? globalThis : this, () => {
  "use strict";

  const MAX_STROKES = 12;
  const MAX_POINTS_PER_STROKE = 48;
  const MAX_POINTS = 192;
  const MAX_WIDTH = 48;
  const CANVAS_SIZE = 412;

  function clone(strokes) {
    return strokes.map((stroke) => ({
      width: stroke.width,
      points: stroke.points.map((point) => ({ x: point.x, y: point.y })),
    }));
  }

  function pointCount(strokes) {
    return strokes.reduce((total, stroke) => total + stroke.points.length, 0);
  }

  function pointDistance(first, second) {
    return Math.hypot(first.x - second.x, first.y - second.y);
  }

  function pointSegmentDistance(point, start, end) {
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    if (dx === 0 && dy === 0) return pointDistance(point, start);
    const amount = Math.max(0, Math.min(1, (
      (point.x - start.x) * dx + (point.y - start.y) * dy
    ) / (dx * dx + dy * dy)));
    return Math.hypot(point.x - (start.x + dx * amount), point.y - (start.y + dy * amount));
  }

  function simplify(points, tolerance = 1.4) {
    if (points.length <= 2) return points.map((point) => ({ ...point }));
    let maxDistance = 0;
    let splitIndex = 0;
    for (let index = 1; index < points.length - 1; index += 1) {
      const distance = pointSegmentDistance(points[index], points[0], points[points.length - 1]);
      if (distance > maxDistance) {
        maxDistance = distance;
        splitIndex = index;
      }
    }
    if (maxDistance <= tolerance) return [{ ...points[0] }, { ...points[points.length - 1] }];
    const left = simplify(points.slice(0, splitIndex + 1), tolerance);
    const right = simplify(points.slice(splitIndex), tolerance);
    return left.slice(0, -1).concat(right);
  }

  function downsample(points, maximum) {
    if (points.length <= maximum) return points.map((point) => ({ ...point }));
    if (maximum <= 1) return [{ ...points[0] }];
    return Array.from({ length: maximum }, (_, index) => ({
      ...points[Math.round(index * (points.length - 1) / (maximum - 1))],
    }));
  }

  function normalize(strokes) {
    const normalized = strokes
      .filter((stroke) => stroke && Array.isArray(stroke.points) && stroke.points.length > 0)
      .slice(0, MAX_STROKES)
      .map((stroke) => ({
        width: Math.max(1, Math.min(MAX_WIDTH, Math.round(Number(stroke.width) || 1))),
        points: downsample(simplify(stroke.points.map((point) => ({
          x: Math.max(0, Math.min(CANVAS_SIZE - 1, Math.round(Number(point.x) || 0))),
          y: Math.max(0, Math.min(CANVAS_SIZE - 1, Math.round(Number(point.y) || 0))),
        }))), MAX_POINTS_PER_STROKE),
      }));

    let total = pointCount(normalized);
    while (total > MAX_POINTS) {
      let longestIndex = -1;
      for (let index = 0; index < normalized.length; index += 1) {
        if (normalized[index].points.length <= 1) continue;
        if (longestIndex < 0 || normalized[index].points.length > normalized[longestIndex].points.length) {
          longestIndex = index;
        }
      }
      if (longestIndex < 0) break;
      const stroke = normalized[longestIndex];
      stroke.points = downsample(stroke.points, stroke.points.length - 1);
      total -= 1;
    }
    return normalized;
  }

  function encode(strokes) {
    const bounded = normalize(strokes);
    const bytes = [1, bounded.length];
    bounded.forEach((stroke) => {
      bytes.push(stroke.width, stroke.points.length);
      stroke.points.forEach((point) => {
        bytes.push((point.x >> 8) & 0xff, point.x & 0xff, (point.y >> 8) & 0xff, point.y & 0xff);
      });
    });
    return bytes.map((value) => value.toString(16).padStart(2, "0")).join("");
  }

  function decode(path) {
    const maximumHexLength = (2 + MAX_STROKES * 2 + MAX_POINTS * 4) * 2;
    if (typeof path !== "string" || path.length < 4 || path.length > maximumHexLength || path.length % 2 || !/^[0-9a-f]+$/i.test(path)) return null;
    const bytes = new Uint8Array(path.match(/../g).map((value) => Number.parseInt(value, 16)));
    if (bytes[0] !== 1 || bytes[1] > MAX_STROKES) return null;
    const strokes = [];
    let offset = 2;
    let total = 0;
    for (let index = 0; index < bytes[1]; index += 1) {
      if (offset + 2 > bytes.length) return null;
      const width = bytes[offset];
      const count = bytes[offset + 1];
      offset += 2;
      total += count;
      if (width < 1 || width > MAX_WIDTH || count < 1 || count > MAX_POINTS_PER_STROKE || total > MAX_POINTS || offset + count * 4 > bytes.length) return null;
      const points = [];
      for (let point = 0; point < count; point += 1) {
        const x = bytes[offset] * 256 + bytes[offset + 1];
        const y = bytes[offset + 2] * 256 + bytes[offset + 3];
        offset += 4;
        if (x >= CANVAS_SIZE || y >= CANVAS_SIZE) return null;
        points.push({ x, y });
      }
      strokes.push({ width, points });
    }
    return offset === bytes.length ? strokes : null;
  }

  return {
    CANVAS_SIZE,
    MAX_POINTS,
    MAX_POINTS_PER_STROKE,
    MAX_STROKES,
    clone,
    decode,
    downsample,
    encode,
    normalize,
    pointCount,
    pointDistance,
    simplify,
  };
}));
