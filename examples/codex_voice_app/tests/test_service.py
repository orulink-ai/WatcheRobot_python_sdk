import asyncio
import base64
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from service import VoiceService


class Mic:
    closed = False
    def read(self, timeout):
        time.sleep(min(timeout, .01))
        if self.closed:
            raise RuntimeError('closed')
        return SimpleNamespace(data=b'\x00\x00' * 160)
    def close(self):
        self.closed = True


class Robot:
    def __init__(self):
        self.mic = Mic()
        self.opens = 0
        self.played = []
        self.microphone = SimpleNamespace(open_pcm=self.open)
        self.audio = SimpleNamespace(play_pcm=self.play, stop=lambda: None)
    def open(self, **kwargs):
        self.opens += 1
        if self.mic.closed:
            self.mic = Mic()
        return self.mic
    def play(self, pcm):
        self.played.append(pcm)
        return SimpleNamespace(wait=lambda timeout: None)


class Agent:
    def __init__(self, error=None):
        self.events = asyncio.Queue()
        self.thread_id = 'test'
        self.calls = []
        self.error = error
        self.closed = False
    async def start(self):
        if self.error:
            raise RuntimeError(self.error)
    async def request(self, method, params):
        self.calls.append((method, params))
    async def append_audio(self, pcm):
        self.calls.append(('thread/realtime/appendAudio', {'pcm': pcm}))
    async def text(self, text):
        self.calls.append(('turn/start', {'text': text}))
    async def interrupt(self):
        self.calls.append(('turn/interrupt', {}))
    async def close(self):
        self.closed = True


async def online():
    return True


def test_accepted_text_closes_real_microphone_before_task_not_just_upload_gating():
    async def run():
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent)
        original = agent.text
        async def text(value):
            assert robot.mic.closed
            return await original(value)
        agent.text = text
        await service.start()
        await service.text('转到30度，自然节奏，不拍照')
        assert not service.mic
        assert service.snapshot()['interactionPhase'] == 'thinking'
        assert service.snapshot()['notice'].startswith('收到')
        async with service._robot_exclusive():
            pass
        assert not service.mic, 'tool completion must not reopen a still-busy microphone'
        await service.stop()
    asyncio.run(run())


def test_voice_commit_closes_mic_before_dispatch_and_actual_actions_drive_phase():
    async def run():
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent)
        original = agent.text
        async def text(value):
            assert robot.mic.closed
            return await original(value)
        agent.text = text
        await service.start()
        assert service._commit_voice('观察一下两侧')
        for _ in range(100):
            if agent.calls:
                break
            await asyncio.sleep(.01)
        assert not service.mic
        service.robot_tools.tasks.append(dict(id='t', tool='aim_camera', status='moving', detail='转到30°', photos=[], turnId=''))
        assert service.snapshot()['interactionPhase'] == 'moving'
        assert service.snapshot()['interactionDetail'] == '转到30°'
        await service.stop()
    asyncio.run(run())


def test_latency_marks_are_elapsed_metadata_not_audio_or_request_content():
    async def run():
        now = [100.0]
        service = VoiceService(Robot(), online, Agent, clock=lambda: now[0])
        await service.start()
        await service.text('测试自然转头')
        now[0] = 101.25
        service._mark_latency('agentStarted')
        now[0] = 103.0
        service._mark_latency('firstTool')
        now[0] = 105.0
        service._mark_latency('firstTool')  # First signal remains the first.
        assert service.snapshot()['turnLatency'] == {'requestAccepted': 0.0, 'agentStarted': 1.25, 'firstTool': 3.0}
        await service.stop()
    asyncio.run(run())


def test_submission_failure_safely_ends_instead_of_leaving_closed_mic_and_thinking():
    async def run():
        agent = Agent()
        async def fail(_):
            raise ConnectionError('task RPC failed')
        agent.text = fail
        service = VoiceService(Robot(), online, lambda: agent)
        await service.start()
        with pytest.raises(ConnectionError):
            await service.text('转头')
        assert not service.state['connected']
        assert service.state['stopStatus'] == 'confirmed'
        assert service.state['stopReason'] == 'runtime-error'
    asyncio.run(run())


@pytest.mark.parametrize('status', ['failed', 'interrupted'])
def test_non_user_failed_turn_restores_listening_instead_of_leaving_thinking(status):
    async def run():
        agent = Agent()
        service = VoiceService(Robot(), online, lambda: agent)
        await service.start()
        await service.text('观察')
        service.current_turn = 't'
        await agent.events.put({'method': 'turn/completed', 'params': {'turn': {'id': 't', 'status': status}}})
        for _ in range(100):
            if not service.state['agentWorking'] and service.mic:
                break
            await asyncio.sleep(.01)
        assert service.state['connected'] and service.mic
        assert service.snapshot()['interactionPhase'] == 'listening'
        assert service.state['error']
        await service.stop()
    asyncio.run(run())


def test_fresh_voice_is_prepared_during_task_and_reused_for_final_answer():
    async def run():
        agent = Agent()
        prepared = []
        async def prepare():
            prepared.append(True)
            await asyncio.sleep(.01)
            agent.voice_epoch = 2
            return 2
        agent.prepare_speech = prepare
        agent.speak = lambda _: asyncio.sleep(0)
        service = VoiceService(Robot(), online, lambda: agent)
        await service.start()
        await service.text('转头')
        for _ in range(100):
            if service.prepared_voice_epoch == 2:
                break
            await asyncio.sleep(.01)
        assert len(prepared) == 1
        service.current_turn = 'one'
        await service._speak_answer('完成', 'one')
        assert len(prepared) == 1, 'final speech must not make a second fresh RTC'
        assert service.answer_voice_epoch == 2
        assert 'speechReady' in service.snapshot()['turnLatency']
        await service.stop()
    asyncio.run(run())


def test_continuous_audio_starts_device_playback_before_whole_utterance_silence():
    async def run():
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent, output_gap=10)
        await service.start()
        service.answer_turn = service.current_turn = 'one'
        service.state['agentWorking'] = True
        audio = {'data': base64.b64encode(b'\x01\x00' * 48000).decode(),
                 'sampleRate': 24000, 'numChannels': 1}
        await agent.events.put({'method': 'thread/realtime/outputAudio/delta', 'params': {'audio': audio}})
        for _ in range(100):
            if robot.played:
                break
            await asyncio.sleep(.01)
        assert robot.played == [b'\x01\x00' * 48000]
        assert service.state['agentWorking'], 'a chunk is not final media EOS'
        await service.stop()
    asyncio.run(run())


