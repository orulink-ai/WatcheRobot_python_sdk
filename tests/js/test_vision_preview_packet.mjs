import assert from "node:assert/strict";
import test from "node:test";

import {
  buildPreviewWebSocketUrl,
  parseVisionPacket,
} from "../../examples/vision_debug_lab/web/preview-packet.mjs";

function packet(metadata, jpeg = Uint8Array.of(0xff, 0xd8, 1, 2, 0xff, 0xd9)) {
  const encoded = new TextEncoder().encode(JSON.stringify(metadata));
  const output = new Uint8Array(8 + encoded.length + jpeg.length);
  output.set(new TextEncoder().encode("VDL1"), 0);
  new DataView(output.buffer).setUint32(4, encoded.length, true);
  output.set(encoded, 8);
  output.set(jpeg, 8 + encoded.length);
  return output.buffer;
}

test("parses VDL1 metadata and JPEG without changing sequence", () => {
  const result = parseVisionPacket(packet({ sequence: 42, width: 416, height: 416 }));
  assert.equal(result.metadata.sequence, 42);
  assert.deepEqual([...result.jpeg], [0xff, 0xd8, 1, 2, 0xff, 0xd9]);
});

test("rejects malformed packet boundaries and non-JPEG payload", () => {
  assert.throws(() => parseVisionPacket(new Uint8Array(4).buffer), /too short/);
  assert.throws(
    () => parseVisionPacket(packet({ sequence: 1 }, Uint8Array.of(1, 2))),
    /not JPEG/,
  );
  assert.throws(
    () => parseVisionPacket(packet({ sequence: 1 }, Uint8Array.of(0xff, 0xd8, 1, 2))),
    /not JPEG/,
  );
});

test("builds preview socket from the current loopback page", () => {
  assert.equal(
    buildPreviewWebSocketUrl({ protocol: "http:", host: "127.0.0.1:43210" }),
    "ws://127.0.0.1:43210/ws/preview",
  );
  assert.equal(
    buildPreviewWebSocketUrl({ protocol: "https:", host: "localhost:43210" }),
    "wss://localhost:43210/ws/preview",
  );
});
