"""Project-relative voice configuration; credentials never appear in reprs."""
from __future__ import annotations

import math
import os
import re
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Mapping

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


class ConfigurationError(ValueError):
    """An actionable, credential-free configuration error."""


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str = ""
    base_url: str = ""
    language: str = ""
    voice_id: str = ""
    resource_id: str = ""
    timeout: float = 45.0
    parameters: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    credentials: dict[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ConversationConfig:
    history_turns: int = 6
    silence_ms: int = 800
    max_utterance_seconds: float = 30.0
    speech_threshold: int = 550
    pre_roll_ms: int = 300
    segment_characters: int = 160
    max_reply_characters: int = 4000


@dataclass(frozen=True)
class VoiceConfiguration:
    asr: ModelConfig
    llm: ModelConfig
    tts: ModelConfig
    conversation: ConversationConfig
    prompt: str


def read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise ConfigurationError(f"无法读取有效 TOML：{path.as_posix()}") from None


def _unknown(data: dict[str, Any], allowed: set[str], location: str) -> None:
    extra = data.keys() - allowed
    if extra:
        raise ConfigurationError(f"{location}：未知字段 {', '.join(sorted(extra))}")


def _credential(value: Any, location: str) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(f"{location}：凭据必须是字符串")
    match = re.fullmatch(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}", value)
    if match:
        value = os.environ.get(match[1], "")
        if not value.strip():
            raise ConfigurationError(f"{location}：环境变量 {match[1]} 未设置")
    return value.strip()


def default_credential_directory(root: Path, app_id: str, *, installed: bool = False) -> Path:
    """Locate this application's private storage independently of shell overrides."""
    root = root.resolve()
    if not installed and (root / "credentials").is_dir():
        return root / "credentials"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", app_id):
        raise ConfigurationError("无效的 Application ID")
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
    elif sys.platform == "darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return base / "watcherobot/applications" / app_id / "credentials"


def credential_directory(
    root: Path, app_id: str, *, installed: bool = False, use_environment: bool = True,
) -> Path:
    """Resolve a configure override, a saved source, or per-app private storage.

    Managed applications disable shell overrides: their Daemon may have started
    with a different environment from the terminal running the configuration UI.
    """
    root = root.resolve()
    override = os.environ.get("WATCHER_VOICE_CREDENTIALS_DIR") if use_environment else None
    if override:
        return (root / Path(override).expanduser()).resolve()
    directory = default_credential_directory(root, app_id, installed=installed)
    source = directory / ".source.toml"
    if directory.is_symlink() or source.is_symlink():
        raise ConfigurationError("凭据来源目录或文件不能是符号链接。")
    if source.exists():
        data = read_toml(source)
        _unknown(data, {"directory"}, "凭据来源配置")
        value = data.get("directory")
        if not isinstance(value, str) or not value.strip() or not Path(value).is_absolute():
            raise ConfigurationError("凭据来源配置 directory 必须是绝对路径。")
        return Path(value)
    return directory


def load_configuration(root: Path, *, credentials_dir: Path | None = None) -> VoiceConfiguration:
    root = root.resolve()
    secret_root = (root / (credentials_dir or Path("credentials")).expanduser()).resolve()
    return _load_configuration(root, secret_root, read_toml)


def _load_model_configuration(
    root: Path, kind: str, secret_path: Path,
    read_credentials: Callable[[Path], dict[str, Any]],
) -> ModelConfig:
    """Validate one service without loading unrelated credentials or prompts."""
    location = f"config/models/{kind}.toml"
    data = read_toml(root / location)
    version = data.pop("schema_version", 1)
    if type(version) is not int or version != 1:
        raise ConfigurationError(f"{location}：不支持的 schema_version")
    _unknown(data, {f.name for f in fields(ModelConfig)} - {"credentials"}, location)
    for key in ("provider", "model", "base_url", "language", "voice_id", "resource_id"):
        if key in data and not isinstance(data[key], str):
            raise ConfigurationError(f"{location} → {key}：必须是字符串")
    if not data.get("provider", "").strip():
        raise ConfigurationError(f"{location} → provider：未填写")
    for key in ("parameters", "options"):
        if key in data and not isinstance(data[key], dict):
            raise ConfigurationError(f"{location} → {key}：必须是表")
    timeout = data.get("timeout", 45.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 300:
        raise ConfigurationError(f"{location} → timeout：必须在 1–300 秒之间")
    secrets = {key: _credential(value, f"{secret_path.as_posix()} → {key}")
               for key, value in read_credentials(secret_path).items()}
    config = ModelConfig(**data, credentials=secrets)
    validate_credentials(kind, config, secret_path)
    validate_model(kind, config, location)
    return config


def _load_configuration(
    root: Path, secret_root: Path, read_credentials: Callable[[Path], dict[str, Any]],
) -> VoiceConfiguration:
    """Shared validation for disk credentials and an unsaved interactive draft."""
    models = {
        kind: _load_model_configuration(root, kind, secret_root / f"{kind}.toml", read_credentials)
        for kind in ("asr", "llm", "tts")
    }
    location = "config/conversation.toml"
    values = read_toml(root / location)
    _unknown(values, {f.name for f in fields(ConversationConfig)}, location)
    limits = {"history_turns": (0, 100), "silence_ms": (200, 5000),
              "max_utterance_seconds": (1, 120), "speech_threshold": (1, 32767),
              "pre_roll_ms": (0, 1000), "segment_characters": (20, 500),
              "max_reply_characters": (100, 20000)}
    for key, value in values.items():
        low, high = limits[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not low <= value <= high
                or (key != "max_utterance_seconds" and type(value) is not int)):
            raise ConfigurationError(f"{location} → {key}：必须在 {low}–{high} 范围内")
    variables = read_toml(root / "config/persona.toml")
    try:
        prompt = (root / "prompts/system.md").read_text(encoding="utf-8-sig")
    except OSError:
        raise ConfigurationError("无法读取 prompts/system.md") from None

    def replace(match: re.Match[str]) -> str:
        name = match[1].strip()
        if name not in variables or not isinstance(variables[name], (str, int, float, bool)):
            raise ConfigurationError(f"prompts/system.md：未定义或无效的变量 {name}")
        return str(variables[name])

    prompt = re.sub(r"\{\{([^{}]+)\}\}", replace, prompt)
    if not prompt.strip():
        raise ConfigurationError("prompts/system.md：提示词不能为空")
    return VoiceConfiguration(models["asr"], models["llm"], models["tts"],
                              ConversationConfig(**values), prompt)


def credential_keys(kind: str, provider: str, credentials: Mapping[str, Any]) -> tuple[str, ...] | None:
    """Fields for built-in adapters; custom adapters define their own schema."""
    if provider == "volcengine":
        return ("app_id", "access_token")
    if kind == "asr" and provider == "aliyun":
        return ("app_key", "token") if credentials.get("token") else (
            "app_key", "access_key_id", "access_key_secret")
    # Custom adapters own their validation; no fixed provider whitelist.
    known = {"volcengine", "aliyun", "deepgram", "ark", "qwen", "openai_chat",
             "cartesia", "elevenlabs", "minimaxi"}
    return ("api_key",) if provider in known else None


def validate_credentials(kind: str, config: ModelConfig, path: Path) -> None:
    required = credential_keys(kind, config.provider, config.credentials)
    if required is None:
        return
    if config.provider == "openai_chat" and config.base_url:
        return
    for key in required:
        if not config.credentials.get(key):
            raise ConfigurationError(f"{path.as_posix()} → {key}：未填写")


def validate_model(kind: str, config: ModelConfig, location: str) -> None:
    """Validate protocol requirements, never model/voice allowlists."""
    from urllib.parse import urlsplit

    required = []
    if kind == "llm" and config.provider in ("qwen", "ark", "openai_chat"):
        required.append("model")
    if config.provider == "openai_chat":
        required.append("base_url")
    if kind == "tts" and config.provider in ("cartesia", "elevenlabs", "minimaxi"):
        required.extend(("model", "voice_id"))
    if config.provider == "volcengine":
        required.append("resource_id")
        if kind == "tts":
            required.append("voice_id")
    for key in required:
        if not getattr(config, key).strip():
            raise ConfigurationError(f"{location} → {key}：未填写")
    if config.base_url:
        parsed = urlsplit(config.base_url)
        schemes = ("ws", "wss") if kind == "asr" else ("http", "https")
        if parsed.scheme not in schemes or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ConfigurationError(f"{location} → base_url：需要不含认证、查询或片段的服务端点")
    if kind == "llm":
        for key, low, high in (("temperature", 0, 2), ("top_p", 0, 1), ("max_tokens", 1, 1000000)):
            value = config.parameters.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or not low <= value <= high
                                      or (key == "max_tokens" and type(value) is not int)):
                raise ConfigurationError(f"{location} → parameters.{key}：类型或范围无效")