def test_text_is_a_real_agent_turn_and_failed_cleanup_is_visible():
    async def run():
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent)
        await service.start()
        await service.text('看看前面有什么')
        assert any(method == 'turn/start' for method, _ in agent.calls)
        assert service.snapshot()['messages'][-1]['content'] == '看看前面有什么'
        async def fail():
            raise RuntimeError('close failed')
        agent.close = fail
        await service.stop()
        assert not service.state['connected']
        assert 'close failed' in service.state['error']
    asyncio.run(run())


def test_auth_failure_leaves_device_microphone_closed():
    async def run():
        robot, agent = Robot(), Agent('401 Unauthorized')
        service = VoiceService(robot, online, lambda: agent)
        with pytest.raises(RuntimeError, match='401'):
            await service.start()
        assert robot.opens == 0
        assert agent.closed
        assert service.snapshot()['error'] == '401 Unauthorized'
        assert not service.snapshot()['connected']
    asyncio.run(run())


def test_offline_device_does_not_start_agent():
    async def run():
        async def offline():
            return False
        agent = Agent()
        service = VoiceService(Robot(), offline, lambda: agent)
        with pytest.raises(RuntimeError, match='机器人'):
            await service.start()
        assert not agent.calls
    asyncio.run(run())


def test_real_channel_path_upload_playback_transcripts_and_cleanup_with_fakes():
    async def run():
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent, output_gap=.02)
        await service.start()
        await asyncio.sleep(.06)
        assert any(method == 'thread/realtime/appendAudio' for method, _ in agent.calls)
        await service.mute(True)
        await asyncio.sleep(.03)
        count = len(agent.calls)
        await asyncio.sleep(.03)
        assert len(agent.calls) == count
        await service.mute(False)
        for text, method in [('你', 'delta'), ('你好', 'done')]:
            await agent.events.put({'method': f'thread/realtime/transcript/{method}',
                                    'params': {'role': 'user', 'delta': text, 'text': text}})
        pcm = b'\x00\x00' * 240
        service.answer_turn = 'test'
        await agent.events.put({'method': 'thread/realtime/outputAudio/delta', 'params': {
            'audio': {'data': base64.b64encode(pcm).decode(), 'sampleRate': 24000, 'numChannels': 1}}})
        await asyncio.sleep(.08)
        assert robot.played == [pcm]
        assert service.snapshot()['messages'][-1]['content'] == '你好'
        assert service.snapshot()['micFrames'] > 0
        assert service.snapshot()['outputBytes'] == len(pcm)
        await service.stop()
        assert robot.mic.closed and agent.closed
        assert not service.tasks
        assert not service.snapshot()['connected']
    asyncio.run(run())


def test_upstream_error_stops_session_and_releases_microphone():
    async def run():
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent)
        await service.start()
        await agent.events.put({'method': 'thread/realtime/error', 'params': {'message': 'lost connection'}})
        for _ in range(100):
            if robot.mic.closed and agent.closed:
                break
            await asyncio.sleep(.01)
        assert robot.mic.closed and agent.closed
        assert service.snapshot()['error'] == 'lost connection'
    asyncio.run(run())


def test_cancelling_start_waits_for_inflight_microphone_open_then_closes_it():
    async def run():
        robot, agent = Robot(), Agent()
        original = robot.open
        import threading
        entered = threading.Event()
        def slow_open(**kwargs):
            entered.set()
            time.sleep(.06)
            return original(**kwargs)
        robot.microphone.open_pcm = slow_open
        service = VoiceService(robot, online, lambda: agent)
        task = asyncio.create_task(service.start())
        while not entered.is_set():
            await asyncio.sleep(.001)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert robot.mic.closed and agent.closed
        assert not service.state['error']
    asyncio.run(run())


def test_half_duplex_releases_hardware_microphone_before_speaker_then_reopens():
    async def run():
        robot, agent = Robot(), Agent()
        original_play = robot.play
        def exclusive_play(pcm):
            assert robot.mic.closed, 'Firmware requires exclusive speaker ownership'
            return original_play(pcm)
        robot.audio.play_pcm = exclusive_play
        service = VoiceService(robot, online, lambda: agent, output_gap=.01)
        await service.start()
        service.answer_turn = 'test'
        await agent.events.put({'method': 'thread/realtime/outputAudio/delta', 'params': {
            'audio': {'data': base64.b64encode(b'\x01\x00' * 240).decode(),
                      'sampleRate': 24000, 'numChannels': 1}}})
        deadline = asyncio.get_running_loop().time() + 3
        while asyncio.get_running_loop().time() < deadline:
            if robot.opens == 2 and not service.state['speaking']:
                break
            await asyncio.sleep(.02)
        assert robot.played, service.snapshot()
        assert robot.opens == 2, service.snapshot()
        assert not robot.mic.closed
        assert service.state['connected'] and not service.state['error']
        await service.stop()
    asyncio.run(run())


def test_browser_disconnect_cannot_interrupt_stop_cleanup():
    async def run():
        robot, agent = Robot(), Agent()
        entered = asyncio.Event()
        async def slow_close():
            entered.set()
            await asyncio.sleep(.06)
            agent.closed = True
        agent.close = slow_close
        service = VoiceService(robot, online, lambda: agent)
        await service.start()
        stopping = asyncio.create_task(service.stop())
        await entered.wait()
        stopping.cancel()
        await asyncio.gather(stopping, return_exceptions=True)
        assert agent.closed and robot.mic.closed
        assert not service.state['connected']
        assert service.agent is None and service.mic is None
    asyncio.run(run())


def test_failed_stop_ends_upstream_and_blocks_restart_until_retry_confirmed():
    async def run():
        robot, agent = Robot(), Agent()
        robot.motion = SimpleNamespace(stop=lambda: (_ for _ in ()).throw(TimeoutError('motion offline')))
        service = VoiceService(robot, online, lambda: agent)
        await service.start()
        await service.cancel_actions()
        assert service.state['stopStatus'] == 'unconfirmed'
        assert not service.mic and agent.closed and not service.state['connected']
        with pytest.raises(RuntimeError, match='停止'):
            await service.start()
        robot.motion.stop = lambda: None
        await service.cancel_actions()
        assert service.state['stopStatus'] == 'confirmed'
    asyncio.run(run())


def test_hundred_session_cycles_release_all_owned_resources():
    async def run():
        robot = Robot()
        service = VoiceService(robot, online, Agent)
        for _ in range(100):
            await service.start()
            assert service.state['stopStatus'] == 'idle'
            agent = service.agent
            await service.stop()
            assert agent.closed and robot.mic.closed
            assert not service.tasks and service.agent is None and service.mic is None
            assert not any(service.robot_tools.permissions.values())
    asyncio.run(run())


