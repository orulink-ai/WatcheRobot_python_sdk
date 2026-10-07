import { controlAvailability, resolveRtcMode, rtcModeHasAudio } from "./media-resource-policy.mjs";

export const SCENE_HISTORY_LIMIT = 240;

export function sceneRecordingLabel(value) {
  return String(value ?? "").trim().slice(0, 80) || "combined-scene";
}

export function createScenePageLifecycle() {
  let generation = 0;
  let pendingStarts = 0;
  return {
    async startProcedural(start, stopLateStart) {
      const startedGeneration = generation;
      pendingStarts++;
      try {
        const result = await start();
        if (result && startedGeneration !== generation) await stopLateStart();
        return result;
      } finally { pendingStarts--; }
    },
    close(proceduralState) {
      generation++;
      return pendingStarts > 0 || ["starting", "running", "stop_required"].includes(proceduralState);
    },
    reopen() { generation++; },
  };
}

export function createCombinedSceneLifecycle({
  isProceduralRunning, startProcedural, stopProcedural, startAudio, stopAudio, onChange = () => {},
}) {
  let generation = 0;
  let pendingGeneration = null;
  const pendingProcedural = new Set();
  let active = false;
  let proceduralOwner = null;
  let audioOwner = null;
  let releasePromise = null;
  let stoppingPromise = null;
  const snapshot = () => ({ active, ownsProcedural: proceduralOwner !== null,
    starting: pendingGeneration !== null, stopping: Boolean(stoppingPromise),
    busy: pendingGeneration !== null || pendingProcedural.size > 0 || Boolean(stoppingPromise || releasePromise) });
  const notify = () => onChange(snapshot());

  async function releaseAnimation(owner = proceduralOwner) {
    if (owner === null || owner !== proceduralOwner) return;
    if (releasePromise) return releasePromise;
    releasePromise = (async () => {
      const acknowledgement = await stopProcedural();
      if (acknowledgement?.state !== "idle") throw new Error("Procedural stop is unconfirmed");
      if (proceduralOwner === owner) proceduralOwner = null;
    })();
    try { await releasePromise; }
    finally { releasePromise = null; notify(); }
  }

  async function stopAttemptAudio(owner, lateSuccess = false) {
    if (audioOwner !== owner && !(lateSuccess && audioOwner === null)) return;
    await stopAudio(owner);
    if (audioOwner === owner) audioOwner = null;
  }

  return {
    snapshot,
    async start() {
      if (snapshot().busy || active) return false;
      const token = ++generation;
      pendingGeneration = token;
      notify();
      let attemptedAudio = false;
      let attemptedProcedural = false;
      try {
        if (!isProceduralRunning()) {
          attemptedProcedural = true;
          proceduralOwner = token;
          pendingProcedural.add(token);
          let acknowledgement;
          try { acknowledgement = await startProcedural(); }
          finally { pendingProcedural.delete(token); }
          if (acknowledgement?.state !== "running") throw new Error("Procedural animation did not start");
          if (proceduralOwner === null && token !== generation) proceduralOwner = token;
        }
        if (token !== generation) { await releaseAnimation(token); return false; }
        attemptedAudio = true;
        audioOwner = token;
        if (await startAudio(token) !== true) throw new Error("Full-duplex call did not start");
        if (token !== generation) { await stopAttemptAudio(token, true); await releaseAnimation(token); return false; }
        active = true;
        return true;
      } catch (error) {
        let cleanupError = null;
        if (attemptedAudio) { try { await stopAttemptAudio(token); } catch (failure) { cleanupError = failure; } }
        if (attemptedProcedural && !attemptedAudio && proceduralOwner === null && token !== generation) proceduralOwner = token;
        try { await releaseAnimation(token); } catch (failure) { cleanupError = failure; }
        if (cleanupError) throw new Error(`${error.message}; ${cleanupError.message}`, { cause: error });
        if (token !== generation) return false;
        throw error;
      } finally { if (pendingGeneration === token) pendingGeneration = null; notify(); }
    },
    async end() {
      if (stoppingPromise) return stoppingPromise;
      generation++;
      pendingGeneration = null;
      active = false;
      stoppingPromise = (async () => {
        try { await stopAudio(audioOwner); audioOwner = null; }
        finally { await releaseAnimation(); }
      })();
      notify();
      try { await stoppingPromise; }
      finally { stoppingPromise = null; notify(); }
    },
    async audioStopped() {
      if (pendingGeneration === null) generation++;
      active = false;
      audioOwner = null;
      await releaseAnimation();
      notify();
    },
    proceduralStopped() { proceduralOwner = null; notify(); },
    cancelForPageExit() { generation++; pendingGeneration = null; active = false; },
  };
}

