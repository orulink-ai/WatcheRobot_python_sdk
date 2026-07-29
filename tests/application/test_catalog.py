from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from watcherobot.application.catalog import (
    ApplicationCatalog,
    CatalogBusyError,
    CatalogConflictError,
    CatalogPackageError,
    ProtectedApplicationError,
    package_application,
)


def _write_application(
    root: Path,
    *,
    app_id: str = "demo_app",
    version: str = "1.0.0",
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("app.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": app_id,
                "name": "Demo Application",
                "version": version,
                "requires_watcherobot": ">=0.1.0a4,<0.2",
                "dependencies": [],
            }
        ),
        encoding="utf-8",
    )
    root.joinpath("app.py").write_text("print('demo')\n", encoding="utf-8")
    root.joinpath("assets").mkdir()
    root.joinpath("assets", "note.txt").write_text("asset", encoding="utf-8")


def test_package_install_select_list_and_uninstall_round_trip(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "demo.wapp"
    _write_application(source)
    package_application(source, archive)
    catalog = ApplicationCatalog(tmp_path / "catalog")

    installed = catalog.install(archive)

    assert installed.app_id == "demo_app"
    assert installed.version == "1.0.0"
    assert installed.path.joinpath("assets", "note.txt").read_text() == "asset"
    assert catalog.list() == [installed]
    assert catalog.select("demo_app", version="1.0.0") == installed
    assert catalog.selected() == installed

    catalog.uninstall("demo_app", version="1.0.0")
    assert catalog.list() == []
    assert catalog.selected() is None


def test_catalog_rejects_duplicate_and_path_traversal_packages(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "demo.wapp"
    _write_application(source)
    package_application(source, archive)
    catalog = ApplicationCatalog(tmp_path / "catalog")
    catalog.install(archive)

    with pytest.raises(CatalogConflictError):
        catalog.install(archive)

    malicious = tmp_path / "malicious.wapp"
    with zipfile.ZipFile(malicious, "w") as package:
        package.writestr("../escape.txt", "escape")
        package.writestr("app.py", "print('bad')")
        package.writestr(
            "app.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "bad_app",
                    "name": "Bad",
                    "version": "1.0.0",
                    "requires_watcherobot": ">=0.1.0a4,<0.2",
                    "dependencies": [],
                }
            ),
        )
    with pytest.raises(CatalogPackageError, match="unsafe"):
        catalog.install(malicious)
    assert not tmp_path.joinpath("escape.txt").exists()


def test_catalog_is_immutable_while_application_is_running(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "demo.wapp"
    _write_application(source)
    package_application(source, archive)
    running = False
    catalog = ApplicationCatalog(
        tmp_path / "catalog",
        is_runtime_active=lambda: running,
    )
    installed = catalog.install(archive)
    running = True

    with pytest.raises(CatalogBusyError):
        catalog.select(installed.app_id, version=installed.version)
    with pytest.raises(CatalogBusyError):
        catalog.uninstall(installed.app_id, version=installed.version)


def test_builtin_default_application_cannot_be_uninstalled(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "default.wapp"
    _write_application(source, app_id="watcher_default")
    package_application(source, archive)
    catalog = ApplicationCatalog(tmp_path / "catalog")
    catalog.install(archive)

    with pytest.raises(ProtectedApplicationError):
        catalog.uninstall("watcher_default", version="1.0.0")


def test_package_honors_wappignore_patterns(tmp_path: Path) -> None:
    source = tmp_path / "source"
    archive = tmp_path / "demo.wapp"
    _write_application(source)
    source.joinpath("tests").mkdir()
    source.joinpath("tests", "test_app.py").write_text(
        "raise AssertionError\n",
        encoding="utf-8",
    )
    source.joinpath(".venv-test").mkdir()
    source.joinpath(".venv-test", "installed.py").write_text(
        "installed = True\n",
        encoding="utf-8",
    )
    source.joinpath("build.tmp").write_text("temporary", encoding="utf-8")
    source.joinpath(".wappignore").write_text(
        "tests/\n.venv*/\n*.tmp\n",
        encoding="utf-8",
    )

    package_application(source, archive)

    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
    assert "app.json" in names
    assert "app.py" in names
    assert "tests/test_app.py" not in names
    assert ".venv-test/installed.py" not in names
    assert "build.tmp" not in names
    assert ".wappignore" not in names
