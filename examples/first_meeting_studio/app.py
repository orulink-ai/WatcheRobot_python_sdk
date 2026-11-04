"""Launch through `watcherobot app run`; never create a device connection."""
from __future__ import annotations

import asyncio
import logging.handlers
import os
import threading
import webbrowser
from pathlib import Path

import uvicorn
from watcherobot.application import ApplicationContext

from studio.cloud import VolcCloud
from studio.config import ConfigStore
from studio.dashboard import open_listener
from studio.robot import SDKRobot
from studio.service import MeetingService
from studio.web import create_web_app

ROOT = Path(__file__).resolve().parent


async def main() -> None:
    artifacts = ROOT / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    store = ConfigStore(artifacts / 'settings.json')
    listener = open_listener(int(os.environ.get("FIRST_MEETING_PORT", "64649")))
    port = listener.getsockname()[1]
    async with ApplicationContext.from_environment() as context:
        log_file = logging.handlers.RotatingFileHandler(artifacts / 'meeting.log', maxBytes=2_000_000, backupCount=2, encoding='utf-8')
        log_file.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        context.logger.addHandler(log_file)
        cloud = VolcCloud(store.settings)
        robot = SDKRobot(context.robot, threading.Event(), rtc=context.rtc, logger=context.logger)
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
                info = context.robot.device_info
                service.device_id = str(info.get("device_id") or info.get("mac") or "") if online else ""
                if online and not was_online:
                    try:
                        await asyncio.to_thread(context.robot.refresh_device_info, timeout=2)
                        service.log("device", "身份字段：" + ", ".join(context.robot.device_info.keys()))
                    except Exception:
                        service.log('error', '读取设备能力失败，稍后重试')
                        await asyncio.sleep(1)
                        continue
                    service.log('stage', '机器人已连接')
                if not online and was_online:
                    service.request_stop()
                    service.log('error', '机器人已断开，请重连后重新开始')
                if online and service.settings.auto_boot and not auto_started:
                    async with web.state.operation_lock:
                        check_task = web.state.check_task
                        checking = check_task is not None and not check_task.done()
                        if not service.running and not service.cleanup_required and not checking:
                            auto_started = True
                            if service.settings.missing_speech_fields():
                                service.log('stage', '请先在声音与聆听中配置语音凭据并检测服务')
                            elif not service.memory.get(service.device_id):
                                service.start(onboarding=True)
                            else:
                                service.log('stage', '已完成初见；日常启动就绪，等待主动开始对话')
                was_online = online
                # Consume unsupported Desktop frames so they cannot accumulate.
                try:
                    await context.desktop.receive(timeout=0.1)
                except TimeoutError:
                    pass
                await asyncio.sleep(0.9)

        url = f'http://127.0.0.1:{port}'
        (artifacts / 'dashboard.url').write_text(url, encoding='utf-8')
        service.log('stage', '初见工作室控制台：' + url)
        monitor = asyncio.create_task(lifecycle())
        try:
            if os.environ.get('FIRST_MEETING_NO_BROWSER') != '1':
                await asyncio.to_thread(webbrowser.open, url)
            await server.serve(sockets=[listener])
        finally:
            service.request_stop()
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            await service.stop()
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
