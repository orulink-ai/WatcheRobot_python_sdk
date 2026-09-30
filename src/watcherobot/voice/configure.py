"""Local credential editing, separate from providers and the voice runtime."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Callable

from watcherobot.runtime.daemon.application.manifest import ApplicationManifest

from .configuration import (
    ConfigurationError,
    _load_configuration,
    _load_model_configuration,
    credential_directory,
    credential_keys,
    read_toml,
)


def configure_voice(
    root: Path, prompt: Callable[[str], str], output: Callable[[str], None], *, service: str | None = None,
) -> None:
    """Collect all answers and validate before changing any credential file."""
    root = root.resolve()
    manifest = ApplicationManifest.load(root)
    directory = credential_directory(root, manifest.app_id)
    if service is not None and service not in ("asr", "llm", "tts"):
        raise ConfigurationError("不支持的语音服务。")
    kinds = (service,) if service is not None else ("asr", "llm", "tts")
    paths = {kind: directory / f"{kind}.toml" for kind in kinds}
    if directory.is_symlink() or any(path.is_symlink() for path in paths.values()):
        raise ConfigurationError("凭据目录或文件不能是符号链接；请直接编辑目标文件。")
    drafts: dict[Path, dict[str, str]] = {}
    original: dict[Path, bytes | None] = {}
    fields: dict[str, tuple[str, ...]] = {}
    providers: dict[str, str] = {}
    optional: set[str] = set()
    for kind, path in paths.items():
        model = read_toml(root / f"config/models/{kind}.toml")
        provider = model.get("provider")
        if not isinstance(provider, str) or not provider.strip():
            raise ConfigurationError(f"config/models/{kind}.toml → provider：未填写")
        providers[kind] = provider
        original[path] = path.read_bytes() if path.exists() else None
        values = read_toml(path) if path.exists() else {}
        if any(not isinstance(value, str) for value in values.values()):
            raise ConfigurationError(f"{path}：凭据必须是字符串")
        keys = credential_keys(kind, provider, values)
        if keys is None:
            if not values:
                raise ConfigurationError(f"{kind.upper()} 自定义供应商没有凭据字段；请先在 {path} 定义字段。")
            keys = tuple(values)
            optional.add(kind)
        if provider == "openai_chat" and model.get("base_url"):
            optional.add(kind)
        fields[kind] = keys
        drafts[path] = values.copy()

    output(f"凭据目录：{directory}")
    output("输入不回显；已有值按回车保留。仅校验本地配置，不调用云服务。")
    for kind, path in paths.items():
        output(f"{kind.upper()} / {providers[kind]}")
        for key in fields[kind]:
            old = drafts[path].get(key, "")
            hint = "已设置，回车保留" if old.strip() else "可留空" if kind in optional else "必填"
            while True:
                value = prompt(f"  {key} [{hint}]: ").strip()
                if not value:
                    value = old
                if value.strip() or kind in optional:
                    drafts[path][key] = value
                    break
                output(f"{key} 不能为空，请重新输入。")

    # Share runtime validation, but a selected service must not require the others.
    if service is None:
        _load_configuration(root, directory, lambda path: drafts[path])
    else:
        _load_model_configuration(root, service, paths[service], lambda path: drafts[path])
    changed: dict[Path, bytes] = {}
    for path, values in drafts.items():
        if original[path] is not None and values == read_toml(path):
            continue  # Preserve comments and environment references on no-op runs.
        text = ''.join(f'{json.dumps(key, ensure_ascii=False)} = {json.dumps(value, ensure_ascii=False)}\n'
                       for key, value in values.items())
        changed[path] = text.encode("utf-8")
    _save_credentials(changed, original)
    if service is None:
        output("凭据配置已保存，本地配置校验通过；尚未验证云服务权限。修改后重启应用生效。")
    else:
        output(f"{service.upper()} 凭据已保存，该服务本地配置校验通过；其他服务未检查，尚未验证云服务权限。修改后重启应用生效。")


def _stage(path: Path, content: bytes) -> Path:
    """Stage beside the target so replacement is atomic on both host platforms."""
    fd, filename = tempfile.mkstemp(prefix=".voice-config-", dir=path.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _save_credentials(changed: dict[Path, bytes], original: dict[Path, bytes | None]) -> None:
    staged: dict[Path, Path] = {}
    applied: list[Path] = []
    try:
        for path, content in changed.items():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            staged[path] = _stage(path, content)
        for path, temporary in staged.items():
            # Detect edits made while the prompt was open instead of overwriting them.
            current = path.read_bytes() if path.exists() else None
            if current != original[path] or path.is_symlink():
                raise ConfigurationError("配置期间凭据文件已变化，请重新执行 app configure。")
            os.replace(temporary, path)
            applied.append(path)
    except BaseException:
        for path in reversed(applied):
            previous = original[path]
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                backup = _stage(path, previous)
                try:
                    os.replace(backup, path)
                finally:
                    backup.unlink(missing_ok=True)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
