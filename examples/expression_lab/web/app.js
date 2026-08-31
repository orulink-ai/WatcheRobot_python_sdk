const byId = (id) => document.getElementById(id);
const canvas = byId("faceCanvas");
const GAZE_TRAVEL_PIXELS = 32;
const SECONDARY_GAZE_FOLLOW = 0.5;
const POINTER_SMOOTHING_MS = 90;
const POINTER_GAZE_GAIN_DEFAULT = 1.45;
const POINTER_FLAT_SYNC_INTERVAL_MS = 55;
const POINTER_FLAT_TRANSITION_MS = 90;
const POINTER_SYNC_EPSILON = 0.015;
const LID_MASK_HALF_WIDTH_PIXELS = 112;
const LID_MASK_HALF_HEIGHT_PIXELS = 64;
const EYELID_PRESET_STORAGE_KEY = "watcher.expressionLab.eyelidPresets.v1";
const EYELID_PRESET_COOKIE_KEY = "watcherExpressionEyelidsV1";
const EYELID_PRESET_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;
const MAX_EYELID_PRESETS = 12;
const displayCtx = canvas.getContext("2d", { alpha: false });
const flatCanvas = document.createElement("canvas");
flatCanvas.width = canvas.width; flatCanvas.height = canvas.height;
const ctx = flatCanvas.getContext("2d", { alpha: false });
const eyeCanvas = document.createElement("canvas");
eyeCanvas.width = canvas.width; eyeCanvas.height = canvas.height;
const eyeCtx = eyeCanvas.getContext("2d");
const controls = {
  style: byId("style"), tag: byId("tag"), accessory: byId("accessory"),
  accessoryScale: byId("accessoryScale"), accessoryX: byId("accessoryX"),
  accessoryY: byId("accessoryY"), accessoryRotation: byId("accessoryRotation"),
  gazeX: byId("gazeX"), gazeY: byId("gazeY"),
  openness: byId("openness"), spacing: byId("spacing"), scale: byId("scale"),
  scaleX: byId("scaleX"), scaleY: byId("scaleY"),
  stroke: byId("stroke"), roundness: byId("roundness"),
  leftOpenness: byId("leftOpenness"), rightOpenness: byId("rightOpenness"),
  tilt: byId("tilt"), leftTilt: byId("leftTilt"), rightTilt: byId("rightTilt"),
  leftUpperLidY: byId("leftUpperLidY"), leftUpperLidRotation: byId("leftUpperLidRotation"),
  rightUpperLidY: byId("rightUpperLidY"), rightUpperLidRotation: byId("rightUpperLidRotation"),
  leftLowerLidY: byId("leftLowerLidY"), leftLowerLidRotation: byId("leftLowerLidRotation"),
  rightLowerLidY: byId("rightLowerLidY"), rightLowerLidRotation: byId("rightLowerLidRotation"),
  transition: byId("transition"), autoBlink: byId("autoBlink"),
  blinkInterval: byId("blinkInterval"), blinkDuration: byId("blinkDuration"), eyeColor: byId("eyeColor"),
  pointerTracking: byId("pointerTracking"), pointerGain: byId("pointerGain"),
};
const rangeNumberEditors = new Map();
const eyelidControlNames = [
  "leftUpperLidY", "leftUpperLidRotation", "rightUpperLidY", "rightUpperLidRotation",
  "leftLowerLidY", "leftLowerLidRotation", "rightLowerLidY", "rightLowerLidRotation",
];
const state = {
  active: false, serviceReady: false, statusInitialized: false,
  deviceConnected: false, expressionSupported: false,
  preset: "standby", activeEyelidPresetId: null, eyelidPresets: [], sending: false, pairing: false, statusBusy: false,
  intentActive: false, resumePending: false, resumeTimer: 0,
  lastFrame: performance.now(), phase: 0, debounce: 0,
  pointerInside: false, pointerRawX: 0, pointerRawY: 0, pointerTargetX: 0, pointerTargetY: 0,
  pointerX: 0, pointerY: 0, pointerLastSyncAt: 0,
  pointerLastSentX: Number.NaN, pointerLastSentY: Number.NaN,
  updateBusy: false, queuedUpdate: null,
};
const presetDefaults = {
  standby: { openness: 1, spacing: .85, tilt: 0, tag: "none" },
  thinking: { openness: .72, spacing: .82, tilt: -7, tag: "thinking" },
  speaking: { openness: .9, spacing: .88, tilt: 0, tag: "none" },
};
const eyelidControlDefaults = {
  leftUpperLidY: -80, leftUpperLidRotation: 0,
  rightUpperLidY: -80, rightUpperLidRotation: 0,
  leftLowerLidY: 80, leftLowerLidRotation: 0,
  rightLowerLidY: 80, rightLowerLidRotation: 0,
};
const eyeControlDefaults = {
  style: "watcher", gazeX: 0, gazeY: 0, openness: 1, spacing: .85,
  scale: 1, scaleX: 2, scaleY: 2, stroke: 1, roundness: 1,
  leftOpenness: 1, rightOpenness: 1, tilt: 0, leftTilt: 0, rightTilt: 0,
  transition: 180, autoBlink: true, blinkInterval: 3600, blinkDuration: 200,
  eyeColor: "#a1f03c",
};
const accessoryControlDefaults = {
  tag: "none", accessory: "none", accessoryScale: 1,
  accessoryX: 0, accessoryY: 0, accessoryRotation: 0,
};

function rangeEditorLabel(range) {
  const source = range.getAttribute("aria-label") || range.id;
  return `${source.replace(/滑杆$/, "")}精确值`;
}

function syncRangeValueFromEditor(range, editor) {
  if (editor.value === "" || !Number.isFinite(Number(editor.value))) return;
  range.value = editor.value;
  editor.value = range.value;
  range.dispatchEvent(new Event("input", { bubbles: true }));
}

