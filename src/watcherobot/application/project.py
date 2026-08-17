"""Create a complete local WatcheRobot Application project."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version

from watcherobot import __version__
from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifest,
    ApplicationManifestError,
    parse_application_manifest,
)


_INITIAL_APPLICATION_VERSION = "0.1.0"
_ICON_PATH = "icon.svg"
_DEFAULT_PROJECT_SLUG = "hello_robot"
_LOCAL_APPLICATION_ID_PREFIX = "local."
_APP_TEMPLATE = '''"""WatcheRobot Application entrypoint."""

import asyncio

from watcherobot.application import ApplicationContext


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        app.logger.info("Hello, WatcheRobot! Your first Application worked.")
        if not app.robot.supports("behavior"):
            app.logger.info(
                "No compatible robot is connected, so the happy behavior was "
                "skipped. Run 'watcherobot robot setup' to connect one."
            )
            return

        job = await asyncio.to_thread(
            app.robot.behavior.play,
            "happy",
            repeat=1,
        )
        await asyncio.to_thread(job.wait, 20.0)
        app.logger.info("The robot played the happy behavior.")


asyncio.run(main())
'''
_ICON_TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="112" fill="#121826"/>
  <rect x="96" y="128" width="320" height="288" rx="96" fill="#4da3ff"/>
  <circle cx="192" cy="256" r="32" fill="#ffffff"/>
  <circle cx="320" cy="256" r="32" fill="#ffffff"/>
  <path d="M176 336c48 32 112 32 160 0" fill="none" stroke="#ffffff"
        stroke-width="24" stroke-linecap="round"/>
  <path d="M256 80v48" stroke="#4da3ff" stroke-width="24"
        stroke-linecap="round"/>
  <circle cx="256" cy="64" r="24" fill="#4da3ff"/>
</svg>
"""
_GITIGNORE_TEMPLATE = """__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
build/
dist/
*.egg-info/
.env
.env.*
!.env.example
"""


class ApplicationProjectInitError(RuntimeError):
    """Raised when a new project cannot be validated or created safely."""


@dataclass(frozen=True)
class ApplicationProjectInitResult:
    """Files and normalized metadata created by one initialization."""

    directory: Path
    app_id: str
    name: str
    version: str
    requires_watcherobot: str
    files: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "directory": str(self.directory),
            "id": self.app_id,
            "name": self.name,
            "version": self.version,
            "requires_watcherobot": self.requires_watcherobot,
            "files": list(self.files),
        }


@dataclass(frozen=True)
class ApplicationProjectDefaults:
    """Development-friendly metadata derived from a project directory."""

    app_id: str
    name: str
    author: str
    description: str


def default_application_project_metadata(
    directory: Path,
) -> ApplicationProjectDefaults:
    """Derive valid local metadata without asking publishing questions."""

    project_name = Path(directory).name.strip() or _DEFAULT_PROJECT_SLUG
    slug = re.sub(r"[^a-z0-9]+", "_", project_name.lower()).strip("_")
    slug = slug[: 64 - len(_LOCAL_APPLICATION_ID_PREFIX)].rstrip("_")
    if not slug:
        slug = _DEFAULT_PROJECT_SLUG
    display_name = re.sub(r"[-_]+", " ", project_name).strip()
    display_name = display_name.title() or "Hello Robot"
    return ApplicationProjectDefaults(
        app_id=f"{_LOCAL_APPLICATION_ID_PREFIX}{slug}",
        name=display_name,
        author="Local Developer",
        description=f"{display_name} WatcheRobot Application.",
    )


