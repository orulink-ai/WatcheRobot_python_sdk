"""Opt-in installation against real, predownloaded SDK release artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.utils import parse_sdist_filename, parse_wheel_filename

from watcherobot.management.compatibility import runtime_contracts
from watcherobot.management.installer import environment_python, install_environment
from watcherobot.management.releases import Artifact, Release


def _prepare_candidate(tmp_path, monkeypatch):
    wheelhouse = Path(os.environ["WATCHER_SDK_OTA_TEST_WHEELHOUSE"])
    resources = Path(os.environ["WATCHER_SDK_OTA_TEST_RUNTIME"])
    manifest = json.loads((resources / "runtime.json").read_bytes())
    artifacts = []
    for path in sorted(wheelhouse.iterdir()):
        if path.name.endswith(".whl"):
            name, version, _, _ = parse_wheel_filename(path.name)
        elif path.name.endswith(".tar.gz"):
            name, version = parse_sdist_filename(path.name)
        else:
            continue
        artifacts.append(
            Artifact(
                str(name),
                str(version),
                path.name,
                "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/" + path.name,
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_size,
            )
        )
    sdk = next(item for item in artifacts if item.name == "watcherobot")
    build = tuple(
        item for item in artifacts if item.name in {"setuptools", "wheel", "packaging"}
    )
    dependencies = tuple(
        item for item in artifacts if item.name not in {"setuptools", "wheel"}
    )
    release = Release(
        sdk.version,
        manifest["platform"],
        ">=3.10,<3.13",
        runtime_contracts(),
        dependencies,
        "Local integration artifact",
        "f" * 64,
        build,
    )
    instance = tmp_path / "instance"
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(instance))
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(tmp_path / "state"))
    runtime = SimpleNamespace(
        python_executable=resources / manifest["python"]["executable"],
        uv_executable=resources / manifest["uv"]["executable"],
    )
    candidate = tmp_path / "sdk-management" / "environments" / ("a" * 64)
    info = install_environment(
        candidate,
        runtime,
        release,
        [wheelhouse / item.filename for item in artifacts],
        tmp_path / "cache",
    )
    assert info["sdk_version"] == sdk.version
    assert info["contracts"] == runtime_contracts()
    assert info["build_id"].startswith("source:")
    assert (candidate / "sdk-environment.json").is_file()
    assert not (instance / "runtime.json").exists()
    request = {
        "action": "status",
        "desktop": {
            "manager": ">=1,<2",
            "management": ">=1,<2",
            "distribution": ">=1,<2",
        },
    }
    result = subprocess.run(
        [str(environment_python(candidate)), "-I", "-m", "watcherobot.management"],
        input=json.dumps(request),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    status = json.loads(result.stdout.splitlines()[-1])
    assert status["ok"] is True
    assert status["data"]["selection"]["version"] == sdk.version
    assert status["data"]["daemon"]["state"] == "stopped"

    return info, candidate, request


@pytest.mark.skipif(
    not os.environ.get("WATCHER_SDK_OTA_TEST_WHEELHOUSE"),
    reason="requires prepared SDK artifacts and bundled Python/uv resources",
)
def test_real_candidate_installs_without_network_or_starting_daemon(
    tmp_path, monkeypatch
):
    _prepare_candidate(tmp_path, monkeypatch)


@pytest.mark.skipif(
    not os.environ.get("WATCHER_SDK_OTA_TEST_WHEELHOUSE"),
    reason="requires prepared SDK artifacts and bundled Python/uv resources",
)
def test_real_activation_and_rollback_replace_only_the_isolated_daemon(
    tmp_path, monkeypatch
):
    from watcherobot import cli
    from watcherobot.management.deployment import activate
    from watcherobot.management.service import Manager, _Runtime
    from watcherobot.runtime.manager import stop_shared_runtime

    info, candidate, request = _prepare_candidate(tmp_path, monkeypatch)
    for key in ("CONTROL_PORT", "EXTERNAL_PORT", "PAIRING_PORT", "PREVIEW_UDP_PORT"):
        monkeypatch.setenv("WATCHER_RUNTIME_" + key, "0")
    manager = Manager(request, root=candidate.parent.parent)
    try:
        manager.restore_default()
        original = cli._live_runtime_state()
        assert original is not None
        before_build = manager.live()["runtime"]["build_id"]
        target = {
            "kind": "environment",
            "root": str(candidate),
            "version": info["sdk_version"],
            "build_id": info["build_id"],
            "contracts": info["contracts"],
        }
        with manager.store.operation("update"):
            activate(manager.store, target, manager.baseline(), _Runtime(manager))
        updated = cli._live_runtime_state()
        assert updated is not None and updated.pid != original.pid
        assert manager.live()["runtime"]["build_id"] == info["build_id"]
        assert manager.selection()["root"] == str(candidate)
        manager.rollback()
        restored = cli._live_runtime_state()
        assert restored is not None and restored.pid != updated.pid
        assert manager.live()["runtime"]["build_id"] == before_build
        assert manager.selection()["kind"] == "bundled"
        assert manager.store.snapshot()["transaction"] is None
    finally:
        stop_shared_runtime()
    assert cli._live_runtime_state() is None


def _local_sdk_version_fixture(original: Path, version: str, target: Path) -> Path:
    """Derive an explicitly local test wheel; never used as a published release."""
    import base64
    import csv
    import io
    import re
    from zipfile import ZIP_DEFLATED, ZipFile

    filename = target / f"watcherobot-{version}-py3-none-any.whl"
    with ZipFile(original) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    old_dist = next(
        name.split("/")[0] for name in entries if name.endswith(".dist-info/METADATA")
    )
    new_dist = f"watcherobot-{version}.dist-info"
    entries = {
        name.replace(old_dist + "/", new_dist + "/"): content
        for name, content in entries.items()
        if not name.endswith(".dist-info/RECORD")
    }
    entries["watcherobot/__init__.py"] = re.sub(
        rb'__version__\s*=\s*["\'][^"\']+["\']',
        f'__version__ = "{version}"'.encode(),
        entries["watcherobot/__init__.py"],
    )
    metadata = new_dist + "/METADATA"
    entries[metadata] = re.sub(
        rb"(?m)^Version: .+$", f"Version: {version}".encode(), entries[metadata]
    )
    rows = io.StringIO()
    writer = csv.writer(rows, lineterminator="\n")
    for name, content in entries.items():
        writer.writerow(
            [
                name,
                "sha256="
                + base64.urlsafe_b64encode(hashlib.sha256(content).digest())
                .rstrip(b"=")
                .decode(),
                len(content),
            ]
        )
    writer.writerow([new_dist + "/RECORD", "", ""])
    entries[new_dist + "/RECORD"] = rows.getvalue().encode()
    with ZipFile(filename, "w", ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return filename


@pytest.mark.skipif(
    not os.environ.get("WATCHER_SDK_OTA_TEST_WHEELHOUSE"),
    reason="requires prepared release artifacts and bundled runtime",
)
def test_real_update_transaction_downloads_installs_switches_and_rolls_back(
    tmp_path, monkeypatch
):
    import base64
    from dataclasses import asdict

    import httpx
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    from watcherobot import cli
    from watcherobot.management.mirror import CATALOG_INDEX, Mirror
    from watcherobot.management.publishing import catalog_wheel, sign_catalog
    from watcherobot.management.service import Manager
    from watcherobot.runtime.manager import stop_shared_runtime

    wheelhouse = Path(os.environ["WATCHER_SDK_OTA_TEST_WHEELHOUSE"])
    original = next(wheelhouse.glob("watcherobot-*.whl"))
    target_wheel = _local_sdk_version_fixture(original, "99.0.0", tmp_path)
    artifacts = []
    routes = {}
    for path in [
        target_wheel,
        *(path for path in wheelhouse.iterdir() if path != original),
    ]:
        if path.name.endswith(".whl"):
            name, version, _, _ = parse_wheel_filename(path.name)
        elif path.name.endswith(".tar.gz"):
            name, version = parse_sdist_filename(path.name)
        else:
            continue
        url = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/" + path.name
        data = path.read_bytes()
        artifacts.append(
            Artifact(
                str(name),
                str(version),
                path.name,
                url,
                hashlib.sha256(data).hexdigest(),
                len(data),
            )
        )
        routes[url] = data
    payload = {
        "schema_version": 1,
        "sequence": 1,
        "releases": [
            {
                "version": "99.0.0",
                "platform": "darwin-arm64",
                "python": ">=3.10,<3.13",
                "contracts": runtime_contracts(),
                "requirements": [
                    asdict(item)
                    for item in artifacts
                    if item.name not in {"setuptools", "wheel"}
                ],
                "build_requirements": [
                    asdict(item)
                    for item in artifacts
                    if item.name in {"setuptools", "wheel", "packaging"}
                ],
                "notes": "LOCAL TEST FIXTURE ONLY; not published",
            }
        ],
    }
    signing_key = Ed25519PrivateKey.generate()
    catalog = sign_catalog(payload, "local-test", signing_key)
    catalog_url = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/packages/watcherobot_desktop_release-1.0.0-py3-none-any.whl"
    routes[CATALOG_INDEX] = f'<a href="{catalog_url}">catalog</a>'.encode()
    routes[catalog_url] = catalog_wheel("1.0.0", catalog)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=routes[str(request.url)])
    )
    monkeypatch.setattr(
        "watcherobot.management.mirror.Mirror", lambda: Mirror(transport=transport)
    )
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path / "instance"))
    monkeypatch.setenv("WATCHER_RUNTIME_STATE_ROOT", str(tmp_path / "state"))
    for key in ("CONTROL_PORT", "EXTERNAL_PORT", "PAIRING_PORT", "PREVIEW_UDP_PORT"):
        monkeypatch.setenv("WATCHER_RUNTIME_" + key, "0")
    request = {
        "desktop": {
            "manager": ">=1,<2",
            "management": ">=1,<2",
            "distribution": ">=1,<2",
        },
        "platform": "darwin-arm64",
        "resources": os.environ["WATCHER_SDK_OTA_TEST_RUNTIME"],
    }
    manager = Manager(request)
    manager.public_keys = {
        "local-test": base64.b64encode(
            signing_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        ).decode()
    }
    try:
        manager.restore_default()
        before = cli._live_runtime_state()
        assert before is not None
        assert manager.check_update()["recommendation"]["version"] == "99.0.0"
        state = manager.update()
        after = cli._live_runtime_state()
        assert after is not None and after.pid != before.pid
        assert state["selection"]["version"] == "99.0.0"
        assert manager.live()["runtime"]["sdk_version"] == "99.0.0"
        assert state["operation"]["phase"] == "completed"
        assert not (manager.store.root / "downloads").exists()
        assert manager.store.snapshot()["candidate"] is None
        manager.rollback()
        assert manager.live()["runtime"]["sdk_version"] == manager.baseline()["version"]
        assert manager.store.snapshot()["transaction"] is None
    finally:
        stop_shared_runtime()
    assert cli._live_runtime_state() is None


@pytest.mark.skipif(
    not os.environ.get("WATCHER_SDK_OTA_TEST_WHEELHOUSE"),
    reason="requires prepared SDK artifacts and bundled Python/uv resources",
)
def test_persistent_host_forwards_to_selected_sdk_without_losing_request_ids(
    tmp_path, monkeypatch
):
    import sys

    from watcherobot.management.service import Manager

    info, candidate, request = _prepare_candidate(tmp_path, monkeypatch)
    monkeypatch.setenv("WATCHER_RUNTIME_INSTANCE_ROOT", str(tmp_path))
    monkeypatch.setenv("WATCHER_RUNTIME_CONTROL_PORT", "0")
    manager = Manager(request)
    state = manager.store.snapshot()
    state["selection"] = {
        "kind": "environment",
        "root": str(candidate),
        "version": info["sdk_version"],
        "build_id": info["build_id"],
        "contracts": info["contracts"],
    }
    manager.store.save(state)
    frames = "".join(
        json.dumps({"id": identifier, "request": request}) + "\n"
        for identifier in ("first", "second")
    )
    result = subprocess.run(
        [sys.executable, "-m", "watcherobot.runtime.daemon", "--sdk-manager-stream"],
        input=frames,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    responses = [json.loads(line) for line in result.stdout.splitlines()]
    assert {item["id"] for item in responses} == {"first", "second"}
    assert all(
        item["ok"] and item["data"]["selection"]["root"] == str(candidate)
        for item in responses
    )
    assert not (tmp_path / "runtime-state.json").exists()


@pytest.mark.skipif(
    not os.environ.get("WATCHER_SDK_OTA_TEST_WHEELHOUSE"),
    reason="requires prepared SDK artifacts and bundled Python/uv resources",
)
@pytest.mark.parametrize("failure", ["disk_full", "hard_exit"])
def test_real_daemon_recovers_uncommitted_activation_without_second_restart(
    tmp_path, monkeypatch, failure
):
    """Local wheel and actual isolated Daemon; never publish or use production keys."""
    import errno
    import sys
    import time

    from watcherobot import cli
    from watcherobot.management.deployment import activate
    from watcherobot.management.service import Manager, _Runtime
    from watcherobot.runtime.manager import stop_shared_runtime

    info, candidate, request = _prepare_candidate(tmp_path, monkeypatch)
    for key in ("CONTROL_PORT", "EXTERNAL_PORT", "PAIRING_PORT", "PREVIEW_UDP_PORT"):
        monkeypatch.setenv("WATCHER_RUNTIME_" + key, "0")
    manager = Manager(request, root=candidate.parent.parent)
    target = {
        "kind": "environment",
        "root": str(candidate),
        "version": info["sdk_version"],
        "build_id": info["build_id"],
        "contracts": info["contracts"],
    }
    process = None
    try:
        manager.restore_default()
        original = cli._live_runtime_state()
        assert original is not None
        if failure == "disk_full":
            save = manager.store.save
            failed = False

            def fail_commit(state):
                nonlocal failed
                if not failed and state.get("selection") == target:
                    failed = True
                    raise OSError(errno.ENOSPC, "injected activation commit failure")
                save(state)

            monkeypatch.setattr(manager.store, "save", fail_commit)
            with (
                pytest.raises(OSError, match="commit failure"),
                manager.store.operation("update") as task,
            ):
                manager.store.transition(task, "switching")
                activate(manager.store, target, manager.baseline(), _Runtime(manager))
        else:
            ready = tmp_path / "activation-ready"
            code = r"""