function initializeRangeNumberEditors() {
  document.querySelectorAll('input[type="range"]').forEach((range) => {
    let shell = range.parentElement?.classList.contains("range-with-number") ? range.parentElement : null;
    if (!shell) {
      shell = document.createElement("span");
      shell.className = "range-with-number";
      range.parentNode.insertBefore(shell, range);
      shell.append(range);
    }

    let editor = byId(`${range.id}Number`);
    if (!editor) {
      editor = document.createElement("input");
      editor.id = `${range.id}Number`;
      editor.className = "number-editor";
      editor.type = "number";
      editor.setAttribute("aria-label", rangeEditorLabel(range));
      for (const attribute of ["min", "max", "step"]) {
        if (range.hasAttribute(attribute)) editor.setAttribute(attribute, range.getAttribute(attribute));
      }
      shell.append(editor);
    }
    editor.value = range.value;
    rangeNumberEditors.set(range, editor);
    range.addEventListener("input", () => { editor.value = range.value; });
    editor.addEventListener("input", () => syncRangeValueFromEditor(range, editor));
    editor.addEventListener("blur", () => { editor.value = range.value; });
    editor.addEventListener("keydown", (event) => {
      if (event.key === "Enter") editor.blur();
    });
  });
}

function refreshRangeNumberEditors() {
  rangeNumberEditors.forEach((editor, range) => {
    if (document.activeElement !== editor || editor.value !== "") editor.value = range.value;
    editor.disabled = range.disabled;
  });
}

function readEyelidValues() {
  return Object.fromEntries(eyelidControlNames.map((name) => [name, Number(controls[name].value)]));
}

function normalizeEyelidPresets(parsed) {
  if (!Array.isArray(parsed)) return [];
  const expanded = parsed.map((preset) => {
    if (!Array.isArray(preset)) return preset;
    const [id, name, compactValues] = preset;
    if (!Array.isArray(compactValues)) return null;
    return { id, name, values: Object.fromEntries(eyelidControlNames.map((key, index) => [key, compactValues[index]])) };
  });
  return expanded.filter((preset) => (
    preset && typeof preset.id === "string" && typeof preset.name === "string" &&
    preset.values && eyelidControlNames.every((name) => Number.isFinite(Number(preset.values[name])))
  )).slice(0, MAX_EYELID_PRESETS);
}

function loadEyelidPresets() {
  try {
    const prefix = `${EYELID_PRESET_COOKIE_KEY}=`;
    const cookie = document.cookie.split("; ").find((part) => part.startsWith(prefix));
    if (cookie) return normalizeEyelidPresets(JSON.parse(decodeURIComponent(cookie.slice(prefix.length))));
    return normalizeEyelidPresets(JSON.parse(localStorage.getItem(EYELID_PRESET_STORAGE_KEY) || "[]"));
  } catch {
    return [];
  }
}

function persistEyelidPresets() {
  const compact = state.eyelidPresets.map((preset) => [
    preset.id,
    preset.name,
    eyelidControlNames.map((name) => preset.values[name]),
  ]);
  document.cookie = `${EYELID_PRESET_COOKIE_KEY}=${encodeURIComponent(JSON.stringify(compact))}; Max-Age=${EYELID_PRESET_COOKIE_MAX_AGE_SECONDS}; Path=/; SameSite=Strict`;
  localStorage.setItem(EYELID_PRESET_STORAGE_KEY, JSON.stringify(state.eyelidPresets));
}

function renderEyelidPresets() {
  const list = byId("eyelidPresetList");
  list.replaceChildren();
  byId("eyelidPresetEmpty").hidden = state.eyelidPresets.length > 0;
  state.eyelidPresets.forEach((preset) => {
    const item = document.createElement("div");
    item.className = "eyelid-preset-item";
    item.setAttribute("role", "listitem");

    const applyButton = document.createElement("button");
    applyButton.type = "button";
    applyButton.className = "user-eyelid-preset";
    applyButton.classList.toggle("active", state.activeEyelidPresetId === preset.id);
    applyButton.textContent = preset.name;
    applyButton.title = `调用眼皮预设：${preset.name}`;
    applyButton.addEventListener("click", () => applyEyelidPreset(preset.id));

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "delete-eyelid-preset";
    deleteButton.textContent = "×";
    deleteButton.setAttribute("aria-label", `删除眼皮预设 ${preset.name}`);
    deleteButton.addEventListener("click", () => deleteEyelidPreset(preset.id));

    item.append(applyButton, deleteButton);
    list.append(item);
  });
}

function saveCurrentEyelidPreset() {
  const nameInput = byId("eyelidPresetName");
  const name = nameInput.value.trim();
  if (!name) {
    toast("请先输入眼皮预设名称", "error");
    return;
  }
  const existing = state.eyelidPresets.find((preset) => preset.name.toLocaleLowerCase() === name.toLocaleLowerCase());
  if (!existing && state.eyelidPresets.length >= MAX_EYELID_PRESETS) {
    toast(`最多保存 ${MAX_EYELID_PRESETS} 个眼皮预设`, "error");
    return;
  }
  const preset = existing || { id: `eyelid-${Date.now()}`, name, values: {} };
  preset.name = name;
  preset.values = readEyelidValues();
  if (!existing) state.eyelidPresets.push(preset);
  state.activeEyelidPresetId = preset.id;
  try {
    persistEyelidPresets();
  } catch {
    toast("浏览器无法保存眼皮预设", "error");
    return;
  }
  nameInput.value = "";
  byId("saveEyelidPreset").disabled = true;
  renderEyelidPresets();
  refreshReadouts();
  toast(existing ? `眼皮预设“${name}”已更新` : `眼皮预设“${name}”已保存`);
}

function applyEyelidPreset(presetId) {
  const preset = state.eyelidPresets.find((candidate) => candidate.id === presetId);
  if (!preset) return;
  applyControlDefaults(preset.values);
  state.activeEyelidPresetId = preset.id;
  renderEyelidPresets();
  queueUpdate();
  toast(`已调用眼皮预设“${preset.name}”`);
}

function deleteEyelidPreset(presetId) {
  const preset = state.eyelidPresets.find((candidate) => candidate.id === presetId);
  if (!preset) return;
  state.eyelidPresets = state.eyelidPresets.filter((candidate) => candidate.id !== presetId);
  if (state.activeEyelidPresetId === presetId) state.activeEyelidPresetId = null;
  try {
    persistEyelidPresets();
  } catch {
    toast("浏览器无法更新眼皮预设", "error");
    return;
  }
  renderEyelidPresets();
  refreshReadouts();
  toast(`眼皮预设“${preset.name}”已删除`);
}

function effectiveGaze() {
  if (controls.pointerTracking.checked) {
    return { x: state.pointerX, y: state.pointerY };
  }
  return { x: Number(controls.gazeX.value), y: Number(controls.gazeY.value) };
}

