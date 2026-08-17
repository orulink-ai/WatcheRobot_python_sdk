import assert from "node:assert/strict";
import test from "node:test";

import {
  shouldCaptureMutation,
  translateText,
} from "../../examples/sdk_media_lab/web/i18n.mjs";

test("SDK Test Bench defaults Chinese source copy to English presentation", () => {
  assert.equal(translateText("SDK 测试台"), "SDK Test Bench");
  assert.equal(translateText("开启全双工通话"), "Start Full-duplex Call");
  assert.equal(
    translateText("正在录制 5 秒…"),
    "Recording 5 s…",
  );
});

test("Chinese locale preserves the canonical Chinese product copy", () => {
  assert.equal(translateText("SDK 测试台", "zh-CN"), "SDK 测试台");
  assert.equal(translateText("开启实时画面", "zh-CN"), "开启实时画面");
});

test("technical protocol names are preserved in both locales", () => {
  const source = "RTC 运行中 · AEC · OPUS · WebRTC · MJPEG";
  const translated = translateText(source);

  for (const term of ["RTC", "AEC", "OPUS", "WebRTC", "MJPEG"]) {
    assert.match(translated, new RegExp(term));
  }
});

test("language rendering does not overwrite the canonical source copy", () => {
  assert.equal(shouldCaptureMutation("SDK Test Bench", "SDK Test Bench"), false);
  assert.equal(shouldCaptureMutation("SDK Test Bench", "设备在线"), true);
});
