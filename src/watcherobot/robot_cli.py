"""Hardware-oriented ``watcherobot robot`` commands."""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import mimetypes
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import urlsplit, urlunsplit

from .desktop_session import DesktopRobotSession
from .errors import CommandError
from .protocol import FLAG_LAST
from .recordings import RecordingMode, iter_wrec_records, mux_wrec_to_mp4, write_pcm_wav
from .robot import WatcheRobot

_HARDWARE_COMMANDS = {"capabilities", "camera", "audio", "light", "screen", "recording"}
_TERMINAL_RECORDING_STATES = {"completed", "interrupted", "failed"}
_TERMINAL_MAINTENANCE_STATES = {"completed", "succeeded", "failed", "cancelled"}
_WORK_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,22}$")


class RobotCliError(RuntimeError):
    pass


def _output_flags(parser: argparse.ArgumentParser, *, streaming: bool = False) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--json", action="store_true", help="Emit one stable JSON object")
    if streaming:
        group.add_argument("--jsonl", action="store_true", help="Emit progress as JSON Lines")


def register_commands(subparsers: Any) -> None:
    capabilities = subparsers.add_parser("capabilities", help="Show firmware and hardware capabilities")
    _output_flags(capabilities)

    camera = subparsers.add_parser("camera", help="Capture photos or device-side recordings")
    camera_sub = camera.add_subparsers(dest="camera_command", required=True)
    capture = camera_sub.add_parser("capture", aliases=["cap"], help="Capture one JPEG")
    capture.add_argument("-o", "--output", type=Path)
    capture.add_argument("--width", type=int, default=0)
    capture.add_argument("--height", type=int, default=0)
    capture.add_argument("--quality", type=int, default=0)
    _output_flags(capture)
    record = camera_sub.add_parser("record", help="Record reliable JPEG video to the host or device")
    _record_options(record, default_suffix=".mp4")
    record.add_argument("--with-audio", action="store_true")

    audio = subparsers.add_parser("audio", help="Record or play robot audio")
    audio_sub = audio.add_subparsers(dest="audio_command", required=True)
    audio_record = audio_sub.add_parser("record", help="Record robot microphone audio to the host or device")
    _record_options(audio_record, default_suffix=".wav", video_options=False)
    play = audio_sub.add_parser("play", help="Decode and play WAV, MP3, or OGG")
    play.add_argument("file", type=Path)
    _output_flags(play)
    stop = audio_sub.add_parser("stop", help="Stop host audio playback")
    _output_flags(stop)

    light = subparsers.add_parser("light", help="Control side and bottom RGB zones")
    light_sub = light.add_subparsers(dest="light_command", required=True)
    light_set = light_sub.add_parser("set")
    _light_options(light_set)
    effect = light_sub.add_parser("effect")
    effect.add_argument("effect", choices=("blink", "breathing", "rainbow", "status-pulse"))
    _light_options(effect)
    effect.add_argument("--period", type=int, default=500, metavar="MS")
    effect.add_argument("--repeat", type=int, default=0)
    for name in ("off", "status"):
        leaf = light_sub.add_parser(name)
        _output_flags(leaf)

    screen = subparsers.add_parser("screen", help="List, play, install, or delete screen content")
    screen_sub = screen.add_subparsers(dest="screen_command", required=True)
    for name in ("list", "stop"):
        leaf = screen_sub.add_parser(name)
        _output_flags(leaf)
    play_screen = screen_sub.add_parser("play")
    play_screen.add_argument("animation_id")
    _output_flags(play_screen)
    play_work = screen_sub.add_parser("play-work")
    play_work.add_argument("work_id")
    play_work.add_argument("--clip", default="main")
    _output_flags(play_work)
    install = screen_sub.add_parser("install")
    install.add_argument("file", type=Path)
    install.add_argument("--id", dest="work_id")
    _maintenance_options(install)
    _output_flags(install, streaming=True)
    delete_work = screen_sub.add_parser("delete-work")
    delete_work.add_argument("work_id")
    _maintenance_options(delete_work)
    _output_flags(delete_work)

    recordings = subparsers.add_parser("recording", help="Manage recordings retained on device storage")
    recording_sub = recordings.add_subparsers(dest="recording_command", required=True)
    for name in ("list", "status"):
        leaf = recording_sub.add_parser(name)
        if name == "status":
            leaf.add_argument("recording_id", nargs="?")
        _output_flags(leaf)
    for name in ("stop", "delete"):
        leaf = recording_sub.add_parser(name)
        leaf.add_argument("recording_id")
        _output_flags(leaf)
    download = recording_sub.add_parser("download")
    download.add_argument("recording_id")
    download.add_argument("-o", "--output", type=Path)
    download.add_argument("--raw-dir", type=Path)
    _output_flags(download, streaming=True)


