"""Process entrypoint for the single per-user SDK Runtime."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
import time
from pathlib import Path

from watcherobot.runtime.daemon.instance import (
    RuntimeAlreadyRunningError,
    RuntimeInstanceLock,
    RuntimeProcessState,
    RuntimeStateStore,
    default_runtime_state_root,
)
from watcherobot.runtime.daemon.runtime import DaemonRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="watcherobot-runtime")
    parser.add_argument(
        "--state-root",
        type=Path,
        default=default_runtime_state_root(),
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=int(os.environ.get("WATCHER_RUNTIME_CONTROL_PORT", "8767")),
    )
    parser.add_argument(
        "--external-port",
        type=int,
        default=int(os.environ.get("WATCHER_RUNTIME_EXTERNAL_PORT", "8765")),
    )
    parser.add_argument(
        "--pairing-port",
        type=int,
        default=int(os.environ.get("WATCHER_RUNTIME_PAIRING_PORT", "37021")),
    )
    return parser


async def run_runtime(args: argparse.Namespace) -> int:
    state_root = Path(args.state_root).resolve()
    instance_lock = RuntimeInstanceLock(state_root / "runtime.lock")
    state_store = RuntimeStateStore(state_root)
    try:
        instance_lock.acquire()
    except RuntimeAlreadyRunningError:
        return 3

    state_store.remove()
    runtime = DaemonRuntime(
        application_dir=state_root / "unselected",
        current_app="unselected",
        external_port=args.external_port,
        control_port=args.control_port,
        pairing_udp_port=args.pairing_port,
        application_log_dir=state_root / "logs" / "applications",
        daemon_log_path=state_root / "logs" / "daemon.jsonl",
        catalog_root=state_root / "catalog",
    )
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        loop.call_soon_threadsafe(runtime.request_shutdown)

    for signal_number in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(
                signal_number,
                lambda _signum, _frame: request_shutdown(),
            )
        except (OSError, ValueError):
            pass

    try:
        await runtime.start()
        state_store.write(
            RuntimeProcessState(
                pid=os.getpid(),
                control_url=runtime.control_server.base_url,
                external_url=runtime.external_server.url,
                started_at=time.time(),
            )
        )
        await runtime.wait_for_shutdown()
        return 0
    finally:
        state_store.remove()
        await runtime.stop()
        instance_lock.release()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return asyncio.run(run_runtime(args))


if __name__ == "__main__":
    sys.exit(main())
