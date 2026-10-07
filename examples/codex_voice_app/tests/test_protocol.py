import base64
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("voice_protocol", Path(__file__).parents[1] / "protocol.py")
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)


def test_audio_request_matches_installed_codex_schema():
    request = protocol.audio_params("thread-1", b"\x01\x00" * 960)
    assert request == {
        "threadId": "thread-1",
        "audio": {"data": base64.b64encode(b"\x01\x00" * 960).decode(),
                  "sampleRate": 16000, "numChannels": 1, "samplesPerChannel": 960},
    }


@pytest.mark.parametrize("pcm", [b"", b"x", b"xx" * 16001])
def test_invalid_or_oversized_audio_is_rejected(pcm):
    with pytest.raises(ValueError):
        protocol.audio_params("thread-1", pcm)


def test_start_uses_native_audio_without_model_override():
    assert protocol.start_params("t") == {
        "threadId": "t", "outputModality": "audio", "transport": {"type": "websocket"},
        'includeStartupContext': False, 'clientManagedHandoffs': True,
        'prompt': protocol.VOICE_INSTRUCTIONS,
    }


def test_remote_rpc_errors_are_explicit():
    with pytest.raises(RuntimeError, match="permission denied"):
        protocol.rpc_result({"id": 1, "error": {"message": "permission denied"}})
def test_native_chatgpt_webrtc_selects_current_server_protocol():
    params = protocol.start_params('thread', sdp='v=0\r\n')
    assert params['transport'] == {'type': 'webrtc', 'sdp': 'v=0\r\n'}
    assert params['version'] == 'v3'
    assert params['includeStartupContext'] is False
    assert params['clientManagedHandoffs'] is True


def test_continuous_frontend_can_respond_and_acknowledge_without_task_completion():
    params = protocol.start_params('voice', sdp='v=0\r\n', realtime=True)
    assert params['delegationAckFiller'] is True
    assert params['prompt'] == protocol.REALTIME_INSTRUCTIONS
    assert '不自行回答' not in params['prompt']
    assert params['clientManagedHandoffs'] is True
