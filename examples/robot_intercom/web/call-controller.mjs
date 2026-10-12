import { createRtcMicrophoneConstraints } from "./rtc-audio-capture.mjs";
import { createConnectionRecovery } from "./rtc-connection-recovery.mjs";
import {
  configureLowLatencyAudioReceivers,
  sampleAudioJitterBuffer,
  selectMediaRoundTripUs,
} from "./rtc-audio-latency.mjs";

/** One page owns one call. Async microphone/ACK work never owns a newer call. */
export function createCallController({
  api,
  getMicrophone = () =>
    navigator.mediaDevices.getUserMedia({
      audio: createRtcMicrophoneConstraints(),
      video: false,
    }),
  makePeer = () => new RTCPeerConnection({ iceServers: [] }),
  makeId = () => crypto.randomUUID(),
  schedule = (fn, ms) => setTimeout(fn, ms),
  cancel = (id) => clearTimeout(id),
  onState = () => {},
  onRemote = () => {},
  onStats = () => {},
  onDispose = () => {},
} = {}) {
  let generation = 0,
    requestId = null,
    state = "idle",
    stream = null,
    peer = null,
    allocation = null;
  let allocated = false,
    stopping = null,
    recovery = null,
    muted = false,
    remoteCandidates = [],
    jitter = null;
  const timers = new Set();
  const snapshot = () => ({ requestId, state, muted });
  function update(next, message = "") {
    state = next;
    onState({ ...snapshot(), message });
  }
  const current = (token) => token === generation && requestId !== null;
  function later(callback, delay) {
    const id = schedule(() => {
      timers.delete(id);
      callback();
    }, delay);
    timers.add(id);
    return id;
  }
  function dispose() {
    for (const timer of timers) cancel(timer);
    timers.clear();
    recovery?.stop();
    recovery = null;
    peer?.close();
    peer = null;
    for (const track of stream?.getTracks() || []) track.stop();
    stream = null;
    remoteCandidates = [];
    jitter = null;
    onDispose();
  }
  async function fail(error) {
    if (
      state === "stopping" ||
      state === "idle" ||
      state === "cleanup_required"
    )
      return;
    try {
      await controller.stop();
      update("idle", error.message || String(error));
    } catch (releaseError) {
      update(
        "cleanup_required",
        `通话已关闭，设备释放待重试：${releaseError.message}`,
      );
    }
  }
  async function poll(token, id) {
    if (!current(token)) return;
    try {
      const payload = await api(
        `/api/call/events?request_id=${encodeURIComponent(id)}&after=${poll.cursor}`,
      );
      if (!current(token)) return;
      for (const event of payload.events || []) {
        poll.cursor = Math.max(poll.cursor, event.id || 0);
        const message = event.message || {},
          data = message.data || {};
        if (
          message.type === "sys.nack" ||
          (message.type === "evt.rtc.state" && data.state === "failed")
        )
          throw Error(data.reason || data.error || "机器人通话中断");
        if (message.type === "evt.rtc.state" && data.state === "stopped")
          throw Error("机器人已结束通话");
        if (message.type === "evt.rtc.signal" && peer) {
          const activePeer = peer;
          if (
            data.kind === "answer" &&
            data.sdp &&
            !activePeer.remoteDescription
          ) {
            await activePeer.setRemoteDescription({
              type: "answer",
              sdp: data.sdp,
            });
            if (!current(token)) return;
            configureLowLatencyAudioReceivers(activePeer);
            for (const candidate of remoteCandidates.splice(0)) {
              await activePeer.addIceCandidate(candidate);
              if (!current(token)) return;
            }
          } else if (data.kind === "candidate" && data.candidate) {
            const candidate = {
              candidate: data.candidate,
              sdpMid: data.sdp_mid,
              sdpMLineIndex: data.sdp_mline_index,
            };
            if (activePeer.remoteDescription)
              await activePeer.addIceCandidate(candidate);
            else remoteCandidates.push(candidate);
          }
        }
        if (!current(token)) return;
      }
      if (current(token)) later(() => poll(token, id), 120);
    } catch (error) {
      if (current(token)) await fail(error);
    }
  }
  async function heartbeat(token, id) {
    if (!current(token)) return;
    try {
      await api("/api/call/heartbeat", {
        request_id: id,
        browser_send_us: Math.round(
          (performance.timeOrigin + performance.now()) * 1000,
        ),
      });
    } catch (error) {
      if (current(token)) await fail(error);
      return;
    }
    if (current(token)) later(() => heartbeat(token, id), 1500);
  }
  async function feedback(token, id) {
    const activePeer = peer;
    if (!current(token) || !activePeer) return;
    try {
      const reports = await activePeer.getStats();
      if (!current(token)) return;
      let sent = 0,
        received = 0,
        lost = 0,
        queue = 0,
        jitterUs = 0,
        concealed = 0,
        level = 0;
      reports.forEach((report) => {
        if (report.kind !== "audio" && report.mediaType !== "audio") return;
        if (report.type === "outbound-rtp") sent += report.packetsSent || 0;
        if (report.type === "media-source")
          level = Math.max(level, report.audioLevel || 0);
        if (report.type === "inbound-rtp") {
          received += report.packetsReceived || 0;
          lost += Math.max(0, report.packetsLost || 0);
          jitterUs = Math.max(jitterUs, Math.round((report.jitter || 0) * 1e6));
          concealed += report.concealedSamples || 0;
          const sample = sampleAudioJitterBuffer(jitter, report);
          jitter = sample.counter;
          queue = Math.max(queue, sample.actualMs);
        }
      });
      onStats({ sent, received, lost, level });
      await api("/api/call/feedback", {
        request_id: id,
        display_fps_x100: 0,
        frame_age_p95_us: 0,
        rtt_us: selectMediaRoundTripUs(reports),
        audio_queue_ms: queue,
        audio_packet_loss_x100: Math.round(
          (lost / Math.max(1, received + lost)) * 10000,
        ),
        audio_jitter_us: jitterUs,
        audio_concealed_frames: concealed,
        congestion_level: 0,
      });
    } catch (error) {
      if (current(token)) await fail(error);
      return;
    }
    if (current(token)) later(() => feedback(token, id), 1000);
  }
  const controller = {
    snapshot,
    async start() {
      if (requestId || stopping) return false;
      const token = ++generation,
        id = makeId();
      requestId = id;
      allocated = false;
      muted = false;
      update("starting", "请允许使用电脑麦克风…");
      try {
        const microphone = await getMicrophone();
        if (!current(token)) {
          for (const track of microphone.getTracks()) track.stop();
          return false;
        }
        stream = microphone;
        allocated = true;
        allocation = api("/api/call/start", { request_id: id });
        await allocation;
        if (!current(token)) return false;
        const activePeer = (peer = makePeer());
        for (const track of stream.getAudioTracks())
          activePeer.addTrack(track, stream);
        recovery = createConnectionRecovery(
          (reason) => {
            if (current(token)) fail(Error(reason));
          },
          { schedule: (fn) => later(fn, 3000), cancel },
        );
        activePeer.addEventListener("connectionstatechange", () => {
          if (!current(token)) return;
          recovery?.update(activePeer.connectionState);
          if (activePeer.connectionState === "connected")
            update("running", "通话已连接，你的声音正在传到机器人。");
          else if (activePeer.connectionState === "disconnected")
            update("connecting", "网络短暂中断，正在恢复…");
        });
        activePeer.addEventListener("track", (event) => {
          if (current(token) && event.track.kind === "audio") {
            configureLowLatencyAudioReceivers(activePeer);
            onRemote(event.streams[0] || new MediaStream([event.track]), () =>
              current(token),
            );
          }
        });
        activePeer.addEventListener("icecandidate", (event) => {
          if (!current(token) || !event.candidate) return;
          api("/api/call/signal", {
            request_id: id,
            kind: "candidate",
            candidate: event.candidate.candidate,
            sdp_mid: event.candidate.sdpMid || "0",
            sdp_mline_index: event.candidate.sdpMLineIndex || 0,
          }).catch((error) => {
            if (current(token)) fail(error);
          });
        });
        const offer = await activePeer.createOffer();
        if (!current(token)) return false;
        await activePeer.setLocalDescription(offer);
        if (!current(token)) return false;
        await api("/api/call/signal", {
          request_id: id,
          kind: "offer",
          sdp: offer.sdp,
        });
        if (!current(token)) return false;
        update("connecting", "正在连接同一局域网里的机器人…");
        poll.cursor = 0;
        poll(token, id);
        heartbeat(token, id);
        feedback(token, id);
        later(() => {
          if (current(token) && activePeer.connectionState !== "connected")
            fail(Error("连接超时，请确认电脑与机器人在同一局域网"));
        }, 15000);
        return true;
      } catch (error) {
        if (!current(token)) return false;
        const message =
          error.name === "NotAllowedError"
            ? "麦克风权限被拒绝，请允许权限后重试"
            : error.name === "NotFoundError"
              ? "没有找到电脑麦克风"
              : error.message;
        await fail(Error(message));
        return false;
      }
    },
    async stop() {
      if (stopping) return stopping;
      if (!requestId) return true;
      const id = requestId,
        needsRelease = allocated,
        pending = allocation;
      ++generation;
      update("stopping", "正在结束通话…");
      dispose();
      stopping = (async () => {
        try {
          if (needsRelease) {
            await pending?.catch(() => {});
            const result = await api("/api/call/stop", { request_id: id });
            if (result.state !== "idle" && result.matched !== false)
              throw Error("机器人释放尚未确认");
          }
          requestId = null;
          allocated = false;
          allocation = null;
          update("idle", "通话已结束，可以再次开始。");
          return true;
        } catch (error) {
          update("cleanup_required", "设备释放待重试，请点击结束通话。");
          throw error;
        }
      })();
      try {
        return await stopping;
      } finally {
        stopping = null;
      }
    },
    setMuted(value) {
      muted = value === true;
      for (const track of stream?.getAudioTracks() || [])
        track.enabled = !muted;
      onState({ ...snapshot(), message: "" });
    },
    leave() {
      const id = requestId;
      ++generation;
      dispose();
      // Server-side heartbeat expiry also handles browser/OS crashes.
      if (id && allocated)
        navigator.sendBeacon(
          "/api/call/stop",
          new Blob([JSON.stringify({ request_id: id })], {
            type: "application/json",
          }),
        );
    },
  };
  return controller;
}
