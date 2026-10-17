"""Verify an application's own interpreter without executing its business code."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

_PROBE = r"""
import json
import importlib.metadata as m
import watcherobot
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
versions={canonicalize_name(d.metadata['Name']):d.version for d in m.distributions() if d.metadata['Name']}
ok=True
for d in m.distributions():
    for raw in d.requires or []:
        r=Requirement(raw)
        if r.marker and not r.marker.evaluate({'extra':''}):continue
        version=versions.get(canonicalize_name(r.name))
        if not version or (r.specifier and not r.specifier.contains(version,prereleases=True)):ok=False
print(json.dumps({'sdk_version':watcherobot.__version__,'dependencies_ok':ok,'versions':versions}))
"""


def verify_probe(
    payload: dict[str, Any], sdk_requirement: str, lock: tuple[str, ...]
) -> dict[str, object]:
    version = payload.get("sdk_version")
    if not isinstance(version, str) or not SpecifierSet(sdk_requirement).contains(
        version, prereleases=True
    ):
        raise ValueError("Application environment SDK does not satisfy its manifest")
    if payload.get("dependencies_ok") is not True:
        raise ValueError(
            "Application environment has missing or incompatible dependencies; repair the application"
        )
    for pin in lock:
        requirement = Requirement(pin)
        actual = (
            version
            if canonicalize_name(requirement.name) == "watcherobot"
            else payload.get("versions", {}).get(canonicalize_name(requirement.name))
        )
        if not actual or not requirement.specifier.contains(actual, prereleases=True):
            raise ValueError(
                "Application environment differs from its dependency lock; repair the application"
            )
    return {"status": "compatible", "sdk_version": version}


async def verify_application_environment(
    application: Path,
    executable: Path,
    sdk_requirement: str,
    schema: int,
    dependencies: tuple[str, ...],
) -> dict[str, object]:
    lock: tuple[str, ...] = ()
    if schema == 3:
        from watcherobot.distribution.dependency_lock import read_dependency_lock

        lock = read_dependency_lock(application, sdk_requirement, dependencies)
    environment = dict(os.environ)
    for key in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(key, None)
    environment.update(
        PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1"
    )
    process = await asyncio.create_subprocess_exec(
        str(executable),
        "-I",
        "-c",
        _PROBE,
        cwd=str(application),
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    async def read_bounded(stream: asyncio.StreamReader) -> bytes:
        chunks: list[bytes] = []
        size = 0
        while chunk := await stream.read(8192):
            size += len(chunk)
            if size > 256 * 1024:
                raise ValueError(
                    "Application environment probe exceeded its output limit"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    assert process.stdout is not None and process.stderr is not None
    readers = asyncio.gather(read_bounded(process.stdout), read_bounded(process.stderr))
    try:
        output, _errors = await asyncio.wait_for(readers, 30)
        await asyncio.wait_for(process.wait(), 5)
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        readers.cancel()
        raise
    if process.returncode:
        raise ValueError(
            "Application SDK import or environment verification failed; repair the application"
        )
    return verify_probe(json.loads(output), sdk_requirement, lock)