def test_unsolicited_voice_frontend_audio_and_transcript_are_never_played():
    async def run():
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent, output_gap=.01)
        await service.start()
        await agent.events.put({'method': 'thread/realtime/outputAudio/delta', 'params': {'audio': {
            'data': base64.b64encode(b'\x01\x00' * 240).decode(), 'sampleRate': 24000, 'numChannels': 1}}})
        await agent.events.put({'method': 'thread/realtime/transcript/done', 'params': {'role': 'assistant', 'text': '不该自己回答'}})
        await asyncio.sleep(.05)
        assert not robot.played and not service.state['messages']
        await service.stop()
    asyncio.run(run())


def test_cross_controller_stop_is_immediate_during_blocked_start():
    async def run():
        robot, agent = Robot(), Agent()
        blocked = asyncio.Event()
        async def start():
            blocked.set()
            await asyncio.Event().wait()
        async def slow_close():
            await asyncio.sleep(.4)
            agent.closed = True
        agent.start, agent.close = start, slow_close
        stops = []
        robot.motion = SimpleNamespace(stop=lambda: stops.append('motion'))
        robot.audio.stop = lambda: stops.append('audio')
        service = VoiceService(robot, online, lambda: agent)
        starting = asyncio.create_task(service.start())
        await blocked.wait()
        stopping = asyncio.create_task(service.cancel_actions())
        await asyncio.sleep(.05)
        assert set(stops) == {'motion', 'audio'}
        assert service.state['stopStatus'] == 'stopping'
        await stopping
        await asyncio.gather(starting, return_exceptions=True)
        assert robot.opens == 0 and not service.state['connected']
    asyncio.run(run())


def test_done_without_confirmed_pcm_never_completes_observation():
    async def run():
        service = VoiceService(Robot(), online, Agent, output_gap=.01)
        service.robot_tools.tasks = [dict(id='one', turnId='one', status='answering', photos=[])]
        service.answer_turn, service.answer_done = 'one', True
        service._finish_answer()
        assert service.robot_tools.tasks[0]['status'] != 'completed'
    asyncio.run(run())


def test_previous_rtc_epoch_audio_and_done_cannot_complete_new_answer():
    async def run():
        robot, agent = Robot(), Agent()
        agent.voice_epoch = 2
        service = VoiceService(robot, online, lambda: agent, output_gap=.01)
        await service.start()
        service.answer_turn, service.answer_voice_epoch = 'new', 2
        service.robot_tools.tasks = [dict(id='new', turnId='new', status='answering', photos=[])]
        pcm = dict(data=base64.b64encode(b'\x01\x00' * 240).decode(), sampleRate=24000, numChannels=1)
        await agent.events.put({'method': 'thread/realtime/outputAudio/delta', 'params': {'audio': pcm, 'voiceEpoch': 1}})
        await agent.events.put({'method': 'thread/realtime/transcript/done', 'params': {'role': 'assistant', 'text': '旧回答', 'voiceEpoch': 1}})
        await asyncio.sleep(.03)
        assert not robot.played and not service.answer_done
        await service.stop()
    asyncio.run(run())


def test_answer_completion_waits_for_final_transcript_and_only_updates_matching_turn():
    async def run():
        now = [0]
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent, output_gap=.02, clock=lambda: now[0])
        await service.start()
        service.current_turn = service.answer_turn = 'new'
        service.state['agentWorking'] = True
        service.robot_tools.tasks = [
            dict(id='old', turnId='old', status='answering', photos=[]),
            dict(id='new', turnId='new', status='answering', photos=[])]
        pcm = b'\x01\x00' * 240
        async def audio():
            await agent.events.put({'method': 'thread/realtime/outputAudio/delta', 'params': {'audio': {
                'data': base64.b64encode(pcm).decode(), 'sampleRate': 24000, 'numChannels': 1}}})
        await audio()
        await asyncio.sleep(.4)
        assert service.robot_tools.tasks[1]['status'] == 'answering'
        await audio()
        await agent.events.put({'method': 'thread/realtime/transcript/done', 'params': {'role': 'assistant', 'text': '两段回答'}})
        await asyncio.sleep(.45)
        assert service.robot_tools.tasks[1]['status'] == 'answering'
        now[0] = 31
        service._finish_answer()
        assert service.robot_tools.tasks[1]['status'] == 'completed'
        assert service.robot_tools.tasks[0]['status'] == 'answering'
        await service.stop()
    asyncio.run(run())


def test_stop_invalidates_a_start_queued_before_it():
    async def run():
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent)
        await service.lock.acquire()
        starting = asyncio.create_task(service.start())
        await asyncio.sleep(0)
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(.02)
        service.lock.release()
        results = await asyncio.gather(starting, stopping, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        assert robot.opens == 0
        assert not service.state['connected']
    asyncio.run(run())


def test_device_diagnostic_cannot_bypass_unconfirmed_stop():
    async def run():
        robot = Robot()
        service = VoiceService(robot, online, Agent)
        service.state['stopStatus'] = 'unconfirmed'
        service.robot_tools.faulted = True
        with pytest.raises(RuntimeError, match='停止'):
            await service.test_device(.01)
        assert robot.opens == 0 and not robot.played
    asyncio.run(run())


def test_successful_explicit_stop_retry_does_not_keep_old_cleanup_failures():
    async def run():
        robot = Robot()
        service = VoiceService(robot, online, Agent)
        service.state['stopStatus'] = 'unconfirmed'
        service.state['deviceOnline'] = True
        service.robot_tools.faulted = True
        service.shutdown_failures.append('previous cleanup compensation failed')
        await service.stop()
        assert service.state['stopStatus'] == 'confirmed'
        assert not service.robot_tools.faulted
        assert not service.shutdown_failures
    asyncio.run(run())


def test_no_pcm_timeout_fails_observation_and_releases_session():
    async def run():
        from turn_deadline import TurnDeadline
        now = [0]
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent, output_gap=.02,
                               clock=lambda: now[0], watchdog_interval=.02)
        await service.start()
        service.current_turn = service.answer_turn = 'one'
        service.answer_done = True
        service.answer_done_at = asyncio.get_running_loop().time() - 3
        service.robot_tools.tasks = [dict(id='one', turnId='one', status='answering', photos=[])]
        deadline = service.turn_deadline = TurnDeadline(0)
        deadline.begin_voice(0)
        deadline.voice_requested(0)
        service.turn_watchdog = service._spawn(lambda: service._watch_turn('one', deadline))
        now[0] = 3
        service._finish_answer()
        await asyncio.sleep(.03)
        assert service.state['connected'] and not service.state['error']
        now[0] = 46
        for _ in range(100):
            if agent.closed:
                break
            await asyncio.sleep(.02)
        assert service.robot_tools.tasks[0]['status'] == 'failed'
        assert '未收到' in service.state['error']
        assert agent.closed and robot.mic.closed
        assert not service.state['connected'] and not service.tasks
    asyncio.run(run())


