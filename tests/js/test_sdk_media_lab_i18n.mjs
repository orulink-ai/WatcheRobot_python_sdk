import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  shouldCaptureMutation,
  translateText,
} from "../../examples/sdk_media_lab/web/i18n.mjs";

test("SDK Test Bench keeps English source copy and translates legacy diagnostics", () => {
  assert.equal(translateText("SDK Test Bench"), "SDK Test Bench");
  assert.equal(translateText("SDK 测试台"), "SDK Test Bench");
  assert.equal(translateText("开启全双工通话"), "Start Full-duplex Call");
  assert.equal(
    translateText("正在录制 5 秒…"),
    "Recording 5 s…",
  );
  assert.equal(
    translateText("移动完成：PAN 90° / TILT 115°"),
    "Move complete: PAN 90° / TILT 115°",
  );
  assert.equal(
    translateText("灯光已应用：#00FFB3 / 70%"),
    "Lights applied: #00FFB3 / 70%",
  );
});

test("Chinese locale translates the English-first product copy", () => {
  assert.equal(translateText("SDK Test Bench", "zh-CN"), "SDK 测试台");
  assert.equal(translateText("Start Live Video", "zh-CN"), "开启实时画面");
  assert.equal(translateText("SDK 测试台", "zh-CN"), "SDK 测试台");
});

test("technical protocol names are preserved in both locales", () => {
  const source = "RTC 运行中 · AEC · OPUS · WebRTC · MJPEG";
  const translated = translateText(source);

  for (const term of ["RTC", "AEC", "OPUS", "WebRTC", "MJPEG"]) {
    assert.match(translated, new RegExp(term));
  }
});

test("combined scene controls and guidance translate into Chinese", () => {
  assert.equal(translateText("Talk, Animate, Take a Photo", "zh-CN"), "通话、程序动画与拍照");
  assert.equal(translateText("Enable Procedural Animation", "zh-CN"), "开启程序动画");
  assert.equal(translateText("Take Photo During Call", "zh-CN"), "通话中拍照");
  assert.equal(translateText("Start Measurement", "zh-CN"), "开始记录");
  assert.equal(translateText("Waiting for Speaker Telemetry", "zh-CN"), "等待扬声器数据");
  assert.equal(translateText("Speaker-driven animation available", "zh-CN"), "支持声音驱动程序动画");
});

test("JoyInside combined startup and separate SD tests have explicit translated copy", () => {
  const phrases = [
    ["JoyInside Default Procedural Expression · RADIAL 09", "JoyInside 默认程序表情 · RADIAL 09"],
    ["Start JoyInside Animation + Call", "启动 JoyInside 动画与通话"],
    ["End Scene", "结束全场景"],
    ["Start the scene to enable JoyInside RADIAL 09 before the full-duplex call. The robot mouth follows audio actually played by its speaker.", "启动全场景会先开启 JoyInside RADIAL 09，再建立全双工通话。机器人嘴形跟随扬声器实际播放的声音。"],
    ["End Scene stops the call and animation started by this scene. An independently enabled animation stays on; leaving the page stops both.", "结束全场景会停止通话及本次全场景开启的动画。预先独立开启的动画会保留；离开页面会停止两者。"],
    ["Start the scene, then speak into the computer microphone", "启动全场景，然后对着电脑麦克风说话"],
    ["SD Asset Animation Test", "SD 素材动画测试"],
    ["This separate test plays animation assets from the SD card. Use the Combined Scene above for the JoyInside default RADIAL 09 procedural expression.", "这个独立测试播放 SD 卡里的动画素材。测试 JoyInside 默认 RADIAL 09 程序表情，请使用上方综合场景。"],
    ["Starting JoyInside RADIAL 09 animation and call…", "正在启动 JoyInside RADIAL 09 动画与通话…"],
    ["Scene start cancelled", "全场景启动已取消"],
    ["Ending the combined scene…", "正在结束全场景…"],
    ["Scene ended; independently enabled animation is preserved", "全场景已结束；预先独立开启的动画会保留"],
    ["Procedural animation did not start", "程序动画未能启动"],
    ["Full-duplex call did not start", "全双工通话未能启动"],
    ["Procedural stop is unconfirmed", "程序动画停止未确认"],
    ["RTC stop is unconfirmed; retry End Scene", "RTC 停止未确认，请重试结束全场景"],
  ];
  for (const [english, chinese] of phrases) {
    assert.equal(translateText(english, "zh-CN"), chinese);
    assert.equal(translateText(english, "en-US"), english);
  }
  const page = fs.readFileSync(new URL("../../examples/sdk_media_lab/web/index.html", import.meta.url), "utf8");
  assert.match(page, /id="startSceneAudioButton"[^>]*>Start JoyInside Animation \+ Call</);
  assert.match(page, /id="stopSceneAudioButton"[^>]*>End Scene</);
  assert.match(page, /<h2>SD Asset Animation Test<\/h2>/);
});

test("combined scene captions and observed states translate without losing source copy", () => {
  const phrases = [
    ["Latest Captured Photo", "最新拍摄的照片"],
    ["Latest captured photo", "最近拍摄的照片"],
    ["Procedural Animation", "程序动画"],
    ["Audio-driven Mouth Target", "音频驱动嘴形目标"],
    ["Waiting for Rendered Frames", "等待实际绘制帧"],
    ["Procedural Display Updates Failed", "程序动画绘制失败"],
    ["Animation Running with Update Errors", "动画运行中，存在绘制错误"],
    ["Device Call Active · browser not connected", "设备通话中，当前浏览器未连接"],
    ["Call connected · waiting for speech", "通话已连接，等待说话"],
    ["Last Device Snapshot", "上次设备快照"],
    ["Device Telemetry Stale", "设备数据已过期"],
    ["Device Audio Drops", "设备音频丢帧"],
    ["Uplink drops", "上行丢帧"],
    ["Playback queue drops", "播放队列丢帧"],
    ["Connected with Audio Loss", "通话已连接，存在音频丢帧"],
  ];
  for (const [english, chinese] of phrases) {
    assert.equal(translateText(english, "zh-CN"), chinese);
    assert.equal(translateText(english, "en-US"), english);
  }
});

test("language rendering does not overwrite the canonical source copy", () => {
  assert.equal(shouldCaptureMutation("SDK Test Bench", "SDK Test Bench"), false);
  assert.equal(shouldCaptureMutation("SDK Test Bench", "设备在线"), true);
});

test("page and controller sources contain no Chinese UI hardcoding", () => {
  const testDirectory = path.dirname(fileURLToPath(import.meta.url));
  const webDirectory = path.resolve(testDirectory, "../../examples/sdk_media_lab/web");
  const sources = ["index.html", "app.js"].map((filename) => ({
    filename,
    source: fs.readFileSync(path.join(webDirectory, filename), "utf8"),
  }));
  const chineseLiterals = [];

  for (const { filename, source } of sources) {
    const literals = filename.endsWith(".html")
      ? [
          ...[...source.matchAll(/>([^<>]*[\p{Script=Han}][^<>]*)</gu)].map((match) => match[1].trim()),
          ...[...source.matchAll(/(?:aria-label|placeholder|title|alt)="([^"]*[\p{Script=Han}][^"]*)"/gu)].map((match) => match[1]),
        ]
      : [...source.matchAll(/(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`)/gsu)]
          .map((match) => match[0].slice(1, -1))
          .filter((value) => /\p{Script=Han}/u.test(value));

    for (const literal of new Set(literals)) chineseLiterals.push(`${filename}: ${JSON.stringify(literal)}`);
  }

  assert.deepEqual(chineseLiterals, []);
});
