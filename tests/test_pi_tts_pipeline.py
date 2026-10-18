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
