export const RTC_AUDIO_JITTER_TARGET_MS = 20;

export function calculateRoundTripUs(browserSendUs, browserReceiveUs) {
  const sent = Number(browserSendUs);
  const received = Number(browserReceiveUs);
  if (!Number.isFinite(sent) || !Number.isFinite(received) || sent <= 0 || received <= sent) return 0;
  return Math.round(received - sent);
}

export function selectMediaRoundTripUs(reports) {
  let nominatedUs = 0;
  let fallbackUs = 0;
  reports?.forEach?.((report) => {
    if (report?.type !== "candidate-pair" || report.state !== "succeeded") return;
    const seconds = Number(report.currentRoundTripTime);
    if (!Number.isFinite(seconds) || seconds <= 0) return;
    const roundTripUs = Math.round(seconds * 1_000_000);
    if (report.nominated) nominatedUs = roundTripUs;
    else if (fallbackUs === 0) fallbackUs = roundTripUs;
  });
  return nominatedUs || fallbackUs;
}

export function configureLowLatencyAudioReceivers(
  peer,
  targetMs = RTC_AUDIO_JITTER_TARGET_MS,
) {
  if (!peer?.getReceivers || !Number.isFinite(targetMs) || targetMs < 0 || targetMs > 4000) return 0;
  let configured = 0;
  for (const receiver of peer.getReceivers()) {
    if (receiver?.track?.kind !== "audio") continue;
    let receiverConfigured = false;
    if ("jitterBufferTarget" in receiver) {
      try {
        receiver.jitterBufferTarget = targetMs;
        receiverConfigured = true;
      } catch (_) {
        // Some browsers expose the standards property but reject assignment.
      }
    }
    if ("playoutDelayHint" in receiver) {
      try {
        // Chromium shipped this precursor in seconds. Setting the equivalent
        // value alongside jitterBufferTarget is harmless when both aliases
        // exist and preserves low-latency behavior in older embedded builds.
        receiver.playoutDelayHint = targetMs / 1000;
        receiverConfigured = true;
      } catch (_) {
        // The browser's native adaptive jitter buffer remains the safe fallback.
      }
    }
    if (receiverConfigured) configured += 1;
  }
  return configured;
}

function finiteCounter(value) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : 0;
}

export function sampleAudioJitterBuffer(previous, report) {
  const counter = {
    emitted: finiteCounter(report?.jitterBufferEmittedCount),
    delay: finiteCounter(report?.jitterBufferDelay),
    targetDelay: finiteCounter(report?.jitterBufferTargetDelay),
    minimumDelay: finiteCounter(report?.jitterBufferMinimumDelay),
  };
  const reset = !previous || counter.emitted <= finiteCounter(previous.emitted);
  if (reset) {
    return { sampleValid: false, actualMs: 0, targetMs: 0, minimumMs: 0, counter };
  }

  const emitted = counter.emitted - finiteCounter(previous.emitted);
  const intervalMs = (value, before) => Math.max(
    0,
    Math.round(((value - finiteCounter(before)) / emitted) * 1000),
  );
  return {
    sampleValid: true,
    actualMs: intervalMs(counter.delay, previous.delay),
    targetMs: intervalMs(counter.targetDelay, previous.targetDelay),
    minimumMs: intervalMs(counter.minimumDelay, previous.minimumDelay),
    counter,
  };
}
