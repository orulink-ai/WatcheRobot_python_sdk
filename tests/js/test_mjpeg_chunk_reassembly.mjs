import assert from "node:assert/strict";
import test from "node:test";

import {
  acceptMjpegTransportPacket,
  createMjpegChunkReassembler,
} from "../../examples/sdk_media_lab/web/mjpeg-chunk-reassembly.mjs";

const HEADER_BYTES = 20;
const PAYLOAD_BYTES = 1200;

function chunkPacket({ transfer = 7, index, count, total, flag = 0, payload }) {
  const packet = new Uint8Array(HEADER_BYTES + payload.length);
  packet.set([0x57, 0x44, 0x43, 0x48, 1, flag], 0);
  const view = new DataView(packet.buffer);
  view.setUint16(6, HEADER_BYTES, true);
  view.setUint32(8, transfer, true);
  view.setUint16(12, index, true);
  view.setUint16(14, count, true);
  view.setUint32(16, total, true);
  packet.set(payload, HEADER_BYTES);
  return packet.buffer;
}

function splitPayload(payload, transfer = 7) {
  const count = Math.ceil(payload.length / PAYLOAD_BYTES);
  return Array.from({ length: count }, (_, index) => chunkPacket({
    transfer,
    index,
    count,
    total: payload.length,
    payload: payload.slice(index * PAYLOAD_BYTES, (index + 1) * PAYLOAD_BYTES),
  }));
}

test("passes legacy complete WJPG packets through unchanged", () => {
  const reassembler = createMjpegChunkReassembler();
  const packet = Uint8Array.from([0x57, 0x4a, 0x50, 0x47, 1, 2, 3]).buffer;
  assert.equal(acceptMjpegTransportPacket(reassembler, packet, 0), packet);
});

test("reassembles unordered data chunks into one complete frame", () => {
  const reassembler = createMjpegChunkReassembler();
  const payload = Uint8Array.from({ length: 2505 }, (_, index) => index % 251);
  const chunks = splitPayload(payload);

  assert.equal(acceptMjpegTransportPacket(reassembler, chunks[2], 1), null);
  assert.equal(acceptMjpegTransportPacket(reassembler, chunks[0], 2), null);
  const completed = acceptMjpegTransportPacket(reassembler, chunks[1], 3);

  assert.deepEqual(new Uint8Array(completed), payload);
});

test("recovers one missing data chunk from XOR parity", () => {
  const reassembler = createMjpegChunkReassembler();
  const payload = Uint8Array.from({ length: 2505 }, (_, index) => (index * 17) % 256);
  const dataChunks = splitPayload(payload, 9);
  const parity = new Uint8Array(PAYLOAD_BYTES);
  for (let index = 0; index < dataChunks.length; index += 1) {
    const bytes = new Uint8Array(dataChunks[index], HEADER_BYTES);
    for (let offset = 0; offset < bytes.length; offset += 1) parity[offset] ^= bytes[offset];
  }
  const parityPacket = chunkPacket({
    transfer: 9,
    index: dataChunks.length,
    count: dataChunks.length,
    total: payload.length,
    flag: 1,
    payload: parity,
  });

  assert.equal(acceptMjpegTransportPacket(reassembler, dataChunks[0], 1), null);
  assert.equal(acceptMjpegTransportPacket(reassembler, dataChunks[2], 2), null);
  const completed = acceptMjpegTransportPacket(reassembler, parityPacket, 3);

  assert.deepEqual(new Uint8Array(completed), payload);
});

test("bounds stale and concurrent transfer state", () => {
  const reassembler = createMjpegChunkReassembler({ timeoutMs: 200, maxTransfers: 2 });
  const payload = new Uint8Array(2400);
  for (const transfer of [1, 2, 3]) {
    const first = splitPayload(payload, transfer)[0];
    assert.equal(acceptMjpegTransportPacket(reassembler, first, transfer), null);
  }
  assert.equal(reassembler.transfers.size, 2);
  acceptMjpegTransportPacket(reassembler, splitPayload(payload, 4)[0], 500);
  assert.equal(reassembler.transfers.size, 1);
});