function values() {
  const gaze = effectiveGaze();
  return {
    preset: state.preset, style: controls.style.value, tag: controls.tag.value,
    accessory: controls.accessory.value,
    accessory_scale: Number(controls.accessoryScale.value),
    accessory_x: Number(controls.accessoryX.value),
    accessory_y: Number(controls.accessoryY.value),
    accessory_rotation_deg: Number(controls.accessoryRotation.value),
    gaze_x: Number(gaze.x.toFixed(3)), gaze_y: Number(gaze.y.toFixed(3)),
    openness: Number(controls.openness.value), spacing: Number(controls.spacing.value),
    scale: Number(controls.scale.value),
    scale_x: Number(controls.scaleX.value), scale_y: Number(controls.scaleY.value),
    stroke: Number(controls.stroke.value), roundness: Number(controls.roundness.value),
    left_openness: Number(controls.leftOpenness.value), right_openness: Number(controls.rightOpenness.value),
    tilt_deg: Number(controls.tilt.value), left_tilt_deg: Number(controls.leftTilt.value),
    right_tilt_deg: Number(controls.rightTilt.value), transition_ms: Number(controls.transition.value),
    left_upper_lid_y: Number(controls.leftUpperLidY.value),
    left_upper_lid_rotation_deg: Number(controls.leftUpperLidRotation.value),
    right_upper_lid_y: Number(controls.rightUpperLidY.value),
    right_upper_lid_rotation_deg: Number(controls.rightUpperLidRotation.value),
    left_lower_lid_y: Number(controls.leftLowerLidY.value),
    left_lower_lid_rotation_deg: Number(controls.leftLowerLidRotation.value),
    right_lower_lid_y: Number(controls.rightLowerLidY.value),
    right_lower_lid_rotation_deg: Number(controls.rightLowerLidRotation.value),
    auto_blink: controls.autoBlink.checked, blink_interval_ms: Number(controls.blinkInterval.value),
    blink_duration_ms: Number(controls.blinkDuration.value), color: controls.eyeColor.value.toUpperCase(),
    sphere_strength: 0,
  };
}

function refreshReadouts() {
  renderAccessoryModule();
  refreshPointerReadout();
  refreshRangeNumberEditors();
  byId("opennessValue").value = Number(controls.openness.value).toFixed(2);
  byId("spacingValue").value = Number(controls.spacing.value).toFixed(2);
  byId("scaleValue").value = Number(controls.scale.value).toFixed(2);
  byId("scaleXValue").value = Number(controls.scaleX.value).toFixed(2);
  byId("scaleYValue").value = Number(controls.scaleY.value).toFixed(2);
  byId("accessoryScaleValue").value = Number(controls.accessoryScale.value).toFixed(2);
  byId("accessoryXValue").value = Number(controls.accessoryX.value).toFixed(2);
  byId("accessoryYValue").value = Number(controls.accessoryY.value).toFixed(2);
  byId("accessoryRotationValue").value = `${controls.accessoryRotation.value}°`;
  byId("strokeValue").value = Number(controls.stroke.value).toFixed(2);
  byId("roundnessValue").value = Number(controls.roundness.value).toFixed(2);
  byId("leftOpennessValue").value = Number(controls.leftOpenness.value).toFixed(2);
  byId("rightOpennessValue").value = Number(controls.rightOpenness.value).toFixed(2);
  byId("tiltValue").value = `${controls.tilt.value}°`;
  byId("leftTiltValue").value = `${controls.leftTilt.value}°`;
  byId("rightTiltValue").value = `${controls.rightTilt.value}°`;
  for (const lid of ["leftUpper", "rightUpper", "leftLower", "rightLower"]) {
    byId(`${lid}LidYValue`).value = controls[`${lid}LidY`].value;
    byId(`${lid}LidRotationValue`).value = `${controls[`${lid}LidRotation`].value}°`;
  }
  const activeEyelidPreset = state.eyelidPresets.find((preset) => preset.id === state.activeEyelidPresetId);
  byId("eyeShapeState").textContent = activeEyelidPreset
    ? `已加载：${activeEyelidPreset.name}`
    : "自定义 · 四遮罩独立控制";
  byId("transitionValue").value = `${controls.transition.value} ms`;
  byId("blinkIntervalValue").value = `${controls.blinkInterval.value} ms`;
  byId("blinkDurationValue").value = `${controls.blinkDuration.value} ms`;
  byId("eyeColorValue").value = controls.eyeColor.value.toUpperCase();
  byId("presetReadout").textContent = state.preset.toUpperCase();
  const args = values();
  const lines = Object.entries(args).map(([key, value]) => `    ${key}=${typeof value === "string" ? `"${value}"` : value},`);
  byId("sdkOutput").textContent = `app.robot.expression_runtime.${state.active ? "update" : "start"}(\n${lines.join("\n")}\n)`;
}

function refreshPointerReadout() {
  const gaze = effectiveGaze();
  const tracking = controls.pointerTracking.checked;
  byId("gazeXValue").value = gaze.x.toFixed(2);
  byId("gazeYValue").value = gaze.y.toFixed(2);
  controls.gazeX.disabled = tracking;
  controls.gazeY.disabled = tracking;
  byId("pointerGainValue").value = `${Number(controls.pointerGain.value).toFixed(2)}×`;
  canvas.dataset.pointerTracking = String(tracking);
  byId("pointerTrackingState").textContent = tracking
    ? (state.pointerInside ? "正在跟随鼠标 · Web 与 Watcher 使用同一视线参数" : "将鼠标移入画面，移出后会平滑回正")
    : "鼠标追踪已关闭，可使用水平/垂直视线滑杆";
  document.querySelector(".pointer-tracking-bar").dataset.inside = String(tracking && state.pointerInside);
}

function renderAccessoryModule() {
  const hasAccessory = controls.accessory.value !== "none";
  byId("accessoryControlsModule").dataset.active = String(hasAccessory);
  byId("accessoryModuleState").textContent = hasAccessory ? "装饰可独立变换" : "先选择头部装饰";
  controls.accessoryScale.disabled = !hasAccessory;
  controls.accessoryX.disabled = !hasAccessory;
  controls.accessoryY.disabled = !hasAccessory;
  controls.accessoryRotation.disabled = !hasAccessory;
}

