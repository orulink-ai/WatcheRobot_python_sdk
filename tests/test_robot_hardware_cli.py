from __future__ import annotations

import argparse
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from watcherobot.cli import build_parser
from watcherobot.errors import CommandError
from watcherobot.protocol import FLAG_FIRST, FLAG_LAST, FRAME_RECORDING, BinaryFrame
from watcherobot.recordings import RecordingInfo, WREC_PCM, WrecRecord, encode_wrec_record
from watcherobot.robot_cli import (
    RobotCliError,
    _run_connected,
    _run_maintenance,
    _screen_duration_ms,
    _stable_work_id,
    run,
)


@pytest.mark.parametrize(
    "arguments,attributes",
    [
        (["robot", "camera", "capture"], {"camera_command": "capture"}),
        (["robot", "camera", "record", "--with-audio"], {"with_audio": True, "fps": 5, "storage": "host"}),
        (["robot", "audio", "record", "--duration", "2", "--storage", "device"], {"duration": 2.0, "storage": "device"}),
        (["robot", "light", "set", "--zone", "head", "--color", "#123456"], {"zone": "head"}),
        (["robot", "screen", "play-work", "demo", "--clip", "main"], {"clip": "main"}),
        (["robot", "recording", "download", "rec_1", "--jsonl"], {"recording_id": "rec_1", "jsonl": True}),
    ],
)
def test_hardware_command_tree(arguments: list[str], attributes: dict[str, object]) -> None:
    parsed = build_parser().parse_args(arguments)
    for name, value in attributes.items():
        assert getattr(parsed, name) == value


def test_hardware_cli_rejects_active_application_before_opening_device_session() -> None:
    args = build_parser().parse_args(["robot", "capabilities"])
    def request(_base: str, path: str, **_kwargs):
        assert path == "/daemon/status"
        return {"application": {"state": "running"}}

    with pytest.raises(RobotCliError, match="app stop"):
        run(args, runtime_state=SimpleNamespace(control_url="http://x", external_url="ws://x"), request_json=request)


def test_stable_work_id_is_legal_and_content_addressed() -> None:
    first = _stable_work_id(Path("我的 动画.GIF"), b"one")
    second = _stable_work_id(Path("我的 动画.GIF"), b"two")
    assert first.startswith("work_")
    assert len(first) <= 23
    assert first != second


def test_screen_duration_preserves_gif_frame_timing() -> None:
    payload = io.BytesIO()
    frames = [Image.new("RGB", (2, 2), color) for color in ("red", "blue")]
    frames[0].save(payload, format="GIF", save_all=True, append_images=frames[1:], duration=[120, 230])

    assert _screen_duration_ms(payload.getvalue()) == 350


def test_screen_install_rejects_invalid_explicit_work_id_before_submission(tmp_path: Path) -> None:
    source = tmp_path / "screen.png"
    Image.new("RGB", (2, 2), "red").save(source)
    args = build_parser().parse_args(
        ["robot", "screen", "install", str(source), "--id", "invalid-id", "--port", "COM26"]
    )

    def request(*_args, **_kwargs):
        raise AssertionError("invalid work ID must not create a maintenance job")

    with pytest.raises(RobotCliError, match="Work ID"):
        _run_maintenance(args, "http://x", request)


def test_screen_delete_allows_serial_firmware_to_finish_refreshing_catalog() -> None:
    args = build_parser().parse_args(
        ["robot", "screen", "delete-work", "valid_screen", "--port", "COM26", "--json"]
    )
    observed: dict[str, object] = {}

    def request(_base: str, path: str, **kwargs):
        observed.update(path=path, **kwargs)
        return {"ok": True}

    assert _run_maintenance(args, "http://x", request) == 0
    assert observed["path"] == "/daemon/maintenance/works/delete"
    assert observed["timeout"] == 75.0


