// Slot 3 labels confirmed on the device: 0 paper, 1 rock, 2 scissors.
export function detectionLabel(modelId, target) {
  const gesture = ["Paper", "Rock", "Scissors"];
  return modelId === 3 && Number.isInteger(target) && target >= 0 && target < gesture.length
    ? gesture[target] : `ID ${target}`;
}

export function testBenchModels(models) {
  return models.filter(model => model.model_id === 3 || model.model_id === 4);
}
