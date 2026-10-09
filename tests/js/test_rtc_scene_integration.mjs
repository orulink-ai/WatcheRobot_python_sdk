import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";
import test from "node:test";
import { resolveRtcMode, rtcModeHasAudio, rtcModeHasVideo } from "../../examples/sdk_media_lab/web/media-resource-policy.mjs";
import { createCombinedSceneLifecycle } from "../../examples/sdk_media_lab/web/combined-scene.mjs";
import { microphoneProcessingStatus } from "../../examples/sdk_media_lab/web/rtc-noise-playback.mjs";

const source = fs.readFileSync(new URL("../../examples/sdk_media_lab/web/app.js", import.meta.url), "utf8");
test('diagnostic retains receive loss, concealment and measured worklet input rather than a zero RTC meter', async () => {
  const peer = { getStats: async () => [{ type: 'inbound-rtp', kind: 'audio', packetsReceived: 100,
    packetsLost: 3, concealedSamples: 960, jitter: .02, audioLevel: 0 }] };
  const state = { rtc: { peer, generation: 1, noiseTelemetry: { inputRms: .03 } } };
  const context = { state, elements: { rtcAudioUpPackets: {}, rtcAudioDownPackets: {} },
    isCurrentRtcGeneration: (a,b) => a === b, selectMediaRoundTripUs: () => 0,
    sampleAudioJitterBuffer: () => ({ counter: {}, sampleValid: false }), updateRtcAudioHealth() {}, Math };
  const collect = productionFunction('collectRtcAudioStats', 'async function pollRtcEvents', context);
  await collect(peer, 1);
  assert.equal(state.rtc.browserAudioLevel, .03);
  assert.equal(state.rtc.audioReceiveStats.packetsLost, 3);
  assert.equal(state.rtc.audioReceiveStats.concealedSamples, 960);
  assert.equal(state.rtc.audioReceiveStats.jitterUs, 20000);
});
function productionFunction(name, nextName, context) {
  const start = source.indexOf(`async function ${name}(`);
  const end = source.indexOf(`\n${nextName}`, start);
  assert.ok(start >= 0 && end > start);
  return vm.runInNewContext(`${source.slice(start, end)}\n${name};`, context);
}
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
function fixture() {
  const state = { rtc: { generation: 0, requestId: null, mode: null, peer: null, localStream: null, teardownInProgress: false },
    scene: { operationGeneration: 0 }, localResources: new Set(), status: { connected: true, rtc: {} } };
  const elements = new Proxy({}, { get(target, key) { return target[key] ||= { textContent: "", disabled: false, dataset: {} }; } });
  const context = {
    state, elements, resolveRtcMode, rtcModeHasAudio, rtcModeHasVideo, microphoneProcessingStatus,
    hasCapability: () => true, renderStatus: () => {}, resetLiveVideoMetrics: () => {},
    setRtcAudioState: () => {}, setLiveVideoState: () => {}, notify: () => {},
    refreshStatus: async () => {}, settleCombinedAudioStop: async () => {},
    rtcEndpoint: action => `/api/rtc/session/${action}`,
    rtcDiagnosticAudioEnabled: () => false, rtcBrowserAudioProcessingEnabled: () => false,
    createRtcMicrophoneConstraints: () => ({}),
    crypto: { randomUUID: () => "browser-request-0001" },
    navigator: { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [],
      getAudioTracks: () => [{ getSettings: () => ({}) }] }) } },
    cleanupRtcSession: () => { state.rtc.generation++; state.rtc.mode = null; state.rtc.peer = null; state.rtc.localStream = null; },
    setResult: (element, text) => { element.textContent = text; },
  };
  return { state, elements, context };
}

test("BFCache restore retries the retained RTC release identity before allowing a new call", async () => {
  for (const confirmed of [true, false]) {
    const { state, context } = fixture();
    state.rtc.requestId = "browser-request-0001";
    const requests = [];
    context.api = async (_path, options) => {
      requests.push(JSON.parse(options.body).request_id);
      if (!confirmed) throw new Error("release timeout");
      return {};
    };
    context.scenePageLifecycle = { reopen() {} };
    context.stopRtcSession = productionFunction("stopRtcSession", "async function failRtcSession", context);
    const restore = productionFunction("restoreRtcPageSession", "setInterval(refreshStatus", context);
    await restore();
    assert.deepEqual(requests, ["browser-request-0001"]);
    assert.equal(state.rtc.requestId, confirmed ? null : "browser-request-0001");
  }
});