def test_completed_turn_without_final_answer_fails_and_releases_session():
    async def run():
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent, output_gap=.01)
        await service.start()
        service.current_turn = 'one'
        service.robot_tools.tasks = [dict(id='one', turnId='one', status='analyzing', photos=[])]
        await agent.events.put({'method': 'turn/completed', 'params': {'turn': {
            'id': 'one', 'status': 'completed', 'items': []}}})
        for _ in range(100):
            if agent.closed:
                break
            await asyncio.sleep(.01)
        assert service.robot_tools.tasks[0]['status'] == 'failed'
        assert '最终回答' in service.state['error']
        assert agent.closed and robot.mic.closed and not service.tasks
        assert not service.state['connected']
    asyncio.run(run())


def test_long_task_answer_stays_complete_after_short_tts_transcript():
    async def run():
        robot, agent = Robot(), Agent()
        spoken = []
        async def speak(text):
            spoken.append(text)
        agent.speak = speak
        service = VoiceService(robot, online, lambda: agent, output_gap=.01)
        await service.start()
        service.current_turn = 'one'
        full = '真实任务回答，' * 120
        await agent.events.put({'method': 'turn/completed', 'params': {'turn': {
            'id': 'one', 'status': 'completed', 'items': [
                {'type': 'agentMessage', 'phase': 'final_answer', 'text': full}]}}})
        for _ in range(100):
            if spoken:
                break
            await asyncio.sleep(.01)
        assert spoken and len(spoken[0].encode('utf-8')) <= 500
        await agent.events.put({'method': 'thread/realtime/transcript/done', 'params': {
            'role': 'assistant', 'text': '短节选'}})
        await asyncio.sleep(.03)
        assert service.state['messages'][-1]['content'] == full
        assert service.state['lastSpokenTranscript'] == '短节选'
        await service.stop()
    asyncio.run(run())


def test_second_cancel_cannot_orphan_a_late_microphone_lease():
    async def run():
        import threading
        robot, agent = Robot(), Agent()
        entered, release = threading.Event(), threading.Event()
        original = robot.open
        def blocking_open(**kwargs):
            entered.set()
            release.wait(1)
            return original(**kwargs)
        robot.microphone.open_pcm = blocking_open
        service = VoiceService(robot, online, lambda: agent)
        starting = asyncio.create_task(service.start())
        await asyncio.to_thread(entered.wait, 1)
        starting.cancel()
        await asyncio.sleep(.02)
        starting.cancel()
        release.set()
        await asyncio.gather(starting, return_exceptions=True)
        await asyncio.sleep(.03)
        assert robot.opens == 1 and robot.mic.closed
        assert agent.closed and not service.mic and not service.state['connected']
    asyncio.run(run())


def test_start_cancel_and_external_stop_do_not_erase_first_close_failure():
    async def run():
        robot, agent = Robot(), Agent()
        blocked = asyncio.Event()
        async def start():
            blocked.set()
            await asyncio.Event().wait()
        async def close():
            raise RuntimeError('upstream resource still alive')
        agent.start, agent.close = start, close
        service = VoiceService(robot, online, lambda: agent)
        starting = asyncio.create_task(service.start())
        await blocked.wait()
        await service.stop()
        await asyncio.gather(starting, return_exceptions=True)
        assert service.state['stopStatus'] == 'unconfirmed'
        assert service.agent is agent
        with pytest.raises(RuntimeError, match='停止'):
            await service.start()
        async def recovered_close():
            agent.closed = True
        agent.close = recovered_close
        await service.stop()
        assert service.state['stopStatus'] == 'confirmed' and service.agent is None
    asyncio.run(run())


def test_unmute_queued_before_stop_cannot_reopen_the_microphone():
    async def run():
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent)
        await service.start()
        await service.mute(True)
        opens = robot.opens
        await service.media_lock.acquire()
        unmuting = asyncio.create_task(service.mute(False))
        await asyncio.sleep(0)
        await service.stop()
        service.media_lock.release()
        await asyncio.gather(unmuting, return_exceptions=True)
        assert robot.opens == opens and not service.mic and not service.mic_task
        assert not service.state['connected']
    asyncio.run(run())


def test_stop_drains_unmute_that_has_already_started_opening_a_lease():
    async def run():
        import threading
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent)
        await service.start()
        await service.mute(True)
        entered, release = threading.Event(), threading.Event()
        original = robot.open
        def blocking_open(**kwargs):
            entered.set()
            release.wait(1)
            return original(**kwargs)
        robot.microphone.open_pcm = blocking_open
        unmuting = asyncio.create_task(service.mute(False))
        await asyncio.to_thread(entered.wait, 1)
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(.02)
        assert service.state['stopStatus'] != 'confirmed'
        release.set()
        await asyncio.gather(unmuting, stopping, return_exceptions=True)
        assert robot.mic.closed and service.mic is None
        assert service.state['stopStatus'] == 'confirmed'
    asyncio.run(run())


def test_client_handoff_and_transcript_done_start_one_real_task_not_two():
    async def run():
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent, output_gap=.01)
        await service.start()
        await agent.events.put({'method': 'thread/realtime/transcript/delta', 'params': {
            'role': 'user', 'delta': '看看前面有什么'}})
        await agent.events.put({'method': 'local/delegation', 'params': {
            'id': 'handoff-one', 'text': '看看前面有什么'}})
        await asyncio.sleep(.03)
        assert [method for method, _ in agent.calls].count('turn/start') == 1
        await agent.events.put({'method': 'thread/realtime/transcript/done', 'params': {
            'role': 'user', 'text': '看看前面有什么'}})
        await agent.events.put({'method': 'local/delegation', 'params': {
            'id': 'handoff-one', 'text': '看看前面有什么'}})
        await asyncio.sleep(.05)
        assert [method for method, _ in agent.calls].count('turn/start') == 1
        assert [message['content'] for message in service.state['messages']] == ['看看前面有什么']
        await service.stop()
    asyncio.run(run())


