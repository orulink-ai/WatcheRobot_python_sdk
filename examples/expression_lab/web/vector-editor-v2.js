(function exposeVectorEditorV2(root, factory) {
  const vectorPath = typeof module === "object" && module.exports
    ? require("./vector-path.js")
    : root.VectorPath;
  const api = factory(vectorPath);
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.VectorEditorV2 = api;
}(typeof globalThis === "object" ? globalThis : this, (VectorPath) => {
  "use strict";

  const CANVAS_MAX = VectorPath.CANVAS_SIZE - 1;
  const MIRROR_PIVOT = CANVAS_MAX;
  const SMOOTH_SAMPLE_SPACING = 8;

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function clampPoint(point) {
    return {
      x: clamp(Math.round(Number(point?.x) || 0), 0, CANVAS_MAX),
      y: clamp(Math.round(Number(point?.y) || 0), 0, CANVAS_MAX),
    };
  }

  function clone(strokes) {
    return strokes.map((stroke) => ({
      width: stroke.width,
      smooth: stroke.smooth !== false,
      points: stroke.points.map((point) => ({ ...point })),
    }));
  }

  function normalizeEditorStrokes(strokes) {
    const normalized = (Array.isArray(strokes) ? strokes : [])
      .filter((stroke) => stroke && Array.isArray(stroke.points) && stroke.points.length > 0)
      .slice(0, VectorPath.MAX_STROKES)
      .map((stroke) => ({
        width: clamp(Math.round(Number(stroke.width) || 1), 1, VectorPath.MAX_WIDTH),
        smooth: stroke.smooth !== false,
        points: stroke.points.slice(0, VectorPath.MAX_POINTS_PER_STROKE).map(clampPoint),
      }));

    let total = VectorPath.pointCount(normalized);
    while (total > VectorPath.MAX_POINTS) {
      let longestIndex = -1;
      for (let index = 0; index < normalized.length; index += 1) {
        if (normalized[index].points.length <= 1) continue;
        if (longestIndex < 0 || normalized[index].points.length > normalized[longestIndex].points.length) {
          longestIndex = index;
        }
      }
      if (longestIndex < 0) break;
      normalized[longestIndex].points = VectorPath.downsample(
        normalized[longestIndex].points,
        normalized[longestIndex].points.length - 1,
      );
      total -= 1;
    }
    return normalized;
  }

  // A Catmull-Rom spline is evaluated as cubic segments. It passes through the
  // editable anchors while remaining deterministic before V1 polyline export.
  function catmullRomPoint(p0, p1, p2, p3, amount) {
    const t2 = amount * amount;
    const t3 = t2 * amount;
    const coordinate = (a, b, c, d) => 0.5 * (
      2 * b
      + (-a + c) * amount
      + (2 * a - 5 * b + 4 * c - d) * t2
      + (-a + 3 * b - 3 * c + d) * t3
    );
    return clampPoint({
      x: coordinate(p0.x, p1.x, p2.x, p3.x),
      y: coordinate(p0.y, p1.y, p2.y, p3.y),
    });
  }

  function smoothPoints(stroke) {
    const points = stroke.points.map(clampPoint);
    if (stroke.smooth === false || points.length < 3) return points;
    const sampled = [];
    for (let index = 0; index < points.length - 1; index += 1) {
      const p0 = points[Math.max(0, index - 1)];
      const p1 = points[index];
      const p2 = points[index + 1];
      const p3 = points[Math.min(points.length - 1, index + 2)];
      const steps = clamp(
        Math.ceil(VectorPath.pointDistance(p1, p2) / SMOOTH_SAMPLE_SPACING),
        2,
        12,
      );
      for (let step = index === 0 ? 0 : 1; step <= steps; step += 1) {
        const point = catmullRomPoint(p0, p1, p2, p3, step / steps);
        const previous = sampled[sampled.length - 1];
        if (!previous || previous.x !== point.x || previous.y !== point.y) sampled.push(point);
      }
    }
    return sampled;
  }

  function compileToV1(strokes) {
    return VectorPath.normalize(normalizeEditorStrokes(strokes).map((stroke) => ({
      width: stroke.width,
      points: smoothPoints(stroke),
    })));
  }

  function mirrorStroke(stroke, axis) {
    const mirrored = {
      width: stroke.width,
      smooth: stroke.smooth !== false,
      points: stroke.points.map((point) => ({
        x: axis === "horizontal" ? clamp(MIRROR_PIVOT - point.x, 0, CANVAS_MAX) : point.x,
        y: axis === "vertical" ? clamp(MIRROR_PIVOT - point.y, 0, CANVAS_MAX) : point.y,
      })),
    };
    return normalizeEditorStrokes([mirrored])[0];
  }

  function translateStroke(stroke, requestedX, requestedY) {
    const points = stroke.points.map(clampPoint);
    const xs = points.map((point) => point.x);
    const ys = points.map((point) => point.y);
    const dx = clamp(Math.round(requestedX), -Math.min(...xs), CANVAS_MAX - Math.max(...xs));
    const dy = clamp(Math.round(requestedY), -Math.min(...ys), CANVAS_MAX - Math.max(...ys));
    return {
      width: stroke.width,
      smooth: stroke.smooth !== false,
      points: points.map((point) => ({ x: point.x + dx, y: point.y + dy })),
    };
  }

  function duplicateStroke(stroke, offset = 12) {
    const points = stroke.points.map(clampPoint);
    const xs = points.map((point) => point.x);
    const ys = points.map((point) => point.y);
    const chooseOffset = (minimum, maximum) => {
      if (maximum + offset <= CANVAS_MAX) return offset;
      if (minimum - offset >= 0) return -offset;
      return 0;
    };
    return translateStroke(
      stroke,
      chooseOffset(Math.min(...xs), Math.max(...xs)),
      chooseOffset(Math.min(...ys), Math.max(...ys)),
    );
  }

  function hitTestPoint(strokes, point, radius) {
    let best = null;
    let bestDistance = radius;
    strokes.forEach((stroke, strokeIndex) => {
      stroke.points.forEach((anchor, pointIndex) => {
        const distance = VectorPath.pointDistance(anchor, point);
        if (distance <= bestDistance) {
          best = { strokeIndex, pointIndex };
          bestDistance = distance;
        }
      });
    });
    return best;
  }

  function hitTestStroke(strokes, point, radius) {
    let selected = -1;
    let bestDistance = Number.POSITIVE_INFINITY;
    strokes.forEach((stroke, strokeIndex) => {
      const sampled = smoothPoints(stroke);
      const hitRadius = radius + stroke.width / 2;
      if (sampled.length === 1) {
        const distance = VectorPath.pointDistance(sampled[0], point);
        if (distance <= hitRadius && distance < bestDistance) {
          selected = strokeIndex;
          bestDistance = distance;
        }
        return;
      }
      for (let index = 1; index < sampled.length; index += 1) {
        const distance = VectorPath.pointSegmentDistance(point, sampled[index - 1], sampled[index]);
        if (distance <= hitRadius && distance < bestDistance) {
          selected = strokeIndex;
          bestDistance = distance;
        }
      }
    });
    return selected;
  }

  function densifyPoints(points, spacing = 2) {
    const dense = [];
    points.forEach((point, index) => {
      if (index === 0) {
        dense.push(clampPoint(point));
        return;
      }
      const previous = points[index - 1];
      const steps = Math.max(1, Math.ceil(VectorPath.pointDistance(previous, point) / spacing));
      for (let step = 1; step <= steps; step += 1) {
        dense.push(clampPoint({
          x: previous.x + (point.x - previous.x) * step / steps,
          y: previous.y + (point.y - previous.y) * step / steps,
        }));
      }
    });
    return dense;
  }

  // Erasing is evaluated against the same sampled spline that is drawn on the
  // editor canvas. Resulting fragments remain polylines so they cannot curve
  // back across the erased gap when compiled for the V1 device protocol.
  function eraseAt(strokes, point, radius) {
    const center = clampPoint(point);
    const fragments = [];
    normalizeEditorStrokes(strokes).forEach((stroke) => {
      const rendered = densifyPoints(smoothPoints(stroke));
      const hitRadius = Math.max(1, Number(radius) || 1) + stroke.width / 2;
      let fragment = [];
      const flush = () => {
        if (fragment.length > 0) {
          fragments.push({
            width: stroke.width,
            smooth: false,
            points: VectorPath.simplify(fragment, 0.75),
          });
          fragment = [];
        }
      };
      rendered.forEach((renderedPoint) => {
        if (VectorPath.pointDistance(center, renderedPoint) > hitRadius) {
          fragment.push(renderedPoint);
        } else {
          flush();
        }
      });
      flush();
    });
    return normalizeEditorStrokes(fragments);
  }

  return {
    clone,
    compileToV1,
    duplicateStroke,
    eraseAt,
    hitTestPoint,
    hitTestStroke,
    mirrorStroke,
    normalizeEditorStrokes,
    smoothPoints,
    translateStroke,
  };
}));