function drawLidMask(context, centerX, centerY, logicalY, rotationDeg, clipLeft, clipRight) {
  context.save();
  context.beginPath();
  context.rect(clipLeft, 0, clipRight - clipLeft, canvas.height);
  context.clip();
  context.translate(centerX, centerY + logicalY * 2);
  context.rotate(rotationDeg * Math.PI / 180);
  context.fillRect(
    -LID_MASK_HALF_WIDTH_PIXELS,
    -LID_MASK_HALF_HEIGHT_PIXELS,
    LID_MASK_HALF_WIDTH_PIXELS * 2,
    LID_MASK_HALF_HEIGHT_PIXELS * 2,
  );
  context.restore();
}

function drawEyelidMasks(p, eyeSpacing, gazeX, gazeY) {
  eyeCtx.save();
  eyeCtx.globalCompositeOperation = "destination-out";
  eyeCtx.fillStyle = "#000";
  const centerY = canvas.height / 2 + gazeY;
  const splitX = canvas.width / 2 + gazeX;
  drawLidMask(eyeCtx, canvas.width / 2 - eyeSpacing + gazeX, centerY, p.left_upper_lid_y, p.left_upper_lid_rotation_deg, 0, splitX);
  drawLidMask(eyeCtx, canvas.width / 2 + eyeSpacing + gazeX, centerY, p.right_upper_lid_y, p.right_upper_lid_rotation_deg, splitX, canvas.width);
  drawLidMask(eyeCtx, canvas.width / 2 - eyeSpacing + gazeX, centerY, p.left_lower_lid_y, p.left_lower_lid_rotation_deg, 0, splitX);
  drawLidMask(eyeCtx, canvas.width / 2 + eyeSpacing + gazeX, centerY, p.right_lower_lid_y, p.right_lower_lid_rotation_deg, splitX, canvas.width);
  eyeCtx.restore();
}

function roundedRect(x, y, width, height, radius, context = ctx) {
  const r = Math.min(radius, width / 2, height / 2);
  context.beginPath(); context.roundRect(x, y, width, height, r); context.fill();
}

// The three silhouettes are the artist-supplied SVG paths. Web keeps them as
// vectors; firmware uses masks generated from the same 1024-unit source paths.
const TAG_SVG_PATHS = {
  question: "M500.382 0.006c-177.646 19.719-276.341 96.721-296.085 230.93-3.949 43.437 17.757 67.13 65.143 71.066 23.667 3.961 43.411-13.808 59.207-53.296 23.692-82.9 80.862-124.35 171.735-124.35 110.479 7.885 169.698 63.156 177.671 165.8 0 94.759-64.313 110.202-99.248 138.774-44.267 36.218-73.217 72.952-108.655 135.216-29.779 52.314-34.91 164.227-34.91 164.227 0 47.373 21.655 71.066 65.143 71.066 39.413 0 61.194-23.693 65.155-71.066 0 0 5.219-125.594 55.925-181.129 57.472-62.942 194.749-107.071 198.698-268.922C804.365 108.561 697.772 15.789 500.382 0.006zM500.382 859.162c-45.524 0-82.409 36.91-82.409 82.41 0 45.523 36.885 82.422 82.409 82.422s82.422-36.898 82.422-82.422c0-45.5-36.898-82.41-82.422-82.41z",
  love: "M533.504 268.288q33.792-41.984 71.68-75.776 32.768-27.648 74.24-50.176t86.528-19.456q63.488 5.12 105.984 30.208t67.584 63.488 34.304 87.04 6.144 99.84-17.92 97.792-36.864 87.04-48.64 74.752-53.248 61.952q-40.96 41.984-85.504 78.336t-84.992 62.464-73.728 41.472-51.712 15.36q-20.48 1.024-52.224-14.336t-69.632-41.472-79.872-61.952-82.944-75.776q-26.624-25.6-57.344-59.392t-57.856-74.24-46.592-87.552-21.504-100.352 11.264-99.84 39.936-83.456 65.536-61.952 88.064-35.328q24.576-5.12 49.152-1.536t48.128 12.288 45.056 22.016 40.96 27.648q45.056 33.792 86.016 80.896z",
  thinking: "M480 179.2c12.8 6.4 25.6 12.8 32 32l6.4 44.8c12.8 64 44.8 121.6 89.6 166.4 44.8 44.8 102.4 76.8 166.4 89.6l44.8 12.8c6.4 0 12.8 6.4 19.2 12.8 6.4 6.4 6.4 12.8 6.4 25.6 0 6.4 0 19.2-6.4 25.6-6.4 6.4-12.8 12.8-19.2 12.8l-44.8 12.8c-64 12.8-121.6 44.8-166.4 89.6-44.8 44.8-76.8 102.4-89.6 166.4l-6.4 44.8c0 6.4-6.4 12.8-12.8 19.2-6.4 6.4-12.8 6.4-25.6 6.4-6.4 0-19.2 0-25.6-6.4-6.4-6.4-12.8-12.8-12.8-19.2l-12.8-44.8c-12.8-64-44.8-121.6-89.6-166.4a326.4 326.4 0 0 0-166.4-89.6l-51.2-12.8c-6.4 0-12.8-6.4-19.2-12.8-6.4-6.4-6.4-19.2-6.4-25.6 0-6.4 0-19.2 6.4-25.6 6.4-6.4 12.8-12.8 19.2-12.8l51.2-12.8c64-12.8 121.6-44.8 166.4-89.6C377.6 377.6 409.6 320 422.4 256l12.8-44.8c0-12.8 6.4-19.2 19.2-25.6 6.4-6.4 19.2-6.4 25.6-6.4z m-6.4 185.6c-44.8 89.6-115.2 153.6-198.4 198.4 83.2 38.4 153.6 108.8 192 192 38.4-83.2 108.8-153.6 192-192C582.4 518.4 512 454.4 473.6 364.8z m307.2-288c25.6 0 12.8 57.6 51.2 102.4 38.4 38.4 102.4 25.6 102.4 51.2s-64 25.6-96 57.6c-32 38.4-25.6 89.6-51.2 89.6s-12.8-57.6-57.6-96-96-25.6-96-57.6c0-25.6 57.6-12.8 96-51.2 38.4-32 25.6-96 51.2-96z",
};

