"""机器人闹钟 Application。

本地网页（默认 http://127.0.0.1:8766）设置闹钟：时间、重复规则（每天/工作日/
自定义星期）、播报内容，数据持久化在 ``alarms.json``，页面增删改立即生效。
到点用机器人喇叭语音播报文案，同时播放一次 ``happy`` 行为引起注意。

语音通过 edge-tts 在线合成（中文女声），任一环节缺失或失败时回退到内置示例
音频；应用启动时默认立即播报一次（``ANNOUNCE_ON_STARTUP``）验证语音链路。

Application 是常驻进程：网页服务 + 调度循环都随 ``main`` 运行，由 Daemon
管理生命周期。定时提醒依赖 Daemon 与机器人在到点时处于运行/已连接状态。

必须通过 SDK Runtime 运行（``watcherobot app run .``），不要直接执行本文件。
"""

from __future__ import annotations

import asyncio
import datetime
import os
from pathlib import Path

from watcherobot.application import ApplicationContext

from reminder.alarms import AlarmStore
from reminder.schedule import next_fires, seconds_until
from reminder.speech import (
    DEFAULT_VOICE,
    ReminderSpeech,
    edge_tts_available,
    ffmpeg_available,
)
from reminder.web import AlarmWebServer

APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "alarms.json"  # 闹钟数据（网页端编辑，勿手改）
FALLBACK_WAV = APP_DIR / "assets" / "fallback.wav"
CACHE_DIR = APP_DIR / ".cache"

# 网页服务：默认只监听本机；手机/局域网访问把 HOST 改成 0.0.0.0（或用环境变量覆盖）。
WEB_HOST = os.environ.get("WATCHER_ALARM_WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.environ.get("WATCHER_ALARM_WEB_PORT", "8766"))

# 应用启动时是否立即播报一次（验证语音链路用；不需要就改成 False）。
ANNOUNCE_ON_STARTUP = True
STARTUP_TEXT = "你好，机器人闹钟已启动！"

# 调度循环最多睡这么久就醒来重读闹钟配置，保证网页端改动最快 1 分钟内生效。
RELOAD_INTERVAL_S = 60.0
# 单条播报/行为最多等待的秒数；超时不再阻塞调度循环。
MAX_STEP_WAIT_S = 90.0


async def announce(app: ApplicationContext, speech: ReminderSpeech, text: str) -> None:
    """播报一段文案：语音 + 一段吸引注意的行为。失败只记日志，不中断循环。"""
    if not app.robot.supports("audio"):
        app.logger.warning("设备未连接或固件不支持 audio，跳过播报：%s", text)
        return

    wav = await speech.wav_for(text)
    app.logger.info("播放提醒：%r（音频 %s）", text, wav.name)
    try:
        playback = await asyncio.to_thread(app.robot.audio.play_file, wav)
        await asyncio.to_thread(playback.wait, MAX_STEP_WAIT_S)
    except Exception as exc:  # noqa: BLE001 - 播报失败不应终止常驻调度
        app.logger.error("语音播报失败：%s", exc)

    if app.robot.supports("behavior"):
        try:
            job = await asyncio.to_thread(app.robot.behavior.play, "happy", repeat=1)
            await asyncio.to_thread(job.wait, MAX_STEP_WAIT_S)
        except Exception as exc:  # noqa: BLE001
            app.logger.error("行为播放失败：%s", exc)


async def run_scheduler(app: ApplicationContext, speech: ReminderSpeech, store: AlarmStore) -> None:
    """常驻调度循环：热重载闹钟配置，休眠到下一次触发时间，唤醒后播报全部到期闹钟。"""
    while True:
        now = datetime.datetime.now()
        active = store.all_enabled()
        if not active:
            app.logger.info("没有启用的闹钟，等待配置…")
            await asyncio.sleep(RELOAD_INTERVAL_S)
            continue

        fires = next_fires(active, now=now)
        when, _first = fires[0]
        due = [alarm for fire_time, alarm in fires if fire_time == when]
        delay = seconds_until(when, now=datetime.datetime.now())

        if delay > RELOAD_INTERVAL_S:
            # 还早：分段睡眠，每段醒来重读配置，让网页端的改动尽快生效。
            await asyncio.sleep(RELOAD_INTERVAL_S)
            continue

        app.logger.info(
            "闹钟到点 %s：%d 条待播",
            when.isoformat(timespec="seconds"),
            len(due),
        )
        await asyncio.sleep(max(0.05, delay))
        for alarm in due:
            await announce(app, speech, alarm.text)


async def main() -> None:
    async with ApplicationContext.from_environment() as app:
        app.logger.info("机器人闹钟 Application 已启动")

        store = AlarmStore(DATA_FILE).load()
        web = AlarmWebServer(store, host=WEB_HOST, port=WEB_PORT)
        try:
            web.start()
            app.logger.info("闹钟设置页：%s", web.base_url)
        except OSError as exc:
            app.logger.error("网页服务启动失败（%s），闹钟仍会按 alarms.json 播报", exc)

        if not edge_tts_available():
            app.logger.warning(
                "未检测到 edge-tts，播报将使用内置示例音频。"
                "安装后可启用动态文案：python -m pip install 'edge-tts>=6.1.10'"
            )
        elif not ffmpeg_available():
            app.logger.warning(
                "未检测到 ffmpeg，播报将使用内置示例音频。"
                "请安装 ffmpeg 并加入 PATH 以启用动态文案合成。"
            )

        speech = ReminderSpeech(
            cache_dir=CACHE_DIR,
            fallback_wav=FALLBACK_WAV,
            voice=DEFAULT_VOICE,
        )
        if ANNOUNCE_ON_STARTUP:
            app.logger.info("应用启动，立即播报一次")
            await announce(app, speech, STARTUP_TEXT)
        await run_scheduler(app, speech, store)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass