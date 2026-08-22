export function scaleFace(face, sourceWidth, sourceHeight, canvasWidth, canvasHeight) {
  const scaleX = canvasWidth / sourceWidth;
  const scaleY = canvasHeight / sourceHeight;
  return {
    x: face.x * scaleX,
    y: face.y * scaleY,
    width: face.width * scaleX,
    height: face.height * scaleY,
  };
}

export function deadZoneRect(width, height, fraction = 0.24) {
  const zoneWidth = width * fraction;
  const zoneHeight = height * fraction;
  return {
    x: (width - zoneWidth) / 2,
    y: (height - zoneHeight) / 2,
    width: zoneWidth,
    height: zoneHeight,
  };
}

export function targetPoint(metadata, canvasWidth, canvasHeight) {
  const target = metadata.faces?.find((face) => face.target) ?? metadata.faces?.[0];
  if (!target) return null;
  const scaled = scaleFace(
    target,
    metadata.width,
    metadata.height,
    canvasWidth,
    canvasHeight,
  );
  return {
    x: scaled.x + scaled.width / 2,
    y: scaled.y + scaled.height / 2,
  };
}