for (const [reported, label] of [[true, "active"], [false, "inactive"], [undefined, "unreported"]]) {
  test(`capture status uses actual browser gain settings: ${label}`, async () => {
    const { state, elements, context } = fixture();
    const requested = deferred(), ack = deferred();
    context.navigator.mediaDevices.getUserMedia = async () => ({ getTracks: () => [],
      getAudioTracks: () => [{ getSettings: () => ({ autoGainControl: reported }) }] });
    context.api = async path => {
      if (path.endsWith("/start")) { requested.resolve(); return ack.promise; }
      return {};
    };
    const start = productionFunction("startRtcSession", "function bindRtcPeerEvents", context);
    const pending = start("audio");
    await requested.promise;
    assert.equal(elements.rtcAudioLocalState.textContent, `Capturing · microphone gain ${label}`);
    state.rtc.generation++;
    ack.resolve({});
    assert.equal(await pending, false);
  });
}

test("production RTC start branch cannot globally stop a newer call when the old device ACK arrives late", async () => {
  for (const newerCall of [true, false]) {
    const { state, context } = fixture();
    const deviceAck = deferred();
    const deviceRequested = deferred();
    const calls = [];
    const stopOwners = [];
    context.api = async (path, options) => {
      calls.push(path);
      if (path.endsWith("/start")) { deviceRequested.resolve(); return deviceAck.promise; }
      stopOwners.push(JSON.parse(options.body).request_id);
      return {};
    };
    const start = productionFunction("startRtcSession", "function bindRtcPeerEvents", context);
    const oldStart = start("audio");
    await deviceRequested.promise;
    state.rtc.generation += 2;
    state.rtc.mode = newerCall ? "audio" : null;
    state.rtc.peer = newerCall ? {} : null;
    state.rtc.localStream = null;
    state.rtc.requestId = newerCall ? "new-browser-request" : null;
    deviceAck.resolve({ state: "running" });
    assert.equal(await oldStart, false);
    assert.deepEqual(stopOwners, ["browser-request-0001"]);
    assert.equal(state.rtc.requestId, newerCall ? "new-browser-request" : null);
  }
});

test("production RTC stop returns unconfirmed instead of reporting success on a failed device release", async () => {
  const { state, context } = fixture();
  state.rtc.mode = "audio";
  state.rtc.requestId = "browser-request-0001";
  context.api = async () => { throw new Error("release timeout"); };
  const stop = productionFunction("stopRtcSession", "async function failRtcSession", context);
  assert.equal(await stop(), false);
  assert.equal(state.rtc.teardownInProgress, false);
  assert.equal(state.rtc.requestId, "browser-request-0001");
});

test("production late RTC cleanup blocks a new start until the global stop completes", async () => {
  const { state, context } = fixture();
  const deviceAck = deferred();
  const deviceRequested = deferred();
  const stopRequested = deferred();
  const stopAck = deferred();
  const calls = [];
  context.api = async path => {
    calls.push(path);
    if (path.endsWith("/start")) { deviceRequested.resolve(); return deviceAck.promise; }
    stopRequested.resolve();
    return stopAck.promise;
  };
  const start = productionFunction("startRtcSession", "function bindRtcPeerEvents", context);
  const oldStart = start("audio");
  await deviceRequested.promise;
  state.rtc.generation++;
  state.rtc.mode = null;
  state.rtc.localStream = null;
  state.rtc.requestId = null;
  state.localResources.delete("media");
  deviceAck.resolve({ state: "running" });
  await stopRequested.promise;
  assert.equal(state.rtc.teardownInProgress, true);
  assert.equal(await start("audio"), false);
  assert.equal(calls.filter(path => path.endsWith("/start")).length, 1);
  stopAck.resolve({});
  assert.equal(await oldStart, false);
  assert.equal(state.rtc.teardownInProgress, false);
});

test("cancelled pending microphone cleanup cannot stop an external diagnostic RTC session", async () => {
  const { state, context } = fixture();
  const microphone = deferred();
  let externalActive = true;
  const requests = [];
  context.navigator.mediaDevices.getUserMedia = () => microphone.promise;
  context.api = async (path, options) => {
    const body = options?.body ? JSON.parse(options.body) : {};
    requests.push({ path, body });
    if (!body.request_id) externalActive = false;
    return { stopped: false, matched: false };
  };
  const start = productionFunction("startRtcSession", "function bindRtcPeerEvents", context);
  const stop = productionFunction("stopRtcSession", "async function failRtcSession", context);
  const oldStart = start("audio");
  state.status.rtc = { active: true, mode: "audio" };
  assert.equal(await stop(), true);
  let localTracksStopped = 0;
  microphone.resolve({ getTracks: () => [{ stop: () => { localTracksStopped++; } }] });
  assert.equal(await oldStart, false);
  assert.equal(externalActive, true);
  assert.deepEqual(requests, [{ path: "/api/rtc/session/stop", body: { request_id: "browser-request-0001" } }]);
  assert.equal(localTracksStopped, 1);
});