def _record_options(parser: argparse.ArgumentParser, *, default_suffix: str, video_options: bool = True) -> None:
    parser.set_defaults(default_suffix=default_suffix)
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--duration", type=float)
    parser.add_argument(
        "--storage", choices=("host", "device"), default="host",
        help="Persist live media on this computer (default) or retain it on device SD",
    )
    if video_options:
        parser.add_argument("--width", type=int, default=640)
        parser.add_argument("--height", type=int, default=480)
        parser.add_argument("--fps", type=int, choices=range(1, 11), default=5)
        parser.add_argument("--quality", type=int, choices=range(1, 101), default=80)
    _output_flags(parser, streaming=True)


def _light_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--zone", choices=("side", "head", "bottom", "all"), default="all")
    parser.add_argument("--color", default="#FFFFFF")
    parser.add_argument("--brightness", type=int, choices=range(0, 101), default=100)
    _output_flags(parser)


def _maintenance_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--port")
    group.add_argument("--volume-id")


def handles(args: argparse.Namespace) -> bool:
    return args.command == "robot" and args.robot_command in _HARDWARE_COMMANDS


def run(
    args: argparse.Namespace,
    *,
    runtime_state: Any,
    request_json: Callable[..., dict[str, Any]],
) -> int:
    status = request_json(runtime_state.control_url, "/daemon/status")
    application = status.get("application", {})
    if isinstance(application, dict) and application.get("state") in {"starting", "running", "stopping"}:
        raise RobotCliError("An Application is active; run 'watcherobot app stop' before using robot hardware commands")
    devices = request_json(runtime_state.control_url, "/daemon/devices")
    device = devices.get("device", devices)
    if not isinstance(device, dict) or not bool(device.get("online")):
        raise RobotCliError("Robot is not connected; run 'watcherobot robot status' first")
    if args.robot_command == "screen" and args.screen_command in {"install", "delete-work"}:
        return _run_maintenance(args, runtime_state.control_url, request_json)

    session = DesktopRobotSession(_loopback_url(runtime_state.external_url), command_timeout=10.0)
    robot = WatcheRobot(session)
    try:
        session.start()
        return _run_connected(args, robot)
    except RobotCliError:
        raise
    except (OSError, TimeoutError, ValueError) as error:
        raise RobotCliError(str(error)) from error
    finally:
        robot.close()


