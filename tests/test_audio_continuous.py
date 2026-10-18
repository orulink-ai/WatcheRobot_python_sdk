import asyncio
import hashlib
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from watcherobot.application.transport import DaemonApplicationTransport
from watcherobot.desktop_session import DesktopRobotSession
from watcherobot.errors import WatcheRobotError
from watcherobot.job import JobState
from watcherobot.protocol import FLAG_FIRST, FLAG_LAST, parse_wspk
from watcherobot.robot import WatcheRobot
from watcherobot.voice.configuration import ConversationConfig
from watcherobot.voice.device import SDKVoiceDevice


def resolved(value=None):
    result = Future()
    result.set_result(value)
    return result


class Transport:
    capabilities = ('audio.stream', 'audio.stream.continuous.v1')
    command_timeout = .1
    def __init__(self): self.commands, self.writes = [], []
    def set_callbacks(self, *callbacks): pass
    def send_command(self, kind, data, timeout=None):
        self.commands.append((kind, data))
        return {'data': {}}
    def open_audio_stream(self, stream_id): return resolved()
    def close_audio_stream(self, stream_id): return resolved()
    def write_audio_stream(self, pcm, *, stream_id, last=False):
        self.writes.append((pcm, stream_id, last))
        return resolved()


def test_continuous_begin_has_real_contract_and_hash_before_eos():
    transport = Transport()
    robot = WatcheRobot(transport)
    playback = robot.audio.open_stream(max_bytes=100)
    kind, data = transport.commands[0]
    assert kind == 'ctrl.audio.stream.begin'
    assert data == dict(stream_id=playback.id, mode='continuous', max_bytes=100,
                        sample_rate_hz=24000, channels=1, sample_width_bytes=2)
    playback.write(b'\1\0' * 20).result()
    assert playback.expected_sha256 is None
    playback.finish().result()
    assert playback.expected_sha256 == hashlib.sha256(b'\1\0' * 20).hexdigest()
    assert transport.writes == [(b'\1\0' * 20, playback.id, False), (b'', playback.id, True)]
    robot._on_audio_buffer_status({'stream_id': playback.id, 'reason': 'complete', 'audio_sha256': playback.expected_sha256})
    assert playback.wait(.1).state is JobState.COMPLETED
    assert len(transport.commands) == 1


@pytest.mark.parametrize('sha', [None, 'incorrect'])
def test_continuous_terminal_requires_actual_sha(sha):
    robot = WatcheRobot(Transport())
    playback = robot.audio.open_stream()
    playback.write(b'\1\0').result()
    playback.finish().result()
    robot._on_audio_buffer_status({'stream_id': playback.id, 'reason': 'complete', 'audio_sha256': sha})
    assert playback.state is JobState.FAILED


def test_continuous_budget_empty_and_concurrent_playback_rejected():
    transport = Transport()
    robot = WatcheRobot(transport)
    for budget in (0, 3, 4194306, True):
        with pytest.raises(ValueError): robot.audio.open_stream(max_bytes=budget)
    playback = robot.audio.open_stream(max_bytes=4)
    with pytest.raises(ValueError): playback.finish()
    with pytest.raises(ValueError): playback.write(b'x')
    playback.write(b'\1\0' * 2).result()
    with pytest.raises(ValueError): playback.write(b'\1\0')
    with pytest.raises(WatcheRobotError): robot.audio.play_pcm(b'\1\0')
    with pytest.raises(WatcheRobotError): robot.audio.play('beep')
    with pytest.raises(WatcheRobotError): robot.audio.open_stream()
    assert len(transport.commands) == 1
    robot.audio.stop()
    assert transport.commands[-1][0] == 'ctrl.audio.stop'


@pytest.mark.parametrize('transport_type', [DaemonApplicationTransport, DesktopRobotSession])
def test_continuous_transport_single_first_last_sequence_and_credit(transport_type):
    async def run():
        transport = transport_type() if transport_type is DaemonApplicationTransport else transport_type('ws://unused')
        sent = []
        async def send(*args): sent.append(parse_wspk(args[-1]))
        transport._send = send
        await transport._open_continuous_audio(7)
        await transport._write_continuous_audio(b'a\0' * 6000, 7, False)
        # 3 PCM packets exhausted 3 of the initial 4 credits; LAST consumes the final credit.
        await transport._write_continuous_audio(b'', 7, True)
        assert [packet.sequence for packet in sent] == [0, 1, 2, 3]
        assert [packet.flags for packet in sent] == [FLAG_FIRST, 0, 0, FLAG_LAST]
        assert b''.join(packet.payload for packet in sent) == b'a\0' * 6000
        with pytest.raises(WatcheRobotError): await transport._write_continuous_audio(b'a\0', 7, False)
    asyncio.run(run())


