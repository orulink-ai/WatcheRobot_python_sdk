"""Validated, local-only configuration; secret values never leave GET APIs."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SECRETS = ('tts_token', 'stt_token', 'speech_secret', 'llm_key')


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tts_app_id: str = ''
    tts_token: str = ''
    stt_app_id: str = ''
    stt_token: str = ''
    speech_secret: str = ''
    tts_resource: str = 'seed-tts-2.0'
    tts_voice: str = 'zh_female_vv_uranus_bigtts'
    stt_mode: Literal['stream', 'flash'] = 'stream'
    stt_resource: str = 'volc.bigasr.sauc.duration'
    llm_key: str = ''
    llm_provider: Literal['aliyun', 'volcengine'] = 'aliyun'
    llm_model: str = 'qwen-flash'
    sleep_animation: str = Field(default='standby_loop', pattern=r'^[a-z][a-z0-9_]{0,62}$')
    wake_animation: str = Field(default='standby_end', pattern=r'^[a-z][a-z0-9_]{0,62}$')
    left_animation: Literal['standby3'] = 'standby3'
    right_animation: Literal['standby4'] = 'standby4'
    blink_animation: str = Field(default='blink', pattern=r'^[a-z][a-z0-9_]{0,62}$')
    sleep_seconds: float = Field(default=3, ge=0, le=30)
    look_seconds: float = Field(default=0.1, ge=0, le=10)
    tilt_down: int = Field(default=100, ge=100, le=130)
    tilt_up: int = Field(default=120, ge=100, le=130)
    pan_center: int = Field(default=90, ge=30, le=150)
    pan_left: int = Field(default=108, ge=30, le=150)
    pan_right: int = Field(default=78, ge=30, le=150)
    move_ms: int = Field(default=1000, ge=600, le=5000)
    voice_enabled: bool = True
    face_tracking_enabled: bool = False
    vad_threshold: int = Field(default=550, ge=50, le=10000)
    silence_seconds: float = Field(default=0.9, ge=0.3, le=3)
    max_utterance_seconds: float = Field(default=15, ge=3, le=45)
    auto_boot: bool = False


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.chmod(temporary, 0o600)
    temporary.replace(path)


class ConfigStore:
    def __init__(self, path: Path):
        self.path = path
        self.settings = Settings.model_validate_json(path.read_text(encoding='utf-8')) if path.exists() else Settings()

    def update(self, changes: dict) -> Settings:
        values = self.settings.model_dump()
        values.update({k: v for k, v in changes.items() if k not in SECRETS or v})
        candidate = Settings.model_validate(values)
        save_json(self.path, candidate.model_dump())
        self.settings = candidate
        return candidate

    def public(self) -> dict:
        values = self.settings.model_dump()
        for key in SECRETS:
            values[key] = ''
            values[key + '_configured'] = bool(getattr(self.settings, key))
        return values

    def redact(self, text: str) -> str:
        for name in SECRETS:
            value = getattr(self.settings, name)
            if value:
                text = text.replace(value, '[已隐藏]')
        return text