def _run_connected(args: argparse.Namespace, robot: WatcheRobot) -> int:
    if args.robot_command == "capabilities":
        payload: dict[str, Any] = {"capabilities": list(robot.capabilities), "device": robot.device_info}
        try:
            response = robot._command("resource.storage.get", {})
            storage = response.get("data", {})
            if isinstance(storage, dict):
                storage = {key: value for key, value in storage.items() if key != "command_id"}
                total = int(storage.get("total_bytes", 0) or 0)
                free = int(storage.get("free_bytes", 0) or 0)
                reserve = max(256 * 1024 * 1024, (total * 5 + 99) // 100) if total > 0 else 0
                storage["recording_reserve_bytes"] = reserve
                storage["recording_usable_bytes"] = max(0, free - reserve)
                payload["storage"] = storage
        except (CommandError, TimeoutError):
            payload["storage"] = {"available": False}
        return _emit(args, payload, "Robot capabilities")
    if args.robot_command == "camera":
        if args.camera_command in {"capture", "cap"}:
            path = args.output or _timestamp_path("photo", ".jpg")
            partial = path.with_name(path.name + ".part")
            image = robot.camera.capture(width=args.width, height=args.height, quality=args.quality)
            partial.write_bytes(image.data)
            partial.replace(path)
            return _emit(args, {"path": str(path.resolve()), "bytes": len(image.data), "content_type": image.content_type}, f"Photo saved: {path}")
        return _record_and_download(args, robot, "av" if args.with_audio else "video")
    if args.robot_command == "audio":
        if args.audio_command == "record":
            return _record_and_download(args, robot, "audio")
        if args.audio_command == "play":
            playback = robot.audio.play_file(args.file)
            try:
                playback.wait(timeout=max(15.0, playback.expected_duration_seconds + 10.0))
            except KeyboardInterrupt:
                playback.cancel()
                raise
            except TimeoutError as error:
                playback.cancel()
                raise RobotCliError("Audio playback did not complete before its safety timeout") from error
            return _emit(args, {"state": "completed", "stream_id": playback.id}, "Audio playback completed")
        robot.audio.stop()
        return _emit(args, {"state": "stopped"}, "Audio playback stopped")
    if args.robot_command == "light":
        if args.light_command == "set":
            robot.lights.set_color(args.color, brightness=args.brightness / 100.0, zone=_zone(args.zone))
            return _emit(args, {"state": "set", "zone": _zone(args.zone), "color": args.color.upper(), "brightness": args.brightness}, "Light updated")
        if args.light_command == "effect":
            effect = args.effect.replace("-", "_")
            job = robot.lights.play_effect(effect, color=args.color, brightness=args.brightness / 100.0, zone=_zone(args.zone), period_ms=args.period, repeat=args.repeat)
            return _emit(args, {"state": "started", "operation_id": job.id, "effect": effect}, f"Light effect started: {effect}")
        if args.light_command == "off":
            robot.lights.off()
            return _emit(args, {"state": "off", "zones": ["side", "bottom"]}, "Side and bottom lights are off")
        response = robot._command("ctrl.light.status.get", {})
        data = response.get("data", {})
        payload = {key: value for key, value in data.items() if key not in {"command_id", "type"}} if isinstance(data, dict) else {}
        return _emit(args, payload, "Light status")
    if args.robot_command == "screen":
        if args.screen_command == "list":
            response = robot._command("resource.catalog.get", {})
            data = response.get("data", {})
            official = data.get("official", {}) if isinstance(data, dict) else {}
            works = data.get("works", {}) if isinstance(data, dict) else {}
            payload = {
                "official": official.get("expressions", []) if isinstance(official, dict) else [],
                "works": works.get("works", []) if isinstance(works, dict) else [],
            }
            return _emit(args, payload, "Screen content")
        if args.screen_command == "play":
            job = robot.animation.play(args.animation_id)
            return _emit(args, {"state": "started", "operation_id": job.id, "animation_id": args.animation_id}, f"Animation started: {args.animation_id}")
        if args.screen_command == "play-work":
            robot.works.play_expression(args.work_id, clip_id=args.clip)
            return _emit(args, {"state": "started", "work_id": args.work_id, "clip": args.clip}, f"Work animation started: {args.work_id}/{args.clip}")
        robot.animation.stop()
        return _emit(args, {"state": "stopped"}, "Screen playback stopped")
    if args.robot_command == "recording":
        if args.recording_command == "list":
            items = [item.as_dict() for item in robot.recordings.list()]
            return _emit(args, {"recordings": items}, f"Recordings: {len(items)}")
        if args.recording_command == "status":
            info = robot.recordings.status(args.recording_id)
            return _emit(args, info.as_dict(), f"Recording {info.id}: {info.state}")
        if args.recording_command == "stop":
            info = robot.recordings.stop(args.recording_id)
            return _emit(args, info.as_dict(), f"Recording stopped: {info.id}")
        if args.recording_command == "delete":
            robot.recordings.delete(args.recording_id)
            return _emit(args, {"id": args.recording_id, "deleted": True}, f"Recording deleted: {args.recording_id}")
        return _download(args, robot)
    raise RobotCliError("unsupported robot hardware command")


def _record_and_download(args: argparse.Namespace, robot: WatcheRobot, mode: str) -> int:
    if args.storage == "host":
        return _record_to_host(args, robot, mode)
    video = mode in {"video", "av"}
    output = args.output or _timestamp_path("video" if video else "audio", ".mp4" if video else ".wav")
    kwargs = {"duration": args.duration}
    if video:
        kwargs.update(width=args.width, height=args.height, fps=args.fps, quality=args.quality)
    recording = robot.recordings.start(cast(RecordingMode, mode), **kwargs)
    _progress(args, {"event": "started", **recording.info.as_dict()})
    try:
        while True:
            time.sleep(5.0)
            info = recording.status()
            _progress(args, {"event": "progress", **info.as_dict()})
            if info.state in _TERMINAL_RECORDING_STATES:
                break
            robot.recordings.heartbeat(recording.id)
    except KeyboardInterrupt:
        info = recording.stop()
        _progress(args, {"event": "stopped", **info.as_dict()})
    if info.state == "failed":
        raise RobotCliError(f"Recording failed: {info.stop_reason or 'unknown'}")
    download_args = argparse.Namespace(
        recording_id=recording.id, output=output, raw_dir=None,
        json=getattr(args, "json", False), jsonl=getattr(args, "jsonl", False),
    )
    return _download(download_args, robot)


def _record_to_host(args: argparse.Namespace, robot: WatcheRobot, mode: str) -> int:
    video = mode in {"video", "av"}
    output = args.output or _timestamp_path("video" if video else "audio", ".mp4" if video else ".wav")
    raw_path = output.with_name(f".{output.stem}.wrec")
    partial = raw_path.with_name(raw_path.name + ".part")
    partial.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {"duration": args.duration}
    if video:
        kwargs.update(width=args.width, height=args.height, fps=args.fps, quality=args.quality)
    recording = robot.recordings.start_host(cast(RecordingMode, mode), **kwargs)
    _progress(args, {"event": "started", "storage": "host", **recording.info.as_dict()})
    info = recording.info
    last_progress = time.monotonic()
    stop_requested = False
    try:
        with partial.open("wb") as sink:
            while True:
                try:
                    frame = recording.read(timeout=1.0)
                except KeyboardInterrupt:
                    if stop_requested:
                        raise
                    info = recording.stop()
                    stop_requested = True
                    _progress(args, {"event": "stopping", "storage": "host", **info.as_dict()})
                    continue
                except TimeoutError:
                    now = time.monotonic()
                    if now - last_progress >= 5.0:
                        info = recording.status()
                        _progress(args, {"event": "progress", "storage": "host", **info.as_dict()})
                        if info.state in _TERMINAL_RECORDING_STATES:
                            raise RobotCliError("Host recording ended without a terminal stream marker")
                        robot.recordings.heartbeat(recording.id)
                        last_progress = now
                    continue
                sink.write(frame.payload)
                now = time.monotonic()
                if now - last_progress >= 5.0:
                    info = recording.status()
                    _progress(args, {"event": "progress", "storage": "host", **info.as_dict()})
                    if info.state in _TERMINAL_RECORDING_STATES and not frame.flags & FLAG_LAST:
                        raise RobotCliError("Host recording ended without a terminal stream marker")
                    if info.state not in _TERMINAL_RECORDING_STATES:
                        robot.recordings.heartbeat(recording.id)
                    last_progress = now
                if frame.flags & FLAG_LAST:
                    break
    except BaseException:
        try:
            recording.stop()
        except Exception:
            pass
        raise
    finally:
        recording.close()
    partial.replace(raw_path)
    records = list(iter_wrec_records([raw_path]))
    if mode == "audio":
        write_pcm_wav(records, output)
    else:
        mux_wrec_to_mp4(
            records,
            output,
            width=info.width or args.width,
            height=info.height or args.height,
            fps=info.fps or args.fps,
            with_audio=mode == "av",
        )
    payload = {
        "id": recording.id,
        "storage": "host",
        "path": str(output.resolve()),
        "raw_path": str(raw_path.resolve()),
        "bytes": raw_path.stat().st_size,
    }
    return _emit(args, payload, f"Recording saved: {output}")


def _download(args: argparse.Namespace, robot: WatcheRobot) -> int:
    info = robot.recordings.status(args.recording_id)
    output = args.output or _timestamp_path(info.id, ".wav" if info.mode == "audio" else ".mp4")
    raw_dir = args.raw_dir or output.with_name(f".{output.stem}.wrec")
    result = robot.recordings.download(info.id, raw_dir, progress=lambda event: _progress(args, {"event": "download", **event}))
    paths = sorted(raw_dir.glob("*.wrec"))
    records = list(iter_wrec_records(paths, tolerate_truncated_tail=info.state == "interrupted"))
    if info.mode == "audio":
        write_pcm_wav(records, output)
    else:
        mux_wrec_to_mp4(
            records,
            output,
            width=info.width or 640,
            height=info.height or 480,
            fps=info.fps or 5,
            with_audio=info.mode == "av",
        )
    payload = {"id": info.id, "path": str(output.resolve()), "raw_directory": str(raw_dir.resolve()), "resumed_bytes": result.resumed_bytes, "downloaded_bytes": result.downloaded_bytes}
    return _emit(args, payload, f"Recording downloaded: {output}")


def _run_maintenance(args: argparse.Namespace, control_url: str, request_json: Callable[..., dict[str, Any]]) -> int:
    transport, port, volume_id = _maintenance_target(args, control_url, request_json)
    if args.screen_command == "delete-work":
        request_json(
            control_url,
            "/daemon/maintenance/works/delete",
            method="POST",
            payload={"transport": transport, "port": port, "volume_id": volume_id, "work_id": args.work_id},
            timeout=75.0,
        )
        return _emit(args, {"work_id": args.work_id, "deleted": True}, f"Work deleted: {args.work_id}")
    source = args.file
    if source.suffix.lower() not in {".gif", ".png", ".jpg", ".jpeg", ".webp"}:
        raise RobotCliError("Screen install accepts GIF, PNG, JPEG, or WebP files")
    payload = source.read_bytes()
    if len(payload) > 2 * 1024 * 1024:
        raise RobotCliError("Screen source exceeds the 2 MiB limit")
    work_id = args.work_id or _stable_work_id(source, payload)
    if not _WORK_ID_PATTERN.fullmatch(work_id):
        raise RobotCliError("Work ID must match ^[a-z][a-z0-9_]{0,22}$")
    mime = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
    duration_ms = _screen_duration_ms(payload)
    composition = {
        "workId": work_id, "name": source.stem, "clips": [
            {"id": "main", "kind": "expression", "resourceId": "custom-expression-main", "label": source.stem, "startMs": 0, "durationMs": duration_ms}
        ],
        "assets": [{"id": "custom-expression-main", "kind": "expression", "name": source.stem, "fileName": source.name, "mimeType": mime, "dataUrl": f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"}],
    }
    response = request_json(control_url, "/daemon/maintenance/work", method="POST", payload={"composition": composition, "port": port, "transport": transport, "volume_id": volume_id}, timeout=30.0)
    job = response.get("job", {})
    job_id = job.get("id")
    job_state = _maintenance_job_state(job)
    last_progress_revision: tuple[Any, ...] | None = None
    while isinstance(job_id, str) and job_state not in _TERMINAL_MAINTENANCE_STATES:
        revision = _maintenance_job_revision(job)
        if revision != last_progress_revision:
            _progress(args, {"event": "install", **job})
            last_progress_revision = revision
        time.sleep(0.25)
        job = request_json(control_url, f"/daemon/maintenance/jobs/{job_id}").get("job", {})
        job_state = _maintenance_job_state(job)
    if job_state not in {"completed", "succeeded"}:
        raise RobotCliError(f"Screen install failed: {job.get('error') or job.get('message') or 'unknown'}")
    return _emit(args, {"work_id": work_id, "state": "completed", "job_id": job_id}, f"Work installed: {work_id}")


def _maintenance_target(args: argparse.Namespace, control_url: str, request_json: Callable[..., dict[str, Any]]) -> tuple[str, str, str]:
    if args.volume_id:
        return "card_reader", "", args.volume_id
    if args.port:
        return "serial", args.port, ""
    ports = request_json(control_url, "/daemon/maintenance/ports").get("ports", [])
    if len(ports) != 1:
        raise RobotCliError("Specify --port when zero or multiple maintenance serial ports are available")
    value = ports[0].get("device") if isinstance(ports[0], dict) else None
    if not isinstance(value, str) or not value:
        raise RobotCliError("The maintenance serial port could not be identified")
    return "serial", value, ""


def _maintenance_job_state(job: dict[str, Any]) -> str:
    """Accept both legacy ``state`` and current maintenance ``status`` fields."""
    value = job.get("state", job.get("status", ""))
    return value if isinstance(value, str) else ""


def _maintenance_job_revision(job: dict[str, Any]) -> tuple[Any, ...]:
    logs = job.get("logs")
    last_log = logs[-1] if isinstance(logs, list) and logs else None
    return (
        _maintenance_job_state(job),
        job.get("phase"),
        job.get("progress"),
        job.get("updated_at_ms"),
        len(logs) if isinstance(logs, list) else 0,
        last_log,
    )


def _emit(args: argparse.Namespace, payload: dict[str, Any], human: str) -> int:
    if getattr(args, "json", False) or getattr(args, "jsonl", False):
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        print(human)
        if payload and human in {"Robot capabilities", "Light status", "Screen content"}:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _progress(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if getattr(args, "jsonl", False):
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
    elif not getattr(args, "json", False):
        state = payload.get("state", payload.get("event", "working"))
        print(f"{payload.get('event', 'recording')}: {state}", flush=True)


def _timestamp_path(prefix: str, suffix: str) -> Path:
    return Path(f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}{suffix}")


def _stable_work_id(path: Path, payload: bytes) -> str:
    stem = re.sub(r"[^a-z0-9_]+", "_", path.stem.lower()).strip("_")
    if not stem or not stem[0].isalpha():
        stem = "work"
    digest = hashlib.sha256(payload).hexdigest()[:8]
    return f"{stem[:14].rstrip('_')}_{digest}"[:23]


def _screen_duration_ms(payload: bytes) -> int:
    from PIL import Image

    with Image.open(io.BytesIO(payload)) as image:
        frames = min(int(getattr(image, "n_frames", 1)), 120)
        if frames <= 1:
            return 1000
        duration = 0
        for index in range(frames):
            image.seek(index)
            duration += max(10, int(image.info.get("duration", 100)))
        return duration


def _zone(value: str) -> str:
    return "side" if value == "head" else value


def _loopback_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.hostname not in {"0.0.0.0", "::", "[::]"}:
        return url
    port = f":{parsed.port}" if parsed.port is not None else ""
    return urlunsplit((parsed.scheme, f"127.0.0.1{port}", parsed.path, parsed.query, parsed.fragment))
