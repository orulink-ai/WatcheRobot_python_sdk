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
