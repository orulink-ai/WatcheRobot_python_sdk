export const RESOURCE_BASELINE_TOLERANCE_BYTES = 8192;
export const RESOURCE_PSRAM_TOLERANCE_BYTES = 128 * 1024;
const LIFECYCLE_CONTEXT_RESOURCES = ["animation", "animation_runtime"];

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

function metric(snapshot, heap, field) {
  const value = snapshot?.memory?.[heap]?.[field];
  return Number.isFinite(Number(value)) ? Number(value) : null;
}

function delta(current, baseline, heap, field) {
  const currentValue = metric(current, heap, field);
  const baselineValue = metric(baseline, heap, field);
  return currentValue === null || baselineValue === null ? null : currentValue - baselineValue;
}

function belowTolerance(value, tolerance) {
  return value !== null && value < -tolerance;
}

function lifecycleContextChanges(current, baseline) {
  return LIFECYCLE_CONTEXT_RESOURCES.filter((name) => {
    const currentResources = current?.resources;
    const baselineResources = baseline?.resources;
    if (!currentResources || !baselineResources) return false;
    if (!Object.hasOwn(currentResources, name) || !Object.hasOwn(baselineResources, name)) return false;
    return Boolean(currentResources[name]) !== Boolean(baselineResources[name]);
  });
}

function animationMemoryContextActive(current, baseline) {
  return [current, baseline].some((snapshot) => (
    snapshot?.resources?.animation === true
    || snapshot?.resources?.animation_runtime === true
  ));
}

function releaseTrend(history) {
  const stableReleaseSamples = (history || [])
    .filter((snapshot) => snapshot?.stage === "rtc_release_3000ms")
    .slice(-4);
  const values = stableReleaseSamples
    .map((snapshot) => metric(snapshot, "internal", "largest_free_block_bytes"))
    .filter((value) => value !== null);
  const monotonicDecline = values.length >= 4
    && values.slice(1).every((value, index) => value < values[index]);
  return {
    sampleCount: values.length,
    monotonicDecline,
    internalLargestLossBytes: monotonicDecline ? values[0] - values.at(-1) : 0,
  };
}

export function evaluateResourceLifecycle(current, baseline, history = []) {
  if (!current || !baseline) {
    return { state: "waiting", deltas: {}, trend: releaseTrend(history), contextChanges: [] };
  }
  const deltas = {
    internalFreeBytes: delta(current, baseline, "internal", "free_bytes"),
    internalLargestBytes: delta(current, baseline, "internal", "largest_free_block_bytes"),
    dmaFreeBytes: delta(current, baseline, "dma", "free_bytes"),
    dmaLargestBytes: delta(current, baseline, "dma", "largest_free_block_bytes"),
    psramFreeBytes: delta(current, baseline, "psram", "free_bytes"),
    psramLargestBytes: delta(current, baseline, "psram", "largest_free_block_bytes"),
  };
  const trend = releaseTrend(history);
  const contextChanges = lifecycleContextChanges(current, baseline);
  if (current.release?.complete === false || (current.release?.failures || []).length > 0) {
    return { state: "failed", deltas, trend, contextChanges };
  }
  if (!String(current.stage || "").startsWith("rtc_release_")) {
    return { state: "observing", deltas, trend, contextChanges };
  }
  if (contextChanges.length > 0) {
    return { state: "context_changed", deltas, trend, contextChanges };
  }
  const coreMemoryDegraded = belowTolerance(deltas.internalFreeBytes, RESOURCE_BASELINE_TOLERANCE_BYTES)
    || belowTolerance(deltas.internalLargestBytes, RESOURCE_BASELINE_TOLERANCE_BYTES)
    || belowTolerance(deltas.dmaFreeBytes, RESOURCE_BASELINE_TOLERANCE_BYTES)
    || belowTolerance(deltas.dmaLargestBytes, RESOURCE_BASELINE_TOLERANCE_BYTES)
    || trend.monotonicDecline;
  if (coreMemoryDegraded) {
    return { state: "degraded", deltas, trend, contextChanges };
  }
  const psramDegraded = belowTolerance(deltas.psramFreeBytes, RESOURCE_PSRAM_TOLERANCE_BYTES)
    || belowTolerance(deltas.psramLargestBytes, RESOURCE_PSRAM_TOLERANCE_BYTES);
  if (psramDegraded && animationMemoryContextActive(current, baseline)) {
    return {
      state: "context_changed",
      deltas,
      trend,
      contextChanges: [...contextChanges, "animation_memory"],
    };
  }
  return { state: psramDegraded ? "degraded" : "recovered", deltas, trend, contextChanges };
}
