import test from "node:test";
import assert from "node:assert/strict";
import { detectionLabel, testBenchModels } from "../../examples/sdk_media_lab/web/model-preview.mjs";

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
