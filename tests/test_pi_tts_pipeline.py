"""Continuous synthesis is bounded and a reply uses one device stream."""
import asyncio
import importlib.util
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]/'examples'/'joyinside_pi'


def pipeline():
    spec=importlib.util.spec_from_file_location('pi_tts_pipeline',ROOT/'tts_pipeline.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_synthesis_keeps_filling_queue_while_first_audio_is_playing():
    async def run():
        produced=[];playing=asyncio.Event();release=asyncio.Event();all_ready=asyncio.Event();played=[]
        async def synth(text,number):
            produced.append(text)
            if len(produced)==5:all_ready.set()
            return text.encode()*2
        async def play(pcm,number):
            played.append(pcm)
            if number==1:playing.set();await release.wait()
        task=asyncio.create_task(pipeline().run_tts_pipeline(list('abcde'),synthesize=synth,play_segment=play))
        try:
            await asyncio.wait_for(playing.wait(),1);await asyncio.wait_for(all_ready.wait(),1)
            assert produced==list('abcde')
            release.set();await task
            assert played==[b'aa',b'bbccddee'],'Ready segments should share a legacy stream'
        finally:
            release.set();task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run())


def test_continuous_sink_is_called_once_and_keeps_pcm_in_order():
    async def run():
        streams=[];legacy=[]
        async def synth(text,n):return text.encode()*2
        async def play(pcm,n):legacy.append(pcm)
        async def stream(frames):streams.append([pcm async for pcm in frames])
        await pipeline().run_tts_pipeline(['a','b','c'],synthesize=synth,play_segment=play,play_stream=stream)
        assert streams==[[b'aa',b'bb',b'cc']] and legacy==[]
    asyncio.run(run())


@pytest.mark.parametrize("byte_budget,slots,expected", [(8,20,3),(100,2,2)])
def test_queue_reserves_both_byte_budget_and_slot_before_synthesis(byte_budget,slots,expected):
    async def run():
        mod=pipeline();produced=[];entered=asyncio.Event();release=asyncio.Event()
        async def synth(text,n):produced.append(n);return b'xx'
        async def play(pcm,n):entered.set();await release.wait()
        task=asyncio.create_task(mod.run_tts_pipeline(['a']*20,synthesize=synth,play_segment=play,
            max_segment_bytes=4,buffer_bytes=byte_budget,max_segments=slots))
        try:
            await asyncio.wait_for(entered.wait(),1);await asyncio.sleep(.02)
            assert len(produced)==expected,'Both in-flight reservation and retained PCM must count toward the budget'
            release.set();await task
        finally:
            release.set();task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(run())


def test_failed_producer_cancels_and_joins_playback():
    async def run():
        playing=asyncio.Event();ended=asyncio.Event()
        async def synth(text,n):
            if n==2:await playing.wait();raise RuntimeError('cloud failed')
            return b'xx'
        async def play(pcm,n):
            try:playing.set();await asyncio.Event().wait()
            finally:ended.set()
        with pytest.raises(RuntimeError,match='cloud failed'):
            await pipeline().run_tts_pipeline(['a','b'],synthesize=synth,play_segment=play)
        assert ended.is_set()
    asyncio.run(run())


def test_cancelled_resistant_synthesis_cannot_play_late_pcm():
    async def run():
        entered=asyncio.Event();finished=asyncio.Event();played=[]
        async def synth(text,n):
            try:entered.set();await asyncio.Event().wait()
            except asyncio.CancelledError:return b'xx'
            finally:finished.set()
        async def play(pcm,n):played.append(pcm)
        task=asyncio.create_task(pipeline().run_tts_pipeline(['a'],synthesize=synth,play_segment=play))
        await entered.wait();task.cancel();await asyncio.gather(task,return_exceptions=True)
        assert finished.is_set() and played==[]
    asyncio.run(run())


@pytest.mark.parametrize('bad',[b'',b'x',b'x'*10])
def test_invalid_or_oversize_pcm_is_rejected_without_playing(bad):
    async def run():
        played=[]
        async def synth(text,n):return bad
        async def play(pcm,n):played.append(pcm)
        with pytest.raises(ValueError):
            await pipeline().run_tts_pipeline(['a'],synthesize=synth,play_segment=play,
                max_segment_bytes=4,buffer_bytes=8,max_segments=2)
        assert played==[]
    asyncio.run(run())