def test_blank_background_failure_is_visible_and_has_a_stop_reason():
    async def run():
        service = VoiceService(Robot(), online, Agent)
        await service.start()
        async def fail():
            raise TimeoutError()
        await service._guard(fail)
        for _ in range(100):
            if service.state['stopStatus'] == 'confirmed':
                break
            await asyncio.sleep(.01)
        assert 'TimeoutError' in service.state['error']
        assert service.state['stopReason'] == 'runtime-error'
        assert service.state['lastFailure']['type'] == 'TimeoutError'
    asyncio.run(run())


def test_unknown_microphone_open_outcome_cannot_be_confirmed_by_an_empty_retry():
    async def run():
        robot = Robot()
        def lost_ack(**kwargs):
            robot.open(**kwargs)  # Device applied open, but caller never receives a lease.
            raise TimeoutError()
        robot.microphone.open_pcm = lost_ack
        service = VoiceService(robot, online, Agent)
        with pytest.raises(TimeoutError):
            await service.start()
        assert service.state['stopStatus'] == 'unconfirmed'
        await service.stop()
        assert service.state['stopStatus'] == 'unconfirmed'
        with pytest.raises(RuntimeError, match='停止'):
            await service.start()
    asyncio.run(run())


def test_muted_late_voice_cannot_create_a_task_or_a_completed_user_message():
    async def run():
        robot, agent = Robot(), Agent()
        service = VoiceService(robot, online, lambda: agent, output_gap=.01)
        await service.start()
        await service.mute(True)
        for method, params in [
            ('thread/realtime/transcript/delta', {'role': 'user', 'delta': '看看前面'}),
            ('local/delegation', {'id': 'late', 'text': '看看前面'}),
            ('thread/realtime/transcript/done', {'role': 'user', 'text': '看看前面'}),
        ]:
            await agent.events.put({'method': method, 'params': params})
        await asyncio.sleep(.04)
        assert not any(method == 'turn/start' for method, _ in agent.calls)
        assert not service.state['messages']
        await service.stop()
    asyncio.run(run())


def test_same_epoch_new_utterance_can_repeat_after_busy_rejection_or_failed_turn():
    async def run():
        robot, agent = Robot(), Agent()
        agent.voice_epoch = 1
        service = VoiceService(robot, online, lambda: agent, output_gap=.01)
        await service.start()
        service.state['agentWorking'] = True
        async def utterance(identifier):
            await agent.events.put({'method': 'thread/realtime/transcript/delta', 'params': {
                'role': 'user', 'delta': '看看前面', 'voiceEpoch': 1}})
            await agent.events.put({'method': 'local/delegation', 'params': {
                'id': identifier, 'text': '看看前面', 'voiceEpoch': 1}})
            await agent.events.put({'method': 'thread/realtime/transcript/done', 'params': {
                'role': 'user', 'text': '看看前面', 'voiceEpoch': 1}})
            await asyncio.sleep(.03)
        await utterance('busy')
        service.state['agentWorking'] = False
        await utterance('retry-one')
        assert [m for m, _ in agent.calls].count('turn/start') == 1
        service.current_turn = 'failed'
        await agent.events.put({'method': 'turn/completed', 'params': {'turn': {
            'id': 'failed', 'status': 'failed', 'error': {'message': 'task failed'}}}})
        await asyncio.sleep(.02)
        await utterance('retry-two')
        assert [m for m, _ in agent.calls].count('turn/start') == 2
        await service.stop()
    asyncio.run(run())


@pytest.mark.parametrize('status,expected', [('failed', 'failed'), ('interrupted', 'cancelled')])
def test_late_photo_attachment_cannot_revive_ended_task(status, expected):
    async def run():
        agent = Agent()
        service = VoiceService(Robot(), online, lambda: agent, output_gap=.01)
        await service.start()
        service.current_turn = 'one'
        task = dict(id='photo-task', callId='call', turnId='one', status='attaching', photos=[])
        service.robot_tools.tasks = [task]
        await agent.events.put({'method': 'turn/completed', 'params': {'turn': {
            'id': 'one', 'status': status, 'error': {'message': 'failed'}}}})
        await agent.events.put({'method': 'local/toolImagesAttached', 'params': {
            'turnId': 'one', 'callId': 'call', 'photos': []}})
        await asyncio.sleep(.04)
        assert task['status'] == expected
        await service.stop()
    asyncio.run(run())


def test_rejected_photo_event_fails_only_a_live_matching_attachment():
    async def run():
        service = VoiceService(Robot(), online, Agent, output_gap=.01)
        await service.start()
        service.current_turn = 'one'
        tasks = [dict(id=str(i), callId=str(i), turnId='one', status=status, photos=[])
                 for i, status in enumerate(['attaching', 'completed'])]
        service.robot_tools.tasks = tasks
        for i in range(2):
            await service.agent.events.put({'method': 'local/toolImagesRejected', 'params': {
                'turnId': 'one', 'callId': str(i), 'message': '图像被拒绝'}})
        await asyncio.sleep(.04)
        assert [t['status'] for t in tasks] == ['failed', 'completed']
        await service.stop()
    asyncio.run(run())


def test_done_before_handoff_is_the_same_utterance_and_does_not_execute_twice():
    async def run():
        agent = Agent()
        service = VoiceService(Robot(), online, lambda: agent, output_gap=.01)
        await service.start()
        await agent.events.put({'method': 'thread/realtime/transcript/delta', 'params': {
            'role': 'user', 'delta': '看看前面'}})
        await agent.events.put({'method': 'thread/realtime/transcript/done', 'params': {
            'role': 'user', 'text': '看看前面'}})
        await asyncio.sleep(.03)
        service.state['agentWorking'] = False  # Fast completion must not permit the twin signal.
        await agent.events.put({'method': 'local/delegation', 'params': {
            'id': 'same-speech', 'text': '看看前面'}})
        await asyncio.sleep(.03)
        assert [m for m, _ in agent.calls].count('turn/start') == 1
        assert len(service.state['messages']) == 1
        await service.stop()
    asyncio.run(run())