test("old controller cleanup preserves a newer independent local RTC peer", async () => {
  const { state, context } = fixture();
  const newPeer = {};
  state.rtc.requestId = "new-browser-request";
  state.rtc.mode = "audio";
  state.rtc.peer = newPeer;
  let cleanupCalls = 0;
  context.cleanupRtcSession = () => { cleanupCalls++; };
  const requests = [];
  context.api = async (path, options) => {
    requests.push({ path, body: JSON.parse(options.body) });
    return { stopped: false, matched: false };
  };
  const stop = productionFunction("stopRtcSession", "async function failRtcSession", context);
  assert.equal(await stop("old-browser-request"), true);
  assert.deepEqual(requests, [{ path: "/api/rtc/session/stop", body: { request_id: "old-browser-request" } }]);
  assert.equal(cleanupCalls, 0);
  assert.equal(state.rtc.peer, newPeer);
  assert.equal(state.rtc.requestId, "new-browser-request");
});

test("an idle unowned page never stops a status-only external RTC session", async () => {
  const { state, context } = fixture();
  state.status.rtc = { active: true, mode: "audio" };
  let stops = 0;
  context.api = async () => { stops++; };
  const stop = productionFunction("stopRtcSession", "async function failRtcSession", context);
  assert.equal(await stop(), true);
  assert.equal(stops, 0);
});

test("audio entry forwards the combined controller's captured request identity", async () => {
  const calls = [];
  const start = productionFunction("startRtcAudio", "function startRtcControlLoops", {
    startRtcSession: async (...args) => { calls.push(args); return true; },
  });
  assert.equal(await start("scene-attempt-0001"), true);
  assert.deepEqual(Array.from(calls[0]), ["audio", "scene-attempt-0001"]);
});

test("failed stop can retry its retained identity after status no longer supplies a mode", async () => {
  const { state, context } = fixture();
  state.rtc.requestId = "browser-request-0001";
  state.rtc.mode = "audio";
  let attempt = 0;
  context.api = async () => {
    if (++attempt === 1) throw new Error("stop timeout");
    return { stopped: false, matched: false };
  };
  const stop = productionFunction("stopRtcSession", "async function failRtcSession", context);
  assert.equal(await stop(), false);
  assert.equal(await stop(), true);
  assert.equal(state.rtc.requestId, null);
});

test("End Scene retries only a retained combined-owned request after local audio was cleaned up", async () => {
  for (const combinedOwned of [true, false]) {
    const { state, context } = fixture();
    state.rtc.requestId = combinedOwned ? "browser-request-0001:1" : "independent-request";
    const requests = [];
    let attempts = 0;
    const start = source.indexOf("const sceneRtcRequestPrefix =");
    const end = source.indexOf("\nconst actionLabels =", start);
    const lifecycle = vm.runInNewContext(`${source.slice(start, end)}\ncombinedScene;`, {
      ...context, createCombinedSceneLifecycle, updateCombinedScene: () => {},
      stopRtcSession: async requestId => {
        requests.push(requestId);
        return ++attempts > 1;
      },
    });
    if (combinedOwned) {
      await assert.rejects(lifecycle.end(), /stop is unconfirmed/);
      await lifecycle.end();
      assert.deepEqual(requests, ["browser-request-0001:1", "browser-request-0001:1"]);
    } else {
      await lifecycle.end();
      assert.deepEqual(requests, []);
    }
  }
});

test("production scene wrapper cannot overwrite a newer call result with an old cancelled start", async () => {
  const { state, elements, context } = fixture();
  const oldResult = deferred();
  context.combinedScene = { start: () => oldResult.promise };
  const start = productionFunction("startCombinedScene", "async function endCombinedScene", context);
  const oldStart = start();
  state.scene.operationGeneration++;
  state.scene.audioResultActive = true;
  elements.sceneResult.textContent = "New call verified";
  oldResult.resolve(false);
  await oldStart;
  assert.equal(elements.sceneResult.textContent, "New call verified");
  assert.equal(state.scene.audioResultActive, true);
});
