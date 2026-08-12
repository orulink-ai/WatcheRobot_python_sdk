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


MAX_DIFF_CHARS = 100_000
REMOTE_REVIEW_TIMEOUT_SECONDS = 600
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

    hermes_host = os.environ.get("LUXIAO_HERMES_HOST", "").strip()
    remote_script = os.environ.get(
        "LUXIAO_REMOTE_SCRIPT", "/home/hermesadmin/scripts/luxiao-run.sh"
    ).strip()
    remote_dir = os.environ.get(
        "LUXIAO_REMOTE_DIR", "/home/hermesadmin/.cache/luxiao-review"
    ).strip()
    if not hermes_host:
        _write_result(
            output_path,
            "## 🤖 Luxiao PR 审查报告\n\n⚠️ Runner 未配置 LUXIAO_HERMES_HOST。",
        )
        return 1

    full_diff = diff_path.read_text(encoding="utf-8", errors="replace")
    diff_text = full_diff[:MAX_DIFF_CHARS]
    truncation_notice = ""
    if len(full_diff) > MAX_DIFF_CHARS:
        omitted_chars = len(full_diff) - MAX_DIFF_CHARS
        omitted_lines = full_diff[MAX_DIFF_CHARS:].count("\n")
        truncation_notice = (
            "\n\n> ⚠️ Diff 过大，本次输入已明确截断："
            f"省略 {omitted_chars} 个字符、约 {omitted_lines} 行。"
            "审查结论必须注明未覆盖范围，不能宣称完成全量审查。"
        )
    if not diff_text.strip() or diff_text.strip() == "empty":
        _write_result(output_path, "## 🤖 Luxiao PR 审查报告\n\n✅ 无代码变更。")
        return 0

    prompt = f"""请审查以下 Pull Request。按照你的审查框架（架构、产品、规范、损伤 + 意图分析 + Merge 建议）给出完整审查报告。

## PR 信息

- 标题: {os.environ.get('PR_TITLE', '')}
- 描述: {os.environ.get('PR_BODY', '')}
- 变更: {os.environ.get('PR_FILES', '')} 个文件, +{os.environ.get('PR_ADDITIONS', '')} / -{os.environ.get('PR_DELETIONS', '')}

## 代码 Diff

{diff_text}{truncation_notice}

请直接输出审查报告，不要多余的前缀。"""

    runner_temp = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    runner_temp.mkdir(parents=True, exist_ok=True)
    remote_file = f"{remote_dir}/{uuid.uuid4().hex}.txt"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="luxiao-prompt-", suffix=".txt", dir=runner_temp, delete=False
    ) as prompt_file:
        prompt_file.write(prompt)
        local_file = Path(prompt_file.name)

    try:
        subprocess.run(
            ["ssh", *SSH_OPTIONS, hermes_host, "mkdir", "-p", remote_dir],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        subprocess.run(
            ["scp", *SSH_OPTIONS, str(local_file), f"{hermes_host}:{remote_file}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        result = subprocess.run(
            [
                "ssh",
                *SSH_OPTIONS,
                hermes_host,
                "timeout",
                "--signal=TERM",
                "--kill-after=30s",
                f"{REMOTE_REVIEW_TIMEOUT_SECONDS}s",
                shlex.quote(remote_script),
                shlex.quote(remote_file),
            ],
            capture_output=True,
            text=True,
            timeout=REMOTE_REVIEW_TIMEOUT_SECONDS + 60,
        )
    finally:
        local_file.unlink(missing_ok=True)
        subprocess.run(
            ["ssh", *SSH_OPTIONS, hermes_host, "rm", "-f", "--", remote_file],
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
