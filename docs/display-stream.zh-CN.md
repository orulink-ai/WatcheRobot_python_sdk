# 在受管 Application 中推送显示画面

**状态：SDK 开发分支功能，尚未正式发布；需要包含本实现的 Application SDK、Daemon 和配套固件。** 不要仅凭已发布 SDK 的版本号判断兼容，也不要把普通串口固件当作支持此功能的固件。实际设备必须声明 `display.stream.v1`，并实现本文的显示协议；选择 USB 时，还必须实现 WRU1。

Application 通过 `app.robot.display` 申请显示会话，把完整 JPEG 交给设备解码和呈现。设备连接由 Daemon 管理，Application 使用注入的 Device channel，不自行打开串口或建立第二条设备连接。

## 先显式连接 USB，再运行 Application

USB 是一种通用设备传输，与显示业务分开管理。使用包含本实现的 SDK CLI，明确指定端口；以下 `COM7` 只是 Windows 写法示例，执行前替换为实际端口：

~~~text
watcherobot robot usb connect --port COM7 --baudrate 1000000
watcherobot robot status
~~~

示例使用 `1000000 baud、8N1`；实际速率以所安装固件的发布说明为准（桌宠配套社区固件 0.3.2 使用 `2000000 baud`）。CLI 接受的其他波特率必须由固件另行支持，不会自动协商，也不会回退旧串口协议。连接成功表示串口传输已建立，仍需由 Application 查询设备能力。

Daemon 只允许一个设备连接槽位。已有 USB、LAN 设备连接或配对流程占用时，USB 连接请求会失败，不自动替换现有连接。没有端口自动扫描、轮询试连或启动 Application 时自动开串口的行为。

| 主机平台 | 端口名称示例 | 使用条件 |
| --- | --- | --- |
| Windows | `COM7` | USB 串口驱动已安装，端口可访问且未被占用 |
| macOS | `/dev/cu.usbserial-<id>` | 使用实际串口设备路径 |
| Linux | `/dev/ttyUSB0` 或 `/dev/ttyACM0` | 使用实际设备路径并具备串口访问权限 |

命令形式相同，端口名称取决于系统和 USB 串口芯片。上述路径示例不代表各平台已完成相同的实机验收。

默认串口工厂先创建关闭的 `Serial(port=None)`，在打开之前设置 `dtr=False`、`rts=False`，再指定端口并 `open()`，避免 CH342 板卡因默认控制线状态被复位或暂停。自定义串口工厂也应保持这一打开顺序；不要依靠打开后再改控制线补救。

在已有 `app.json` 的 Application 目录中，通过 Daemon 启动：

~~~text
watcherobot app run .
~~~

停止画面推送并结束 Application 后，如需释放 USB 连接，显式执行：

~~~text
watcherobot robot usb disconnect
~~~

对应管理接口为 `POST /daemon/devices/usb/connect`，请求体为 `{"port":"COM7","baudrate":1000000}`；断开接口为 `POST /daemon/devices/usb/disconnect`，请求体可为 `{}`。USB 断开接口只释放 USB 会话，不解除 LAN 绑定或中断 LAN 连接；没有 USB 会话时，也不会转而调用 LAN 断开流程。

## 最小受管 Application 示例

将下面代码用作现有 Application 的 `app.py`，并在同目录准备有效的 `frame.jpg`。逻辑画布固定为 `412 × 412`；配套固件可接收 `412 × 412` 或 `206 × 206` 的 JPEG，具体尺寸支持由固件决定。JPEG 必须完整，且不超过本次协商的 `max_frame_bytes`。

显示 API 是同步阻塞调用。示例把整个显示会话放入一个工作线程，顺序提交帧；`finish_thread` 是示例内的辅助函数，不是 SDK 导出的 API。取消时先通知线程退出，再等正在执行的有界调用和关闭操作结束，最后退出 `ApplicationContext`，避免设备通道先关闭而线程仍在发送。

~~~python
import asyncio
import logging
import threading
from pathlib import Path

from watcherobot.application import ApplicationContext


async def finish_thread(func, *args, cancel_event, **kwargs):
    worker = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancel_event.set()
        # 重复取消也必须等工作线程完成清理，才能关闭 Application 通道。
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            error = worker.exception()
            if error is not None:
                logging.error(
                    "显示线程退出失败",
                    exc_info=(type(error), error, error.__traceback__),
                )
        raise


