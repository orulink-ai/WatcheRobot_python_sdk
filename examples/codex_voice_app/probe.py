"""Explicit real-service diagnostic; does not open robot microphone."""
import asyncio
import argparse
import json
from pathlib import Path
import wave

from codex_agent import CodexAgent


def load_input_wav(path: Path) -> bytes:
    with wave.open(str(path), 'rb') as source:
        if (source.getframerate(), source.getnchannels(), source.getsampwidth(), source.getcomptype()) != (16000, 1, 2, 'NONE'):
            raise ValueError('Probe input must be 16 kHz mono PCM s16le WAV')
        if not 0 < source.getnframes() <= 16000 * 30:
            raise ValueError('Probe input must contain 0–30 seconds of speech')
        pcm = source.readframes(source.getnframes())
        if len(pcm) != source.getnframes() * 2:
            raise ValueError('Truncated WAV speech input')
        return pcm


async def main(input_pcm: bytes | None = None) -> None:
    agent = CodexAgent()
    input_task = None
    try:
        await agent.start()
        print("REALTIME_REQUEST_ACCEPTED", flush=True)
        async def feed_speech():
            for offset in range(0, len(input_pcm), 640):
                await agent.append_audio(input_pcm[offset:offset + 640])
                await asyncio.sleep(.02)
        if input_pcm:
            input_task = asyncio.create_task(feed_speech())
        else:
            print('TEXT_TASK_PROBE: no robot microphone is involved.', flush=True)
            await agent.text('请用中文简短说：你好，我是机器人语音助手。不要执行工具或命令。')
        deadline = asyncio.get_running_loop().time() + 90 + (len(input_pcm or b'') / 32000)
        task_started = not input_pcm
        answer_received = False
        answer_epoch = None
        while asyncio.get_running_loop().time() < deadline:
            try:
                event = await asyncio.wait_for(
                    agent.events.get(),
                    max(0.01, deadline - asyncio.get_running_loop().time()),
                )
            except asyncio.TimeoutError:
                raise RuntimeError('Realtime probe timed out without output audio')
            method = event.get("method", "")
            print(method, flush=True)
            if method.endswith("error") or method.endswith("closed"):
                print(json.dumps(event.get("params", {}), ensure_ascii=False), flush=True)
                raise RuntimeError('Realtime session failed before output audio')
            if method == 'thread/realtime/transcript/done' and event.get('params', {}).get('role') == 'user' and not task_started:
                text = event['params'].get('text', '').strip()
                if text:
                    print('REAL_TRANSCRIPT_RECEIVED', flush=True)
                    task_started = True
                    await agent.text(text[:4000])
            if method == 'turn/completed':
                turn = event.get('params', {}).get('turn', {})
                if turn.get('status') != 'completed':
                    raise RuntimeError('Codex task failed before voice output')
                answer = '\n'.join(item['text'] for item in turn.get('items', [])
                    if item.get('type') == 'agentMessage' and item.get('phase') in {None, 'final_answer'} and item.get('text'))
                if not answer:
                    raise RuntimeError('Codex task produced no answer')
                print('REAL_AGENT_ANSWER_RECEIVED', flush=True)
                if input_task:
                    input_task.cancel()
                    await asyncio.gather(input_task, return_exceptions=True)
                    input_task = None
                if hasattr(agent, 'prepare_speech'):
                    answer_epoch = await agent.prepare_speech()
                answer_received = True
                await agent.speak(answer[:150])
            if (method == "thread/realtime/outputAudio/delta" and answer_received
                    and event.get('params', {}).get('voiceEpoch') == answer_epoch):
                print("REAL_AUDIO_RECEIVED", flush=True)
                return
        raise RuntimeError('Realtime probe ended without output audio')
    finally:
        if input_task:
            input_task.cancel()
            await asyncio.gather(input_task, return_exceptions=True)
        await agent.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Probe actual Codex realtime output audio without opening the robot microphone.')
    parser.add_argument('--input-wav', type=Path, help='16 kHz mono s16le WAV speech stimulus, at most 30 seconds')
    args = parser.parse_args()
    asyncio.run(main(load_input_wav(args.input_wav) if args.input_wav else None))
