import assert from "node:assert/strict";
import test from "node:test";

import {
  evaluateResourceLifecycle,
  selectLifecycleBaseline,
  selectLatestReleaseSnapshot,
} from "../../examples/sdk_media_lab/web/resource-health.mjs";

const baseline = {
  stage: "baseline",
  memory: {
    internal: { free_bytes: 50000, largest_free_block_bytes: 28000 },
    dma: { free_bytes: 42000, largest_free_block_bytes: 22000 },
    psram: { free_bytes: 5_000_000, largest_free_block_bytes: 4_500_000 },
  },
  release: { complete: true, failures: [] },
};

function snapshot(stage, freeBytes, largestFreeBlockBytes) {
  return {
    stage,
    memory: {
      internal: {
        free_bytes: freeBytes,
        largest_free_block_bytes: largestFreeBlockBytes,
      },
      dma: {
        free_bytes: freeBytes,
        largest_free_block_bytes: largestFreeBlockBytes,
      },
      psram: {
        free_bytes: freeBytes * 100,
        largest_free_block_bytes: largestFreeBlockBytes * 100,
      },
    },
    release: { complete: true, failures: [] },
  };
}

test("resource lifecycle waits for both current and baseline snapshots", () => {
  assert.equal(evaluateResourceLifecycle(null, baseline).state, "waiting");
});

test("release within baseline tolerance is recovered", () => {
  const current = {
    stage: "rtc_release_1000ms",
    memory: { internal: { free_bytes: 46000, largest_free_block_bytes: 24000 } },
    release: { complete: true, failures: [] },
  };
  assert.equal(evaluateResourceLifecycle(current, baseline).state, "recovered");
});

test("release result failures override memory recovery", () => {
  const current = {
    stage: "rtc_release_1000ms",
    memory: { internal: { free_bytes: 50000, largest_free_block_bytes: 28000 } },
    release: { complete: false, failures: ["media_teardown"] },
  };
  assert.equal(evaluateResourceLifecycle(current, baseline).state, "failed");
});

test("large retained allocation is degraded after release", () => {
  const current = {
    stage: "rtc_release_3000ms",
    memory: { internal: { free_bytes: 35000, largest_free_block_bytes: 12000 } },
    release: { complete: true, failures: [] },
  };
  assert.equal(evaluateResourceLifecycle(current, baseline).state, "degraded");
});

test("animation started during RTC makes the release baseline incomparable instead of a false leak", () => {
  const current = {
    stage: "rtc_release_3000ms",
    memory: {
      internal: { free_bytes: 35000, largest_free_block_bytes: 12000 },
      psram: { free_bytes: 4_200_000, largest_free_block_bytes: 3_700_000 },
    },
    resources: { animation: true },
    release: { complete: true, failures: [] },
  };
  const beforeRtc = {
    ...baseline,
    resources: { animation: false },
  };

  const result = evaluateResourceLifecycle(current, beforeRtc);

  assert.equal(result.state, "context_changed");
  assert.deepEqual(result.contextChanges, ["animation"]);
});

test("animation runtime warmed during RTC is a lifecycle context change even without a visible frame", () => {
  const current = {
    ...baseline,
    stage: "rtc_release_3000ms",
    resources: { animation: false, animation_runtime: true },
  };
  const beforeRtc = {
    ...baseline,
    resources: { animation: false, animation_runtime: false },
  };

  const result = evaluateResourceLifecycle(current, beforeRtc);

  assert.equal(result.state, "context_changed");
  assert.deepEqual(result.contextChanges, ["animation_runtime"]);
});

test("matching animation context still detects a retained RTC allocation", () => {
  const current = {
    stage: "rtc_release_3000ms",
    memory: { internal: { free_bytes: 35000, largest_free_block_bytes: 12000 } },
    resources: { animation: true },
    release: { complete: true, failures: [] },
  };
  const beforeRtc = {
    ...baseline,
    resources: { animation: true },
  };

  assert.equal(evaluateResourceLifecycle(current, beforeRtc).state, "degraded");
});

test("animation cache churn does not masquerade as an RTC PSRAM leak", () => {
  const beforeRtc = {
    ...baseline,
    stage: "rtc_pre_start",
    resources: { animation: true, animation_runtime: true },
  };
  const current = {
    ...baseline,
    stage: "rtc_release_3000ms",
    memory: {
      ...baseline.memory,
      psram: { free_bytes: 4_600_000, largest_free_block_bytes: 3_900_000 },
    },
    resources: { animation: true, animation_runtime: true },
  };

  const result = evaluateResourceLifecycle(current, beforeRtc);

  assert.equal(result.state, "context_changed");
  assert.deepEqual(result.contextChanges, ["animation_memory"]);
});

test("DMA largest-block regression is degraded even when internal heap recovers", () => {
  const current = {
    ...baseline,
    stage: "rtc_release_3000ms",
    memory: {
      ...baseline.memory,
      dma: { free_bytes: 41000, largest_free_block_bytes: 9000 },
    },
  };

  const result = evaluateResourceLifecycle(current, baseline);

  assert.equal(result.state, "degraded");
  assert.equal(result.deltas.dmaLargestBytes, -13000);
});

test("PSRAM largest-block regression is visible instead of hidden by free bytes", () => {
  const current = {
    ...baseline,
    stage: "rtc_release_3000ms",
    memory: {
      ...baseline.memory,
      psram: { free_bytes: 5_000_000, largest_free_block_bytes: 4_000_000 },
    },
  };

  const result = evaluateResourceLifecycle(current, baseline);

  assert.equal(result.state, "degraded");
  assert.equal(result.deltas.psramLargestBytes, -500000);
});

test("monotonic post-release decline is flagged as a fragmentation trend", () => {
  const history = [
    snapshot("rtc_release_3000ms", 50000, 28000),
    snapshot("rtc_release_3000ms", 49500, 27000),
    snapshot("rtc_release_3000ms", 49000, 26000),
    snapshot("rtc_release_3000ms", 48500, 25000),
  ];

  const result = evaluateResourceLifecycle(history.at(-1), baseline, history);

  assert.equal(result.state, "degraded");
  assert.equal(result.trend.monotonicDecline, true);
});

test("latest release sample remains the lifecycle verdict after periodic telemetry resumes", () => {
  const release200 = snapshot("rtc_release_200ms", 118000, 59000);
  const release3000 = snapshot("rtc_release_3000ms", 127000, 63500);
  const periodic = snapshot("periodic", 126000, 63000);

  assert.equal(
    selectLatestReleaseSnapshot([release200, release3000, periodic]),
    release3000,
  );
});

test("RTC lifecycle uses its own pre-start baseline instead of connection baseline", () => {
  const connectionBaseline = snapshot("baseline", 50000, 28000);
  const rtcBaseline = snapshot("rtc_pre_start", 11000, 3700);
  const release = snapshot("rtc_release_3000ms", 10500, 3600);

  assert.equal(evaluateResourceLifecycle(release, connectionBaseline).state, "degraded");
  assert.equal(evaluateResourceLifecycle(release, rtcBaseline).state, "recovered");
});

test("empty RTC baseline falls back to the connection baseline", () => {
  assert.equal(
    selectLifecycleBaseline({ rtc_baseline: {}, baseline }),
    baseline,
  );
});
