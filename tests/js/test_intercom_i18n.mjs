import assert from "node:assert/strict";
import test from "node:test";
import { createI18n } from "../../examples/robot_intercom/web/i18n.mjs";
import fs from 'node:fs';

test('all sound controls and hints have English translations', () => {
  const en = createI18n({ storage: null, languages: ['en'] });
  const html = fs.readFileSync(new URL('../../examples/robot_intercom/web/index.html', import.meta.url), 'utf8');
  for (const match of html.matchAll(/data-i18n="([^"]+)"/g)) {
    if (/[\u4e00-\u9fff]/.test(match[1])) assert.notEqual(en.text(match[1]), match[1], match[1]);
  }
});

test("browser language is used until a saved choice overrides it", () => {
  const values = new Map();
  const storage = { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value) };
  const english = createI18n({ storage, languages: ["fr-FR", "en-US"] });
  assert.equal(english.locale, "en");
  assert.equal(english.text("开始通话"), "Start call");
  assert.equal(createI18n({ storage: null, languages: ["en-GB", "zh-CN"] }).locale, "en");
  english.setLocale("zh-CN");
  assert.equal(createI18n({ storage, languages: ["en-US"] }).locale, "zh-CN");
});

test("switching translates current states and nested errors in both directions", () => {
  const i18n = createI18n({ storage: null, languages: ["zh-CN"] });
  i18n.setLocale("en");
  assert.equal(i18n.text("麦克风已静音"), "Microphone muted");
  assert.equal(i18n.text("请检查输入：连接码需为六位数字，IP 地址需为有效 IPv4 地址"),
    "Enter a six-digit connection code and a valid IPv4 address.");
  assert.equal(i18n.text("请升级机器人固件，缺少：rtc.audio.full_duplex.v1"),
    "Update the robot firmware. Missing: rtc.audio.full_duplex.v1");
  assert.equal(i18n.text("通话已关闭，设备释放待重试：机器人释放尚未确认"),
    "Call closed. Retry releasing the robot: Robot release has not been confirmed.");
  i18n.setLocale("zh-CN");
  assert.equal(i18n.text("麦克风已静音"), "麦克风已静音");
});

test("storage denial and unsupported preferences keep the UI usable", () => {
  const storage = { getItem() { throw Error("blocked"); }, setItem() { throw Error("blocked"); } };
  const i18n = createI18n({ storage, languages: ["de-DE"] });
  assert.equal(i18n.locale, "en");
  assert.doesNotThrow(() => i18n.setLocale("zh-CN"));
  assert.equal(i18n.locale, "zh-CN");
  assert.throws(() => i18n.setLocale("fr"), RangeError);
  assert.equal(i18n.text("robot-123"), "robot-123");
  i18n.setLocale("en");
  assert.equal(i18n.text("当前机器人 · {device}", { device: "robot-123" }), "Current robot · robot-123");
});