@pytest.mark.parametrize('handoff_first', [True, False])
@pytest.mark.parametrize('still_busy', [True, False])
def test_refined_transcript_and_handoff_commit_one_utterance_without_false_busy_error(handoff_first, still_busy):
    async def run():
        agent = Agent()
        agent.voice_epoch = 1
        service = VoiceService(Robot(), online, lambda: agent, output_gap=.01)
        await service.start()
        await agent.events.put({'method': 'thread/realtime/transcript/delta', 'params': {
            'role': 'user', 'delta': '自动验收', 'voiceEpoch': 1}})
        handoff = {'method': 'local/delegation', 'params': {
            'id': 'speech-one', 'text': '自动验收,编号 937,请拍当前水泥的照片。', 'voiceEpoch': 1}}
        done = {'method': 'thread/realtime/transcript/done', 'params': {
            'role': 'user', 'text': '自动验收，编号九三七。请拍当前视野的照片。', 'voiceEpoch': 1}}
        first, second = (handoff, done) if handoff_first else (done, handoff)
        await agent.events.put(first)
        await asyncio.sleep(.03)
        service.state['agentWorking'] = still_busy
        await agent.events.put(second)
        await asyncio.sleep(.03)
        assert [method for method, _ in agent.calls].count('turn/start') == 1
        assert len(service.state['messages']) == 1
        assert not service.state['error']
        # An actual next utterance remains distinct, even with identical words.
        service.state['agentWorking'] = False
        await agent.events.put({'method': 'thread/realtime/transcript/delta', 'params': {
            'role': 'user', 'delta': '自动验收', 'voiceEpoch': 1}})
        await agent.events.put({'method': 'local/delegation', 'params': {
            **handoff['params'], 'id': 'speech-two'}})
        await asyncio.sleep(.03)
        assert [method for method, _ in agent.calls].count('turn/start') == 2
        await service.stop()
    asyncio.run(run())


def test_watchdog_does_not_claim_safe_stop_before_cleanup_confirmation():
    async def run():
        now = [0]
        robot = Robot()
        robot.audio.stop = lambda: time.sleep(.15)
        service = VoiceService(robot, online, Agent, output_gap=.01,
                               clock=lambda: now[0], watchdog_interval=.005)
        await service.start()
        await service.agent.events.put({'method': 'turn/started', 'params': {'turn': {'id': 'one'}}})
        while service.turn_deadline is None:
            await asyncio.sleep(.001)
        now[0] = 91
        for _ in range(100):
            if service.state['stopStatus'] == 'stopping':
                break
            await asyncio.sleep(.001)
        assert service.state['stopStatus'] == 'stopping'
        assert '正在结束' in service.state['error']
        assert '已安全结束' not in service.state['error']
        await service.stop()
        assert service.state['stopStatus'] == 'confirmed'
    asyncio.run(run())


def test_active_response_and_device_playback_survive_the_old_total_90_second_limit():
    async def run():
        import threading
        now = [0]
        entered, release = threading.Event(), threading.Event()
        robot, agent = Robot(), Agent()
        def wait(timeout):
            entered.set()
            release.wait(5)
        robot.audio.play_pcm = lambda pcm: SimpleNamespace(wait=wait)
        async def speak(text):
            pass
        agent.speak = speak
        service = VoiceService(robot, online, lambda: agent, output_gap=.01,
                               clock=lambda: now[0], watchdog_interval=.005)
        snapshots = asyncio.Queue()
        service.subscribers.add(snapshots)
        await service.start()
        await service.mute(True)
        await agent.events.put({'method': 'turn/started', 'params': {'turn': {'id': 'one'}}})
        await asyncio.sleep(.02)
        now[0] = 80
        await agent.events.put({'method': 'item/agentMessage/delta', 'params': {'turnId': 'one', 'delta': '正在分析照片'}})
        await asyncio.sleep(.02)
        now[0] = 95
        await asyncio.sleep(.02)
        assert service.state['connected'] and not service.state['error']
        await agent.events.put({'method': 'turn/completed', 'params': {'turn': {
            'id': 'one', 'status': 'completed', 'items': [{'type': 'agentMessage', 'text': '实拍照片里有办公桌。'}]}}})
        await asyncio.sleep(.02)
        assert service.turn_deadline.phase == 'awaiting-audio'
        now[0] = 120
        await agent.events.put({'method': 'thread/realtime/outputAudio/delta', 'params': {'audio': {
            'data': base64.b64encode(b'\x01\x00'*240).decode(), 'sampleRate': 24000, 'numChannels': 1}}})
        assert await asyncio.to_thread(entered.wait, 2)
        now[0] = 200  # Playback remains bounded by the SDK wait, not an idle clock.
        await asyncio.sleep(.02)
        assert service.state['connected'] and service.state['speaking'] and not service.state['error']
        await agent.events.put({'method': 'thread/realtime/transcript/done', 'params': {
            'role': 'assistant', 'text': '实拍照片里有办公桌。'}})
        release.set()
        async def wait_completed():
            while (await snapshots.get()).get('speechStatus') != 'completed':
                pass
        await asyncio.wait_for(wait_completed(), 2)
        assert service.state['speechStatus'] == 'completed', (service.state, service.answer_done,
            service.answer_received_bytes, service.answer_played_bytes, service.turn_deadline.phase)
        assert service.turn_deadline.phase == 'done' and not service.state['error']
        await service.stop()
        assert service.state['stopStatus'] == 'confirmed'
    asyncio.run(run())


def test_voice_metadata_is_retained_after_session_cleanup():
    async def run():
        agent = Agent()
        agent.diagnostics = lambda: {'current': {'receivedFrames': 42, 'silentFrames': 42}}
        service = VoiceService(Robot(), online, lambda: agent)
        await service.start()
        await service.stop()
        assert service.agent is None
        assert service.snapshot()['voiceDiagnostics']['current']['receivedFrames'] == 42
    asyncio.run(run())


def test_transport_heartbeat_and_old_turn_delta_are_not_task_progress():
    async def run():
        now = [0]
        agent = Agent()
        service = VoiceService(Robot(), online, lambda: agent, clock=lambda: now[0],
                               watchdog_interval=.02, output_gap=.02)
        await service.start()
        await agent.events.put({'method': 'turn/started', 'params': {'turn': {'id': 'one'}}})
        await asyncio.sleep(.03)
        now[0] = 80
        for method, params in [('remoteControl/status/changed', {}),
                               ('item/agentMessage/delta', {'turnId': 'old', 'delta': '旧回答'})]:
            await agent.events.put({'method': method, 'params': params})
        service.publish()  # A connection/microphone snapshot is not task progress either.
        await asyncio.sleep(.03)
        assert service.turn_deadline.last_progress == 0
        now[0] = 91
        await asyncio.sleep(.06)
        assert service.state['stopReason'] == 'runtime-error'
        await service.stop()
        assert service.state['stopStatus'] == 'confirmed'
    asyncio.run(run())