export function combinedSceneAvailability(status, {
  localResources = new Set(), rtcMode = null, teardownInProgress = false,
} = {}) {
  const owners = status?.resource_owners || {};
  const mode = resolveRtcMode(rtcMode, status?.rtc?.mode,
    owners.camera || owners.microphone || owners.speaker, status?.rtc?.active === true);
  const availability = controlAvailability({
    connected: status?.connected, capabilities: status?.capabilities,
    resourceOwners: owners, localResources, rtcActive: Boolean(mode || status?.rtc?.active), rtcMode: mode,
  });
  const local = localResources instanceof Set ? localResources : new Set(localResources);
  const proceduralSupported = (status?.capabilities || []).includes("expression.audio_follow.v1");
  const proceduralState = status?.procedural?.state || "idle";
  const proceduralRunning = proceduralSupported && proceduralState === "running";
  const proceduralPending = local.has("animation") || ["starting", "stopping"].includes(proceduralState);
  return {
    proceduralSupported,
    startProcedural: Boolean(status?.connected) && proceduralSupported && !proceduralPending
      && proceduralState === "idle" && !owners.animation,
    stopProcedural: Boolean(status?.connected) && proceduralSupported && !proceduralPending
      && ["running", "stop_required"].includes(proceduralState),
    startAudio: availability.startRtcAudio && !teardownInProgress
      && (status?.capabilities || []).includes("rtc.audio.full_duplex.v1"),
    stopAudio: availability.stopRtc && rtcModeHasAudio(mode),
    snapshot: availability.camera && (status?.capabilities || []).includes("camera.capture")
      && !proceduralPending && (proceduralRunning || (!owners.animation && !local.has("animation"))),
    photoResources: proceduralRunning ? ["camera"] : ["camera", "animation"],
  };
}

export function evaluateProceduralRender(status) {
  const animation = status?.resources?.current?.animation || {};
  const updateErrors = measurement(animation.update_errors);
  if (!status?.connected || (status.resources?.telemetry && status.resources.telemetry.status !== "available")) {
    return { state: "waiting_data", updateErrors: null };
  }
  if (status.procedural?.state !== "running") return { state: status.procedural?.state || "idle", updateErrors };
  if (animation.active === false && updateErrors > 0) return { state: "failed", updateErrors };
  if (animation.active === true && updateErrors > 0) return { state: "degraded", updateErrors };
  return { state: animation.active === true && animation.sample_valid === true ? "running" : "warming", updateErrors };
}

function measurement(value) {
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0 ? numeric : null;
}

export function audioDropCounters(stats) {
  return {
    uplinkFrames: measurement(stats?.audio_tx_dropped_frames),
    playbackFrames: measurement(stats?.audio_queue_dropped),
  };
}

function heapSample(memory, heap) {
  return {
    free: measurement(memory?.[heap]?.free_bytes),
    minimum: measurement(memory?.[heap]?.minimum_free_bytes),
    largest: measurement(memory?.[heap]?.largest_free_block_bytes),
  };
}

export function createSceneSample(status, { nowMs = Date.now(), rtcMode = null } = {}) {
  const snapshot = status?.resources?.current;
  const freshness = status?.resources?.telemetry?.status;
  if (!status?.connected || (freshness && freshness !== "available")
    || !snapshot || measurement(snapshot.sequence) === null) return null;
  const animation = snapshot.animation || {};
  const procedural = status.procedural || {};
  const audio = status.rtc?.stats || {};
  const drops = audioDropCounters(audio);
  const proceduralFrames = procedural.state === "running" && animation.audio_follow === true;
  const fps = proceduralFrames && animation.sample_valid === true ? measurement(animation.measured_fps_x100) : null;
  return {
    deviceId: status.device?.device_id || "unknown",
    sequence: snapshot.sequence,
    capturedAtMs: measurement(snapshot.captured_at_ms),
    timestampMs: nowMs,
    stage: snapshot.stage || "unknown",
    proceduralState: procedural.state || "idle",
    rtcMode: rtcMode || (status.rtc?.active ? status.rtc.mode : null),
    internal: heapSample(snapshot.memory, "internal"),
    dma: heapSample(snapshot.memory, "dma"),
    psram: heapSample(snapshot.memory, "psram"),
    animationFps: fps === null ? null : fps / 100,
    animationTargetFps: !proceduralFrames || measurement(animation.target_fps_x100) === null
      ? null : Number(animation.target_fps_x100) / 100,
    mouthLevelMilli: procedural.telemetry_available === true ? measurement(procedural.mouth_level_milli) : null,
    pcmFrames: procedural.telemetry_available === true ? measurement(procedural.pcm_frames) : null,
    audioTxErrors: measurement(audio.audio_tx_errors),
    audioRenderErrors: measurement(audio.audio_render_errors),
    audioTxDroppedFrames: drops.uplinkFrames,
    audioQueueDroppedFrames: drops.playbackFrames,
  };
}

export function appendSceneSample(samples, sample, limit = SCENE_HISTORY_LIMIT) {
  if (!sample) return samples;
  const previous = samples.at(-1);
  if (previous && (previous.deviceId !== sample.deviceId
    || (sample.capturedAtMs !== null && previous.capturedAtMs !== null && sample.capturedAtMs < previous.capturedAtMs))) {
    return [sample];
  }
  if (samples.some(item => item.deviceId === sample.deviceId && item.sequence === sample.sequence)) return samples;
  const boundedLimit = Number.isInteger(limit) && limit > 0 ? limit : SCENE_HISTORY_LIMIT;
  return [...samples, sample].slice(-boundedLimit);
}

export function summarizeSceneSamples(samples) {
  const minimum = (read) => {
    const values = samples.map(read).filter(value => value !== null && Number.isFinite(value));
    return values.length ? Math.min(...values) : null;
  };
  return {
    sampleCount: samples.length,
    durationMs: samples.length > 1 ? Math.max(0, samples.at(-1).timestampMs - samples[0].timestampMs) : 0,
    internalFreeLow: minimum(sample => sample.internal.free),
    dmaLargestLow: minimum(sample => sample.dma.largest),
    psramFreeLow: minimum(sample => sample.psram.free),
    animationFpsLow: minimum(sample => sample.animationFps),
  };
}
