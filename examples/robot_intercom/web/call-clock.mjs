/** Elapsed connected time belongs to a call, including temporary recovery. */
export function createCallClock(now = () => performance.now()) {
  let id = null,
    started = null;
  return {
    update(call) {
      if (call.requestId !== id) {
        id = call.requestId;
        started = null;
      }
      if (call.state === "running" && started === null) started = now();
      if (started === null || !id) return "—";
      const seconds = Math.max(0, Math.floor((now() - started) / 1000));
      return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    },
  };
}