@pytest.mark.parametrize('previous_error', ['', '真正的设备错误'])
def test_busy_voice_notice_does_not_fail_current_turn_or_erase_real_failure(previous_error):
    async def run():
        agent = Agent()
        service = VoiceService(Robot(), online, lambda: agent)
        await service.start()
        service.state.update(agentWorking=True, error=previous_error)
        assert not service._commit_voice('另一段后续请求')
        assert '未执行' in service.state.get('notice', '')
        assert service.state['error'] == previous_error
        assert not any(method == 'turn/start' for method, _ in agent.calls)
        service.state['agentWorking'] = False
        await service.text('这是明确的新任务')
        assert service.state['notice'].startswith('收到')
        await service.stop()
    asyncio.run(run())


@pytest.mark.parametrize('second_piece_fails', [True, False])
@pytest.mark.parametrize('done_before_audio', [True, False])
def test_done_cannot_complete_first_piece_or_hide_late_playback_failure(second_piece_fails, done_before_audio):
    async def run():
        now = [0]
        robot, agent = Robot(), Agent()
        jobs = []
        def play(pcm):
            jobs.append(pcm)
            def wait(timeout):
                if len(jobs) == 2 and second_piece_fails:
                    raise RuntimeError('second device playback failed')
            return SimpleNamespace(wait=wait)
        robot.audio.play_pcm = play
        async def speak(text):
            pass
        agent.speak = speak
        service = VoiceService(robot, online, lambda: agent, clock=lambda: now[0],
                               output_gap=.02, watchdog_interval=.02)
        snapshots = asyncio.Queue()
        service.subscribers.add(snapshots)
        async def wait_snapshot(predicate):
            while True:
                if predicate(await snapshots.get()):
                    return
        await service.start()
        await service.mute(True)
        await agent.events.put({'method': 'turn/started', 'params': {'turn': {'id': 'one'}}})
        await agent.events.put({'method': 'turn/completed', 'params': {'turn': {
            'id': 'one', 'status': 'completed', 'items': [{'type': 'agentMessage', 'text': '测试回答'}]}}})
        await asyncio.wait_for(wait_snapshot(lambda s:s.get('speechStatus')=='requested'), 2)
        done = {'method': 'thread/realtime/transcript/done', 'params': {
            'role': 'assistant', 'text': '测试回答'}}
        if done_before_audio:
            await agent.events.put(done)
            await asyncio.sleep(.03)
        event = {'method': 'thread/realtime/outputAudio/delta', 'params': {'audio': {
            'data': base64.b64encode(b'\x01\x00'*240).decode(), 'sampleRate': 24000, 'numChannels': 1}}}
        await agent.events.put(event)
        await asyncio.wait_for(wait_snapshot(lambda s:s.get('outputBytes')==480 and not s.get('speaking')), 2)
        if not done_before_audio:
            await agent.events.put(done)
            await asyncio.wait_for(wait_snapshot(lambda s:s.get('lastSpokenTranscript')=='测试回答'), 2)
        now[0] = 10  # Well past batching gap, but inside the conservative reorder window.
        service._finish_answer()
        assert service.state['speechStatus'] != 'completed'
        await agent.events.put(event)
        if second_piece_fails:
            await asyncio.wait_for(wait_snapshot(lambda s:not s.get('connected') and s.get('stopStatus')=='confirmed'), 2)
            assert 'second device playback failed' in service.state['error']
            assert service.state['speechStatus'] != 'completed'
        else:
            await asyncio.wait_for(wait_snapshot(lambda s:s.get('outputBytes')==960 and not s.get('speaking')), 2)
            now[0] = 20
            service._finish_answer()
            assert service.state['speechStatus'] != 'completed'
            now[0] = 41
            service._finish_answer()
            assert service.state['speechStatus'] == 'completed'
            await service.stop()
    asyncio.run(run())


@pytest.mark.parametrize('now,can_complete', [(179.999, True), (180, False), (180.001, False)])
@pytest.mark.parametrize('pending_playback', [False, True])
def test_voice_hard_boundary_cannot_be_hidden_by_completion(now, can_complete, pending_playback):
    from turn_deadline import TurnDeadline
    service = VoiceService(Robot(), online, Agent, clock=lambda: now)
    service.answer_turn, service.answer_done = 'one', True
    service.answer_received_bytes = service.answer_played_bytes = 480
    service.last_audio_at = 0
    service.state['speaking'] = pending_playback
    deadline = service.turn_deadline = TurnDeadline(0)
    deadline.begin_voice(0)
    deadline.audio_progress(0)
    deadline.playback_started(0)
    if not pending_playback:
        deadline.playback_finished(0)
    service._finish_answer()
    assert (service.state.get('speechStatus') == 'completed') == (can_complete and not pending_playback)
    if now >= 180:
        assert '3分钟' in deadline.error(now)


def test_finish_waits_for_already_queued_pcm_before_sealing_turn():
    async def run():
        from turn_deadline import TurnDeadline
        service = VoiceService(Robot(), online, Agent, clock=lambda: 31)
        service.agent = Agent()
        service.answer_turn, service.answer_done = 'one', True
        service.state['agentWorking'] = True
        service.answer_received_bytes = service.answer_played_bytes = 480
        deadline = service.turn_deadline = TurnDeadline(0)
        deadline.begin_voice(0)
        deadline.audio_progress(0)
        audio = {'data': base64.b64encode(b'\x01\x00' * 240).decode(), 'sampleRate': 24000, 'numChannels': 1}
        service.agent.events.put_nowait({'method': 'thread/realtime/outputAudio/delta', 'params': {'audio': audio}})
        service._finish_answer()
        assert service.state.get('speechStatus') != 'completed'
        assert service.answer_turn == 'one' and deadline.phase != 'done'
        # Once consumed, pending PCM must still be drained through device playback.
        event = service.agent.events.get_nowait()
        service.output.add(event['params']['audio'])
        service._finish_answer()
        assert service.state.get('speechStatus') != 'completed'
        assert service.state['agentWorking']
    asyncio.run(run())


@pytest.mark.parametrize('getter_inflight', [False, True])
def test_watchdog_does_not_soft_timeout_pcm_pending_dispatch_but_keeps_hard_limit(getter_inflight):
    async def run():
        from turn_deadline import TurnDeadline
        now = [31]
        service = VoiceService(Robot(), online, Agent, clock=lambda: now[0], watchdog_interval=.02)
        service.agent = Agent()
        service.state.update(connected=True, agentWorking=True)
        service.current_turn = service.answer_turn = 'one'
        service.answer_done = True
        service.answer_received_bytes = service.answer_played_bytes = 480
        deadline = service.turn_deadline = TurnDeadline(0)
        deadline.begin_voice(0)
        deadline.audio_progress(0)
        service.agent.events.put_nowait({'method': 'thread/realtime/outputAudio/delta', 'params': {}})
        if getter_inflight:
            # Model Python 3.10 wait_for's completed getter before parent dispatch.
            await service._receive_agent_event()
            assert service.agent.events.empty()
        watch = asyncio.create_task(service._watch_turn('one', deadline))
        try:
            await asyncio.sleep(.03)
            assert not watch.done()
            assert service.state.get('speechStatus') != 'completed'
            assert deadline.last_progress == 0  # Dispatch gate does not renew progress.
            now[0] = 180
            with pytest.raises(RuntimeError, match='3分钟'):
                await asyncio.wait_for(watch, 1)
        finally:
            watch.cancel()
            await asyncio.gather(watch, return_exceptions=True)
    asyncio.run(run())


