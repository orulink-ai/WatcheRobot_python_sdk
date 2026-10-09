"""Optional discovery; results never grant access or change saved configuration."""
from __future__ import annotations

from typing import Any

import httpx

from .configuration import ModelConfig
from .contracts import Catalog, CatalogItem, VoiceError
from .providers.http import HTTPProvider


async def discover(kind: str, config: ModelConfig, *, voices: bool = False,
                   client: httpx.AsyncClient | None = None) -> Catalog:
    if kind not in ('asr', 'llm', 'tts') or (voices and kind != 'tts'):
        raise ValueError('请选择 asr/llm/tts；音色查询仅适用于 tts')
    provider = config.provider
    if provider in ('ark', 'volcengine'):
        return Catalog(note='此适配器未实现管理面查询；账号资源查询可能需要额外 AK/SK 管理凭据，请手动填写模型、资源或音色 ID。')
    if provider == 'aliyun':
        return Catalog(note='NLS 识别模型在阿里云项目中选择，使用 AppKey；不提供通用模型目录。')
    supported = ((provider in ('qwen', 'openai_chat') and kind == 'llm') or
                 (provider == 'deepgram' and kind in ('asr', 'tts')) or
                 (provider == 'elevenlabs' and kind == 'tts') or
                 (provider in ('cartesia', 'minimaxi') and kind == 'tts' and voices))
    if not supported:
        return Catalog()
    http = HTTPProvider(config, client=client)
    items: dict[str, CatalogItem] = {}
    scope = 'public' if provider == 'deepgram' or not voices else 'account'
    params: dict[str, Any] = {}
    seen: set[str] = set()
    try:
        for _ in range(100):
            headers = http.bearer()
            method = 'GET'
            body = None
            if provider in ('qwen', 'openai_chat'):
                url = http.endpoint('https://dashscope.aliyuncs.com/compatible-mode/v1', '/models')
            elif provider == 'deepgram':
                # ASR base_url is a websocket endpoint, not the management API.
                url = 'https://api.deepgram.com/v1/models'
                headers = {'Authorization': 'Token ' + config.credentials.get('api_key', '')} if config.credentials.get('api_key') else {}
            elif provider == 'elevenlabs':
                base = config.base_url or 'https://api.elevenlabs.io/v1'
                url = base.rstrip('/') + ('/models' if not voices else '/voices')
                if voices and not config.base_url:
                    url = 'https://api.elevenlabs.io/v2/voices'
                headers = {'xi-api-key': config.credentials.get('api_key', '')}
            elif provider == 'cartesia':
                url = http.endpoint('https://api.cartesia.ai', '/voices')
                headers['Cartesia-Version'] = str(config.options.get('api_version', '2026-08-14'))
                params['limit'] = 100
            else:
                url = http.endpoint('https://api.minimax.cn/v1', '/get_voice')
                method, body = 'POST', {'voice_type': 'all'}
            response = await http.client.request(method, url, headers=headers, params=params, json=body)
            http.check(response, '模型/音色查询')
            data = response.json()
            next_token = None
            if provider == 'deepgram':
                rows = data['stt' if kind == 'asr' else 'tts']
                id_key, name_key = 'canonical_name', 'name'
            elif provider == 'elevenlabs':
                rows = data['voices'] if voices else [r for r in data if r.get('can_do_text_to_speech', True)]
                id_key, name_key = 'voice_id' if voices else 'model_id', 'name'
                if voices and data.get('has_more'):
                    next_token = data.get('next_page_token')
                    if not next_token:
                        raise VoiceError('音色目录分页响应不完整')
                    params['page_token'] = next_token
            elif provider == 'minimaxi':
                if data.get('base_resp', {}).get('status_code') != 0:
                    raise VoiceError('MiniMax 音色查询失败')
                rows = [r for group in ('system_voice', 'voice_cloning', 'voice_generation') for r in data.get(group, [])]
                id_key, name_key = 'voice_id', 'voice_name'
            else:
                rows = data['data']
                id_key, name_key = 'id', 'name'
                if provider == 'cartesia' and data.get('has_more'):
                    next_token = data.get('next_page')
                    if not next_token:
                        raise VoiceError('音色目录分页响应不完整')
                    params['starting_after'] = next_token
            for row in rows:
                identity = row[id_key]
                if not isinstance(identity, str) or not identity:
                    raise VoiceError('目录 ID 格式无效')
                items[identity] = CatalogItem(identity, str(row.get(name_key) or identity))
            if not next_token:
                return Catalog(tuple(items.values()), True, scope, '候选目录不代表调用权限或模型/音色组合兼容；请实际调用验证。')
            if next_token in seen:
                raise VoiceError('目录分页游标重复')
            seen.add(next_token)
        raise VoiceError('目录超过 100 页，请缩小查询范围')
    except VoiceError:
        raise
    except Exception:
        raise VoiceError('模型/音色查询失败；可继续手动填写 ID') from None
    finally:
        await http.close()
