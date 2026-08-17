const CHUNK_MAGIC = [0x57, 0x44, 0x43, 0x48];
const CHUNK_VERSION = 1;
const CHUNK_HEADER_BYTES = 20;
const CHUNK_PAYLOAD_BYTES = 1200;
const CHUNK_FLAG_DATA = 0;
const CHUNK_FLAG_XOR_PARITY = 1;

export function createMjpegChunkReassembler({ timeoutMs = 500, maxTransfers = 4 } = {}) {
  return {
    timeoutMs,
    maxTransfers,
    transfers: new Map(),
  };
}

function isChunkPacket(bytes) {
  return bytes.length >= CHUNK_MAGIC.length
    && CHUNK_MAGIC.every((value, index) => bytes[index] === value);
}

function pruneTransfers(reassembler, nowMs) {
  for (const [sequence, transfer] of reassembler.transfers) {
    if (nowMs - transfer.createdAtMs > reassembler.timeoutMs) {
      reassembler.transfers.delete(sequence);
    }
  }
  while (reassembler.transfers.size >= reassembler.maxTransfers) {
    reassembler.transfers.delete(reassembler.transfers.keys().next().value);
  }
}

function expectedChunkBytes(transfer, index) {
  const offset = index * CHUNK_PAYLOAD_BYTES;
  return Math.min(CHUNK_PAYLOAD_BYTES, transfer.totalBytes - offset);
}

function recoverMissingChunk(transfer, missingIndex) {
  if (!transfer.xorParity) return false;
  const expectedBytes = expectedChunkBytes(transfer, missingIndex);
  const recovered = transfer.xorParity.slice(0, expectedBytes);
  for (let index = 0; index < transfer.chunkCount; index += 1) {
    const chunk = transfer.chunks[index];
    if (index === missingIndex || !chunk) continue;
    for (let offset = 0; offset < Math.min(recovered.length, chunk.length); offset += 1) {
      recovered[offset] ^= chunk[offset];
    }
  }
  transfer.chunks[missingIndex] = recovered;
  transfer.receivedChunks += 1;
  return true;
}

function completeTransfer(transfer) {
  if (transfer.receivedChunks + 1 === transfer.chunkCount) {
    const missingIndex = transfer.chunks.findIndex((chunk) => chunk === null);
    if (missingIndex >= 0) recoverMissingChunk(transfer, missingIndex);
  }
  if (transfer.receivedChunks !== transfer.chunkCount) return null;

  const frame = new Uint8Array(transfer.totalBytes);
  for (let index = 0; index < transfer.chunkCount; index += 1) {
    const chunk = transfer.chunks[index];
    const expectedBytes = expectedChunkBytes(transfer, index);
    if (!chunk || chunk.length !== expectedBytes) return null;
    frame.set(chunk, index * CHUNK_PAYLOAD_BYTES);
  }
  return frame.buffer;
}

export function acceptMjpegTransportPacket(reassembler, packet, nowMs = performance.now()) {
  const bytes = new Uint8Array(packet);
  if (!isChunkPacket(bytes)) return packet;
  if (bytes.length < CHUNK_HEADER_BYTES) return null;

  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const version = bytes[4];
  const flag = bytes[5];
  const headerBytes = view.getUint16(6, true);
  const sequence = view.getUint32(8, true);
  const chunkIndex = view.getUint16(12, true);
  const chunkCount = view.getUint16(14, true);
  const totalBytes = view.getUint32(16, true);
  const payload = bytes.slice(headerBytes);
  const maximumBytes = chunkCount * CHUNK_PAYLOAD_BYTES;
  const validMetadata = version === CHUNK_VERSION
    && headerBytes === CHUNK_HEADER_BYTES
    && chunkCount > 0
    && totalBytes > 0
    && totalBytes <= maximumBytes
    && totalBytes > (chunkCount - 1) * CHUNK_PAYLOAD_BYTES;
  const validData = flag === CHUNK_FLAG_DATA
    && chunkIndex < chunkCount
    && payload.length === Math.min(CHUNK_PAYLOAD_BYTES, totalBytes - chunkIndex * CHUNK_PAYLOAD_BYTES);
  const validParity = flag === CHUNK_FLAG_XOR_PARITY
    && chunkIndex === chunkCount
    && payload.length === CHUNK_PAYLOAD_BYTES;
  if (!validMetadata || (!validData && !validParity)) return null;

  pruneTransfers(reassembler, nowMs);
  let transfer = reassembler.transfers.get(sequence);
  if (!transfer || transfer.chunkCount !== chunkCount || transfer.totalBytes !== totalBytes) {
    if (transfer) reassembler.transfers.delete(sequence);
    pruneTransfers(reassembler, nowMs);
    transfer = {
      createdAtMs: nowMs,
      chunkCount,
      totalBytes,
      chunks: Array.from({ length: chunkCount }, () => null),
      receivedChunks: 0,
      xorParity: null,
    };
    reassembler.transfers.set(sequence, transfer);
  }

  if (validData && !transfer.chunks[chunkIndex]) {
    transfer.chunks[chunkIndex] = payload;
    transfer.receivedChunks += 1;
  } else if (validParity) {
    transfer.xorParity = payload;
  }
  const frame = completeTransfer(transfer);
  if (frame) reassembler.transfers.delete(sequence);
  return frame;
}
