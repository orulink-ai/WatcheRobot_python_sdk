import assert from "node:assert/strict";
import test from "node:test";

import { parseHttpResponseBody } from "../../examples/vision_debug_lab/web/http-response.mjs";

test("parses a JSON response body", () => {
  assert.deepEqual(parseHttpResponseBody('{"message":"busy"}'), { message: "busy" });
});

test("keeps a plain-text server error as a readable message", () => {
  assert.deepEqual(parseHttpResponseBody("Internal Server Error"), {
    message: "Internal Server Error",
  });
});

test("uses a stable fallback for an empty response", () => {
  assert.deepEqual(parseHttpResponseBody("  "), { message: "操作失败" });
});
