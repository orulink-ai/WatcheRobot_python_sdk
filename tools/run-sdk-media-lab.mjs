import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const repositoryRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const windowsVenvPython = join(
  repositoryRoot,
  ".venv",
  "Scripts",
  "python.exe",
);
const posixVenvPython = join(repositoryRoot, ".venv", "bin", "python");
const configuredPython = process.env.WATCHEROBOT_PYTHON?.trim();

let executable;
let executablePrefix = [];
if (configuredPython) {
  executable = configuredPython;
} else if (existsSync(windowsVenvPython)) {
  executable = windowsVenvPython;
} else if (existsSync(posixVenvPython)) {
  executable = posixVenvPython;
} else if (process.platform === "win32") {
  executable = "py";
  executablePrefix = ["-3"];
} else {
  executable = "python3";
}

const applicationRoot = join(repositoryRoot, "examples", "sdk_media_lab");
const sdkArguments = ["-m", "watcherobot.cli", "app", "run", applicationRoot];
const child = spawn(executable, [...executablePrefix, ...sdkArguments], {
  cwd: repositoryRoot,
  env: process.env,
  shell: false,
  stdio: "inherit",
});

child.once("error", (error) => {
  console.error(
    `无法启动 Python SDK 测试台：${error.message}\n` +
      "请先执行 Python 依赖安装，或通过 WATCHEROBOT_PYTHON 指定解释器。",
  );
  process.exitCode = 1;
});

child.once("exit", (code, signal) => {
  if (signal) {
    process.exitCode = 1;
    return;
  }
  process.exitCode = code ?? 1;
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => {
    if (!child.killed) {
      child.kill(signal);
    }
  });
}