def show_frame(app, stop):
    app.robot.refresh_device_info(timeout=3.0)
    if not app.robot.supports("display.stream.v1"):
        raise RuntimeError("当前设备固件未声明 display.stream.v1")
    jpeg = Path(__file__).with_name("frame.jpg").read_bytes()
    if stop.is_set() or app.shutdown_requested:
        return

    display = app.robot.display.open_stream(
        width=412, height=412, timeout=3.0,
    )
    try:
        while not stop.is_set() and not app.shutdown_requested:
            display.present(jpeg, timeout=3.0)
            stop.wait(0.1)  # 示例限速；不保证固定帧率。
    finally:
        display.close(timeout=3.0)


async def main():
    async with ApplicationContext.from_environment() as app:
        stop = threading.Event()
        await finish_thread(show_frame, app, stop, cancel_event=stop)


if __name__ == "__main__":
    asyncio.run(main())
~~~

使用 `watcherobot app run` 启动，不能直接 `python app.py`：`ApplicationContext.from_environment()` 依赖 Daemon 注入的应用身份和通道。不要在示例中加入串口扫描、USB 连接或自行构造设备连接的代码。

## API、能力和租约

| 调用或属性 | 含义 |
| --- | --- |
| `app.robot.refresh_device_info(timeout=3.0)` | 请求新设备信息；设备重连后应重新核对能力 |
| `app.robot.supports("display.stream.v1")` | 检查当前固件声明；`open_stream` 也会执行能力检查 |
| `app.robot.display.open_stream(width=412, height=412, timeout=3.0)` | 申请独占显示会话，返回 `DisplaySession` |
| `session.stream_id` | 设备确认分配的流 ID，范围为 `1..65535` |
| `session.max_frame_bytes` | 本次协商的 JPEG 字节上限，最大为 `65536` |
| `session.lease_ms` | 固件返回的租约时长；未返回时为 `None` |
| `session.present(jpeg: bytes, timeout=3.0) -> int` | 提交完整 JPEG，等待设备呈现结果，返回本会话的 SDK 帧序号 |
| `app.robot.display.close_stream(timeout=3.0)` | 有界清理当前已确认归属的流；可在打开 ACK 迟到、没有取得句柄时调用；成功后仍可重新打开 |
| `session.close(timeout=3.0)` | 请求关闭自己持有的流；成功后可重复调用 |

`open_stream`、`present`、`close`、`close_stream` 的超时参数必须是有限数值，满足 `0 < timeout <= 120` 秒，默认 `3.0` 秒。`present` 使用同一个截止时间覆盖发送完成和设备呈现应答，不会为两个阶段分别重新计算完整超时。SDK 在发送前检查 JPEG 的 SOI/EOI 标记和协商字节上限，实际解码与尺寸校验由固件完成。

同一个 `DisplayDomain` 在打开、运行和关闭期间只保留一个会话。每个会话只允许一帧在途；上一帧 `present` 返回后再发下一帧。并发提交会报错，不会自动建立多帧队列。需要实时画面的应用应自行限制生产速度，避免无界积压。

`lease_ms` 描述固件租约，不是主机调用超时。配套固件会在有效画面提交时续租，并在租约到期后释放显示资源；应用暂停推送时须按固件的租约约定处理。`lease_ms is None` 只表示未提供时长，不能视为零租约、无限租约或已经释放。应用仍应在退出前调用有界 `close`，不能用一段自行估计的等待代替设备确认。

### 超时和关闭后的所有权

- 发送失败、呈现超时或设备返回呈现失败后，当前会话不能继续 `present`；仍可对原句柄调用 `close`。不要把迟到的呈现事件当作旧句柄恢复可用。
- `close` 超时或失败不等于设备已释放。SDK 保留本地所有权用于显式重试，新的 `open_stream` 会被阻止；应使用原句柄重试有界关闭，并向调用者报告清理失败。
- `open_stream` 超时且没有拿到有效 `stream_id` 时，设备端是否已分配租约是未知的。SDK 不会猜测 ID，也不会发送可能关闭他人会话的清理命令。先确认设备状态，并按配套固件的租约约定处理，不要立即反复抢占。
- `open_stream` 已取得有效 ACK、但总截止时间已过时，SDK 保留流的所有权且不会返回可呈现句柄。调用 `app.robot.display.close_stream(timeout=3.0)` 清理；清理失败仍可重试，成功后才可重新打开。该入口也等待进行中的打开操作，并受同一个有限截止时间约束；没有已确认所有权时不发送清理命令。
- 设备明确拒绝打开，例如返回 `busy`，也不应关闭设备上其他所有者的流。
- 断开设备连接会唤醒等待者并退役旧句柄。重连后重新查询能力、重新打开；旧关闭操作在实际传输循环接纳命令前重新核对归属，已观察到离线的旧句柄不能向重连后的流发起关闭。已经由通道接纳的在途命令不能撤回，也不能在固件尚未报告离线时推断设备已重启。