@pytest.mark.parametrize('previous_error', ['', '真实播放故障'])
def test_busy_text_is_explicit_rejection_without_mutating_active_task(previous_error):
    async def run():
        from errors import BusyRequest
        agent = Agent()
        service = VoiceService(Robot(), online, lambda: agent)
        await service.start()
        service.state.update(agentWorking=True, error=previous_error)
        before = service.snapshot()
        with pytest.raises(BusyRequest):
            await service.text('追加任务')
        assert service.snapshot() == before
        assert not any(method == 'turn/start' for method, _ in agent.calls)
        await service.stop()
    asyncio.run(run())


def test_ui_answer_length_limit_explicitly_reports_truncation():
    async def run():
        agent = Agent()
        async def speak(text):
            pass
        agent.speak = speak
        service = VoiceService(Robot(), online, lambda: agent, output_gap=.01)
        await service.start()
        service.current_turn = 'one'
        await agent.events.put({'method': 'turn/completed', 'params': {'turn': {
            'id': 'one', 'status': 'completed', 'items': [
                {'type': 'agentMessage', 'phase': 'final_answer', 'text': '答' * 16001}]}}})
        await asyncio.sleep(.03)
        answer = service.state['messages'][-1]['content']
        assert len(answer) <= 16000 and '截断' in answer
        await service.stop()
    asyncio.run(run())


def test_late_unmute_ack_loss_is_rechecked_after_owner_drain_before_confirmation():
    async def run():
        import threading
        robot = Robot()
        service = VoiceService(robot, online, Agent)
        await service.start()
        await service.mute(True)
        entered, release = threading.Event(), threading.Event()
        def lost_late_ack(**kwargs):
            entered.set()
            release.wait(1)
            robot.open(**kwargs)
            raise TimeoutError('late open ACK lost')
        robot.microphone.open_pcm = lost_late_ack
        opening = asyncio.create_task(service.mute(False))
        await asyncio.to_thread(entered.wait, 1)
        stopping = asyncio.create_task(service.stop())
        await asyncio.sleep(.02)
        assert service.state['stopStatus'] == 'stopping'
        assert not service.mic_open_uncertain
        release.set()
        await asyncio.gather(opening, stopping, return_exceptions=True)
        assert service.mic_open_uncertain
        assert service.state['stopStatus'] == 'unconfirmed'
        with pytest.raises(RuntimeError, match='停止'):
            await service.start()
        with pytest.raises(RuntimeError, match='停止'):
            await service.test_device(.01)
        await service.stop()
        assert service.state['stopStatus'] == 'unconfirmed'
    asyncio.run(run())


@pytest.mark.parametrize('requested_stop', [True, False])
def test_device_playback_cancel_receipt_is_normal_only_during_requested_stop(requested_stop):
    async def run():
        import threading
        from watcherobot.errors import JobCancelledError
        robot = Robot()
        entered, aborted = threading.Event(), threading.Event()
        def wait(timeout):
            entered.set()
            aborted.wait(1)
            raise JobCancelledError(1, state='was cancelled', reason='aborted')
        robot.audio.play_pcm = lambda pcm: SimpleNamespace(wait=wait)
        robot.audio.stop = aborted.set
        # Force receipt to arrive before the cleanup owner cancels the playback task.
        robot.motion = SimpleNamespace(stop=lambda: time.sleep(.04))
        service = VoiceService(robot, online, Agent, output_gap=.01)
        await service.start()
        await service.mute(True)
        service.answer_turn = 'one'
        await service.agent.events.put({'method':'thread/realtime/outputAudio/delta','params': {'audio': {
            'data':base64.b64encode(b'\x01\x00'*240).decode(),'sampleRate':24000,'numChannels':1}}})
        await asyncio.to_thread(entered.wait, 1)
        if requested_stop:
            await service.stop()
            assert service.state['stopStatus'] == 'confirmed'
            assert not service.state['error'] and not service.shutdown_failures
        else:
            aborted.set()
            for _ in range(100):
                if service.state['stopStatus'] == 'confirmed':
                    break
                await asyncio.sleep(.01)
            assert 'cancelled' in service.state['error']
            assert not service.state['connected']
    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['cancelled','timeout','failed','connection'])
def test_cancel_before_sdk_playback_receipt_only_accepts_exact_cancelled_outcome(outcome):
    async def run():
        import threading
        from watcherobot.errors import JobCancelledError, JobFailedError
        robot = Robot()
        entered, aborted = threading.Event(), threading.Event()
        errors = {'cancelled':JobCancelledError(1,state='was cancelled'),
                  'timeout':TimeoutError('playback stop receipt timed out'),
                  'failed':JobFailedError(1,state='failed',error_code=1),
                  'connection':ConnectionError('lost playback receipt')}
        def wait(timeout):
            entered.set()
            aborted.wait(1)
            raise errors[outcome]
        stops = []
        def stop():
            stops.append(True)
            # First physical stop succeeds, but its completion receipt is delayed
            # until the playback task's cancellation-compensation stop.
            if len(stops) >= 2:
                aborted.set()
        robot.audio.play_pcm = lambda pcm: SimpleNamespace(wait=wait)
        robot.audio.stop = stop
        service = VoiceService(robot, online, Agent, output_gap=.01)
        await service.start()
        await service.mute(True)
        service.answer_turn = 'one'
        await service.agent.events.put({'method':'thread/realtime/outputAudio/delta','params': {'audio': {
            'data':base64.b64encode(b'\x01\x00'*240).decode(),'sampleRate':24000,'numChannels':1}}})
        await asyncio.to_thread(entered.wait,1)
        await service.stop()
        assert len(stops) >= 2
        if outcome == 'cancelled':
            assert service.state['stopStatus'] == 'confirmed' and not service.state['error']
        else:
            assert service.state['stopStatus'] == 'unconfirmed'
            assert service.shutdown_failures and str(errors[outcome]) in service.state['error']
            with pytest.raises(RuntimeError,match='停止'):
                await service.start()
    asyncio.run(run())