def test_screen_install_stops_on_current_status_field_failure(tmp_path: Path) -> None:
    source = tmp_path / "screen.png"
    Image.new("RGB", (2, 2), "red").save(source)
    args = build_parser().parse_args(
        ["robot", "screen", "install", str(source), "--id", "valid_screen", "--port", "COM26", "--jsonl"]
    )
    requests = 0

    def request(_base: str, path: str, **_kwargs):
        nonlocal requests
        requests += 1
        assert path == "/daemon/maintenance/work"
        return {"job": {"id": "job-1", "status": "failed", "error": "conversion failed"}}

    with pytest.raises(RobotCliError, match="conversion failed"):
        _run_maintenance(args, "http://x", request)
    assert requests == 1


def test_screen_install_jsonl_suppresses_unchanged_job_polls(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "screen.png"
    Image.new("RGB", (2, 2), "red").save(source)
    args = build_parser().parse_args(
        ["robot", "screen", "install", str(source), "--id", "valid_screen", "--port", "COM26", "--jsonl"]
    )
    running = {
        "id": "job-1",
        "status": "running",
        "phase": "installing",
        "progress": 82,
        "updated_at_ms": 1,
        "logs": ["working"],
    }
    replies = iter([{"job": running}, {"job": running}, {"job": {**running, "status": "succeeded"}}])
    monkeypatch.setattr("watcherobot.robot_cli.time.sleep", lambda _seconds: None)

    assert _run_maintenance(args, "http://x", lambda *_args, **_kwargs: next(replies)) == 0
    output = capsys.readouterr().out.splitlines()
    assert sum('"event":"install"' in line for line in output) == 1


def test_streaming_output_modes_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["robot", "camera", "record", "--json", "--jsonl"])


def test_hardware_cli_translates_camera_timeout_to_stable_error(monkeypatch) -> None:
    args = build_parser().parse_args(["robot", "camera", "capture", "--json"])

    class FakeSession:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def start(self) -> None:
            pass

    class FakeRobot:
        def __init__(self, _session) -> None:
            self.camera = SimpleNamespace(capture=lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("camera unavailable")))

        def close(self) -> None:
            pass

    monkeypatch.setattr("watcherobot.robot_cli.DesktopRobotSession", FakeSession)
    monkeypatch.setattr("watcherobot.robot_cli.WatcheRobot", FakeRobot)

    def request(_base: str, path: str, **_kwargs):
        if path == "/daemon/status":
            return {"application": {"state": "stopped"}}
        return {"device": {"online": True}}

    with pytest.raises(RobotCliError, match="camera unavailable"):
        run(args, runtime_state=SimpleNamespace(control_url="http://x", external_url="ws://x"), request_json=request)


def test_audio_play_uses_media_duration_bounded_wait(tmp_path: Path) -> None:
    source = tmp_path / "tone.wav"
    source.write_bytes(b"placeholder")
    waits: list[float | None] = []

    class FakePlayback:
        id = 7
        expected_duration_seconds = 1.25

        def wait(self, timeout: float | None = None) -> None:
            waits.append(timeout)

        def cancel(self) -> None:
            raise AssertionError("successful playback must not be cancelled")

    robot = SimpleNamespace(audio=SimpleNamespace(play_file=lambda _path: FakePlayback()))
    args = build_parser().parse_args(["robot", "audio", "play", str(source), "--json"])

    assert _run_connected(args, robot) == 0
    assert waits == [15.0]


def test_audio_play_timeout_cancels_device_stream(tmp_path: Path) -> None:
    source = tmp_path / "tone.wav"
    source.write_bytes(b"placeholder")
    cancelled = False

    class FakePlayback:
        id = 8
        expected_duration_seconds = 20.0

        def wait(self, timeout: float | None = None) -> None:
            assert timeout == 30.0
            raise TimeoutError("stalled")

        def cancel(self) -> None:
            nonlocal cancelled
            cancelled = True

    robot = SimpleNamespace(audio=SimpleNamespace(play_file=lambda _path: FakePlayback()))
    args = build_parser().parse_args(["robot", "audio", "play", str(source), "--json"])

    with pytest.raises(RobotCliError, match="safety timeout"):
        _run_connected(args, robot)
    assert cancelled


