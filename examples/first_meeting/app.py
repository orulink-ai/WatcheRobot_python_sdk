"""Launch through `watcherobot app run`; never create a device connection."""
from __future__ import annotations

import asyncio
import logging.handlers
import os
import socket
import threading
import webbrowser
from pathlib import Path

import uvicorn
from watcherobot.application import ApplicationContext

from meeting.cloud import VolcCloud
from meeting.config import ConfigStore
from meeting.robot import SDKRobot
from meeting.service import MeetingService
from meeting.web import create_web_app

ROOT = Path(__file__).resolve().parent


async def main() -> None:
    artifacts = ROOT / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    store = ConfigStore(artifacts / 'settings.json')
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    async with ApplicationContext.from_environment() as context:
        log_file = logging.handlers.RotatingFileHandler(artifacts / 'meeting.log', maxBytes=2_000_000, backupCount=2, encoding='utf-8')
        log_file.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        context.logger.addHandler(log_file)
        cloud = VolcCloud(store.settings)
        robot = SDKRobot(context.robot, threading.Event())
        service = MeetingService(robot, cloud, store.settings, artifacts, context.logger)
        web = create_web_app(service, store, ROOT / 'web', os.environ.get('WATCHER_APP_DEVICE_STATUS_URL', ''))
        server = uvicorn.Server(uvicorn.Config(web, host='127.0.0.1', port=port, access_log=False, log_level='warning'))

        async def lifecycle():
            was_online = False
            auto_started = False
            while not server.should_exit:
                if context.shutdown_requested:
                    service.request_stop()
                    server.should_exit = True
                    return
                device = await web.state.refresh_device()
                online = bool(device.get('online'))
                if online and not was_online:
                    try:
                        await asyncio.to_thread(context.robot.refresh_device_info, timeout=2)
                    except Exception:
                        service.log('error', '读取设备能力失败，稍后重试')
                        await asyncio.sleep(1)
                        continue
                    service.log('stage', '机器人已连接')
                if not online and was_online:
                    service.request_stop()
                    service.log('error', '机器人已断开，请重连后重新开始')
                if online and service.settings.auto_boot and not auto_started and not service.running:
                    auto_started = True
                    service.start()
                was_online = online
                # Consume unsupported Desktop frames so they cannot accumulate.
                try:
                    await context.desktop.receive(timeout=0.1)
                except TimeoutError:
                    pass
                await asyncio.sleep(0.9)

        url = f'http://127.0.0.1:{port}'
        (artifacts / 'dashboard.url').write_text(url, encoding='utf-8')
        service.log('stage', 'First Meeting 控制台：' + url)
        monitor = asyncio.create_task(lifecycle())
        try:
            if os.environ.get('FIRST_MEETING_NO_BROWSER') != '1':
                await asyncio.to_thread(webbrowser.open, url)
            await server.serve(sockets=[listener])
        finally:
            service.request_stop()
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            if service.task:
                await service.task
            check_task = web.state.check_task
            if check_task:
                check_task.cancel()
                await asyncio.gather(check_task, return_exceptions=True)
            await cloud.close()
            await web.state.close_device_client()
            context.logger.removeHandler(log_file)
            log_file.close()
            listener.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
