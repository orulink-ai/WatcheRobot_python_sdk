const SUPPORTED_LOCALES = new Set(["en-US", "zh-CN"]);

// Chinese remains the canonical source copy so existing diagnostics and tests keep
// their stable wording. The presentation layer translates every visible mutation,
// including status text produced after an asynchronous device event.
const ENGLISH_PHRASES = new Map([
  ["基础全检将移动云台、点亮灯光、播放声音、拍照并录制麦克风。请确保机器人周围无遮挡，是否继续？", "The basic check moves the gimbal, lights the body, plays audio, captures a photo, and records the microphone. Make sure the robot has clear space. Continue?"],
  ["输入设备屏幕上的六位配对码。配对请求由本机 SDK Daemon 管理，配对码不会保存在测试台日志中。", "Enter the six-digit pairing code shown on the device. The local SDK Daemon handles pairing, and the code is never stored in Test Bench logs."],
  ["从运动、灯光到相机和全双工音频，所有测试均由当前 Python Application 调用公开 SDK API，", "From motion and lighting to camera and full-duplex audio, every test uses public SDK APIs from the current Python Application"],
  ["并通过已授权的设备通道执行。", "and runs through its authorized Device channel."],
  ["设备会动态上报 SD 卡中实际可播放的完整动画目录；音视频会话运行时仍可手动切换或自动随机轮播。", "The device reports the complete animation catalog available on its SD card. You can switch animations manually or use randomized playback during an audio/video session."],
  ["将电脑麦克风实时送到 Watcher 扬声器，同时在浏览器播放 Watcher 麦克风声音。", "Stream the computer microphone to the Watcher speaker while playing the Watcher microphone in the browser."],
  ["信令由当前 Python Application 管理，音频媒体使用局域网 WebRTC 直连。", "The current Python Application handles signaling while audio uses a direct LAN WebRTC connection."],
  ["浏览器通过独立局域网通道直接接收 Watcher 的 MJPEG 画面；", "The browser receives Watcher MJPEG video directly over a dedicated LAN channel;"],
  ["全双工音频继续使用 WebRTC，Python Application 不转发媒体帧。", "full-duplex audio continues over WebRTC, and the Python Application does not relay media frames."],
  ["默认使用原声麦克风采集；建议佩戴耳机，避免设备与电脑扬声器形成声学回路。", "Raw microphone capture is used by default. Wear headphones to avoid an acoustic loop between the device and computer speakers."],
  ["持续观察内部内存、最大连续块和媒体资源；RTC 停止后自动对比启动基线。", "Continuously monitor internal memory, largest contiguous blocks, and media resources; compare against the startup baseline after RTC stops."],
  ["接收 Watcher 的 Opus 音频，由 SDK 解码为 PCM，并保存为经过校验的 WAV。", "Receive Watcher Opus audio, decode it to PCM through the SDK, and save a validated WAV file."],
  ["设置颜色、亮度、灯区或循环效果，通过 robot.lights 公共接口下发。", "Set color, brightness, zones, or looping effects through the public robot.lights API."],
  ["拖动水平与俯仰角度，通过 robot.motion.move_to() 平滑移动机器人。", "Adjust pan and tilt to move the robot smoothly through robot.motion.move_to()."],
  ["通过应用通道传输内置的 24 kHz 单声道 PCM 示例音频。", "Stream the built-in 24 kHz mono PCM sample through the Application channel."],
  ["请求拍摄一张 JPEG 照片，并将其作为托管应用产物返回。", "Capture one JPEG photo and return it as a managed Application artifact."],
  ["机器人已发送音频包，但采集内容接近静音；请对着机器人麦克风说话并检查采集链路", "The robot sent audio packets, but capture is nearly silent. Speak toward the robot microphone and inspect the capture path"],
  ["浏览器已收到机器人音频包，但解码信号接近静音；请检查编码与浏览器音频轨道", "The browser received robot audio packets, but the decoded signal is nearly silent. Inspect encoding and the browser audio track"],
  ["机器人音频已到达，但浏览器播放器处于暂停或静音状态；请点击播放器开启声音", "Robot audio arrived, but the browser player is paused or muted. Enable sound in the player"],
  ["电脑音频已发送，但机器人没有完成有声解码与扬声器输出；请检查设备播放统计", "Computer audio was sent, but the robot did not complete audible decode and speaker output. Inspect device playback metrics"],
  ["机器人麦克风没有产生音频帧，请检查麦克风采集与音频资源占用", "The robot microphone produced no audio frames. Inspect microphone capture and audio resource ownership"],
  ["机器人麦克风音频未到达电脑，请查看机器人发送计数与错误码", "Robot microphone audio did not reach the computer. Inspect robot transmit counters and error codes"],
  ["全双工链路已验证：浏览器正在播放 Watcher 的非静音音频轨道", "Full-duplex path verified: the browser is playing a non-silent Watcher audio track"],
  ["当前固件未声明所需 RTC 能力，请更新并重新连接设备", "The current firmware does not advertise the required RTC capabilities. Update it and reconnect"],
  ["相机视频源未输出画面；请确认 HX6538 已安装配套视频桥固件", "The camera source produced no video. Confirm that the HX6538 has the matching video-bridge firmware"],
  ["基础全检通过：执行器与媒体链路均已完成", "Basic check passed: actuator and media paths completed"],
  ["基础全检已在首个失败环节停止", "Basic check stopped at the first failed stage"],
  ["音视频资源正在使用中，请停止相关功能后重试", "Audio/video resources are busy. Stop the related feature and try again"],
  ["扬声器或动画音效正在播放，请停止后再开启", "Speaker or animation audio is playing. Stop it before starting this feature"],
  ["人脸跟踪画面正在使用相机，请先停止后再开启", "Face-tracking preview is using the camera. Stop it before starting this feature"],
  ["配对请求已提交，请保持机器人开机", "Pairing request submitted. Keep the robot powered on"],
  ["当前已有设备连接或正在配对", "A device is already connected or pairing is in progress"],
  ["未发现对应设备，请确认配对码和网络后重试", "Device not found. Check the pairing code and network, then try again"],
  ["设备连接超时，请重新获取配对码后重试", "Device connection timed out. Get a new pairing code and try again"],
  ["设备重连超时，请重新配对", "Device reconnection timed out. Pair again"],
  ["无法连接 SDK Daemon 的配对服务", "Unable to reach the SDK Daemon pairing service"],
  ["当前浏览器不支持麦克风采集", "This browser does not support microphone capture"],
  ["未获得电脑麦克风权限，请允许后重试", "Computer microphone permission was denied. Allow access and try again"],
  ["未检测到可用的电脑麦克风", "No computer microphone is available"],
  ["浏览器与设备的实时连接建立失败", "The browser could not establish a real-time connection to the device"],
  ["设备相机推流器启动失败", "The device camera streamer failed to start"],
  ["实时视频数据通道已断开", "The live-video data channel disconnected"],
  ["实时视频心跳超时", "Live-video heartbeat timed out"],
  ["设备音频采集启动失败", "Device audio capture failed to start"],
  ["设备扬声器播放启动失败", "Device speaker playback failed to start"],
  ["媒体连接已建立，正在验证机器人麦克风上行…", "Media connected; validating robot microphone uplink…"],
  ["电脑麦克风已开启，等待 Watcher 应答…", "Computer microphone is active; waiting for Watcher…"],
  ["音视频协商已发送，等待 Watcher 应答…", "Audio/video offer sent; waiting for Watcher…"],
  ["已发送浏览器协商信息，等待 Watcher 应答…", "Browser offer sent; waiting for Watcher…"],
  ["本地音视频已结束，设备释放确认超时", "Local audio/video stopped, but device release confirmation timed out"],
  ["随机播放已停止，当前动画将继续显示", "Random playback stopped; the current animation remains visible"],
  ["动画 ID 只能包含小写字母、数字和下划线", "Animation ID may contain only lowercase letters, numbers, and underscores"],
  ["设备尚未上报可播放动画目录", "The device has not reported an animation catalog"],
  ["设备没有可用于随机播放的动画", "The device has no animations available for randomized playback"],
  ["当前固件尚未上报可播放动画目录", "The current firmware has not reported an animation catalog"],
  ["媒体已释放，动画缓存或上下文已变化", "Media released; animation cache or context changed"],
  ["RTC 停止后仍有资源占用", "Resources remain allocated after RTC stopped"],
  ["RTC 资源已回到基线", "RTC resources returned to baseline"],
  ["资源释放调用失败", "Resource release call failed"],
  ["尚未收到 evt.sdk.resource_snapshot", "No evt.sdk.resource_snapshot received"],
  ["一张工作台，验证整台机器人。", "One bench to validate the whole robot."],
  ["运动 → 灯光 → 扬声器 → 相机 → 麦克风", "Motion → Lights → Speaker → Camera → Microphone"],
  ["等待输入设备屏幕上的配对码", "Enter the pairing code shown on the device"],
  ["设备离线，最后样本", "Device offline, last sample"],
  ["随机顺序整轮覆盖且不重复", "full shuffled cycle without repeats"],
  ["未检测到活动动画", "No active animation detected"],
  ["旧固件：无物理麦克风遥测", "Legacy firmware: no physical microphone telemetry"],
  ["未启用（原始麦克风兜底）", "Disabled (raw microphone fallback)"],
  ["工作中 · 等待电脑下行参考音频", "Active · waiting for computer downlink reference audio"],
  ["请对机器人说话", "Speak toward the robot"],
  ["配对码必须是 6 位数字", "Pairing code must contain 6 digits"],
  ["设备已断开，请重新连接后再测试", "Device disconnected. Reconnect before testing"],
  ["正在提交配对请求…", "Submitting pairing request…"],
  ["正在发现设备…", "Discovering device…"],
  ["已发现设备，正在建立连接…", "Device found; connecting…"],
  ["设备配对成功", "Device paired"],
  ["设备配对已开始", "Device pairing started"],
  ["SDK 测试台已就绪", "SDK Test Bench ready"],
  ["SDK 测试台", "SDK Test Bench"],
  ["设备资源监视器", "Device Resource Monitor"],
  ["动画并发验证", "Concurrent Animation Validation"],
  ["相机实时画面", "Live Camera Preview"],
  ["全双工音频通话", "Full-duplex Audio Call"],
  ["扬声器流式播放", "Speaker Stream"],
  ["设备能力矩阵", "Device Capability Matrix"],
  ["麦克风录音", "Microphone Recording"],
  ["连接机器人", "Connect Robot"],
  ["运行基础全检", "Run Basic Check"],
  ["云台姿态", "Gimbal Position"],
  ["机身灯效", "Body Lighting"],
  ["相机拍照", "Camera Capture"],
  ["运行日志", "Run Log"],
  ["设备连接", "Device Connection"],
  ["系统说明", "System Overview"],
  ["设备接入", "Device Access"],
  ["设备遥测信息", "Device Telemetry"],
  ["资源生命周期", "Resource Lifecycle"],
  ["生命周期判断", "Lifecycle Verdict"],
  ["执行器测试台", "Actuator Test Bench"],
  ["运动控制", "Motion Control"],
  ["灯光控制", "Lighting Control"],
  ["屏幕动画", "Screen Animation"],
  ["实时视频", "Live Video"],
  ["RTC 音频", "RTC Audio"],
  ["音频下行", "Audio Downlink"],
  ["图像采集", "Image Capture"],
  ["音频上行", "Audio Uplink"],
  ["能力合同", "Capability Contract"],
  ["运行追踪", "Runtime Trace"],
  ["媒体测试台", "Media Test Bench"],
  ["六位配对码", "Six-digit pairing code"],
  ["开始配对", "Start Pairing"],
  ["设备 IP（可选，广播受限时填写）", "Device IP (optional when broadcast is restricted)"],
  ["设备 IP（可选）", "Device IP (optional)"],
  ["能力数量", "Capabilities"],
  ["最近同步", "Last Sync"],
  ["内部可用", "Internal Free"],
  ["最大连续块", "Largest Block"],
  ["DMA 可用", "DMA Free"],
  ["DMA 最大连续块", "DMA Largest Block"],
  ["PSRAM 可用", "PSRAM Free"],
  ["PSRAM 最大连续块", "PSRAM Largest Block"],
  ["会话内最低内部 RAM", "Session Minimum Internal RAM"],
  ["占用状态", "Owners"],
  ["相对基线", "Against Baseline"],
  ["释放结果", "Release Result"],
  ["水平 PAN", "Horizontal PAN"],
  ["俯仰 TILT", "Vertical TILT"],
  ["向左", "Left"],
  ["回中", "Center"],
  ["向右", "Right"],
  ["执行移动", "Move"],
  ["立即停止", "Stop Now"],
  ["颜色", "Color"],
  ["亮度", "Brightness"],
  ["灯区", "Zone"],
  ["效果", "Effect"],
  ["全部", "All"],
  ["侧面", "Side"],
  ["底部", "Bottom"],
  ["呼吸", "Breathing"],
  ["闪烁", "Blink"],
  ["彩虹", "Rainbow"],
  ["状态脉冲", "Status Pulse"],
  ["应用常亮", "Apply Solid"],
  ["播放效果", "Play Effect"],
  ["关闭", "Off"],
  ["动画 ID", "Animation ID"],
  ["随机切换间隔（秒）", "Random interval (seconds)"],
  ["播放动画", "Play Animation"],
  ["停止动画", "Stop Animation"],
  ["开始随机播放", "Start Random"],
  ["停止随机播放", "Stop Random"],
  ["开启实时画面", "Start Live Video"],
  ["停止直播", "Stop Live Video"],
  ["显示帧率", "Display FPS"],
  ["源 / 目标 / 发送", "Source / Target / Sent"],
  ["平均帧大小 / 发送 P95", "Average Frame / Send P95"],
  ["拥塞压力", "Congestion Pressure"],
  ["动画实测 / 目标", "Animation Actual / Target"],
  ["画面尺寸", "Resolution"],
  ["本地丢帧", "Local Drops"],
  ["等待视频信号", "Waiting for Video"],
  ["开启全双工通话", "Start Full-duplex Call"],
  ["同时开启音视频", "Start Audio + Video"],
  ["结束通话", "End Call"],
  ["会话状态", "Session State"],
  ["电脑麦克风", "Computer Microphone"],
  ["机器人麦克风采集", "Robot Microphone Capture"],
  ["机器人成功发送", "Robot Packets Sent"],
  ["物理麦克风峰值 / 浏览器播放", "Physical Mic Peak / Browser Playback"],
  ["设备回声消除", "Device Echo Cancellation"],
  ["实时音频分段时延", "Real-time Audio Stage Latency"],
  ["验收动作", "Validation Action"],
  ["Watcher 实时麦克风声音", "Live Watcher Microphone Audio"],
  ["播放示例", "Play Sample"],
  ["拍摄 JPEG", "Capture JPEG"],
  ["最近拍摄的照片", "Latest captured photo"],
  ["暂无画面", "No Image"],
  ["拍照通道已就绪", "Capture channel ready"],
  ["下载照片", "Download Photo"],
  ["录音时长", "Recording Duration"],
  ["麦克风录音波形", "Microphone recording waveform"],
  ["录制 PCM", "Record PCM"],
  ["下载录音", "Download Recording"],
  ["清空显示", "Clear Display"],
  ["本机页面", "Local Page"],
  ["应用 → 守护进程 → WATCHER", "APPLICATION → DAEMON → WATCHER"],
  ["正在校准", "Calibrating"],
  ["检测中", "Detecting"],
  ["等待控制", "Awaiting Control"],
  ["等待测试", "Awaiting Test"],
  ["等待开启", "Awaiting Start"],
  ["系统空闲", "System Idle"],
  ["等待设备快照", "Waiting for Device Snapshot"],
  ["等待设备遥测", "Waiting for Device Telemetry"],
  ["等待资源基线", "Waiting for Resource Baseline"],
  ["尚无 RTC 停止记录", "No RTC Stop Record"],
  ["持续观察中", "Monitoring"],
  ["等待分段时延样本", "Waiting for Stage Latency Samples"],
  ["等待 PCM 音频信号", "Waiting for PCM Audio"],
  ["同步中", "Syncing"],
  ["设备在线", "Device Online"],
  ["设备已断开", "Device Disconnected"],
  ["测试台离线", "Test Bench Offline"],
  ["设备离线", "Device Offline"],
  ["已就绪", "Ready"],
  ["设备未声明", "Not Advertised"],
  ["需要新固件", "New Firmware Required"],
  ["未识别", "Unidentified"],
  ["未知操作", "Unknown Action"],
  ["未知阶段", "Unknown Stage"],
  ["未知释放步骤", "Unknown Release Step"],
  ["未知", "Unknown"],
  ["未占用", "Available"],
  ["未启用", "Disabled"],
  ["无活动媒体资源", "No Active Media Resources"],
  ["正在传输 PCM 示例音频…", "Streaming PCM sample…"],
  ["正在请求 JPEG 画面…", "Requesting JPEG frame…"],
  ["正在请求电脑麦克风权限…", "Requesting computer microphone permission…"],
  ["正在申请相机、音频与实时传输资源…", "Acquiring camera, audio, and real-time transport resources…"],
  ["正在申请相机与实时传输资源…", "Acquiring camera and real-time transport resources…"],
  ["正在停止播放…", "Stopping playback…"],
  ["正在停止动画…", "Stopping animation…"],
  ["正在停止运动…", "Stopping motion…"],
  ["正在应用灯光…", "Applying lights…"],
  ["正在启动灯效…", "Starting light effect…"],
  ["动画已停止", "Animation stopped"],
  ["运动已停止", "Motion stopped"],
  ["灯光已关闭", "Lights off"],
  ["已请求停止播放", "Playback stop requested"],
  ["随机动画播放已停止", "Random animation playback stopped"],
  ["实时画面通道已连接", "Live-video channel connected"],
  ["音视频通道已连接", "Audio/video channel connected"],
  ["实时画面通道已关闭", "Live-video channel closed"],
  ["实时画面已停止", "Live video stopped"],
  ["全双工通话已结束", "Full-duplex call ended"],
  ["设备未提供实时视频直连地址", "Device did not provide a direct live-video URL"],
  ["设备拒绝了 RTC 请求", "Device rejected the RTC request"],
  ["设备 RTC 会话失败", "Device RTC session failed"],
  ["RTC 会话尚未开启", "RTC session is not active"],
  ["拖动水平与俯仰角度，通过 ", "Adjust pan and tilt to move the robot smoothly through "],
  [" 平滑移动机器人。", "."],
  ["设置颜色、亮度、灯区或循环效果，通过 ", "Set color, brightness, zones, or looping effects through the public "],
  [" 公共接口下发。", " API."],
  ["相对 连接基线：内部", "Against connection baseline: internal"],
  ["相对 RTC 启动前：内部", "Against RTC pre-start: internal"],
  ["设备离线 · 保留上次", "Device offline · retained"],
  ["个动画", "animations"],
  ["浏览器", "Browser"],
  ["动画", "Animation"],
  ["电脑 → Watcher", "Computer → Watcher"],
  ["Watcher → 电脑", "Watcher → Computer"],
  ["项上次协商", "previously negotiated capabilities"],
  ["无法解码录音波形", "Unable to decode recording waveform"],
  ["采集中", "Capturing"],
  ["状态", "State"],
  ["设备", "Device"],
  ["固件", "Firmware"],
  ["停止", "Stop"],
  ["秒", "s"],
  ["实时", "Live"],
  ["错误", "errors"],
  ["失败", "Failed"],
  ["成功", "Success"],
  ["播放完成", "Playback complete"],
  ["照片接收完成", "Photo received"],
  ["丢帧", "drops"],
  ["解码失败", "decode failures"],
  ["正在录制", "Recording"],
  ["操作已下发", "Command sent"],
  ["正在执行", "Running"],
  ["已开始", " started"],
  ["已完成", " completed"],
  ["已停止", " stopped"],
]);

const ORDERED_ENGLISH_PHRASES = [...ENGLISH_PHRASES.entries()]
  .sort(([left], [right]) => right.length - left.length);
const TRANSLATABLE_ATTRIBUTES = ["aria-label", "placeholder", "title", "alt"];

export function translateText(value, locale = "en-US") {
  const source = String(value ?? "");
  if (locale === "zh-CN") return source;
  let translated = source;
  for (const [chinese, english] of ORDERED_ENGLISH_PHRASES) {
    translated = translated.replaceAll(chinese, english);
  }
  return translated;
}

export function shouldCaptureMutation(lastRendered, currentValue) {
  return lastRendered === undefined || lastRendered !== currentValue;
}

export function initializeI18n({
  defaultLocale = "en-US",
  storageKey,
  englishButton,
  chineseButton,
} = {}) {
  const textSources = new WeakMap();
  const attributeSources = new WeakMap();
  const renderedTextValues = new WeakMap();
  const renderedAttributeValues = new WeakMap();
  let rendering = false;
  let locale = defaultLocale;
  try {
    const saved = window.localStorage.getItem(storageKey);
    if (SUPPORTED_LOCALES.has(saved)) locale = saved;
  } catch (_) {
    locale = defaultLocale;
  }

  function shouldIgnore(node) {
    const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
    return Boolean(element?.closest("[data-i18n-ignore]"));
  }

  function renderTextNode(node, capture = false) {
    if (shouldIgnore(node)) return;
    const current = node.nodeValue || "";
    if (
      (capture && shouldCaptureMutation(renderedTextValues.get(node), current))
      || !textSources.has(node)
    ) {
      textSources.set(node, current);
    }
    const source = textSources.get(node) || "";
    const next = translateText(source, locale);
    if (node.nodeValue !== next) node.nodeValue = next;
    renderedTextValues.set(node, next);
  }

  function renderAttributes(element, capture = false) {
    if (shouldIgnore(element)) return;
    let sources = attributeSources.get(element);
    if (!sources) {
      sources = new Map();
      attributeSources.set(element, sources);
    }
    let rendered = renderedAttributeValues.get(element);
    if (!rendered) {
      rendered = new Map();
      renderedAttributeValues.set(element, rendered);
    }
    for (const name of TRANSLATABLE_ATTRIBUTES) {
      if (!element.hasAttribute(name)) continue;
      const current = element.getAttribute(name) || "";
      if (
        (capture && shouldCaptureMutation(rendered.get(name), current))
        || !sources.has(name)
      ) {
        sources.set(name, current);
      }
      const next = translateText(sources.get(name), locale);
      if (element.getAttribute(name) !== next) element.setAttribute(name, next);
      rendered.set(name, next);
    }
  }

  function renderTree(root, capture = false) {
    if (root.nodeType === Node.TEXT_NODE) {
      renderTextNode(root, capture);
      return;
    }
    if (root.nodeType !== Node.ELEMENT_NODE && root.nodeType !== Node.DOCUMENT_NODE) return;
    if (root.nodeType === Node.ELEMENT_NODE) renderAttributes(root, capture);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      if (node.nodeType === Node.TEXT_NODE) renderTextNode(node, capture);
      else renderAttributes(node, capture);
      node = walker.nextNode();
    }
  }

  function updateControls() {
    document.documentElement.lang = locale === "zh-CN" ? "zh-CN" : "en";
    document.title = locale === "zh-CN" ? "WatcheRobot SDK 测试台" : "WatcheRobot SDK Test Bench";
    englishButton?.setAttribute("aria-pressed", String(locale === "en-US"));
    chineseButton?.setAttribute("aria-pressed", String(locale === "zh-CN"));
  }

  function renderAll() {
    rendering = true;
    renderTree(document.body);
    updateControls();
    rendering = false;
  }

  function setLocale(nextLocale) {
    if (!SUPPORTED_LOCALES.has(nextLocale)) return;
    locale = nextLocale;
    try { window.localStorage.setItem(storageKey, locale); } catch (_) { /* local-only preference */ }
    renderAll();
  }

  rendering = true;
  renderTree(document.body, true);
  updateControls();
  document.documentElement.dataset.i18nReady = "true";
  rendering = false;

  const observer = new MutationObserver((records) => {
    if (rendering) return;
    rendering = true;
    for (const record of records) {
      if (record.type === "characterData") renderTextNode(record.target, true);
      if (record.type === "attributes") renderAttributes(record.target, true);
      for (const node of record.addedNodes || []) renderTree(node, true);
    }
    rendering = false;
  });
  observer.observe(document.body, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: TRANSLATABLE_ATTRIBUTES,
  });

  englishButton?.addEventListener("click", () => setLocale("en-US"));
  chineseButton?.addEventListener("click", () => setLocale("zh-CN"));

  return {
    get locale() { return locale; },
    setLocale,
    translate: (value) => translateText(value, locale),
    disconnect: () => observer.disconnect(),
  };
}
