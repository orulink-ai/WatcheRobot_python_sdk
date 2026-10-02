"""Create a complete local WatcheRobot Application project."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from packaging.version import InvalidVersion, Version

from watcherobot import __version__
from watcherobot.application.templates import (
    DEFAULT_TEMPLATE,
    copy_template_files,
    get_template,
)
from watcherobot.runtime.daemon.application.manifest import (
    ApplicationManifest,
    ApplicationManifestError,
    parse_application_manifest,
)


_DEFAULT_PROJECT_SLUG = "my_app"
_LOCAL_APPLICATION_ID_PREFIX = "local."

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
    supported_host_platforms: tuple[str, ...]
    files: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "directory": str(self.directory),
            "id": self.app_id,
            "name": self.name,
            "version": self.version,
            "requires_watcherobot": self.requires_watcherobot,
            "supported_host_platforms": list(self.supported_host_platforms),
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
    display_name = display_name.title() or "My App"
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
    supported_host_platforms: list[str],
    watcherobot_version: str | None = None,
    template: str = DEFAULT_TEMPLATE,
) -> ApplicationProjectInitResult:
    """Create one new publish-ready project without overwriting a target."""

    target = Path(directory).resolve()
    try:
        selected_template = get_template(template)
    except ValueError as exc:
        raise ApplicationProjectInitError(str(exc)) from exc
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
        supported_host_platforms=supported_host_platforms,
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
        if selected_template.customize is not None:
            selected_template.customize(staging)
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
    except BaseException:
        # Template code and user interrupts must not leave a partial scaffold.
        # Preserve the original exception, including KeyboardInterrupt.
        _remove_staging(staging)
        raise

    files = tuple(sorted(path.name for path in target.iterdir()))
    return ApplicationProjectInitResult(
        directory=target,
        app_id=metadata.app_id,
        name=metadata.name,
        version=metadata.version,
        requires_watcherobot=metadata.requires_watcherobot,
        supported_host_platforms=metadata.supported_host_platforms,
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
    supported_host_platforms: list[str],
) -> bytes:
    payload = json.loads(
        files("watcherobot")
        .joinpath("templates/base/app.json")
        .read_text(encoding="utf-8")
    )
    payload.update({
        "id": app_id.strip(),
        "name": name.strip(),
        "requires_watcherobot": requires_watcherobot,
        "supported_host_platforms": supported_host_platforms,
        "description": description,
        "author": author,
    })
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
    copy_template_files("base", root)
    root.joinpath("app.json").write_bytes(manifest_document)
    readme = root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").format(
            name=name, app_id=app_id, author=author, description=description,
        ),
        encoding="utf-8",
    )


def _remove_staging(staging: Path) -> None:
    try:
        shutil.rmtree(staging)
    except OSError:
        pass