def init_application_project(
    directory: Path,
    *,
    app_id: str,
    name: str,
    author: str,
    description: str,
    watcherobot_version: str | None = None,
) -> ApplicationProjectInitResult:
    """Create one new publish-ready project without overwriting a target."""

    target = Path(directory).resolve()
    if target.exists():
        raise ApplicationProjectInitError(f"Target already exists: {target}")

    normalized_author = author.strip()
    normalized_description = description.strip()
    if not normalized_author:
        raise ApplicationProjectInitError("author must not be empty")
    if not normalized_description:
        raise ApplicationProjectInitError("description must not be empty")

    sdk_version = watcherobot_version or __version__
    requirement = _default_sdk_requirement(sdk_version)
    manifest_document = _manifest_document(
        app_id=app_id,
        name=name,
        author=normalized_author,
        description=normalized_description,
        requires_watcherobot=requirement,
    )
    try:
        metadata = parse_application_manifest(
            manifest_document,
            watcherobot_version=sdk_version,
        )
    except ApplicationManifestError as exc:
        raise ApplicationProjectInitError(str(exc)) from exc

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=".watcherobot-init-",
                dir=target.parent,
            )
        )
    except OSError as exc:
        raise ApplicationProjectInitError(
            f"Unable to prepare project directory: {target}"
        ) from exc

    try:
        _write_project_files(
            staging,
            manifest_document=manifest_document,
            name=metadata.name,
            app_id=metadata.app_id,
            author=metadata.author,
            description=metadata.description,
        )
        ApplicationManifest.load(
            staging,
            watcherobot_version=sdk_version,
        )
        if target.exists():
            raise ApplicationProjectInitError(
                f"Target already exists: {target}"
            )
        staging.rename(target)
    except ApplicationProjectInitError:
        _remove_staging(staging)
        raise
    except ApplicationManifestError as exc:
        _remove_staging(staging)
        raise ApplicationProjectInitError(str(exc)) from exc
    except OSError as exc:
        _remove_staging(staging)
        raise ApplicationProjectInitError(
            f"Unable to create Application project: {target}"
        ) from exc

    files = tuple(sorted(path.name for path in target.iterdir()))
    return ApplicationProjectInitResult(
        directory=target,
        app_id=metadata.app_id,
        name=metadata.name,
        version=metadata.version,
        requires_watcherobot=metadata.requires_watcherobot,
        files=files,
    )


def _default_sdk_requirement(version: str) -> str:
    try:
        parsed = Version(version)
    except InvalidVersion as exc:
        raise ApplicationProjectInitError(
            f"Installed watcherobot version is invalid: {version}"
        ) from exc
    if parsed.major == 0:
        upper = f"0.{parsed.minor + 1}"
    else:
        upper = str(parsed.major + 1)
    return f">={parsed},<{upper}"


def _manifest_document(
    *,
    app_id: str,
    name: str,
    author: str,
    description: str,
    requires_watcherobot: str,
) -> bytes:
    payload = {
        "schema_version": 1,
        "id": app_id.strip(),
        "name": name.strip(),
        "version": _INITIAL_APPLICATION_VERSION,
        "requires_watcherobot": requires_watcherobot,
        "dependencies": [],
        "description": description,
        "author": author,
        "icon": _ICON_PATH,
    }
    return (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _write_project_files(
    root: Path,
    *,
    manifest_document: bytes,
    name: str,
    app_id: str,
    author: str,
    description: str,
) -> None:
    root.joinpath("app.json").write_bytes(manifest_document)
    root.joinpath("app.py").write_text(_APP_TEMPLATE, encoding="utf-8")
    root.joinpath("icon.svg").write_text(_ICON_TEMPLATE, encoding="utf-8")
    root.joinpath(".gitignore").write_text(
        _GITIGNORE_TEMPLATE,
        encoding="utf-8",
    )
    root.joinpath("README.md").write_text(
        _readme(
            name=name,
            app_id=app_id,
            author=author,
            description=description,
        ),
        encoding="utf-8",
    )


def _readme(
    *,
    name: str,
    app_id: str,
    author: str,
    description: str,
) -> str:
    return f"""# {name}

{description}

- Application ID: `{app_id}`
- Author: {author}

## Develop

```powershell
watcherobot robot setup  # first robot only
watcherobot robot status
watcherobot app run
watcherobot app check .
watcherobot app publish .
```

The generated `app.py` always logs a Hello World success. With a compatible
robot connected, it also plays the `happy` behavior once. Run it through the
SDK Runtime; do not execute `app.py` directly.
"""


def _remove_staging(staging: Path) -> None:
    try:
        shutil.rmtree(staging)
    except OSError:
        pass