def test_device_stream_consumes_iterator_on_caller_loop_and_success_needs_no_stop():
    async def run():
        loop = asyncio.get_running_loop()
        events = []
        playback = SimpleNamespace(write=lambda pcm: (events.append(pcm), resolved())[1],
                                   finish=lambda: resolved(), wait=lambda timeout: None, expected_duration_seconds=0)
        robot = SimpleNamespace(capabilities=('audio.stream.continuous.v1',), audio=SimpleNamespace(
            open_stream=lambda **kw: playback, stop=lambda: events.append('stop')))
        device = SDKVoiceDevice(robot, ConversationConfig())
        async def frames():
            assert asyncio.get_running_loop() is loop
            yield b'\1\0'
            yield b'\2\0'
        assert device.supports_continuous_playback
        await device.play_stream(frames())
        assert events == [b'\1\0', b'\2\0']
        assert not device._playing
    asyncio.run(run())


def test_device_stream_source_failure_retains_owner_until_confirmed_stop():
    async def run():
        attempts = []
        def stop():
            attempts.append(1)
            if len(attempts) == 1: raise TimeoutError('stop failed')
        playback = SimpleNamespace(write=lambda pcm: resolved())
        robot = SimpleNamespace(capabilities=('audio.stream.continuous.v1',), audio=SimpleNamespace(open_stream=lambda **kw: playback, stop=stop))
        device = SDKVoiceDevice(robot, ConversationConfig())
        async def frames():
            yield b'\1\0'
            raise RuntimeError('cloud failure')
        with pytest.raises(TimeoutError): await device.play_stream(frames())
        assert device._playing
        await device.stop()
        assert not device._playing and len(attempts) == 2
    asyncio.run(run())


@pytest.mark.parametrize('transport_type', [DaemonApplicationTransport, DesktopRobotSession])
def test_continuous_backpressure_blocks_until_credit_and_stop_wakes_writer(transport_type):
    async def run():
        transport = transport_type(command_timeout=1) if transport_type is DaemonApplicationTransport else transport_type('ws://unused', command_timeout=1)
        sent = []
        async def send(*args): sent.append(parse_wspk(args[-1]))
        transport._send = send
        await transport._open_continuous_audio(8)
        writer = asyncio.create_task(transport._write_continuous_audio(b'a\0' * (4096 * 3), 8, False))
        for _ in range(30):
            if len(sent) == 4: break
            await asyncio.sleep(.001)
        assert len(sent) == 4 and not writer.done()
        # Closing is loop-local, invalidates the stream and wakes its credit wait.
        await transport._close_continuous_audio(8)
        with pytest.raises(WatcheRobotError): await writer
        await transport._open_continuous_audio(9)
        await transport._write_continuous_audio(b'b\0' * 2048, 9, False)
        assert sent[-1].stream_id == 9 and sent[-1].flags == FLAG_FIRST
    asyncio.run(run())


def test_writer_does_not_buffer_unbounded_concurrent_writes():
    transport = Transport()
    pending = Future()
    transport.write_audio_stream = lambda *args, **kwargs: pending
    playback = WatcheRobot(transport).audio.open_stream()
    assert playback.write(b'a\0') is pending
    with pytest.raises(ValueError, match='previous audio write'): playback.write(b'b\0')
    with pytest.raises(ValueError, match='previous audio write'): playback.finish()
    pending.set_result(None)
    playback.finish()
    assert playback.total_bytes == 2


def test_device_stream_cancel_joins_writer_closes_source_and_confirms_stop():
    async def run():
        events = []
        future = Future()
        entered = asyncio.Event()
        def write(pcm):
            entered.set()
            return future
        playback = SimpleNamespace(write=write)
        robot = SimpleNamespace(capabilities=('audio.stream.continuous.v1',), audio=SimpleNamespace(
            open_stream=lambda **kw: playback, stop=lambda: events.append('stop')))
        device = SDKVoiceDevice(robot, ConversationConfig())
        async def frames():
            try:
                yield b'\1\0'
                events.append('unwanted_next_frame')
            finally:
                events.append('source_closed')
        task = asyncio.create_task(device.play_stream(frames()))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert events == ['source_closed', 'stop']
        assert future.cancelled() and not device._playing
    asyncio.run(run())


