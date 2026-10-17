"""SDK management use cases; UI clients supply declarations, never policy."""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any

from packaging.version import Version

from watcherobot import __version__
from watcherobot.runtime.daemon.instance import default_runtime_instance_root
from watcherobot.runtime.identity import runtime_identity

from .compatibility import check_desktop, runtime_contracts
from .installer import environment_python
from .state import ManagerStore


class Manager:
    def __init__(
        self,
        request: dict[str, Any],
        *,
        root: Path | None = None,
        live: Callable[[], dict[str, Any] | None] | None = None,
        emit: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.request = request
        self.desktop = request["desktop"]
        check_desktop(self.desktop, runtime_contracts())
        self.store = ManagerStore(
            root or default_runtime_instance_root() / "sdk-management", emit
        )
        self.live_reader = live
        self.platform = request.get("platform") or f"{sys.platform}-" + {
            "arm64": "arm64",
            "aarch64": "arm64",
            "x86_64": "x64",
            "AMD64": "x64",
        }.get(platform.machine(), platform.machine())
        self.public_keys = json.loads(
            files("watcherobot.management")
            .joinpath("release-keys.json")
            .read_text(encoding="utf-8")
        )

    def baseline(self) -> dict[str, Any]:
        bootstrap = self.request.get("_bootstrap")
        if bootstrap:
            baseline = bootstrap["baseline"]
            if (
                baseline.get("kind") != "bundled"
                or check_desktop(self.desktop, baseline["contracts"]).status
                != "compatible"
            ):
                raise ValueError("Invalid bundled rescue descriptor")
            Version(baseline["version"])
            return dict(baseline)
        identity = runtime_identity()
        return {
            "kind": "bundled",
            "version": __version__,
            "build_id": identity["build_id"],
            "contracts": runtime_contracts(),
        }

    def selection(self) -> dict[str, Any]:
        item = self.store.snapshot().get("selection") or self.baseline()
        return self._validate_target(item)

    def _validate_target(self, item: dict[str, Any]) -> dict[str, Any]:
        if item["kind"] == "environment":
            import re

            from watcherobot.runtime.cleanup import _is_link

            if self.request.get("_selected_manager_error"):
                raise ValueError(self.request["_selected_manager_error"])
            root = Path(item["root"])
            if (
                root.parent != self.store.root / "environments"
                or not re.fullmatch("[0-9a-f]{64}", root.name)
                or _is_link(root)
                or _is_link(root.parent)
            ):
                raise ValueError("SDK environment escaped the managed repository")
            marker_path = root / "sdk-environment.json"
            if _is_link(marker_path) or marker_path.stat().st_size > 256 * 1024:
                raise ValueError("Invalid SDK environment descriptor")
            marker = json.loads(marker_path.read_bytes())
            if (
                marker.get("version") != item["version"]
                or marker.get("build_id") != item["build_id"]
                or marker.get("contracts") != item["contracts"]
                or not environment_python(root).is_file()
                or not (root / "pyvenv.cfg").is_file()
            ):
                raise ValueError("SDK environment is incomplete; repair is required")
        return item

    def command(
        self, target: dict[str, Any] | None = None, *, entry: str = "daemon"
    ) -> list[str]:
        target = (
            self._validate_target(target) if target is not None else self.selection()
        )
        module = (
            "watcherobot.runtime.daemon"
            if entry == "daemon"
            else "watcherobot.distribution.cli"
        )
        if target["kind"] == "environment":
            python = str(environment_python(Path(target["root"])))
            if entry == "distribution":
                return [
                    python,
                    "-I",
                    "-c",
                    "from watcherobot.distribution.cli import main; raise SystemExit(main())",
                ]
            return [python, "-I", "-m", module]
        bootstrap = self.request.get("_bootstrap")
        if bootstrap:
            command = bootstrap["commands"][entry]
            if (
                not isinstance(command, list)
                or not command
                or any(not isinstance(x, str) or not x or "\0" in x for x in command)
            ):
                raise ValueError("Invalid bundled rescue command")
            return list(command)
        if entry == "distribution":
            resource = self.request.get("distribution_executable")
            if resource:
                return [str(resource)]
        if getattr(sys, "frozen", False):
            return [sys.executable]
        if entry == "distribution":
            return [
                sys.executable,
                "-c",
                "from watcherobot.distribution.cli import main; raise SystemExit(main())",
            ]
        return [sys.executable, "-m", module]

    def daemon_args(self) -> list[str]:
        args = self.request.get("daemon_args", [])
        if not isinstance(args, list) or any(not isinstance(x, str) for x in args):
            raise ValueError("Invalid Daemon launch arguments")
        return [
            x
            for x in args
            if x not in {"--ensure-shared", "--activate-shared", "--sdk-manager"}
        ]

    def live(self) -> dict[str, Any] | None:
        if self.live_reader is not None:
            return self.live_reader()
        from watcherobot.cli import _live_runtime_state, _request_json

        live = _live_runtime_state()
        return _request_json(live.control_url, "/daemon/status") if live else None

    def business_state(self, live: dict[str, Any] | None) -> dict[str, Any]:
        from .compatibility import check_desktop_application

        requirements = self.request.get("desktop_business", {})
        if not isinstance(requirements, dict):
            raise TypeError("Desktop business requirements must be an object")
        application = live.get("application", {}) if live else {}
        running = application.get("state") in {"running", "starting", "stopping"}
        trusted = bool(
            live
            and check_desktop(
                self.desktop, live.get("runtime", {}).get("contracts", {})
            ).status
            == "compatible"
        )
        actual = None
        if trusted:
            actual = (
                application.get("desktop_contracts")
                if running
                else {"device_control": 1}
            )
        if not isinstance(actual, dict):
            actual = None
        reports = check_desktop_application(requirements, actual)
        return {
            "mode": "application" if running else "transparent",
            "application_id": application.get("current_app") if running else None,
            "selection_id": application.get("selection_id"),
            "groups": {
                group: {
                    **report.to_dict(),
                    "enabled": report.status == "compatible",
                    "reason": None
                    if report.status == "compatible"
                    else (
                        "daemon_contract_unverified"
                        if not trusted
                        else report.issues[0].code
                    ),
                }
                for group, report in reports.items()
            },
        }

    def status(self) -> dict[str, Any]:
        state = self.store.snapshot()
        try:
            selected = {**self.selection(), "health": "ready"}
        except (OSError, ValueError) as exc:
            selected = {
                **(state.get("selection") or self.baseline()),
                "health": "broken",
                "error": str(exc),
            }
        compatibility = check_desktop(self.desktop, selected["contracts"])
        daemon_error: str | None = None
        try:
            live = self.live()
        except Exception as exc:  # noqa: BLE001 -- status must survive an unavailable Daemon
            live = None
            daemon_error = str(exc)
        runtime = live.get("runtime", {}) if live else {}
        daemon_compatibility = (
            check_desktop(self.desktop, runtime.get("contracts", {})) if live else None
        )
        operation = state.get("operation")
        available = state.get("available") or []
        recommendation = next(
            (
                r
                for r in available
                if Version(r["version"]) > Version(selected["version"])
            ),
            None,
        )
        active = self.store.operation_running()
        interrupted = self.store.needs_recovery() and not active
        busy = bool(
            live
            and live.get("application", {}).get("state")
            in {"running", "starting", "stopping"}
        )
        supported = (
            compatibility.status == "compatible" and selected["health"] == "ready"
        )
        actions: dict[str, dict[str, Any]] = {
            name: {"enabled": True, "reason": None}
            for name in (
                "status",
                "check_update",
                "update",
                "change_version",
                "cancel",
                "repair",
                "rollback",
                "restore_default_runtime",
                "cleanup",
                "recover",
            )
        }
        for name in actions:
            if name == "status":
                continue
            allowed = supported and not active and not interrupted
            reason = (
                "operation_active"
                if active
                else ("recovery_required" if interrupted else "desktop_incompatible")
            )
            if (
                name in {"check_update", "update", "change_version"}
                and not self.public_keys
            ):
                allowed, reason = False, "release_signing_unconfigured"
            if name in {"update", "change_version"} and not state.get("available"):
                allowed, reason = False, "check_update_first"
            if name == "update" and recommendation is None:
                allowed, reason = False, "no_newer_compatible_release"
            if name == "rollback":
                if not state.get("previous"):
                    allowed, reason = False, "rollback_unavailable"
                else:
                    try:
                        self._validate_target(state["previous"])
                    except (OSError, ValueError):
                        allowed, reason = False, "rollback_environment_broken"
            if name in {"rollback", "restore_default_runtime"} and busy:
                allowed, reason = False, "application_running"
            if (
                name == "restore_default_runtime"
                and runtime.get("build_id") == selected["build_id"]
            ):
                allowed, reason = False, "already_selected"
            if name == "cancel":
                allowed = bool(
                    active
                    and operation
                    and operation["phase"]
                    not in {
                        "switching",
                        "health_check",
                        "recovering",
                        "cleaning",
                    }
                )
                reason = "not_cancellable"
            if name == "recover":
                allowed, reason = interrupted, "recovery_not_needed"
            if name == "repair":
                allowed = (
                    not active
                    and not busy
                    and check_desktop(self.desktop, self.baseline()["contracts"]).status
                    == "compatible"
                )
                reason = "application_running" if busy else "operation_active"
            actions[name] = {"enabled": allowed, "reason": None if allowed else reason}
        return {
            "contract_version": 1,
            "revision": state["revision"],
            "selection": selected,
            "daemon": {
                "state": "running"
                if live
                else ("unknown" if daemon_error else "stopped"),
                "version": runtime.get("sdk_version"),
                "build_id": runtime.get("build_id"),
                "compatibility": daemon_compatibility.to_dict()
                if daemon_compatibility
                else None,
                "error": daemon_error,
            },
            "operation": operation,
            "business": self.business_state(live),
            "compatibility": compatibility.to_dict(),
            "available": state.get("available"),
            "recommendation": recommendation,
            "latest_unavailable": state.get("latest_unavailable"),
            "candidate": state.get("candidate"),
            "cleanup": state.get("cleanup"),
            "download_cleanup": state.get("download_cleanup"),
            "cache_cleanup": state.get("cache_cleanup"),
            "actions": actions,
            "diagnostics": {
                "contract_version": 1,
                "revision": state["revision"],
                "selection": {
                    key: selected.get(key)
                    for key in ("version", "build_id", "health", "contracts")
                },
                "daemon": {
                    "state": "running"
                    if live
                    else ("unknown" if daemon_error else "stopped"),
                    "version": runtime.get("sdk_version"),
                    "build_id": runtime.get("build_id"),
                    "contracts": runtime.get("contracts"),
                },
                "operation": {
                    key: operation.get(key) for key in ("id", "action", "phase")
                }
                if operation
                else None,
            },
        }

    def begin_activity(self, kind: str, owner_pid: int) -> dict[str, str]:
        import time

        from .activity import ActivityBusy, ActivityRegistry

        if kind == "desktop_ota":
            live = self.live()
            if live and live.get("application", {}).get("state") in {
                "running",
                "starting",
                "stopping",
            }:
                raise ActivityBusy(
                    "Stop the running application before updating Desktop"
                )
            deadline = time.monotonic() + 120
            while self.store.operation_running():
                task = self.store.snapshot().get("operation")
                if not task:
                    raise ActivityBusy("SDK management is busy")
                self.store.request_cancel(task["id"])
                if time.monotonic() >= deadline:
                    raise ActivityBusy(
                        "SDK operation did not finish; retry Desktop update later"
                    )
                time.sleep(0.1)
        return {"token": ActivityRegistry().begin(kind, owner_pid)}

    def control(
        self, method: str, path: str, body: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Validate the actual Daemon before forwarding management requests."""
        from urllib.parse import unquote, urlsplit

        import httpx

        from watcherobot.cli import _live_runtime_state
        from watcherobot.runtime.registration import register_launch
        from watcherobot.runtime.repository import operation_lock

        parsed = urlsplit(path)
        if (
            method not in {"GET", "POST"}
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or any(ord(char) < 32 for char in path)
            or "\\" in unquote(parsed.path)
            or any(
                segment in {".", ".."} for segment in unquote(parsed.path).split("/")
            )
            or not parsed.path.startswith("/daemon/")
        ):
            raise ValueError("Only local Daemon management requests are accepted")
        with operation_lock(timeout=120):
            live = self.live()
            if not live:
                raise RuntimeError("Daemon is unavailable")
            report = check_desktop(
                self.desktop, live.get("runtime", {}).get("contracts", {})
            )
            if path != "/daemon/status" and report.status != "compatible":
                return {
                    "status": 426,
                    "payload": {
                        "error": "daemon_contract_" + report.status,
                        "compatibility": report.to_dict(),
                    },
                }
            state = _live_runtime_state()
            if state is None:
                raise RuntimeError("Daemon changed before the management request")
            if parsed.path == "/daemon/application/select" and body is not None:
                from watcherobot.runtime.daemon.application.manifest import (
                    ApplicationManifest,
                )

                grant = dict(body)
                metadata = ApplicationManifest.load(
                    Path(body["application_dir"]), daemon=True
                )
                if metadata.app_id == "watcher_default":
                    grant["environment"] = dict(
                        self.request.get("source_launch_environment", [])
                    )
                body = register_launch(grant)
                body.pop("environment", None)
            timeout = (
                120
                if parsed.path
                in {
                    "/daemon/application/start",
                    "/daemon/application/stop",
                    "/daemon/application/restart",
                    "/daemon/application/select",
                }
                else 30
            )
            with (
                httpx.Client(trust_env=False, timeout=timeout) as client,
                client.stream(
                    method, state.control_url.rstrip("/") + path, json=body
                ) as response,
            ):
                content = bytearray()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if len(content) + len(chunk) > 4 * 1024 * 1024:
                        raise ValueError("Daemon management response exceeds limit")
                    content.extend(chunk)
                payload = json.loads(content) if content else None
            if isinstance(payload, dict) and isinstance(
                payload.get("application"), dict
            ):
                basis = (
                    payload
                    if isinstance(payload.get("runtime"), dict)
                    else {**live, "application": payload["application"]}
                )
                payload["business"] = self.business_state(basis)
            return {"status": response.status_code, "payload": payload}

    def _require_supported(self) -> None:
        if (
            check_desktop(self.desktop, self.selection()["contracts"]).status
            != "compatible"
        ):
            raise ValueError("Desktop does not support the selected SDK contracts")

    def check_update(self) -> dict[str, Any]:
        from .mirror import Mirror
        from .releases import compatible_releases
        from .state import atomic_json

        self._require_supported()
        with self.store.operation("check_update"):
            with Mirror() as mirror:
                catalog, blob = mirror.catalog(self.public_keys)
            state = self.store.snapshot()
            accepted = state.get("catalog_sequence", 0)
            if catalog.sequence < accepted or (
                catalog.sequence == accepted
                and state.get("catalog_digest") not in (None, catalog.digest)
            ):
                raise ValueError("SDK catalog replay or sequence replacement rejected")
            available = compatible_releases(
                catalog, self.desktop, self.platform, self._python_version()
            )
            from packaging.specifiers import SpecifierSet

            stable = [
                r
                for r in catalog.releases
                if not Version(r.version).is_prerelease
                and not Version(r.version).is_devrelease
            ]
            newest = max(stable, key=lambda r: Version(r.version)) if stable else None
            unavailable: dict[str, Any] | None = None
            if newest and Version(newest.version) > Version(
                self.selection()["version"]
            ):
                current_platform = next(
                    (
                        r
                        for r in stable
                        if Version(r.version) == Version(newest.version)
                        and r.platform == self.platform
                    ),
                    None,
                )
                if current_platform is None:
                    unavailable = {
                        "version": newest.version,
                        "reason": "platform_release_unavailable",
                    }
                elif not SpecifierSet(current_platform.python).contains(
                    self._python_version()
                ):
                    unavailable = {
                        "version": newest.version,
                        "reason": "python_runtime_unsupported",
                        "required": current_platform.python,
                    }
                elif (
                    check_desktop(self.desktop, current_platform.contracts).status
                    != "compatible"
                ):
                    unavailable = {
                        "version": newest.version,
                        "reason": "desktop_contract_incompatible",
                        "compatibility": check_desktop(
                            self.desktop, current_platform.contracts
                        ).to_dict(),
                    }
            state["latest_unavailable"] = unavailable
            atomic_json(self.store.root / "catalog.json", json.loads(blob))
            state["catalog_sequence"] = catalog.sequence
            state["catalog_digest"] = catalog.digest
            state["available"] = [
                {"version": r.version, "identity": r.identity, "notes": r.notes}
                for r in available
            ]
            self.store.save(state)
        return self.status()

    def _python_version(self) -> str:
        resource = self.request.get("resources")
        if resource:
            manifest = json.loads((Path(resource) / "runtime.json").read_bytes())
            return str(manifest["python"]["version"])
        return platform.python_version()

    def _runtime_resources(self) -> Any:
        from watcherobot.distribution.install import _prepare_runtime, _StorePaths
        from watcherobot.runtime.cleanup import register_reference

        path = self.request.get("resources")
        if not path:
            raise ValueError(
                "Bundled Python/uv resources are required for SDK installation"
            )
        root = self.store.root / "base-store"
        root.mkdir(exist_ok=True)
        register_reference(root, store=True)
        return _prepare_runtime(_StorePaths(root), Path(path))

    def _locked_release(self, identity: str | None = None) -> Any:
        from .releases import compatible_releases, verify_catalog

        blob = (self.store.root / "catalog.json").read_bytes()
        catalog = verify_catalog(blob, self.public_keys)
        state = self.store.snapshot()
        if catalog.sequence != state.get(
            "catalog_sequence"
        ) or catalog.digest != state.get("catalog_digest"):
            raise ValueError("SDK catalog differs from the accepted release")
        releases = compatible_releases(
            catalog, self.desktop, self.platform, self._python_version()
        )
        if identity:
            releases = tuple(r for r in releases if r.identity == identity)
        if not releases:
            raise ValueError("No compatible stable SDK release is available")
        return releases[0]

    def _application_impacts(self, release: Any) -> list[dict[str, str]]:
        from watcherobot.management.compatibility import check_application_daemon
        from watcherobot.runtime.daemon.application.manifest import (
            parse_application_manifest,
        )

        roots = self.request.get("application_stores", [])
        impacts: list[dict[str, str]] = []
        for root in roots:
            applications = Path(root) / "apps"
            if not applications.is_dir():
                continue
            for directory in applications.iterdir():
                if directory.is_symlink() or not directory.is_dir():
                    continue
                try:
                    path = directory / "source" / "app.json"
                    if path.stat().st_size > 256 * 1024:
                        raise ValueError("Manifest exceeds limit")
                    metadata = parse_application_manifest(path.read_bytes())
                    if metadata.schema_version == 3:
                        report = check_application_daemon(
                            metadata.application_protocol,
                            release.contracts["application"],
                        )
                        if report.status != "compatible":
                            impacts.append(
                                {
                                    "id": metadata.app_id,
                                    "reason": "application_protocol_incompatible",
                                }
                            )
                    elif not metadata.supports_watcherobot(release.version):
                        impacts.append(
                            {"id": metadata.app_id, "reason": "legacy_sdk_requirement"}
                        )
                except (OSError, ValueError):
                    impacts.append(
                        {"id": directory.name, "reason": "application_unverified"}
                    )
        return impacts

    def _confirmation(self, release: Any, impacts: list[dict[str, str]]) -> str:
        state = self.store.snapshot()
        return hashlib.sha256(
            json.dumps(
                {
                    "identity": release.identity,
                    "selection": state["selection"] or self.baseline(),
                    "impacts": impacts,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    def update(
        self, identity: str | None = None, confirmation: str | None = None
    ) -> dict[str, Any]:
        import shutil
        import time
        import uuid

        from .deployment import activate
        from .installer import install_environment
        from .mirror import Mirror

        self._require_supported()
        with self.store.operation("update") as task:
            release = self._locked_release(identity)
            if identity is None and Version(release.version) <= Version(
                self.selection()["version"]
            ):
                raise ValueError("No newer compatible stable SDK is available")
            impacts = self._application_impacts(release)
            token = self._confirmation(release, impacts)
            if impacts and confirmation != token:
                return {
                    "confirmation": {
                        "token": token,
                        "release": release.identity,
                        "impacts": impacts,
                    }
                }
            if release.contracts["state"] != self.selection()["contracts"]["state"]:
                raise ValueError("SDK management data is not safely reversible")
            from .cleanup import clean_environments, retire_previous

            # Retire the previous rollback at the next explicit update, then
            # remove unreferenced roots before allocating another candidate.
            retire_previous(self.store, now=time.time(), before_update=True)
            preflight_cleanup = clean_environments(self.store)
            state = self.store.snapshot()
            state["cleanup"] = preflight_cleanup
            self.store.save(state)
            needed = sum(w.size for w in release.artifacts) * 3 + 100 * 1024 * 1024
            if shutil.disk_usage(self.store.root).free < needed:
                import errno

                raise OSError(
                    errno.ENOSPC,
                    "Not enough free space to prepare SDK without replacing the current version",
                )
            runtime = self._runtime_resources()
            state = self.store.snapshot()
            if state.get("candidate"):
                raise ValueError(
                    "An existing candidate must be recovered or cancelled first"
                )
            self.store.transition(task, "downloading")
            try:
                with Mirror() as mirror:
                    wheels = []
                    total = sum(w.size for w in release.artifacts)
                    completed = 0
                    last_progress = 0.0

                    def progress(size: int) -> None:
                        nonlocal last_progress
                        now = time.monotonic()
                        if now - last_progress >= 0.25:
                            self.store.transition(
                                task,
                                "downloading",
                                downloaded_bytes=completed + size,
                                total_bytes=total,
                            )
                            last_progress = now

                    for wheel in release.artifacts:
                        wheels.append(
                            mirror.download(
                                wheel,
                                self.store.root / "downloads",
                                lambda: self.store.checkpoint(task),
                                progress=progress,
                            )
                        )
                        completed += wheel.size
                        self.store.transition(
                            task,
                            "downloading",
                            downloaded_bytes=completed,
                            total_bytes=total,
                        )
                    self.store.checkpoint(task)
            except BaseException:
                self._clean_downloads()
                raise
            root = (
                self.store.root
                / "environments"
                / hashlib.sha256(
                    (release.identity + uuid.uuid4().hex).encode()
                ).hexdigest()
            )
            state = self.store.snapshot()
            state["candidate"] = {
                "kind": "environment",
                "root": str(root),
                "version": release.version,
                "release_identity": release.identity,
            }
            self.store.save(state)
            try:
                self.store.transition(task, "installing")
                identity_result = install_environment(
                    root,
                    runtime,
                    release,
                    wheels,
                    self.store.root / "cache",
                    checkpoint=lambda: self.store.checkpoint(task),
                )
                target = {
                    **self.store.snapshot()["candidate"],
                    "build_id": identity_result["build_id"],
                    "contracts": release.contracts,
                }
                state = self.store.snapshot()
                state["candidate"] = target
                self.store.save(state)
                self.store.transition(task, "verifying")
                # Re-evaluate impacts immediately before switching, not just when
                # a potentially long download began.
                now_impacts = self._application_impacts(release)
                if now_impacts != impacts:
                    raise ValueError(
                        "Installed applications changed; confirm SDK impacts again"
                    )
                while True:
                    self.store.checkpoint(task)
                    live = self.live()
                    if not live or live.get("application", {}).get("state") not in {
                        "running",
                        "starting",
                        "stopping",
                    }:
                        break
                    self.store.transition(task, "waiting", reason="application_running")
                    time.sleep(0.5)
                from .activity import ActivityBusy

                while True:
                    self.store.checkpoint(task)
                    self.store.transition(task, "switching")
                    try:
                        activate(self.store, target, self.baseline(), _Runtime(self))
                        break
                    except ActivityBusy:
                        self.store.transition(
                            task, "waiting", reason="maintenance_or_desktop_update"
                        )
                        time.sleep(0.5)
            except BaseException:
                state = self.store.snapshot()
                if not state.get("transaction"):
                    state["candidate"] = None
                    self.store.save(state)
                    if root.exists() and not root.is_symlink():
                        shutil.rmtree(root)
                raise
            finally:
                # Install uses fully local wheels; they are no longer needed.
                self._clean_downloads()
            self.store.transition(task, "cleaning")
            from .cleanup import clean_cache, clean_environments

            cleanup = clean_environments(self.store)
            cache_cleanup = clean_cache(self.store, runtime.uv_executable)
            state = self.store.snapshot()
            state["cleanup"] = cleanup
            state["cache_cleanup"] = cache_cleanup
            self.store.save(state)
        return self.status()

    def cleanup(self) -> dict[str, Any]:
        import time

        from .cleanup import retire_previous

        with self.store.operation("cleanup") as task:
            self.store.transition(task, "cleaning")
            retire_previous(self.store, now=time.time())
            self._clean_idle_resources()
        return self.status()

    def _clean_idle_resources(self) -> None:
        """Caller owns the SDK task lock and has completed any recovery transaction."""
        from .cleanup import clean_cache, clean_environments

        self._clean_downloads()
        report = clean_environments(self.store)
        cache_report: dict[str, object] = {"deferred": False, "errors": []}
        if (self.store.root / "cache").exists():
            try:
                runtime = self._runtime_resources()
                cache_report = clean_cache(self.store, runtime.uv_executable)
            except Exception as exc:  # noqa: BLE001 -- unavailable cache tools must not conceal environment cleanup
                cache_report = {"deferred": True, "errors": [type(exc).__name__]}
        state = self.store.snapshot()
        state["cleanup"] = report
        state["cache_cleanup"] = cache_report
        self.store.save(state)

    def _clean_downloads(self) -> None:
        from .cleanup import clean_downloads

        report = clean_downloads(self.store)
        state = self.store.snapshot()
        state["download_cleanup"] = report
        self.store.save(state)

    def recover(self) -> dict[str, Any]:
        from .deployment import recover

        with self.store.operation("recover", recovery=True) as task:
            self.store.transition(task, "recovering")
            outcome = recover(self.store, _Runtime(self))
            state = self.store.snapshot()
            if not state.get("transaction"):
                state["candidate"] = None
            state["recovery"] = outcome
            self.store.save(state)
            self._clean_idle_resources()
        return self.status()

    def rollback(self) -> dict[str, Any]:
        from .deployment import activate

        self._require_supported()
        with self.store.operation("rollback") as task:
            previous = self.store.snapshot().get("previous")
            if not previous:
                raise ValueError("No SDK rollback snapshot is available")
            self.store.transition(task, "switching")
            activate(self.store, previous, self.baseline(), _Runtime(self))
        return self.status()

    def repair(self) -> dict[str, Any]:
        """Restore the installer-provided SDK without relying on a broken venv."""
        from .deployment import activate

        if check_desktop(self.desktop, runtime_contracts()).status != "compatible":
            raise ValueError("Bundled SDK does not support this Desktop")
        with self.store.operation("repair", recovery=True) as task:
            self.store.transition(task, "switching")
            activate(self.store, self.baseline(), self.baseline(), _Runtime(self))
        return self.status()

    def restore_default(self) -> dict[str, Any]:
        from watcherobot.runtime.repository import operation_lock

        self._require_supported()
        with self.store.operation("restore_default_runtime") as task:
            self.store.transition(task, "switching")
            from .activity import ActivityRegistry

            with ActivityRegistry().switch(), operation_lock(timeout=120):
                live = self.live()
                if live and live.get("application", {}).get("state") in {
                    "running",
                    "starting",
                    "stopping",
                }:
                    raise ValueError(
                        "Stop the running application before restoring the default SDK"
                    )
                _Runtime(self).start(self.selection())
        return self.status()


class _Runtime:
    def __init__(self, manager: Manager):
        self.manager = manager

    def status(self) -> dict[str, Any] | None:
        return self.manager.live()

    def start(self, target: dict[str, Any]) -> None:
        from watcherobot.runtime.manager import _ensure_command_locked

        _ensure_command_locked(
            self.manager.command(target) + self.manager.daemon_args(),
            activate=True,
            force=True,
            expected_identity={
                "sdk_version": target["version"],
                "build_id": target["build_id"],
            },
        )