@pytest.mark.parametrize('continuous', [False, True])
def test_async_text_is_synthesized_while_playback_gate_is_closed(continuous):
    async def run():
        gate_entered, release_gate, all_ready = asyncio.Event(), asyncio.Event(), asyncio.Event()
        produced, played = [], []
        async def source():
            for text in ['a', 'b', 'c']:
                await asyncio.sleep(0)
                yield text
        async def synth(text, number):
            produced.append(text)
            if len(produced) == 3:
                all_ready.set()
            return text.encode() * 2
        async def gate():
            assert produced, 'The gate starts only after the first PCM is ready'
            gate_entered.set()
            await release_gate.wait()
        async def play(pcm, number):
            played.append(pcm)
        async def stream(frames):
            played.extend([pcm async for pcm in frames])
        task = asyncio.create_task(pipeline().run_tts_pipeline(source(), synthesize=synth,
            play_segment=play, play_stream=stream if continuous else None, before_play=gate))
        try:
            await asyncio.wait_for(gate_entered.wait(), 1)
            await asyncio.wait_for(all_ready.wait(), 1)
            assert played == [] and produced == ['a', 'b', 'c']
            release_gate.set()
            await asyncio.wait_for(task, 1)
            assert b''.join(played) == b'aabbcc'
        finally:
            release_gate.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_gate_cancellation_joins_text_source_and_resistant_gate_without_late_audio():
    async def run():
        gate_entered, source_waiting, source_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        gate_ended, played = asyncio.Event(), []
        async def source():
            try:
                yield 'a'
                source_waiting.set()
                await asyncio.Event().wait()
            finally:
                source_closed.set()
        async def synth(text, number):
            return b'aa'
        async def gate():
            try:
                gate_entered.set()
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
            finally:
                gate_ended.set()
        async def play(pcm, number):
            played.append(pcm)
        task = asyncio.create_task(pipeline().run_tts_pipeline(source(), synthesize=synth,
            play_segment=play, before_play=gate))
        await asyncio.wait_for(gate_entered.wait(), 1)
        await asyncio.wait_for(source_waiting.wait(), 1)
        task.cancel()
        result = await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 1)
        assert isinstance(result[0], asyncio.CancelledError)
        assert source_closed.is_set() and gate_ended.is_set() and played == []
    asyncio.run(run())


def test_async_source_failure_cancels_gate_and_propagates_without_audio():
    async def run():
        gate_entered, gate_closed, source_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        played = []
        async def source():
            try:
                yield 'a'
                await gate_entered.wait()
                raise RuntimeError('text stream failed')
            finally:
                source_closed.set()
        async def synth(text, number):
            return b'aa'
        async def gate():
            try:
                gate_entered.set()
                await asyncio.Event().wait()
            finally:
                gate_closed.set()
        async def play(pcm, number):
            played.append(pcm)
        with pytest.raises(RuntimeError, match='text stream failed'):
            await asyncio.wait_for(pipeline().run_tts_pipeline(source(), synthesize=synth,
                play_segment=play, before_play=gate), 1)
        assert source_closed.is_set() and gate_closed.is_set() and played == []
    asyncio.run(run())


def test_gate_failure_closes_async_source_suspended_after_yield():
    async def run():
        source_closed, played = asyncio.Event(), []
        async def source():
            try:
                yield 'a'
                yield 'b'
            finally:
                source_closed.set()
        async def synth(text, number):
            return b'aa'
        async def gate():
            raise RuntimeError('draft rejected')
        async def play(pcm, number):
            played.append(pcm)
        with pytest.raises(RuntimeError, match='draft rejected'):
            await asyncio.wait_for(pipeline().run_tts_pipeline(source(), synthesize=synth,
                play_segment=play, before_play=gate, max_segments=1), 1)
        assert source_closed.is_set() and played == []
    asyncio.run(run())


def test_empty_async_source_does_not_wait_for_playback_gate():
    async def run():
        async def source():
            for text in ['', '  ']:
                yield text
        async def unexpected(*args):
            raise AssertionError('Empty text cannot synthesize, gate or play')
        await asyncio.wait_for(pipeline().run_tts_pipeline(source(), synthesize=unexpected,
            play_segment=unexpected, before_play=unexpected), 1)
    asyncio.run(run())


def test_async_text_without_gate_starts_audio_before_source_finishes_and_closes_iterator():
    async def run():
        playing = asyncio.Event()
        played = []
        class Source:
            def __init__(self):
                self.number = 0
                self.closed = False
            def __aiter__(self):
                return self
            async def __anext__(self):
                self.number += 1
                if self.number == 1:
                    return 'a'
                if self.number == 2:
                    await playing.wait()
                    return 'b'
                raise StopAsyncIteration
            async def aclose(self):
                self.closed = True
        async def synth(text, number):
            return text.encode() * 2
        async def play(pcm, number):
            played.append(pcm)
            playing.set()
        source = Source()
        await asyncio.wait_for(pipeline().run_tts_pipeline(source, synthesize=synth,
            play_segment=play), 1)
        assert played == [b'aa', b'bb'] and source.closed
    asyncio.run(run())


def test_closed_gate_still_obeys_prepared_pcm_slot_budget():
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        produced = []
        async def source():
            for text in ['a'] * 20:
                yield text
        async def synth(text, number):
            produced.append(number)
            return b'aa'
        async def gate():
            entered.set()
            await release.wait()
        async def play(pcm, number):
            pass
        task = asyncio.create_task(pipeline().run_tts_pipeline(source(), synthesize=synth,
            play_segment=play, before_play=gate, max_segments=2))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.sleep(.02)
            assert produced == [1, 2], 'The gate-held first PCM still occupies its slot'
            release.set()
            await asyncio.wait_for(task, 1)
            assert produced == list(range(1, 21))
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())
