import sys
import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).parents[1]))
from web_server import create_web_app
from service import VoiceService
from test_service import Agent, Robot, online


def test_browser_receives_live_state_and_controls_managed_session(tmp_path):
    service = VoiceService(Robot(), online, Agent)
    with TestClient(create_web_app(service, tmp_path), base_url='http://127.0.0.1') as client:
        with client.websocket_connect('ws://127.0.0.1/api/session', headers={'origin': 'http://127.0.0.1'}) as ws:
            assert ws.receive_json()['type'] == 'snapshot'
            ws.send_json({'type': 'start'})
            for _ in range(10):
                snapshot = ws.receive_json()
                if snapshot['connected']:
                    break
            assert snapshot['connected']
            ws.send_json({'type': 'stop'})
            for _ in range(10):
                if not ws.receive_json()['connected']:
                    break
            else:
                pytest.fail('session did not stop')


def test_cross_origin_cannot_open_microphone_and_dns_rebinding_is_rejected(tmp_path):
    robot = Robot()
    with TestClient(create_web_app(VoiceService(robot, online, Agent), tmp_path),
                    base_url='http://127.0.0.1') as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('ws://127.0.0.1/api/session', headers={'origin': 'https://untrusted.example'}):
                pass
        assert client.get('/api/state', headers={'host': 'untrusted.example'}).status_code == 400
    assert robot.opens == 0


@pytest.mark.parametrize('action_pending', [False, True])
@pytest.mark.parametrize('previous_error', ['', '设备真实故障'])
def test_busy_commands_only_publish_notice_and_keep_real_failure(tmp_path, action_pending, previous_error):
    class PendingService(VoiceService):
        async def text(self, text):
            if not action_pending:
                return await super().text(text)
            self.state.update(agentWorking=True)
            self.publish()
            await asyncio.Event().wait()
    agent = Agent()
    service = PendingService(Robot(), online, lambda: agent)
    with TestClient(create_web_app(service, tmp_path), base_url='http://127.0.0.1') as client:
        with client.websocket_connect('ws://127.0.0.1/api/session', headers={'origin': 'http://127.0.0.1'}) as ws:
            ws.receive_json()
            ws.send_json({'type': 'start'})
            while not ws.receive_json()['connected']:
                pass
            service.state['error'] = previous_error
            ws.send_json({'type': 'text', 'text': '第一个任务'})
            while not ws.receive_json()['agentWorking']:
                pass
            ws.send_json({'type': 'text', 'text': '第二个任务'})
            while True:
                snapshot = ws.receive_json()
                if snapshot.get('notice'):
                    break
            assert snapshot['error'] == previous_error
            assert snapshot['agentWorking'] and snapshot['connected']
            assert snapshot['permissions'] == {name: False for name in ['camera', 'upload', 'motion', 'lights']}
            assert not snapshot['tasks'] and not snapshot['evidence']
            assert not snapshot.get('lastFailure')
            assert len([call for call in agent.calls if call[0] == 'turn/start']) == (0 if action_pending else 1)
