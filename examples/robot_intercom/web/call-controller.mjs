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
  requestTimeoutMs = 20000,
  onState = () => {},
  onRemote = () => {},
  onStats = () => {},
  onDispose = () => {},
  connectPresence = async () => null,
  report = () => {},
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
  let presence = null;
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
  async function request(path, body) {
    const abort = new AbortController();
    let timer;
    const deadline = new Promise((_, reject) => {
      // Keep request deadlines independent of dispose(): stop may still need
      // the bounded allocation result before sending its scoped release.
      timer = schedule(() => {
        abort.abort();
        reject(Error("应用请求超时，请重试结束通话"));
      }, requestTimeoutMs);
    });
    try {
      return await Promise.race([
        api(path, body, { signal: abort.signal }),
        deadline,
      ]);
    } finally {
      cancel(timer);
    }
  }
  function dispose(closePresence = true) {
    if (closePresence) {
      presence?.close();
      presence = null;
    }
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
    report({
      kind: "browser_failure",
      connection_state: peer?.connectionState,
      ice_state: peer?.iceConnectionState,
    });
    try {
      await controller.stop({ reason: "browser_failure" });
      const messages = {
        "WebRTC disconnect recovery timed out":
          "网络连接中断且未恢复，请重新开始通话。",
        "WebRTC connection failed": "语音连接失败，请检查局域网后重新开始。",
        "WebRTC connection closed": "语音连接已关闭，请重新开始。",
        heartbeat_timeout: "通话连接未能保持，已安全结束，请重新开始。",
        peer_disconnected: "机器人音频连接已中断，请重新开始通话。",
      };
      update("idle", messages[error.message] || error.message || String(error));
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
      const payload = await request(
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
      await request("/api/call/heartbeat", {
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
          // Device feedback uses 20 ms frames, while RTC reports PCM samples.
          const rate = reports.get(report.codecId)?.clockRate;
          if (rate > 0)
            concealed += Math.floor(
              ((report.concealedSamples || 0) * 50) / rate,
            );
          const sample = sampleAudioJitterBuffer(jitter, report);
          jitter = sample.counter;
          queue = Math.max(queue, sample.actualMs);
        }
      });
      onStats({ sent, received, lost, level });
      await request("/api/call/feedback", {
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
      const connectionDeadline = later(() => {
        if (current(token) && state !== "running")
          fail(Error("连接超时，请确认电脑与机器人在同一局域网"));
      }, 30000);
      try {
        const microphone = await getMicrophone();
        if (!current(token)) {
          for (const track of microphone.getTracks()) track.stop();
          return false;
        }
        stream = microphone;
        allocated = true;
        allocation = request("/api/call/start", { request_id: id });
        await allocation;
        if (!current(token)) return false;
        heartbeat(token, id);
        const connectedPresence = await connectPresence(id, () => {
          if (current(token)) fail(Error("本机通话连接已断开，请重新开始"));
        });
        if (!current(token)) {
          connectedPresence?.close();
          return false;
        }
        presence = connectedPresence;
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
          report({
            kind: "peer_state",
            connection_state: activePeer.connectionState,
            ice_state: activePeer.iceConnectionState,
          });
          recovery?.update(activePeer.connectionState);
          if (activePeer.connectionState === "connected") {
            cancel(connectionDeadline);
            timers.delete(connectionDeadline);
            update("running", "通话已连接，你的声音正在传到机器人。");
          } else if (activePeer.connectionState === "disconnected")
            update("connecting", "网络短暂中断，正在恢复…");
        });
        activePeer.addEventListener("iceconnectionstatechange", () => {
          if (current(token))
            report({
              kind: "peer_state",
              connection_state: activePeer.connectionState,
              ice_state: activePeer.iceConnectionState,
            });
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
          request("/api/call/signal", {
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
        await request("/api/call/signal", {
          request_id: id,
          kind: "offer",
          sdp: offer.sdp,
        });
        if (!current(token)) return false;
        update("connecting", "正在连接同一局域网里的机器人…");
        poll.cursor = 0;
        poll(token, id);
        feedback(token, id);
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
    async stop({ reason = "user" } = {}) {
      if (stopping) return stopping;
      if (!requestId) return true;
      const id = requestId,
        needsRelease = allocated,
        pending = allocation;
      ++generation;
      update("stopping", "正在结束通话…");
      dispose(false);
      stopping = (async () => {
        try {
          if (needsRelease) {
            await pending?.catch(() => {});
            const result = await request("/api/call/stop", {
              request_id: id,
              reason,
            });
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
        presence?.close();
        presence = null;
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
      // Native presence closure also handles browser/OS crashes.
      if (id && allocated)
        navigator.sendBeacon(
          "/api/call/stop",
          new Blob(
            [JSON.stringify({ request_id: id, reason: "page_closed" })],
            {
              type: "application/json",
            },
          ),
        );
    },
  };
  return controller;
}
