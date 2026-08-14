import assert from "node:assert/strict";
import test from "node:test";

import {
  evaluateResourceLifecycle,
  selectLifecycleBaseline,
  selectLatestReleaseSnapshot,
} from "../../examples/sdk_media_lab/web/resource-health.mjs";

const baseline = {
  stage: "baseline",
  memory: { internal: { free_bytes: 50000, largest_free_block_bytes: 28000 } },
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
