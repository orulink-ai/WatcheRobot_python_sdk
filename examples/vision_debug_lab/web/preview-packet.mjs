const HEADER_BYTES = 8;

export function parseVisionPacket(buffer) {
  const bytes = new Uint8Array(buffer);
  if (bytes.byteLength <= HEADER_BYTES) {
    throw new Error("Preview packet is too short");
  }
  const magic = String.fromCharCode(...bytes.slice(0, 4));
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const metadataLength = view.getUint32(4, true);
  const metadataEnd = HEADER_BYTES + metadataLength;
  if (magic !== "VDL1" || metadataLength === 0 || metadataEnd >= bytes.byteLength) {
    throw new Error("Invalid VDL1 preview packet");
  }
  let metadata;
  try {
    metadata = JSON.parse(new TextDecoder().decode(bytes.slice(HEADER_BYTES, metadataEnd)));
  } catch (error) {
    throw new Error(`Invalid preview metadata: ${error.message}`);
  }
  const jpeg = bytes.slice(metadataEnd);
  if (
    jpeg[0] !== 0xff ||
    jpeg[1] !== 0xd8 ||
    jpeg[jpeg.length - 2] !== 0xff ||
    jpeg[jpeg.length - 1] !== 0xd9
  ) {
    throw new Error("Preview payload is not JPEG");
  }
  return { metadata, jpeg };
}

export function buildPreviewWebSocketUrl(locationLike = window.location) {
  const protocol = locationLike.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${locationLike.host}/ws/preview`;
}
