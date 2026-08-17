const ANIMATION_ID_PATTERN = /^[a-z][a-z0-9_]{0,62}$/;
const DEFAULT_INTERVAL_MS = 8000;
const MIN_INTERVAL_MS = 3000;
const MAX_INTERVAL_MS = 120000;

export function normalizeAnimationCatalog(value) {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  return value.filter((animationId) => {
    if (typeof animationId !== "string" || !ANIMATION_ID_PATTERN.test(animationId) || seen.has(animationId)) {
      return false;
    }
    seen.add(animationId);
    return true;
  });
}

export function selectNextAnimation(value, lastAnimationId = null, random = Math.random) {
  const catalog = normalizeAnimationCatalog(value);
  if (catalog.length === 0) return null;
  const candidates = catalog.length > 1
    ? catalog.filter((animationId) => animationId !== lastAnimationId)
    : catalog;
  const sample = Number(random());
  const normalizedSample = Number.isFinite(sample) ? Math.min(Math.max(sample, 0), 0.999999999) : 0;
  return candidates[Math.floor(normalizedSample * candidates.length)];
}

export function createAnimationShuffleBag(value, lastAnimationId = null, random = Math.random) {
  const bag = normalizeAnimationCatalog(value);
  for (let index = bag.length - 1; index > 0; index -= 1) {
    const sample = Number(random());
    const normalizedSample = Number.isFinite(sample)
      ? Math.min(Math.max(sample, 0), 0.999999999)
      : 0;
    const swapIndex = Math.floor(normalizedSample * (index + 1));
    [bag[index], bag[swapIndex]] = [bag[swapIndex], bag[index]];
  }
  if (bag.length > 1 && bag[0] === lastAnimationId) {
    const replacementIndex = bag.findIndex((animationId) => animationId !== lastAnimationId);
    [bag[0], bag[replacementIndex]] = [bag[replacementIndex], bag[0]];
  }
  return bag;
}

export function clampAnimationIntervalMs(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_INTERVAL_MS;
  return Math.min(MAX_INTERVAL_MS, Math.max(MIN_INTERVAL_MS, Math.round(numeric)));
}