def test_device_rejects_second_play_without_stopping_current_stream():
    async def run():
        stopped = []
        robot = SimpleNamespace(audio=SimpleNamespace(stop=lambda: stopped.append(1)))
        device = SDKVoiceDevice(robot, ConversationConfig())
        device._playing = True
        with pytest.raises(Exception, match='已有音频'): await device.play(b'a\0')
        assert device._playing and not stopped
    asyncio.run(run())


def test_continuous_sender_failure_blocks_replacement_until_stop_confirmation():
    transport = Transport()
    failed = Future()
    failed.set_exception(RuntimeError('send failed'))
    transport.write_audio_stream = lambda *args, **kwargs: failed
    robot = WatcheRobot(transport)
    playback = robot.audio.open_stream()
    with pytest.raises(RuntimeError): playback.write(b'a\0').result()
    assert playback.state is JobState.FAILED
    with pytest.raises(WatcheRobotError): robot.audio.play_pcm(b'a\0')
    playback.cancel()
    assert transport.commands[-1][0] == 'ctrl.audio.stop'
    assert robot._audio_playback is None


def test_real_transport_keeps_async_source_on_caller_loop_and_packets_on_transport_loop():
    import threading
    transport = DaemonApplicationTransport()
    loop = asyncio.new_event_loop()
    transport._loop = loop
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    transport.send_command = lambda *args, **kwargs: {'data': {}}
    robot = WatcheRobot(transport)
    transport.capabilities = ('audio.stream.continuous.v1',)
    pcm = bytearray()
    sent = []
    async def send(channel, packet):
        assert threading.get_ident() == thread.ident
        frame = parse_wspk(packet)
        sent.append(frame)
        pcm.extend(frame.payload)
        if frame.flags & FLAG_LAST:
            robot._on_audio_buffer_status({'stream_id': frame.stream_id, 'reason': 'complete',
                                          'audio_sha256': hashlib.sha256(pcm).hexdigest()})
    transport._send = send
    async def run():
        caller = asyncio.get_running_loop()
        device = SDKVoiceDevice(robot, ConversationConfig())
        async def frames():
            assert asyncio.get_running_loop() is caller
            yield b'a\0' * 100
            await asyncio.sleep(.001)
            assert asyncio.get_running_loop() is caller
            yield b'b\0' * 100
        await device.play_stream(frames())
        assert bytes(pcm) == b'a\0' * 100 + b'b\0' * 100
        assert device.last_playback_stats['uploaded_bytes'] == 400
        assert 0 <= device.last_playback_stats['first_frame_ms'] <= device.last_playback_stats['complete_ms']
        assert [frame.flags for frame in sent] == [FLAG_FIRST, FLAG_LAST]
    try:
        asyncio.run(run())
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(2)
        loop.close()


@pytest.mark.parametrize('transport_type', [DaemonApplicationTransport, DesktopRobotSession])
def test_late_old_sender_cleanup_does_not_clear_replacement_owner(transport_type):
    async def run():
        transport = transport_type() if transport_type is DaemonApplicationTransport else transport_type('ws://unused')
        entered, release = asyncio.Event(), asyncio.Event()
        async def send(*args):
            frame = parse_wspk(args[-1])
            if frame.stream_id == 11:
                entered.set()
                await release.wait()
        transport._send = send
        await transport._open_continuous_audio(11)
        old = asyncio.create_task(transport._write_continuous_audio(b'a\0' * 3000, 11, False))
        await entered.wait()
        await transport._close_continuous_audio(11)
        await transport._open_continuous_audio(12)
        release.set()
        with pytest.raises(WatcheRobotError): await old
        await transport._write_continuous_audio(b'b\0', 12, False)
        assert transport._continuous_audio_sender.stream_id == 12
    asyncio.run(run())


