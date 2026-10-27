const assert = require('node:assert/strict');
const test = require('node:test');
const { pathToFileURL } = require('node:url');
const path = require('node:path');

const extensionUrl = pathToFileURL(path.resolve(__dirname, '../../examples/joyinside_pi/voice_extension.mjs'));
const deferred = () => {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
};

async function setup(confirm = async () => true) {
  const handlers = new Map();
  const tools = new Map();
  const uiCalls = [];
  const controller = new AbortController();
  const context = {
    signal: controller.signal,
    ui: { confirm(...args) { uiCalls.push(args); return confirm(...args); } },
  };
  const { default: register } = await import(extensionUrl.href);
  register({ on(name, handler) { handlers.set(name, handler); }, registerTool(tool) { tools.set(tool.name, tool); } });
  const emit = (name, event = {}, ctx = context) => handlers.get(name)?.(event, ctx);
  await emit('before_agent_start', { messageType: 'user', prompt: '打开小红书' });
  let nextId = 0;
  const gate = (name, id = `call-${++nextId}`, ctx = context) => emit('tool_call', { toolName: name, toolCallId: id, input: {} }, ctx);
  const speak = async (text = '好的，我帮你打开小红书。', ctx = context) => {
    const id = `voice-${++nextId}`;
    const blocked = await gate('voice_reply', id, ctx);
    if (blocked?.block) throw new Error(blocked.reason);
    return tools.get('voice_reply').execute(id, { text }, ctx.signal, undefined, ctx);
  };
  return { tools, context, controller, emit, gate, speak, uiCalls };
}

test('registers a bounded voice tool without requiring tools for ordinary conversation', async () => {
  const h = await setup();
  const tool = h.tools.get('voice_reply');
  assert.equal(tool.parameters.type, 'object');
  assert.equal(tool.parameters.properties.text.type, 'string');
  assert.deepEqual(tool.parameters.required, ['text']);
  assert.ok(tool.parameters.properties.text.maxLength <= 80);
  assert.equal(h.uiCalls.length, 0, 'chat does not produce a generic preface');
});

test('all device, discovery, local and nested tools are blocked before speech', async () => {
  const h = await setup();
  for (const name of ['mcp__android__open_app', 'tool_search', 'codemode', 'bash', 'read']) {
    const result = await h.gate(name);
    assert.equal(result?.block, true, `${name} must not execute`);
    assert.match(result.reason, /voice_reply/);
  }
});

test('parallel sibling calls remain blocked until physical speech completion', async () => {
  const done = deferred();
  const h = await setup(() => done.promise);
  const speaking = h.speak();
  await new Promise(setImmediate);
  assert.equal(h.uiCalls[0][0], 'watcherobot.voice_reply.v1');
  assert.equal(h.uiCalls[0][1], '好的，我帮你打开小红书。');
  assert.ok(h.uiCalls[0][2].signal instanceof AbortSignal);
  assert.ok(h.uiCalls[0][2].timeout > 0);
  const parallel = await Promise.all(['bash', 'mcp__android__open_app'].map(name => h.gate(name)));
  assert.ok(parallel.every(result => result?.block === true));
  done.resolve(true);
  const result = await speaking;
  assert.equal(result.details.spoken, true);
  assert.equal(await h.gate('mcp__android__open_app'), undefined);
});

test('missing, declined and non-boolean completion never authorize tools', async () => {
  for (const result of [undefined, false, null, 'true']) {
    const h = await setup(async () => result);
    await assert.rejects(h.speak(), /播报|speech/i);
    assert.equal((await h.gate('bash'))?.block, true);
  }
});

test('failed playback rejects the voice tool and leaves the action blocked', async () => {
  const h = await setup(async () => { throw new Error('speaker disconnected'); });
  await assert.rejects(h.speak(), /speaker disconnected/);
  assert.equal((await h.gate('mcp__android__open_app'))?.block, true);
});

test('each new user run needs its own model-authored speech', async () => {
  const h = await setup();
  await h.speak();
  assert.equal(await h.gate('bash'), undefined);
  await h.emit('before_agent_start', { messageType: 'user', prompt: '现在搜索手工耿' });
  assert.equal((await h.gate('bash'))?.block, true);
  await h.speak('好的，我来搜索手工耿。');
  assert.equal(await h.gate('bash'), undefined);
});

test('a cancelled speech cannot authorize even if the UI returns true late', async () => {
  const done = deferred();
  const h = await setup(() => done.promise);
  const speaking = h.speak();
  await new Promise(setImmediate);
  h.controller.abort();
  done.resolve(true);
  await assert.rejects(speaking, /取消|abort|speech/i);
  assert.equal((await h.gate('bash'))?.block, true);
});

test('reset cancels pending UI and late previous-round completion cannot unlock the next run', async () => {
  const done = deferred();
  const h = await setup(() => done.promise);
  const speaking = h.speak();
  await new Promise(setImmediate);
  const oldSignal = h.uiCalls[0][2].signal;
  await h.emit('before_agent_start', { messageType: 'user', prompt: '下一轮' });
  assert.equal(oldSignal.aborted, true);
  done.resolve(true);
  await assert.rejects(speaking, /取消|abort|speech/i);
  assert.equal((await h.gate('bash'))?.block, true);
});

test('a delayed execute from an old tool_call cannot be adopted by the next user run', async () => {
  const h = await setup();
  await h.gate('voice_reply', 'old-call');
  await h.emit('before_agent_start', { messageType: 'user', prompt: '下一轮' });
  await assert.rejects(h.tools.get('voice_reply').execute('old-call', { text: '上一轮' }, h.context.signal, undefined, h.context), /取消|abort|speech/i);
  assert.equal(h.uiCalls.length, 0);
  assert.equal((await h.gate('bash'))?.block, true);
});

test('duplicate concurrent voice replies are blocked instead of racing the UI channel', async () => {
  const done = deferred();
  const h = await setup(() => done.promise);
  const speaking = h.speak();
  await new Promise(setImmediate);
  assert.equal((await h.gate('voice_reply'))?.block, true);
  done.resolve(true);
  await speaking;
});

test('empty, non-text and oversized speech never reaches playback', async () => {
  const h = await setup();
  for (const text of ['', '  ', null, '字'.repeat(81)]) {
    await assert.rejects(h.speak(text));
    assert.equal((await h.gate('bash'))?.block, true);
  }
  assert.equal(h.uiCalls.length, 0);
});

test('the action gate also honors cancellation after speech completed', async () => {
  const h = await setup();
  await h.speak();
  h.controller.abort();
  assert.equal((await h.gate('bash'))?.block, true);
});
