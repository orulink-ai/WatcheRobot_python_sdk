const test = require("node:test");
const assert = require("node:assert/strict");

const WebRuntime = require("../web/web-runtime.js");

function response({ ok, status, contentType, body }) {
  return {
    ok,
    status,
    headers: { get: (name) => name.toLowerCase() === "content-type" ? contentType : null },
    text: async () => body,
  };
}

test("API response parser preserves structured JSON errors", async () => {
  await assert.rejects(
    WebRuntime.decodeApiResponse(response({
      ok: false,
      status: 422,
      contentType: "application/json; charset=utf-8",
      body: JSON.stringify({ detail: [{ msg: "参数越界" }, { msg: "格式错误" }] }),
    })),
    /参数越界；格式错误/,
  );
});

test("API response parser reports plain text failures without assuming JSON", async () => {
  await assert.rejects(
    WebRuntime.decodeApiResponse(response({
      ok: false,
      status: 500,
      contentType: "text/plain",
      body: "Internal Server Error",
    })),
    /Internal Server Error/,
  );
});

test("API response parser rejects malformed successful JSON explicitly", async () => {
  await assert.rejects(
    WebRuntime.decodeApiResponse(response({
      ok: true,
      status: 200,
      contentType: "application/json",
      body: "not-json",
    })),
    /SDK 返回了无效的 JSON 响应/,
  );
});

test("timed fetch aborts a stalled local request", async () => {
  const stalledFetch = (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
  });

  await assert.rejects(
    WebRuntime.fetchWithTimeout(stalledFetch, "./api/status", {}, 5),
    /SDK 响应超时/,
  );
});