`DisplaySession` 也支持同步 `with` 上下文管理，其退出会调用默认有界关闭。异步 Application 仍须等工作线程完成，再退出外层 `ApplicationContext`；单独取消 `asyncio.to_thread` 的等待不会停止线程。

## 显示业务使用 WSPK type 8

业务控制为 `ctrl.display.open`、`ctrl.display.close`，沿用 `command_id` 与 `sys.ack` / `sys.nack`。打开成功的 `sys.ack.data` 必须包含 `stream_id` 和 `max_frame_bytes`；`lease_ms` 为可选字段。

每次 `present` 发送一个完整 JPEG，使用现有 16 字节 WSPK 二进制头：

| 字段 | 内容 |
| --- | --- |
| magic | `WSPK` |
| frame_type | `8`，即 `FRAME_DISPLAY` |
| flags | `FIRST` 与 `LAST`，数值为 `3` |
| stream_id | 本次显示会话 ID，`uint16` |
| sequence | 会话内递增的 SDK 帧序号，`uint32` |
| payload_len | 后续 JPEG 字节数，`uint32` |
| payload | 完整 JPEG |

多字节整数为小端。设备通过 `evt.display.presented` 返回 `stream_id`、`sequence`、`device_sequence`、`code`，失败时可带 `reason`。SDK 只接收当前会话、当前在途帧的匹配结果；`code == 0` 才使 `present` 成功返回。仅发送完成或收到 USB 分片 ACK，不能证明设备已经呈现画面。

## WRU1 只负责 USB 分片，不负责业务路由

USB 路径为 `Application Device channel → Daemon → WRU1 → 固件`。WSPK 显示帧作为完整的不透明 `bytes` 进入 WRU1 的 BINARY 逻辑帧；任意 `str` 以严格 UTF-8 编码进入 TEXT，`bytes` 进入 BINARY；不检查 JSON 或业务类型。WRU1 的 `frame_id` / `fragment_index` 与 WSPK 的 `stream_id` / `sequence` 是不同层的标识，不能互换。

WRU1 头为 `<4sBBHIII`、共 `20` 字节；单片负载不超过 `1024` 字节，逻辑帧上限为 `131072` 字节，CRC 为 `zlib.crc32(payload)`。Host 向设备逐片发送并等待同 ID、同分片索引的 ACK；ACK 的 kind 为 `3`、flags 为 `0`、负载长度和 CRC 都为 `0`。默认分片 ACK 超时为 `1` 秒，确认后保留默认 `1 ms` 间隔。超时或 I/O 错误会关闭连接，不自动重传；设备向 Host 发送数据不要求 Host 回传传输 ACK。

USB 读线程与事件循环之间最多保留 128 个排队或正在回调的业务负载，合计 Python 负载存储不超过 1 MiB；Unicode 按实际对象大小计费。交接唤醒合并为最多一个待执行通知，匹配的分片 ACK 使用独立单槽，过期 ACK 不排队。超限立即停止读取，并由事件循环关闭连接；解码器自身仍有独立的长度和超时限制。

USB ACK 只确认对应分片，`sys.ack` 确认业务控制命令，`evt.display.presented` 才返回本次画面呈现结果。这三层确认不能互相替代。

Daemon 不解析、分支或改写 `ctrl.display.*`、`evt.display.*` 或新增业务类型。Application 运行时，Desktop 与 Device 业务分别进入当前 Application 的对应通道；没有 Application 时由 Daemon 透明转发。USB 沿用同一路由规则，不增加显示直达设备的旁路。

相关说明：[共享 Daemon 与 Application](shared-runtime.zh-CN.md)。