import json,sys,time
from pathlib import Path
from watcherobot.management.deployment import activate
from watcherobot.management.service import Manager,_Runtime
manager=Manager(json.loads(sys.argv[1]),root=Path(sys.argv[2]))
target=json.loads(sys.argv[3])
ready=Path(sys.argv[4])
class Runtime:
    def status(self):return manager.live()
    def start(self,target):
        _Runtime(manager).start(target)
        ready.write_text("actual target Daemon started; commit not written")
        time.sleep(60)
with manager.store.operation("update") as task:
    manager.store.transition(task,"switching")
    activate(manager.store,target,manager.baseline(),Runtime())
"""
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    code,
                    json.dumps(request),
                    str(manager.store.root),
                    json.dumps(target),
                    str(ready),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 45
            while (
                not ready.exists()
                and process.poll() is None
                and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            assert ready.exists()
            process.kill()
            process.wait(timeout=5)
        activated = cli._live_runtime_state()
        assert activated is not None and activated.pid != original.pid
        assert manager.store.snapshot()["transaction"] is not None
        assert manager.status()["actions"]["recover"]["enabled"]
        downloads = manager.store.root / "downloads"
        downloads.mkdir()
        (downloads / "interrupted.whl.part").write_bytes(b"test-only interruption")
        recovered = manager.recover()
        assert not downloads.exists()
        assert recovered["selection"]["root"] == str(candidate)
        assert recovered["operation"]["phase"] == "completed"
        assert cli._live_runtime_state().pid == activated.pid
        assert manager.store.snapshot()["transaction"] is None
        manager.rollback()
        assert manager.selection()["kind"] == "bundled"
        assert cli._live_runtime_state().pid != activated.pid
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            if process.stderr:
                process.stderr.close()
        stop_shared_runtime()
    assert cli._live_runtime_state() is None
