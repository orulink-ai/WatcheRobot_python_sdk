import test from "node:test";
import assert from "node:assert/strict";
import { detectionLabel, testBenchModels, createPreviewLifecycle } from "../../examples/sdk_media_lab/web/model-preview.mjs";

test("gesture labels follow the confirmed slot 3 order only", () => {
  assert.deepEqual([0, 1, 2].map(id => detectionLabel(3, id)), ["Paper", "Rock", "Scissors"]);
  assert.equal(detectionLabel(1, 0), "ID 0");
  assert.equal(detectionLabel(3, 9), "ID 9");
});

test("test bench keeps gesture and face models without inventing missing slots", () => {
  const models = [1, 2, 3, 4].map(model_id => ({ model_id, verified: model_id !== 4 }));
  assert.deepEqual(testBenchModels(models), [models[2], models[3]]);
  assert.deepEqual(testBenchModels(models.slice(0, 2)), []);
  assert.equal(models.length, 4);
});

test("preview expires even while no request completes", () => {
  const state = createPreviewLifecycle();
  const token = state.start(0);
  assert.equal(state.accept(token, 100), true);
  assert.equal(state.expired(2101), true);
  assert.equal(state.accept(token, 2200), true);
  assert.equal(state.expired(2201), false);
});

test("late responses after stop or restart cannot refresh the preview", () => {
  const state = createPreviewLifecycle();
  const old = state.start(0);
  state.stop();
  assert.equal(state.accept(old, 100), false);
  const current = state.start(200);
  assert.equal(state.accept(old, 300), false);
  assert.equal(state.expired(2201), true);
  assert.equal(state.accept(current, 2300), true);
  assert.equal(state.expired(2301), false);
});
