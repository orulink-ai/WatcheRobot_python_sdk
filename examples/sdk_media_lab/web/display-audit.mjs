/** Cumulative evidence from unique forward-sequence canvas draws, never packet arrivals.
 * This does not prove sensor field of view, source CRC, or physical scanout. */
export function createDisplayAudit(maxSamples = 36000) {
  let first = null, last = null, sequence = null, frames = 0;
  let duplicates = 0, sequenceErrors = 0, dimensionsInvalid = 0, gapsLost = 0;
  let truncated = false, maxGap = 0, stalls = 0, width = 0, height = 0;
  const intervals = [];
  return {
    record(next, stamp, w, h) {
      if (!Number.isFinite(stamp) || (last !== null && stamp < last) ||
          !Number.isInteger(next) || next < 0 || next > 0xffffffff) {
        sequenceErrors++;
        return;
      }
      if (sequence !== null) {
        const delta = (next - sequence) >>> 0;
        if (delta === 0) { duplicates++; return; }
        if (delta >= 0x80000000) { sequenceErrors++; return; }
        gapsLost += delta - 1;
        const gap = stamp - last;
        maxGap = Math.max(maxGap, gap);
        if (gap > 1000) stalls++;
        if (intervals.length < maxSamples) intervals.push(gap);
        else truncated = true;
      }
      if (w !== 640 || h !== 480) dimensionsInvalid++;
      width = w; height = h;
      if (first === null) first = stamp;
      last = stamp; sequence = next; frames++;
    },
    snapshot(now) {
      const tail = last === null ? 0 : Math.max(0, now - last);
      const seconds = first === null ? 0 : Math.max(0, now - first) / 1000;
      const sorted = [...intervals].sort((a, b) => a - b);
      return {
        method: "unique_forward_canvas_draw", frames, duration_seconds: seconds,
        fps: seconds > 0 ? (frames - 1) / seconds : 0,
        gap_p95_ms: sorted.length ? sorted[Math.ceil(sorted.length * .95) - 1] : 0,
        gap_max_ms: Math.max(maxGap, tail), stalls_over_1s: stalls + (tail > 1000 ? 1 : 0),
        duplicates, sequence_errors: sequenceErrors, sequence_gaps: gapsLost,
        dimensions_invalid: dimensionsInvalid, width, height, truncated,
      };
    },
  };
}
