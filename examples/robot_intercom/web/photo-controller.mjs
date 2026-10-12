/** An estimate describes waiting for a photo; it never delays the shutter. */
export function createPhotoController({
  capture,
  load,
  isCurrent = () => true,
  onProgress = () => {},
  onPhoto = () => {},
  onError = () => {},
  onIdle = () => {},
  onCancel = () => {},
  clock = () => performance.now(),
  schedule = setTimeout,
  cancel = clearTimeout,
  estimateMs = 2000,
  timeoutMs = 15000,
} = {}) {
  let active = null;
  let estimate = estimateMs;
  const elapsed = (job) => Math.max(0, clock() - job.started);
  function notify(job) {
    if (active !== job) return;
    const ms = elapsed(job);
    onProgress({
      phase: job.phase,
      remainingSeconds: Math.max(0, Math.ceil((job.estimate - ms) / 1000)),
      elapsedSeconds: Math.floor(ms / 1000),
    });
  }
  function clear(job) {
    cancel(job.ticker);
    cancel(job.deadline);
  }
  const controller = {
    snapshot: () => ({ busy: active !== null }),
    async take(requestId) {
      if (active || !isCurrent(requestId)) return false;
      const abort = new AbortController();
      let interrupt;
      const interruption = new Promise((resolve, reject) => {
        interrupt = reject;
      });
      const job = {
        requestId,
        abort,
        interrupt,
        started: clock(),
        estimate,
        phase: "capturing",
        cancelled: false,
      };
      active = job;
      notify(job);
      function tick() {
        if (active !== job) return;
        notify(job);
        job.ticker = schedule(tick, 1000);
      }
      job.ticker = schedule(tick, 1000);
      job.deadline = schedule(() => {
        interrupt(Error("照片获取超时，请稍后重试。通话不受影响。"));
        abort.abort();
      }, timeoutMs);
      const work = (async () => {
        const photo = await capture(requestId, abort.signal);
        if (active !== job || !isCurrent(requestId)) return false;
        job.phase = "loading";
        notify(job);
        const image = await load(photo, abort.signal);
        if (active !== job || !isCurrent(requestId)) return false;
        const duration = elapsed(job);
        onPhoto(photo, image, duration);
        estimate = Math.min(
          10000,
          Math.max(2000, Math.ceil(duration / 1000) * 1000),
        );
        return true;
      })();
      try {
        return await Promise.race([work, interruption]);
      } catch (error) {
        if (active === job && !job.cancelled && isCurrent(requestId))
          onError(error);
        return false;
      } finally {
        if (active === job) {
          clear(job);
          active = null;
          onIdle();
        }
      }
    },
    updateCall({ requestId, state }) {
      if (active && (requestId !== active.requestId || state !== "running"))
        controller.cancel();
    },
    cancel() {
      const job = active;
      if (!job) return;
      job.cancelled = true;
      active = null;
      clear(job);
      job.interrupt(Error("Photo cancelled"));
      job.abort.abort();
      onCancel();
      onIdle();
    },
  };
  return controller;
}
