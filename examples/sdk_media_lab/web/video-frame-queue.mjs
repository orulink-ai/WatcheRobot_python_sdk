export function admitVideoFrame(queue, frame) {
  if (!queue.decodeBusy) {
    queue.decodeBusy = true;
    return { ownsDecoder: true, replacedPending: false };
  }
  const replacedPending = queue.pendingFrame !== null;
  queue.pendingFrame = frame;
  return { ownsDecoder: false, replacedPending };
}

export function takePendingVideoFrame(queue) {
  const frame = queue.pendingFrame;
  queue.pendingFrame = null;
  return frame;
}

export function finishVideoFrameDecode(queue, ownsDecoder) {
  if (ownsDecoder) queue.decodeBusy = false;
}