const TAG_SVG_LAYOUTS = {
  thinking: { x: 300, y: 56, width: 72, height: 72, sourceX: 89, sourceY: 76, sourceWidth: 846, sourceHeight: 865 },
  question: { x: 312, y: 68, width: 48, height: 72, sourceX: 203, sourceY: 0, sourceWidth: 618, sourceHeight: 1024 },
  love: { x: 308, y: 70, width: 64, height: 56, sourceX: 59, sourceY: 122, sourceWidth: 922, sourceHeight: 801 },
};

const tagSvgPathCache = new Map();

function drawSvgTagPath(tag, color, gazeOffsetX = 0, gazeOffsetY = 0) {
  const pathData = TAG_SVG_PATHS[tag];
  const layout = TAG_SVG_LAYOUTS[tag];
  if (!pathData || !layout) return;
  if (!tagSvgPathCache.has(tag)) tagSvgPathCache.set(tag, new Path2D(pathData));
  const scale = Math.min(layout.width / layout.sourceWidth, layout.height / layout.sourceHeight);
  const offsetX = layout.x + gazeOffsetX + (layout.width - layout.sourceWidth * scale) / 2;
  const offsetY = layout.y + gazeOffsetY + (layout.height - layout.sourceHeight * scale) / 2;
  ctx.save();
  ctx.fillStyle = color;
  ctx.translate(offsetX, offsetY);
  ctx.scale(scale, scale);
  ctx.translate(-layout.sourceX, -layout.sourceY);
  ctx.fill(tagSvgPathCache.get(tag));
  ctx.restore();
}

function drawTag(tag, color, gazeOffsetX = 0, gazeOffsetY = 0) {
  drawSvgTagPath(tag, color, gazeOffsetX, gazeOffsetY);
}

const accessoryColors = {
  halo: "#FFD43B", devil_horns: "#FF4B55", ninja_mask: "#39445D",
  hero_mask: "#2878FF", eyepatch: "#667085", antenna: "#5DE4FF",
};

const accessoryAnchors = {
  halo: [206, 56], devil_horns: [206, 100], ninja_mask: [206, 206],
  hero_mask: [206, 206], eyepatch: [206, 160], antenna: [206, 80],
};

