"""Exercise the real pairing submit handler with safe, structured errors."""

import json
from pathlib import Path
import subprocess

import pytest


WEB = Path(__file__).resolve().parents[1] / "examples" / "joyinside_pi" / "web"
HARNESS = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const [root, encoded] = process.argv.slice(1);
const scenario = JSON.parse(encoded);
class Element {
  constructor() {
    this.value = ''; this.disabled = false; this.textContent = '';
    this.listeners = {}; this.children = [];
    this.classList = {toggle(){}, remove(){}};
  }
  addEventListener(type, fn) { this.listeners[type] = fn; }
}
const elements = new Map();
const get = id => {
  if (!elements.has(id)) elements.set(id, new Element());
  return elements.get(id);
};
const calls = [];
let finish;
const response = new Promise(resolve => { finish = resolve; });
const context = vm.createContext({
  document: {getElementById: get}, AbortSignal, setTimeout(){},
  fetch(path, options) {
    if (path !== '/api/pair') return new Promise(() => {});
    calls.push({path, options}); return response;
  },
});
vm.runInContext(fs.readFileSync(root + '/app.js', 'utf8'), context);
vm.runInContext('connectionUnavailable = false; renderConnection({state:"idle",online:false})', context);
get('pairing-code').value = '123456';
get('pairing-code').listeners.input();
(async () => {
  let prevented = false;
  const pending = get('pairing-form').listeners.submit({preventDefault(){prevented=true;}});
  assert.equal(prevented, true);
  assert.equal(get('pair').disabled, true, 'pending pairing stays exclusive');
  await get('pairing-form').listeners.submit({preventDefault(){}});
  assert.equal(calls.length, 1, 'double submit must not make a second request');
  assert.deepEqual(JSON.parse(calls[0].options.body), {pairing_code:'123456'});
  finish({ok:false, json:async () => scenario.response});
  await pending;
  assert.equal(get('pairing-detail').textContent, scenario.expected);
  assert.equal(get('pair').disabled, false, 'failed pairing can be retried');
  assert.equal(get('pairing-code').value, '123456');
  try {
    await vm.runInContext('request("/api/pair", "POST", {})', context);
    assert.fail('request must reject');
  } catch (error) {
    assert.equal(error.message, scenario.response.error, 'retain original error for callers');
    assert.equal(error.code, scenario.response.error_code, 'preserve structured error code');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            {"error": "private raw secret", "error_code": "speech_cleanup_required"},
            "上一轮语音资源未释放，请重启 Pi Application 后重新配对。",
        ),
        (
            {"error": "private raw secret", "error_code": "conversation_busy"},
            "对话正在进行或暂停中，请先暂停对话并等待麦克风释放后再配对。",
        ),
        (
            {"error": "private raw token credential"},
            "连接未成功。请确认连接同一 Wi-Fi，并使用新配对码重试。",
        ),
        (
            {"error": "private raw token", "error_code": "unknown_private_code"},
            "连接未成功。请确认连接同一 Wi-Fi，并使用新配对码重试。",
        ),
        (
            {"error": "device_connect_timeout"},
            "已找到机器人，但连接未完成，请重新打开 Desktop Link 后重试。",
        ),
    ],
)
def test_pairing_submit_error_is_specific_and_safe(response, expected):
    result = subprocess.run(
        ["node", "-e", HARNESS, str(WEB), json.dumps({"response": response, "expected": expected})],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
