#!/usr/bin/env python3
"""Bridge one GitHub review job to the Luxiao Hermes profile safely."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


HERMES_HOST = "hermesadmin@192.168.1.116"
REMOTE_SCRIPT = "/home/hermesadmin/scripts/luxiao-run.sh"
REMOTE_DIR = "/home/hermesadmin/.cache/luxiao-review"
SSH_OPTIONS = ("-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes")


def _write_result(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: luxiao_review.py <diff_path> <output_path>", file=sys.stderr)
        return 2

    diff_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    if not diff_path.is_file():
        _write_result(output_path, "## 🤖 Luxiao PR 审查报告\n\n⚠️ Diff 文件不存在。")
        return 1

    diff_text = diff_path.read_text(encoding="utf-8", errors="replace")[:8000]
    if not diff_text.strip() or diff_text.strip() == "empty":
        _write_result(output_path, "## 🤖 Luxiao PR 审查报告\n\n✅ 无代码变更。")
        return 0

    prompt = f"""请审查以下 Pull Request。按照你的审查框架（架构、产品、规范、损伤 + 意图分析 + Merge 建议）给出完整审查报告。

## PR 信息

- 标题: {os.environ.get('PR_TITLE', '')}
- 描述: {os.environ.get('PR_BODY', '')}
- 变更: {os.environ.get('PR_FILES', '')} 个文件, +{os.environ.get('PR_ADDITIONS', '')} / -{os.environ.get('PR_DELETIONS', '')}

## 代码 Diff

{diff_text}

请直接输出审查报告，不要多余的前缀。"""

    runner_temp = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    runner_temp.mkdir(parents=True, exist_ok=True)
    remote_file = f"{REMOTE_DIR}/{uuid.uuid4().hex}.txt"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="luxiao-prompt-", suffix=".txt", dir=runner_temp, delete=False
    ) as prompt_file:
        prompt_file.write(prompt)
        local_file = Path(prompt_file.name)

    try:
        subprocess.run(
            ["ssh", *SSH_OPTIONS, HERMES_HOST, "mkdir", "-p", REMOTE_DIR],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        subprocess.run(
            ["scp", *SSH_OPTIONS, str(local_file), f"{HERMES_HOST}:{remote_file}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        result = subprocess.run(
            ["ssh", *SSH_OPTIONS, HERMES_HOST, f"{REMOTE_SCRIPT} {shlex.quote(remote_file)}"],
            capture_output=True,
            text=True,
            timeout=300,
        )
    finally:
        local_file.unlink(missing_ok=True)
        subprocess.run(
            ["ssh", *SSH_OPTIONS, HERMES_HOST, "rm", "-f", "--", remote_file],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    if result.returncode != 0 or not result.stdout.strip():
        _write_result(
            output_path,
            "## 🤖 Luxiao PR 审查报告\n\n"
            f"⚠️ Luxiao Agent 调用失败（退出码 {result.returncode}）。",
        )
        return 1

    text = result.stdout
    markers = ("🤖 PR 审查报告", "PR 审查报告", "## PR 审查", "## 审查报告")
    for marker in markers:
        if marker in text:
            text = text[text.index(marker) :]
            break
    else:
        text = text[-6000:]
    _write_result(output_path, "## 🤖 Luxiao PR 审查报告\n\n" + text)
    print(f"Review saved to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
