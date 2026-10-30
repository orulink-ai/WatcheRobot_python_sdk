"""Local firmware inspection; never opens a serial port or starts a Daemon."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .contract import FirmwareError
from .package import inspect_package


def add_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    firmware = commands.add_parser(
        "firmware", help="Inspect compiled ESP32 firmware packages"
    )
    actions = firmware.add_subparsers(dest="firmware_command", required=True)
    check = actions.add_parser(
        "check", help="Validate a firmware ZIP without writing hardware"
    )
    check.add_argument("package", type=Path)
    check.add_argument("--jsonl", action="store_true")
    capabilities = actions.add_parser(
        "capabilities", help="Report implemented firmware capabilities"
    )
    capabilities.add_argument("--jsonl", action="store_true")
    execute = actions.add_parser(
        "execute", help="Execute one controlled Desktop firmware operation"
    )
    execute.add_argument(
        "--action",
        required=True,
        choices=(
            "marketplace",
            "download",
            "publish",
            "submit",
            "requirements",
            "history",
            "configure",
            "status",
        ),
    )
    execute.add_argument("--provider", required=True, choices=("huggingface", "gitee"))
    execute.add_argument("--request-json", default="{}")
    execute.add_argument("--cache-root", type=Path, required=True)
    execute.add_argument("--store-root", type=Path, required=True)
    execute.add_argument("--jsonl", action="store_true")


def run(args: argparse.Namespace) -> int:
    if args.firmware_command == "execute":
        from .operations import execute

        def progress(**fields: object) -> None:
            stage = str(fields.pop("stage", "working"))
            if "processed_bytes" in fields:
                fields["stage_completed"] = fields.pop("processed_bytes")
                fields["stage_total"] = fields.pop("total_bytes", None)
            if args.jsonl:
                print(
                    json.dumps(
                        {
                            "type": "progress",
                            "stage": stage,
                            "message": stage,
                            "data": fields,
                        },
                        ensure_ascii=True,
                    ),
                    flush=True,
                )
            else:
                print(stage, file=sys.stderr, flush=True)

        try:
            if len(args.request_json) > 64 * 1024:
                raise FirmwareError("Request exceeds limit")
            request = json.loads(args.request_json)
            if not isinstance(request, dict):
                raise FirmwareError("Request must be an object")
            data = execute(
                args.action,
                request,
                provider=args.provider,
                cache_root=args.cache_root,
                store_root=args.store_root,
                progress=progress,
            )
        except Exception as exc:
            # Network/library exceptions may contain credentials; only our fixed validation messages are public.
            message = (
                str(exc)
                if isinstance(exc, FirmwareError)
                else "Firmware operation failed; check the selected source, permissions or device and retry"
            )
            print(
                json.dumps(
                    {
                        "type": "error",
                        "ok": False,
                        "code": "firmware_operation_failed",
                        "message": message,
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )
            return 2
        print(
            json.dumps({"type": "result", "ok": True, "data": data}, ensure_ascii=True),
            flush=True,
        )
        return 0
    if args.firmware_command == "capabilities":
        data = {
            "capabilities": [
                "firmware_package_check",
                "firmware_requirement_contract",
                "firmware_operations_v1",
            ]
        }
    else:
        try:
            data = asdict(inspect_package(args.package))
        except FirmwareError:
            message = "Firmware package is missing, corrupt, or does not satisfy the supported format"
            if args.jsonl:
                print(
                    json.dumps(
                        {
                            "type": "error",
                            "ok": False,
                            "code": "firmware_package_invalid",
                            "message": message,
                        }
                    )
                )
            else:
                print(message)
            return 2
    if args.jsonl:
        print(
            json.dumps({"type": "result", "ok": True, "data": data}, ensure_ascii=True)
        )
    else:
        print(json.dumps(data, ensure_ascii=True, indent=2))
    return 0
