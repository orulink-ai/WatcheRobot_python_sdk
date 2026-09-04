"""Process entrypoint for the single per-user SDK Runtime."""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

from watcherobot.runtime.daemon.control.rest import RuntimeInstanceGroup
from watcherobot.runtime.daemon.instance import (
    RuntimeAlreadyRunningError,
    RuntimeInstanceLock,
    RuntimeProcessState,
    RuntimeStateStore,
    default_runtime_instance_root,
    default_runtime_state_root,
    runtime_instance_id,
    system_runtime_instance_root,
    system_runtime_state_root,
)
from watcherobot.runtime.daemon.pairing.bindings_store import DeviceBindingsStore
from watcherobot.runtime.daemon.runtime import DaemonRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="watcherobot-runtime")
    parser.add_argument(
        "--state-root",
        type=Path,
        default=default_runtime_state_root(),
    )
    parser.add_argument(
        "--instance-root",
        type=Path,
        default=default_runtime_instance_root(),
        help=(
            "per-user coordination directory shared by all Runtime launchers; "
            "changing it bypasses the default single-instance group"
        ),
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
    parser.add_argument(
        "--preview-udp-port",
        type=int,
        default=int(os.environ.get("WATCHER_RUNTIME_PREVIEW_UDP_PORT", "37022")),
    )
    parser.add_argument("--managed-app-root", type=Path)
    parser.add_argument("--bundled-resource-root", type=Path)
    parser.add_argument("--source-default-application-root", type=Path)
    parser.add_argument("--source-default-launcher", type=Path)
    return parser


def _validate_source_default_options(args: argparse.Namespace) -> None:
    has_application_root = args.source_default_application_root is not None
    has_launcher = args.source_default_launcher is not None
    if has_application_root != has_launcher:
        raise ValueError(
            "--source-default-application-root and "
            "--source-default-launcher must be provided together"
        )


async def run_runtime(args: argparse.Namespace) -> int:
    # Keep programmatic callers subject to the same invariant as the CLI.
    _validate_source_default_options(args)
    state_root = Path(args.state_root).resolve()
    instance_root = Path(args.instance_root).resolve()
    system_instance_root = system_runtime_instance_root().resolve()
    if instance_root == system_instance_root:
        # The shared coordination lock must remain first for every default-group
        # launcher.  Acquiring it before compatibility locks prevents cycles
        # between launchers that use different private state roots.
        lock_roots = tuple(
            dict.fromkeys(
                (instance_root, system_runtime_state_root().resolve(), state_root)
            )
        )
    else:
        lock_roots = (instance_root,)
    instance_group: RuntimeInstanceGroup = (
        "default" if instance_root == system_instance_root else "isolated"
    )
    instance_id = runtime_instance_id(instance_root)
    state_roots = (
        tuple(dict.fromkeys((*lock_roots, state_root)))
        if instance_group == "default"
        else (instance_root,)
    )
    instance_locks = [
        RuntimeInstanceLock(root / "runtime.lock") for root in lock_roots
    ]
    state_stores = [RuntimeStateStore(root) for root in state_roots]
    acquired_locks: list[RuntimeInstanceLock] = []

    def release_acquired_locks() -> list[BaseException]:
        errors: list[BaseException] = []
        for acquired_lock in reversed(acquired_locks):
            try:
                acquired_lock.release()
            except BaseException as exc:
                errors.append(exc)
        return errors

    try:
        for instance_lock in instance_locks:
            instance_lock.acquire()
            acquired_locks.append(instance_lock)
    except RuntimeAlreadyRunningError:
        release_errors = release_acquired_locks()
        if release_errors:
            raise release_errors[0]
        return 3
    except BaseException:
        release_acquired_locks()
        raise

    runtime: DaemonRuntime | None = None
    published_state: RuntimeProcessState | None = None
    published_stores: list[RuntimeStateStore] = []
    try:
        runtime = DaemonRuntime(
            application_dir=state_root / "unselected",
            current_app=None,
            external_port=args.external_port,
            control_port=args.control_port,
            pairing_udp_port=args.pairing_port,
            preview_udp_port=args.preview_udp_port,
            application_log_dir=state_root / "logs" / "applications",
            daemon_log_path=state_root / "logs" / "daemon.jsonl",
            device_bindings_store=DeviceBindingsStore(state_root),
            managed_app_root=(
                Path(args.managed_app_root).resolve()
                if args.managed_app_root is not None
                else state_root / "application-store"
            ),
            bundled_resource_root=(
                Path(args.bundled_resource_root).resolve()
                if args.bundled_resource_root is not None
                else state_root / "bundled-resources"
            ),
            source_default_application_root=(
                Path(args.source_default_application_root).resolve()
                if args.source_default_application_root is not None
                else None
            ),
            source_default_launcher_executable=(
                Path(os.path.abspath(args.source_default_launcher))
                if args.source_default_launcher is not None
                else None
            ),
            instance_group=instance_group,
            instance_id=instance_id,
        )
        loop = asyncio.get_running_loop()

        def request_shutdown() -> None:
            if runtime is not None:
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
        except Exception as exc:
            runtime.logs.record(
                f"Daemon Runtime startup failed ({type(exc).__name__}: {exc})"
            )
            raise
        runtime_metadata = runtime.runtime_metadata()
        state = RuntimeProcessState(
            pid=int(runtime_metadata["pid"]),
            control_url=runtime.control_server.base_url,
            external_url=str(runtime_metadata["external_url"]),
            started_at=float(runtime_metadata["started_at"]),
        )
        published_state = state
        for state_store in state_stores:
            state_store.write(state)
            published_stores.append(state_store)
        await runtime.wait_for_shutdown()
        return 0
    finally:
        original_error = sys.exc_info()[1]
        cleanup_errors: list[BaseException] = []
        if runtime is not None:
            try:
                await runtime.stop()
            except BaseException as exc:
                cleanup_errors.append(exc)
        if published_state is not None:
            for state_store in published_stores:
                try:
                    state_store.remove_if_matches(published_state)
                except BaseException as exc:
                    cleanup_errors.append(exc)
        cleanup_errors.extend(release_acquired_locks())
        if original_error is None and cleanup_errors:
            raise cleanup_errors[0]


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        # Convert the invariant failure into an actionable argparse message.
        _validate_source_default_options(args)
    except ValueError as exc:
        parser.error(str(exc))
    return asyncio.run(run_runtime(args))


if __name__ == "__main__":
    sys.exit(main())
