"""Simulation gates, followed by automatic awakening; no network mutation."""
import math
import time
import uuid


def is_wake_touch(event):
    return (getattr(event, 'source', None) == 'screen_touch'
            and getattr(event, 'action', None) == 'tap'
            and (event.x - 206) ** 2 + (event.y - 206) ** 2 <= 140 ** 2)


def invitation_path():
    """Physical 412px coordinates, matching firmware vector accessory contract."""
    ring = [(round(206 + 116 * math.cos(i * math.tau / 40)),
             round(206 + 116 * math.sin(i * math.tau / 40))) for i in range(41)]
    strokes = [(5, ring)]
    for center in (165, 247):
        strokes.append((8, [(center - 20, 201), (center - 12, 209),
                            (center, 212), (center + 12, 209), (center + 20, 201)]))
    encoded = bytearray([1, len(strokes)])
    for width, points in strokes:
        encoded.extend([width, len(points)])
        for x, y in points:
            encoded.extend(x.to_bytes(2, 'big') + y.to_bytes(2, 'big'))
    return encoded.hex()


# Static QR encodes the literal WATCHER:DEMO. It is not a provisioning credential.
# 51x51 MSB-first pixel mask; white quiet zone with dark QR modules.
QR_MASK = (
    '00000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000fffffff800001fffffff000003ffffffe000007ffffffc00000f01ec07800001efa3bef000003d16d45e000007a2ca8bc00000f455517800001efbabef000003c05501e000007ffe3ffc00000f55ef6f800001e94fa0f000003d41d9fe000007a56eb3c00000f88745f800001ffed73f000003c07319e000007bef123c00000f450dc7800001e8b7b5f000003d15d9de000007becea3c00000f011477800001fffffff000003ffffffe000007ffffffc00000fffffff8000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000'
)


def connection_path(connected=False):
    strokes = [(7, [(174, 186), (156, 186), (146, 196), (146, 216), (156, 226), (174, 226)]),
               (7, [(238, 186), (256, 186), (266, 196), (266, 216), (256, 226), (238, 226)])]
    if connected:
        strokes += [(6, [(173, 206), (239, 206)]), (6, [(192, 248), (203, 258), (224, 237)])]
    data = bytearray([1, len(strokes)])
    for width, points in strokes:
        data.extend([width, len(points)])
        for x, y in points:
            data.extend(x.to_bytes(2, 'big') + y.to_bytes(2, 'big'))
    return data.hex()


class Onboarding:
    def __init__(self):
        self.revision = 0
        self.stage = 'idle'
        self.run_id = ''
        self.ready = False
        self._advance = False

    def reset(self):
        self.revision = 0
        self.stage = 'preparing'
        self.run_id = uuid.uuid4().hex
        self.ready = False
        self._advance = False

    def snapshot(self):
        return dict(stage=self.stage, run_id=self.run_id, simulated=True, revision=self.revision,
                    can_advance=self.ready and self.stage in ('scan', 'network', 'failed'))

    def advance(self, run_id, stage, action='next', revision=None):
        if not isinstance(stage, str) or not isinstance(action, str):
            raise ValueError('步骤或操作无效')
        if revision is not None and (type(revision) is not int or revision != self.revision):
            raise ValueError('步骤已经变化，请等待当前状态刷新')
        allowed = {'scan': ('next',), 'network': ('next', 'fail'), 'failed': ('password',)}
        if action not in allowed.get(stage, ()):
            raise ValueError('当前步骤不支持此操作')
        if run_id != self.run_id or stage != self.stage or not self.snapshot()['can_advance']:
            raise ValueError('步骤已经变化，请等待当前状态刷新')
        self.ready = False
        self._advance = action

    async def run(self, robot, check, pause, log):
        try:
            stage = 'scan'
            messages = {'scan': '请模拟扫码成功，进入手机输入密码页',
                        'network': '等待手机输入密码 · 完成后开始苏醒',
                        'failed': '配网未完成 · 请返回手机输入密码页重试'}
            while True:
                check()
                self.stage = stage
                self.revision += 1
                await robot.onboarding_screen(stage)
                check()
                self._advance = False
                self.ready = True
                log('stage', messages[stage])
                while not self._advance:
                    check()
                    await pause(.1)
                self.ready = False
                action = self._advance
                if action == 'fail':
                    stage = 'failed'
                elif action == 'password' or stage == 'scan':
                    stage = 'network'
                else:
                    break
            self.stage = 'awakening'
            check()
            log('stage', '模拟配网完成 · 正在慢慢苏醒')
            await robot.finish_invitation()
            check()
        except InterruptedError:
            self.stage = 'interrupted'
            raise
        except Exception:
            self.stage = 'error'
            raise
        finally:
            self.ready = False
