export const VIDEO_FEEDBACK_CLEAR_WINDOWS = 3;

export function createVideoCongestionFeedback() {
  return { level: 0, rawLevel: 0, clearWindows: 0 };
}

export function updateVideoCongestionFeedback(previous, metrics) {
  const droppedDelta = Number.isFinite(metrics?.droppedDelta)
    ? Math.max(0, Number(metrics.droppedDelta))
    : Math.max(0, Number(metrics?.droppedFrames || 0) - Number(metrics?.previousDroppedFrames || 0));
  const receivedDelta = Number.isFinite(metrics?.receivedDelta)
    ? Math.max(0, Number(metrics.receivedDelta))
    : Math.max(0, Number(metrics?.receivedFrames || 0) - Number(metrics?.previousReceivedFrames || 0));
  const displayFps = Math.max(0, Number(metrics?.displayFps || 0));
  const targetFps = Math.max(0, Number(metrics?.targetFps || 0));
  const sentFps = Math.max(0, Number(metrics?.sentFps || 0));
  const frameAgeMs = Math.max(0, Number(metrics?.frameAgeMs || 0));
  const attempted = receivedDelta + droppedDelta;
  const dropRatio = attempted > 0 ? droppedDelta / attempted : 0;
  // Browser congestion describes loss after the device has sent a frame. A
  // CPU-limited camera source must not be fed back as browser/network
  // congestion, otherwise the firmware governor forms a self-reinforcing
  // downshift loop. The requested target remains useful until sent telemetry
  // is available; afterwards compare display throughput with actual egress.
  const browserExpectedFps = sentFps > 0 && targetFps > 0
    ? Math.min(sentFps, targetFps)
    : (sentFps || targetFps);
  const displayRatio = browserExpectedFps > 0 ? displayFps / browserExpectedFps : 1;
  const expectedFrameIntervalMs = browserExpectedFps > 0 ? 1000 / browserExpectedFps : 0;
  const moderateFrameAgeMs = Math.max(120, expectedFrameIntervalMs * 1.5);
  const severeFrameAgeMs = Math.max(200, expectedFrameIntervalMs * 2.5);
  const hasVideoEvidence = receivedDelta > 0 || droppedDelta > 0 || frameAgeMs > 0;

  let rawLevel = 0;
  if (droppedDelta >= 4 || (attempted >= 8 && dropRatio >= 0.25)
      || (hasVideoEvidence && displayRatio < 0.7 && frameAgeMs >= severeFrameAgeMs)) {
    rawLevel = 2;
  } else if (droppedDelta > 0 || dropRatio >= 0.1
      || (hasVideoEvidence && displayRatio < 0.85) || frameAgeMs >= moderateFrameAgeMs) {
    rawLevel = 1;
  }

  if (rawLevel > 0) {
    return {
      level: rawLevel,
      rawLevel,
      clearWindows: 0,
      droppedDelta,
      receivedDelta,
      dropRatio,
    };
  }

  const previousLevel = Math.max(0, Number(previous?.level || 0));
  const clearWindows = previousLevel > 0 ? Math.max(0, Number(previous?.clearWindows || 0)) + 1 : 0;
  return {
    level: clearWindows >= VIDEO_FEEDBACK_CLEAR_WINDOWS ? 0 : previousLevel,
    rawLevel: 0,
    clearWindows,
    droppedDelta,
    receivedDelta,
    dropRatio,
  };
}
