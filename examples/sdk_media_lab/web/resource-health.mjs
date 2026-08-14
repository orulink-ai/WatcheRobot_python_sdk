export const RESOURCE_BASELINE_TOLERANCE_BYTES = 8192;

export function selectLatestReleaseSnapshot(history) {
  return [...(history || [])]
    .reverse()
    .find((snapshot) => String(snapshot?.stage || "").startsWith("rtc_release_"));
}

export function selectLifecycleBaseline(resources) {
  const rtcBaseline = resources?.rtc_baseline;
  if (rtcBaseline && Object.keys(rtcBaseline).length > 0) return rtcBaseline;
  return resources?.baseline || null;
}

export function evaluateResourceLifecycle(current, baseline) {
  if (!current || !baseline) return { state: "waiting", deltas: {} };
  const currentInternal = Number(current.memory?.internal?.free_bytes || 0);
  const baselineInternal = Number(baseline.memory?.internal?.free_bytes || 0);
  const currentLargest = Number(current.memory?.internal?.largest_free_block_bytes || 0);
  const baselineLargest = Number(baseline.memory?.internal?.largest_free_block_bytes || 0);
  const deltas = {
    internalFreeBytes: currentInternal - baselineInternal,
    internalLargestBytes: currentLargest - baselineLargest,
  };
  if (current.release?.complete === false || (current.release?.failures || []).length > 0) {
    return { state: "failed", deltas };
  }
  if (!String(current.stage || "").startsWith("rtc_release_")) {
    return { state: "observing", deltas };
  }
  const recovered = deltas.internalFreeBytes >= -RESOURCE_BASELINE_TOLERANCE_BYTES
    && deltas.internalLargestBytes >= -RESOURCE_BASELINE_TOLERANCE_BYTES;
  return { state: recovered ? "recovered" : "degraded", deltas };
}
