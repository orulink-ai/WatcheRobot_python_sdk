export const ANIMATION_CONFIRM_TIMEOUT_MS = 5000;

export function evaluateAnimationConfirmation({
  animationId,
  requestedAtMs,
  nowMs,
  active,
}) {
  const normalizedId = String(animationId || "");
  if (!normalizedId || !Number.isFinite(Number(requestedAtMs))) {
    return { state: "idle", animationId: normalizedId };
  }
  if (active === true) {
    return { state: "confirmed", animationId: normalizedId };
  }
  if (Number(nowMs) - Number(requestedAtMs) >= ANIMATION_CONFIRM_TIMEOUT_MS) {
    return { state: "failed", animationId: normalizedId };
  }
  return { state: "pending", animationId: normalizedId };
}
