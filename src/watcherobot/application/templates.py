"""Built-in project templates shared by the CLI and project generator.

Templates customize the common Application scaffold; runtime behavior belongs
in the generated Application or its SDK libraries, not in the initializer.
"""
from __future__ import annotations

from dataclasses import dataclass
from importlib.abc import Traversable
from importlib.resources import files
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class ApplicationTemplate:
    name: str
    description: str
    customize: Callable[[Path], None] | None = None
    setup_hint: str = ""
    configuration_files: tuple[str, ...] = ()
    configure: Callable[[Path, Callable[[str], str], Callable[[str], None], str | None], None] | None = None
    configuration_services: tuple[str, ...] = ()


def _write_voice(root: Path) -> None:
    # Creating a base project must not load voice implementation modules.
    from watcherobot.voice.template import write_voice_template

    write_voice_template(root)


def _configure_voice(
    root: Path, prompt: Callable[[str], str], output: Callable[[str], None], service: str | None,
) -> None:
    from watcherobot.voice.configure import configure_voice

    configure_voice(root, prompt, output, service=service)


def configure_application(
    root: Path, prompt: Callable[[str], str], output: Callable[[str], None], *, service: str | None = None,
) -> None:
    """Dispatch built-in configuration without executing Application source."""
    from watcherobot.runtime.daemon.application.manifest import ApplicationManifest

    ApplicationManifest.load(root)
    for template in BUILTIN_TEMPLATES:
        if template.configure and template.configuration_files and all(
            (root / name).is_file() for name in template.configuration_files
        ):
            if service is not None and service not in template.configuration_services:
                raise ValueError("此模板不支持指定的服务配置。")
            template.configure(root, prompt, output, service)
            return
    raise ValueError("此应用没有内置配置向导；请按应用 README 手动配置。")


DEFAULT_TEMPLATE = "base"
BUILTIN_TEMPLATES = (
    ApplicationTemplate("base", "Minimal Application (five files)"),
    ApplicationTemplate(
        "voice",
        "Voice conversation (ASR, LLM and TTS credentials required)",
        _write_voice,
        "watcherobot app configure\n"
        "  # Or edit credentials/asr.toml, credentials/llm.toml and credentials/tts.toml.\n"
        "  # Already on Wi-Fi: replace 123456 with the robot's current six-digit code.\n"
        "  watcherobot robot pair 123456\n"
        "  # First Wi-Fi setup instead: watcherobot robot setup\n"
        "  watcherobot robot status",
        configuration_files=("application/voice.py",),
        configuration_services=("asr", "llm", "tts"),
        configure=_configure_voice,
    ),
)


def get_template(name: str) -> ApplicationTemplate:
    for template in BUILTIN_TEMPLATES:
        if template.name == name:
            return template
    raise ValueError(f"Unknown template: {name}")


def copy_template_files(name: str, root: Path) -> None:
    """Copy packaged assets; each specialization overlays the common base."""
    _copy_tree(files("watcherobot").joinpath(f"templates/{name}"), root)


def _copy_tree(source: Traversable, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for entry in source.iterdir():
        if entry.name == "__pycache__" or entry.name.endswith((".pyc", ".pyo")):
            continue
        destination = target / entry.name
        if entry.is_dir():
            _copy_tree(entry, destination)
        elif entry.name == ".gitignore" and destination.exists():
            current = destination.read_text(encoding="utf-8")
            existing_lines = set(current.splitlines())
            additions = [
                line for line in entry.read_text(encoding="utf-8").splitlines()
                if line and line not in existing_lines
            ]
            if additions:
                destination.write_text(
                    current.rstrip("\n") + "\n\n" + "\n".join(additions) + "\n",
                    encoding="utf-8",
                )
        else:
            destination.write_bytes(entry.read_bytes())
