"""语音应用组装入口：在自己的项目中修改此文件。"""
import json
from pathlib import Path

from watcherobot.application import ApplicationContext
from watcherobot.voice.configuration import credential_directory, load_configuration
from watcherobot.voice.providers import ProviderRegistry
from watcherobot.voice.runtime import VoiceApplication


def create_registry() -> ProviderRegistry:
    """注册或替换 ASR / LLM / TTS 适配器的扩展点。"""
    registry = ProviderRegistry.builtin()
    # 例如：registry.register_llm("my_provider", MyLLM)
    return registry


async def main(root: Path) -> None:
    app_id = json.loads((root / 'app.json').read_text(encoding='utf-8'))['id']
    config = load_configuration(root, credentials_dir=credential_directory(root, app_id))
    registry = create_registry()
    # 设备由 robot pair 配对并由 Runtime 管理；应用复用注入通道。
    async with ApplicationContext.from_environment() as app:
        await VoiceApplication(app, config, registry).run()