def test_capabilities_tolerates_older_firmware_without_storage(capsys) -> None:
    robot = SimpleNamespace(
        capabilities=("camera.capture",),
        device_info={"firmware_version": "old"},
        _command=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CommandError("resource.storage.get", "unsupported_type")
        ),
    )
    args = build_parser().parse_args(["robot", "capabilities", "--json"])

    assert _run_connected(args, robot) == 0
    assert '"storage":{"available":false}' in capsys.readouterr().out


def test_audio_record_defaults_to_reliable_host_stream_and_writes_atomic_wav(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "recording.wav"
    encoded = encode_wrec_record(WrecRecord(WREC_PCM, 0, 0, 10, b"\x01\x00" * 160))
    frames = iter([
        BinaryFrame(FRAME_RECORDING, FLAG_FIRST, 9, 0, encoded[:17]),
        BinaryFrame(FRAME_RECORDING, FLAG_LAST, 9, 1, encoded[17:]),
    ])
    info = RecordingInfo("host_1", "audio", "recording")

    class Recording:
        id = "host_1"
        stream_id = 9

        def __init__(self) -> None:
            self.info = info
            self.closed = False

        def read(self, timeout=None):
            del timeout
            return next(frames)

        def stop(self):
            raise AssertionError("duration-completed recording must not be stopped")

        def status(self):
            return self.info

        def close(self):
            self.closed = True

    recording = Recording()
    calls: list[tuple[str, dict[str, object]]] = []
    heartbeats: list[str] = []

    def start_host(mode: str, **kwargs):
        calls.append((mode, kwargs))
        return recording

    robot = SimpleNamespace(recordings=SimpleNamespace(
        start_host=start_host,
        heartbeat=heartbeats.append,
    ))
    clock = iter((0.0, 6.0, 7.0))
    monkeypatch.setattr("watcherobot.robot_cli.time.monotonic", lambda: next(clock))
    args = build_parser().parse_args(
        ["robot", "audio", "record", "--duration", "0.01", "-o", str(output), "--json"]
    )

    assert _run_connected(args, robot) == 0
    assert calls == [("audio", {"duration": 0.01})]
    assert heartbeats == ["host_1"]
    assert output.is_file()
    assert output.with_name(".recording.wrec").is_file()
    assert not output.with_name(".recording.wrec.part").exists()
    assert recording.closed


def test_host_recording_disconnect_preserves_partial_without_publishing_output(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "interrupted.wav"
    payload = encode_wrec_record(WrecRecord(WREC_PCM, 0, 0, 10, b"\x01\x00" * 160))

    class Recording:
        id = "host_2"
        stream_id = 10
        info = RecordingInfo("host_2", "audio", "recording")

        def __init__(self) -> None:
            self.reads = 0
            self.stop_calls = 0
            self.closed = False

        def read(self, timeout=None):
            del timeout
            self.reads += 1
            if self.reads == 1:
                return BinaryFrame(FRAME_RECORDING, FLAG_FIRST, 10, 0, payload)
            raise RuntimeError("robot connection is closed")

        def stop(self):
            self.stop_calls += 1
            raise RuntimeError("robot connection is closed")

        def close(self):
            self.closed = True

    recording = Recording()
    robot = SimpleNamespace(recordings=SimpleNamespace(start_host=lambda *_args, **_kwargs: recording))
    monkeypatch.setattr("watcherobot.robot_cli.time.monotonic", lambda: 0.0)
    args = build_parser().parse_args(
        ["robot", "audio", "record", "--duration", "30", "-o", str(output)]
    )

    with pytest.raises(RuntimeError, match="connection is closed"):
        _run_connected(args, robot)

    partial = output.with_name(".interrupted.wrec.part")
    assert partial.read_bytes() == payload
    assert not output.exists()
    assert not output.with_name(".interrupted.wrec").exists()
    assert recording.stop_calls == 1
    assert recording.closed