function drawAccessory(accessory, layer, t, transform, gazeOffsetX = 0, gazeOffsetY = 0) {
  if (accessory === "none") return;
  ctx.save();
  const [anchorX, anchorY] = accessoryAnchors[accessory] || [206, 206];
  ctx.translate(
    anchorX + transform.accessory_x * 206 + gazeOffsetX,
    anchorY + transform.accessory_y * 206 + gazeOffsetY,
  );
  ctx.rotate(transform.accessory_rotation_deg * Math.PI / 180);
  ctx.scale(transform.accessory_scale, transform.accessory_scale);
  ctx.translate(-anchorX, -anchorY);
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.strokeStyle = accessoryColors[accessory];
  ctx.fillStyle = accessoryColors[accessory];
  ctx.lineWidth = 8;
  if (layer === "back" && accessory === "halo") {
    ctx.beginPath(); ctx.ellipse(206, 56, 108, 18, 0, 0, Math.PI * 2); ctx.stroke();
  } else if (layer === "back" && accessory === "devil_horns") {
    ctx.beginPath(); ctx.moveTo(86, 126); ctx.lineTo(110, 48); ctx.lineTo(146, 126); ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.moveTo(266, 126); ctx.lineTo(302, 48); ctx.lineTo(328, 126); ctx.closePath(); ctx.fill();
  } else if (layer === "back" && accessory === "antenna") {
    ctx.beginPath(); ctx.moveTo(206, 112); ctx.lineTo(206, 48); ctx.stroke();
    ctx.beginPath(); ctx.arc(206, 32, 16, 0, Math.PI * 2); ctx.fill();
  } else if (layer === "back" && accessory === "ninja_mask") {
    ctx.fillRect(40, 152, 334, 126);
    ctx.beginPath(); ctx.moveTo(352, 164); ctx.lineTo(406, 144); ctx.lineTo(374, 194); ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.moveTo(352, 184); ctx.lineTo(406, 208); ctx.lineTo(366, 226); ctx.closePath(); ctx.fill();
  } else if (layer === "back" && accessory === "hero_mask") {
    ctx.beginPath(); ctx.moveTo(48, 152); ctx.lineTo(184, 136); ctx.lineTo(206, 164); ctx.lineTo(176, 268); ctx.lineTo(76, 264); ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.moveTo(364, 152); ctx.lineTo(228, 136); ctx.lineTo(206, 164); ctx.lineTo(236, 268); ctx.lineTo(336, 264); ctx.closePath(); ctx.fill();
  } else if (layer === "front" && accessory === "eyepatch") {
    ctx.strokeStyle = accessoryColors.eyepatch; ctx.lineWidth = 10;
    ctx.beginPath(); ctx.moveTo(18, 160); ctx.lineTo(394, 100); ctx.stroke();
    ctx.fillStyle = "#050607"; ctx.beginPath(); ctx.arc(132, 206, 64, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  }
  ctx.restore();
}

function applyPointerGain() {
  const configuredGain = Number(controls.pointerGain.value);
  const gain = Number.isFinite(configuredGain) ? configuredGain : POINTER_GAZE_GAIN_DEFAULT;
  state.pointerTargetX = Math.max(-1, Math.min(1, state.pointerRawX * gain));
  state.pointerTargetY = Math.max(-1, Math.min(1, state.pointerRawY * gain));
}

function updatePointerTarget(event) {
  if (!controls.pointerTracking.checked) return;
  const bounds = canvas.getBoundingClientRect();
  if (bounds.width <= 0 || bounds.height <= 0) return;
  state.pointerRawX = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
  state.pointerRawY = ((event.clientY - bounds.top) / bounds.height) * 2 - 1;
  applyPointerGain();
  state.pointerInside = true;
  refreshPointerReadout();
}

function releasePointerTarget() {
  if (!controls.pointerTracking.checked) return;
  state.pointerInside = false;
  state.pointerRawX = 0;
  state.pointerRawY = 0;
  state.pointerTargetX = 0;
  state.pointerTargetY = 0;
  refreshPointerReadout();
}

function updatePointerMotion(dt) {
  if (!controls.pointerTracking.checked) return;
  const smoothing = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ? 1
    : 1 - Math.exp(-dt / POINTER_SMOOTHING_MS);
  state.pointerX += (state.pointerTargetX - state.pointerX) * smoothing;
  state.pointerY += (state.pointerTargetY - state.pointerY) * smoothing;
  if (Math.abs(state.pointerTargetX - state.pointerX) < 0.001) state.pointerX = state.pointerTargetX;
  if (Math.abs(state.pointerTargetY - state.pointerY) < 0.001) state.pointerY = state.pointerTargetY;
}

function maybeSyncPointerGaze(now) {
  if (!controls.pointerTracking.checked || !state.active || state.sending ||
      now - state.pointerLastSyncAt < POINTER_FLAT_SYNC_INTERVAL_MS) return;
  const gaze = effectiveGaze();
  if (Number.isFinite(state.pointerLastSentX) &&
      Math.abs(gaze.x - state.pointerLastSentX) < POINTER_SYNC_EPSILON &&
      Math.abs(gaze.y - state.pointerLastSentY) < POINTER_SYNC_EPSILON) return;
  state.pointerLastSyncAt = now;
  state.pointerLastSentX = gaze.x;
  state.pointerLastSentY = gaze.y;
  sendExpressionUpdate({
    gaze_x: Number(gaze.x.toFixed(3)),
    gaze_y: Number(gaze.y.toFixed(3)),
    transition_ms: POINTER_FLAT_TRANSITION_MS,
  });
}

function render(now) {
  // Leave a small tolerance for 60 Hz requestAnimationFrame timestamps. A strict
  // 50 ms gate can miss the third callback at 49.x ms and fall back to 15 FPS.
  if (now - state.lastFrame < 45) { requestAnimationFrame(render); return; }
  const dt = Math.min(100, now - state.lastFrame); state.lastFrame = now; state.phase += dt / 1000;
  updatePointerMotion(dt);
  const p = values();
  let openness = p.openness;
  if (p.auto_blink) {
    const interval = p.blink_interval_ms / 1000;
    const duration = p.blink_duration_ms / 1000;
    const blink = state.phase % interval;
    const blinkStart = interval - duration;
    if (blink >= blinkStart) openness *= Math.max(.06, Math.abs(blink - (blinkStart + duration / 2)) / (duration / 2));
  }
  if (p.preset === "speaking" || p.style === "watcher_pulse") openness *= .83 + Math.sin(state.phase * 7.4) * .14;
  const styleScale = { watcher: 1, watcher_compact: .94, watcher_focus: .9, watcher_open: 1.12, watcher_pulse: 1 }[p.style];
  const gazeX = p.gaze_x * GAZE_TRAVEL_PIXELS; const gazeY = p.gaze_y * GAZE_TRAVEL_PIXELS;
  const secondaryGazeX = gazeX * SECONDARY_GAZE_FOLLOW;
  const secondaryGazeY = gazeY * SECONDARY_GAZE_FOLLOW;
  const segments = [
    [-30.75, 0, 5, 30], [-18.25, 0, 6, 58], [-6.25, -23.5, 6, 25], [-6.25, 24, 6, 24],
    [6.25, -23.5, 5, 25], [6.25, 23.5, 5, 25], [18.25, 0, 5, 58], [30.75, 0, 6, 30],
  ];
  const eyeSpacing = 88 * p.spacing;
  ctx.fillStyle = "#000"; ctx.fillRect(0, 0, canvas.width, canvas.height);
  drawAccessory(p.accessory, "back", state.phase, p, secondaryGazeX, secondaryGazeY);
  eyeCtx.clearRect(0, 0, eyeCanvas.width, eyeCanvas.height);
  eyeCtx.fillStyle = p.color;
  [-1, 1].forEach((side) => {
    const center = canvas.width / 2 + side * eyeSpacing + gazeX;
    const independentTilt = side < 0 ? p.left_tilt_deg : p.right_tilt_deg;
    const eyeOpenness = side < 0 ? p.left_openness : p.right_openness;
    const angle = (side * (p.tilt_deg + (p.style === "watcher_focus" ? 4 : 0)) + independentTilt) * Math.PI / 180;
    eyeCtx.save(); eyeCtx.translate(center, canvas.height / 2 + gazeY); eyeCtx.rotate(angle);
    segments.forEach(([x, y, width, height]) => {
      const scaledWidth = width * p.scale * p.scale_x * p.stroke;
      const scaledHeight = height * p.scale * p.scale_y * openness * styleScale * eyeOpenness;
      const scaledX = x * p.scale * p.scale_x;
      const scaledY = y * p.scale * p.scale_y * openness * styleScale * eyeOpenness;
      roundedRect(scaledX - scaledWidth / 2, scaledY - scaledHeight / 2, scaledWidth, scaledHeight, scaledWidth / 2 * p.roundness, eyeCtx);
    });
    eyeCtx.restore();
  });
  drawEyelidMasks(p, eyeSpacing, gazeX, gazeY);
  ctx.drawImage(eyeCanvas, 0, 0);
  drawAccessory(p.accessory, "front", state.phase, p, secondaryGazeX, secondaryGazeY);
  drawTag(p.tag, p.color, secondaryGazeX, secondaryGazeY);
  displayCtx.drawImage(flatCanvas, 0, 0);
  refreshPointerReadout();
  maybeSyncPointerGaze(now);
  byId("fpsReadout").textContent = `${(1000 / Math.max(1, dt)).toFixed(1)} FPS`;
  requestAnimationFrame(render);
}

function toast(message, tone = "ok") {
  const node = byId("toast"); node.textContent = message; node.dataset.tone = tone; node.classList.add("visible");
  window.clearTimeout(toast.timer); toast.timer = window.setTimeout(() => node.classList.remove("visible"), 2800);
}

async function api(path, body) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  const contentType = response.headers.get("content-type") || "";
  let payload;
  if (contentType.includes("application/json")) {
    payload = await response.json();
  } else if (state.resumePending) {
    title = "正在恢复代码表情";
    hint = "设备通道已重新连接，正在恢复上次的参数";
    status = "正在恢复同步";
    sync = "RESUMING";
    guideState = "connected";
    startLabel = "正在恢复…";
  } else {
    const detail = (await response.text()).trim();
    payload = { detail: detail || `请求失败（HTTP ${response.status}）` };
  }
  if (!response.ok) {
    const detail = Array.isArray(payload.detail)
      ? payload.detail.map((item) => item.msg || String(item)).join("；")
      : payload.detail;
    throw new Error(detail || "设备拒绝了参数");
  }
  return payload;
}

