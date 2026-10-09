import sys
from pathlib import Path
import wave
import asyncio

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))


def test_probe_accepts_bounded_16khz_mono_pcm_wav(tmp_path):
    from probe import load_input_wav
    path = tmp_path / 'speech.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\x01\x00' * 320)
    assert load_input_wav(path) == b'\x01\x00' * 320


def test_probe_rejects_incompatible_wav_without_network(tmp_path):
    from probe import load_input_wav
    path = tmp_path / 'stereo.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\x00\x00' * 640)
    with pytest.raises(ValueError, match='16 kHz mono'):
        load_input_wav(path)


def test_probe_drives_task_agent_and_speaks_its_actual_answer(monkeypatch, capsys):
    import probe
    class Agent:
        def __init__(self):
            self.events = asyncio.Queue()
            self.calls = []
        async def start(self):
            self.events.put_nowait({'method': 'thread/realtime/transcript/done', 'params': {'role': 'user', 'text': '你好'}})
        async def append_audio(self, pcm):
            pass
        async def text(self, text):
            self.calls.append(('text', text))
            self.events.put_nowait({'method': 'turn/completed', 'params': {'turn': {'status': 'completed', 'items': [{'type': 'agentMessage', 'phase': 'final_answer', 'text': '真实模型回答'}]}}})
        async def speak(self, text):
            self.calls.append(('speak', text))
            self.events.put_nowait({'method': 'thread/realtime/outputAudio/delta', 'params': {}})
        async def close(self):
            pass
    agent = Agent()
    monkeypatch.setattr(probe, 'CodexAgent', lambda: agent)
    asyncio.run(probe.main(b'\x00\x00' * 320))
    assert agent.calls == [('text', '你好'), ('speak', '真实模型回答')]
    assert 'REAL_AUDIO_RECEIVED' in capsys.readouterr().out