@pytest.mark.parametrize('transport_type', [DaemonApplicationTransport, DesktopRobotSession])
def test_tiny_writes_coalesce_across_calls_before_claiming_device_credit(transport_type):
    async def run():
        transport = transport_type(command_timeout=.05) if transport_type is DaemonApplicationTransport else transport_type('ws://unused', command_timeout=.05)
        sent = []
        async def send(*args): sent.append(parse_wspk(args[-1]))
        transport._send = send
        await transport._open_continuous_audio(20)
        # Real firmware grants slots, not byte capacity: 100 tiny writes must not
        # spend 100 slots before the byte prefill threshold can start playback.
        for _ in range(100):
            await transport._write_continuous_audio(b'a\0', 20, False)
        assert sent == []
        await transport._write_continuous_audio(b'b\0' * 2048, 20, False)
        assert len(sent) == 1 and len(sent[0].payload) == 4096
        # A short tail at the sentence boundary stays staged for the next sentence.
        await transport._write_continuous_audio(b'c\0' * 100, 20, False)
        assert len(sent) == 1
        await transport._write_continuous_audio(b'', 20, True)
        assert [len(packet.payload) for packet in sent] == [4096, 400, 0]
        assert b''.join(packet.payload for packet in sent) == b'a\0' * 100 + b'b\0' * 2048 + b'c\0' * 100
        assert [packet.flags for packet in sent] == [FLAG_FIRST, 0, FLAG_LAST]
    asyncio.run(run())


@pytest.mark.parametrize('transport_type', [DaemonApplicationTransport, DesktopRobotSession])
def test_slot_credit_and_byte_prefill_model_completes_many_small_writes(transport_type):
    async def run():
        transport = transport_type(command_timeout=.05) if transport_type is DaemonApplicationTransport else transport_type('ws://unused', command_timeout=.05)
        received = bytearray()
        packets = []
        pending = 0
        playing = False
        async def send(*args):
            nonlocal pending, playing
            frame = parse_wspk(args[-1])
            packets.append(frame)
            received.extend(frame.payload)
            if frame.payload:
                pending += 1
            # Real device slots are consumed independently of payload bytes. Until
            # enough byte prefill or EOS exists, those slots cannot be drained.
            playing = playing or len(received) >= 4 * 4096 or bool(frame.flags & FLAG_LAST)
            if playing:
                pending = 0
            await transport._update_audio_flow({
                'stream_id': 21, 'reason': 'buffering',
                'expected_rx_seq': frame.sequence + 1,
                'pending_frames': pending, 'free_frames': 16 - pending,
                'queue_depth': 16, 'start_buffer_frames': 4, 'playing': playing,
            })
        transport._send = send
        await transport._open_continuous_audio(21)
        chunks = [b'a\0'] * 100 + [b'b\0' * 1000] * 20
        for chunk in chunks:
            await transport._write_continuous_audio(chunk, 21, False)
        await transport._write_continuous_audio(b'', 21, True)
        assert received == b''.join(chunks)
        assert all(len(packet.payload) == 4096 for packet in packets[:-2])
        assert 0 < len(packets[-2].payload) < 4096 and packets[-1].payload == b''
        assert packets[0].flags == FLAG_FIRST and packets[-1].flags == FLAG_LAST
    asyncio.run(run())


@pytest.mark.parametrize('fault', ['dropped_frames', 'drop_timeout_frames', 'seq_gap_frames'])
def test_continuous_complete_rejects_dropped_frames_even_when_received_sha_matches(fault):
    transport = Transport()
    robot = WatcheRobot(transport)
    playback = robot.audio.open_stream()
    playback.write(b'a\0').result()
    playback.finish().result()
    robot._on_audio_buffer_status({'stream_id': playback.id, 'reason': 'complete',
                                  'audio_sha256': playback.expected_sha256, fault: 1})
    assert playback.state is JobState.FAILED
    assert playback.reason == 'audio_frames_lost'
    with pytest.raises(WatcheRobotError): robot.audio.open_stream()
    playback.cancel()
    assert transport.commands[-1][0] == 'ctrl.audio.stop'


@pytest.mark.parametrize('transport_type', [DaemonApplicationTransport, DesktopRobotSession])
def test_close_discards_staged_partial_slot_without_sending_it(transport_type):
    async def run():
        transport = transport_type() if transport_type is DaemonApplicationTransport else transport_type('ws://unused')
        sent = []
        async def send(*args): sent.append(args)
        transport._send = send
        await transport._open_continuous_audio(23)
        await transport._write_continuous_audio(b'a\0' * 10, 23, False)
        sender = transport._continuous_audio_sender
        assert len(sender._pending) == 20
        await transport._close_continuous_audio(23)
        assert sender.finished and not sender._pending and not sent
    asyncio.run(run())