function renderConnectionState() {
  const guide = byId("connectionGuide");
  const start = byId("startButton");
  const stop = byId("stopButton");
  const pairingForm = byId("pairingForm");
  const pairingCode = byId("pairingCode");
  const pairingButton = byId("pairingButton");
  let title; let hint; let status; let sync; let guideState = "preview"; let startLabel = "请从 SDK 启动";

  if (!state.statusInitialized) {
    title = "正在连接 SDK";
    hint = "正在读取 Application 与 Watcher 通道状态";
    status = "正在连接 SDK";
    sync = "CHECKING SDK";
  } else if (!state.serviceReady) {
    title = "仅本地预览";
    hint = "请通过 watcherobot app run 启动 Expression Lab";
    status = "SDK 未启动";
    sync = "PREVIEW ONLY";
  } else if (!state.deviceConnected) {
    title = "等待 Watcher";
    hint = "在 Watcher 打开 Desktop Link；首次连接需要屏幕上的 6 位配对码";
    status = "等待 Watcher";
    sync = "WAITING FOR DEVICE";
    guideState = "waiting";
    startLabel = "等待 Watcher";
  } else if (!state.expressionSupported) {
    title = "Watcher 已连接，固件不兼容";
    hint = "当前设备缺少 expression.runtime.v3 能力，请更新实验固件";
    status = "固件需更新";
    sync = "FIRMWARE MISMATCH";
    guideState = "error";
    startLabel = "固件需更新";
  } else {
    title = state.active ? "代码表情运行中" : "Watcher 已连接";
    hint = state.active ? "参数正在通过 SDK Device channel 同步" : "设备通道已就绪，可以发送代码表情";
    status = state.active ? "代码表情运行中" : "Watcher 已连接";
    sync = state.active ? "DEVICE SYNC" : "DEVICE READY";
    guideState = "connected";
    startLabel = "发送到 Watcher";
  }

  guide.dataset.state = guideState;
  byId("connectionTitle").textContent = title;
  byId("connectionHint").textContent = hint;
  byId("statusText").textContent = status;
  byId("statusLamp").parentElement.dataset.active = String(state.deviceConnected && state.expressionSupported);
  byId("syncState").textContent = sync;
  start.textContent = state.sending ? "正在连接…" : startLabel;
  start.disabled = !state.serviceReady || !state.deviceConnected || !state.expressionSupported || state.active || state.sending || state.resumePending;
  stop.disabled = !state.active || state.sending;
  pairingForm.hidden = !state.serviceReady || state.deviceConnected;
  pairingCode.disabled = state.pairing;
  pairingButton.disabled = state.pairing || !/^[0-9]{6}$/.test(pairingCode.value);
  pairingButton.textContent = state.pairing ? "连接中…" : "连接";
  refreshReadouts();
}

function applyStatus(snapshot) {
  const wasActive = state.active;
  const wasConnected = state.deviceConnected;
  const snapshotConnected = Boolean(snapshot.device_connected);
  const snapshotActive = Boolean(snapshot.active) && snapshotConnected;
  state.statusInitialized = true;
  state.serviceReady = true;
  state.deviceConnected = snapshotConnected;
  state.expressionSupported = Boolean(snapshot.expression_supported);
  state.active = snapshotActive;
  if (snapshotActive) {
    state.intentActive = true;
    state.resumePending = false;
  } else if (state.intentActive && (wasActive || (!wasConnected && snapshotConnected))) {
    state.resumePending = true;
  }
  const performance = snapshot.performance || {};
  byId("deviceFps").textContent = performance.sample_valid ? `${Number(performance.measured_fps).toFixed(1)} / ${Number(performance.target_fps).toFixed(1)}` : "—";
  byId("deviceDraw").textContent = performance.sample_valid ? `${Number(performance.draw_ms).toFixed(1)} ms` : "—";
  byId("devicePsram").textContent = Number(performance.psram_free_bytes) > 0 ? `${(Number(performance.psram_free_bytes) / 1048576).toFixed(2)} MB` : "—";
  renderConnectionState();
  scheduleExpressionResume();
}

function scheduleExpressionResume() {
  window.clearTimeout(state.resumeTimer);
  if (!state.resumePending || state.sending || !state.deviceConnected || !state.expressionSupported) return;
  state.resumeTimer = window.setTimeout(() => startExpression({ resume: true }), 180);
}

async function refreshConnectionStatus() {
  if (state.statusBusy) return;
  state.statusBusy = true;
  try {
    const response = await fetch("./api/status", { cache: "no-store" });
    if (!response.ok) throw new Error("SDK service unavailable");
    applyStatus(await response.json());
  } catch (_) {
    if (state.active && state.intentActive) state.resumePending = true;
    state.statusInitialized = true;
    state.serviceReady = false;
    state.deviceConnected = false;
    state.expressionSupported = false;
    state.active = false;
    renderConnectionState();
  } finally {
    state.statusBusy = false;
  }
}

async function pairWatcher(event) {
  event.preventDefault();
  const input = byId("pairingCode");
  const pairingCode = input.value.replace(/\D/g, "").slice(0, 6);
  input.value = pairingCode;
  if (!/^[0-9]{6}$/.test(pairingCode) || state.pairing) return;
  state.pairing = true; renderConnectionState();
  try {
    applyStatus(await api("./api/pair", { pairing_code: pairingCode }));
    input.value = "";
    toast("配对成功，正在建立设备通道");
    await refreshConnectionStatus();
  } catch (error) {
    toast(error.message, "error");
    input.focus();
  } finally {
    state.pairing = false; renderConnectionState();
  }
}

async function startExpression({ resume = false } = {}) {
  if (state.sending) return;
  state.sending = true; renderConnectionState();
  try {
    const snapshot = await api("./api/expression/start", values());
    state.intentActive = true;
    state.resumePending = false;
    const gaze = effectiveGaze();
    state.pointerLastSentX = gaze.x;
    state.pointerLastSentY = gaze.y;
    state.pointerLastSyncAt = performance.now();
    applyStatus(snapshot);
    toast(resume ? "连接恢复，代码表情已重新同步" : "Watcher 已切换到代码表情");
  }
  catch (error) {
    state.active = false;
    state.intentActive = false;
    state.resumePending = false;
    toast(error.message, "error");
  }
  finally { state.sending = false; renderConnectionState(); }
}

