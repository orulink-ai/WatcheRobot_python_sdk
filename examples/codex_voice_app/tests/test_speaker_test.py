import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from test_continuous_service import service, DevicePeer
from fastapi.testclient import TestClient
from web_server import create_web_app


def test_fixed_test_uses_native_device_channel_without_starting_codex(monkeypatch):
    import continuous_service as module
    monkeypatch.setattr(module, 'load_test_audio', lambda: b'\x00\x01' * 960)

    class PlayingPeer(DevicePeer):
        async def start(self):
            self.playing = asyncio.create_task(self.play())
        async def play(self):
            while True:
                await self.track.recv()
        async def close(self):
            self.playing.cancel()
            await asyncio.gather(self.playing, return_exceptions=True)
            await super().close()

    async def run():
        svc, robot, agent = service()
        svc.device_peer_factory = PlayingPeer
        await svc.test_speaker()
        assert agent.calls == []
        assert robot.opens == 0 and not robot.played
        assert svc.state['speakerTest']['status'] == 'finished'
        assert svc.state['stopStatus'] == 'confirmed'
        assert not svc.state['busy'] and svc.device_peer is None
        await svc.start()  # Diagnostic does not poison the next conversation.
        await svc.stop()
    asyncio.run(run())


def test_fixed_test_rejects_active_conversation_without_touching_it():
    async def run():
        svc, _, _ = service()
        await svc.start()
        peer = svc.device_peer
        with pytest.raises(Exception, match='结束.*对话'):
            await svc.test_speaker()
        assert svc.state['connected'] and svc.device_peer is peer and not peer.closed
        await svc.stop()
    asyncio.run(run())


def test_fixed_test_can_be_cancelled_during_start_and_cleans_up(monkeypatch):
    import continuous_service as module
    monkeypatch.setattr(module, 'load_test_audio', lambda: b'\x00\x01' * 960)

    class SlowPeer(DevicePeer):
        async def start(self):
            await asyncio.Event().wait()

    async def run():
        svc, _, agent = service()
        svc.device_peer_factory = SlowPeer
        testing = asyncio.create_task(svc.test_speaker())
        while svc.device_peer is None:
            await asyncio.sleep(0)
        await svc.cancel_actions()
        await asyncio.gather(testing, return_exceptions=True)
        assert svc.state['speakerTest']['status'] == 'cancelled'
        assert svc.state['stopStatus'] == 'confirmed'
        assert not svc.state['busy'] and svc.device_peer is None
        assert agent.calls == []
    asyncio.run(run())


def test_reference_route_serves_only_bundled_audio_and_rejects_cross_origin(tmp_path):
    svc, _, _ = service()
    with TestClient(create_web_app(svc, tmp_path), base_url='http://127.0.0.1') as client:
        response = client.get('/api/speaker-reference.wav')
        assert response.status_code == 200
        assert response.content.startswith(b'RIFF')
        assert response.headers['cross-origin-resource-policy'] == 'same-origin'
        assert client.get('/api/speaker-reference.wav', headers={'sec-fetch-site': 'cross-site'}).status_code == 403


def test_reference_pcm_matches_previous_fixed_ab_source():
    import hashlib
    from speaker_test import load_test_audio
    assert hashlib.sha256(load_test_audio()).hexdigest() == '90db15ab702ff9687a47e5eeb51e12c0599fae625e5cded6db240e5096ff1134'


def test_native_service_refuses_legacy_record_replay_before_acquiring_resources():
    async def run():
        svc, robot, agent = service()
        with pytest.raises(ValueError, match='固定语音'):
            await svc.test_device()
        assert not robot.opens and svc.device_peer is None and not agent.calls
        assert not svc.state['busy']
    asyncio.run(run())


def test_missing_stop_receipt_remains_unsafe_and_blocks_next_conversation(monkeypatch):
    import continuous_service as module
    monkeypatch.setattr(module, 'load_test_audio', lambda: b'\x00\x01' * 960)

    class FailedClosePeer(DevicePeer):
        async def start(self):
            raise RuntimeError('test start failure')
        async def close(self):
            raise RuntimeError('missing device stopped receipt')

    async def run():
        svc, _, agent = service()
        svc.device_peer_factory = FailedClosePeer
        with pytest.raises(RuntimeError, match='test start failure'):
            await svc.test_speaker()
        assert svc.state['speakerTest']['status'] == 'failed'
        assert svc.state['stopStatus'] == 'unconfirmed'
        assert not svc.state['busy'] and svc.device_peer is not None
        with pytest.raises(RuntimeError, match='停止未确认'):
            await svc.start()
        assert agent.calls == []
    asyncio.run(run())


def test_double_cancel_failed_release_can_be_retried(monkeypatch):
    import continuous_service as module
    monkeypatch.setattr(module, 'load_test_audio', lambda: b'\x00\x01' * 960)

    class RetryPeer(DevicePeer):
        can_close = False
        close_calls = 0
        async def start(self):
            await asyncio.Event().wait()
        async def close(self):
            self.close_calls += 1
            await asyncio.sleep(0)
            if not self.can_close:
                raise RuntimeError('missing device stopped receipt')
            await super().close()

    async def run():
        svc, _, _ = service()
        svc.device_peer_factory = RetryPeer
        testing = asyncio.create_task(svc.test_speaker())
        while svc.device_peer is None:
            await asyncio.sleep(0)
        peer = svc.device_peer
        testing.cancel()  # Web action cancellation.
        while svc.cleanup_task is None:
            await asyncio.sleep(0)
        await svc.cancel_actions()  # Stop cancels the lifecycle owner again.
        await asyncio.gather(testing, return_exceptions=True)
        assert svc.lifecycle_task is None
        assert svc.state['stopStatus'] == 'unconfirmed'
        previous = peer.close_calls
        peer.can_close = True
        await svc.stop()
        assert peer.close_calls > previous
        assert svc.state['stopStatus'] == 'confirmed' and svc.device_peer is None
        assert svc.state['speakerTest']['status'] == 'cancelled'
    asyncio.run(run())