async function stopExpression() {
  state.intentActive = false;
  state.resumePending = false;
  window.clearTimeout(state.resumeTimer);
  state.sending = true; renderConnectionState();
  try { applyStatus(await api("./api/expression/stop")); toast("已恢复 Watcher 默认显示"); }
  catch (error) { toast(error.message, "error"); }
  finally { state.sending = false; renderConnectionState(); }
}

function handleUpdateFailure(error) {
  state.active = false;
  state.resumePending = state.intentActive;
  state.queuedUpdate = null;
  byId("syncState").textContent = "SYNC ERROR";
  renderConnectionState();
  scheduleExpressionResume();
  toast(error.message, "error");
}

async function sendExpressionUpdate(payload) {
  if (!state.active) return;
  if (state.updateBusy) {
    state.queuedUpdate = { ...(state.queuedUpdate || {}), ...payload };
    return;
  }
  state.updateBusy = true;
  try {
    await api("./api/expression/update", payload);
    byId("syncState").textContent = "DEVICE SYNC";
  } catch (error) {
    handleUpdateFailure(error);
  } finally {
    state.updateBusy = false;
    const queued = state.queuedUpdate;
    state.queuedUpdate = null;
    if (queued && state.active) sendExpressionUpdate(queued);
  }
}

function queueUpdate() {
  refreshReadouts();
  if (!state.active) return;
  window.clearTimeout(state.debounce);
  state.debounce = window.setTimeout(() => sendExpressionUpdate(values()), 80);
}

function applyControlDefaults(defaults) {
  Object.entries(defaults).forEach(([name, value]) => {
    if (typeof value === "boolean") controls[name].checked = value;
    else controls[name].value = value;
  });
}

function resetEyeControls() {
  applyControlDefaults(eyeControlDefaults);
  state.preset = "standby";
  document.querySelectorAll(".preset").forEach((button) => {
    button.classList.toggle("active", button.dataset.preset === state.preset);
  });
  state.pointerInside = false;
  state.pointerRawX = 0; state.pointerRawY = 0;
  state.pointerTargetX = 0; state.pointerTargetY = 0;
  state.pointerX = 0; state.pointerY = 0;
  state.pointerLastSentX = Number.NaN; state.pointerLastSentY = Number.NaN;
  queueUpdate();
  toast("眼睛参数已恢复默认");
}

function resetEyelidControls() {
  state.activeEyelidPresetId = null;
  applyControlDefaults(eyelidControlDefaults);
  renderEyelidPresets();
  queueUpdate();
  toast("眼皮参数已恢复默认");
}

function resetAccessoryControls() {
  applyControlDefaults(accessoryControlDefaults);
  queueUpdate();
  toast("标签与装饰已恢复默认");
}

document.querySelectorAll(".preset").forEach((button) => button.addEventListener("click", () => {
  state.preset = button.dataset.preset;
  document.querySelectorAll(".preset").forEach((candidate) => candidate.classList.toggle("active", candidate === button));
  const defaults = presetDefaults[state.preset];
  controls.openness.value = defaults.openness; controls.spacing.value = defaults.spacing; controls.tilt.value = defaults.tilt; controls.tag.value = defaults.tag;
  queueUpdate();
}));
const lidControlNames = new Set(eyelidControlNames);
function markEyelidsCustom() {
  if (state.activeEyelidPresetId === null) return;
  state.activeEyelidPresetId = null;
  renderEyelidPresets();
}

initializeRangeNumberEditors();
Object.entries(controls).filter(([name]) => name !== "pointerTracking" && name !== "pointerGain")
  .forEach(([name, control]) => control.addEventListener("input", () => {
    if (lidControlNames.has(name)) {
      markEyelidsCustom();
    }
    queueUpdate();
  }));
controls.pointerTracking.addEventListener("input", () => {
  if (controls.pointerTracking.checked) {
    state.pointerX = Number(controls.gazeX.value);
    state.pointerY = Number(controls.gazeY.value);
    state.pointerRawX = 0;
    state.pointerRawY = 0;
    state.pointerTargetX = 0;
    state.pointerTargetY = 0;
  } else {
    state.pointerInside = false;
  }
  queueUpdate();
});
controls.pointerGain.addEventListener("input", () => {
  if (state.pointerInside) applyPointerGain();
  refreshReadouts();
});
byId("resetEyeDefaults").addEventListener("click", resetEyeControls);
byId("resetEyelidDefaults").addEventListener("click", resetEyelidControls);
byId("resetAccessoryDefaults").addEventListener("click", resetAccessoryControls);
byId("eyelidPresetName").addEventListener("input", (event) => {
  byId("saveEyelidPreset").disabled = event.target.value.trim() === "";
});
byId("eyelidPresetName").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.target.value.trim() !== "") saveCurrentEyelidPreset();
});
byId("saveEyelidPreset").addEventListener("click", saveCurrentEyelidPreset);
canvas.addEventListener("pointermove", updatePointerTarget);
canvas.addEventListener("pointerleave", releasePointerTarget);
canvas.addEventListener("pointercancel", releasePointerTarget);
byId("startButton").addEventListener("click", startExpression);
byId("stopButton").addEventListener("click", stopExpression);
byId("pairingForm").addEventListener("submit", pairWatcher);
byId("pairingCode").addEventListener("input", (event) => {
  event.target.value = event.target.value.replace(/\D/g, "").slice(0, 6);
  renderConnectionState();
});
byId("layout").addEventListener("change", (event) => { document.body.dataset.layout = event.target.value; });
byId("scanlines").addEventListener("change", (event) => document.body.classList.toggle("scanlines", event.target.checked));
byId("closeTweaks").addEventListener("click", () => { byId("tweaks").hidden = true; byId("openTweaks").hidden = false; });
byId("openTweaks").addEventListener("click", () => { byId("tweaks").hidden = false; byId("openTweaks").hidden = true; });
window.addEventListener("pagehide", () => { if (state.active) navigator.sendBeacon("./api/expression/stop"); });

document.body.classList.add("scanlines");
state.eyelidPresets = loadEyelidPresets();
renderEyelidPresets();
refreshReadouts();
renderConnectionState();
refreshConnectionStatus();
window.setInterval(refreshConnectionStatus, 1500);
requestAnimationFrame(render);
