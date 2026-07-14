from __future__ import annotations

import queue
import re
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from ._internal.audio_status import AudioStatusKind, classify_audio_status
from .errors import CommandError, WatcheRobotError
from .audio import AudioPlayback, PCMAudio, load_pcm_wave
from .job import Job, JobState
from .media import AudioFormat, AudioRecording, ImageFrame, MicrophoneSession
from .protocol import (
    DISCOVERY_PORT,
    FLAG_FIRST,
    FLAG_FRAGMENT,
    FLAG_LAST,
    FRAME_AUDIO,
    FRAME_IMAGE,
    WEBSOCKET_PORT,
    BinaryFrame,
)
from .transport import BackgroundTransport


MAX_IMAGE_BYTES = 8 * 1024 * 1024


@dataclass
class _ImageAssembly:
    next_sequence: int
    data: bytearray


class _Domain:
    def __init__(self, robot: WatcheRobot) -> None:
        self._robot = robot


class BehaviorDomain(_Domain):
    def play(self, behavior_id: str, *, repeat: int = 1) -> Job:
        if not behavior_id or repeat <= 0:
            raise ValueError("behavior_id and a positive repeat are required")
        return self._robot._start_job("ctrl.behavior.play", {"behavior_id": behavior_id, "repeat": repeat})

    def stop(self) -> None:
        self._robot._command("ctrl.behavior.stop", {})


class AnimationDomain(_Domain):
    def play(self, animation_id: str) -> Job:
        if not animation_id:
            raise ValueError("animation_id is required")
        return self._robot._start_job("ctrl.animation.play", {"animation_id": animation_id})

    def stop(self) -> None:
        self._robot._command("ctrl.animation.stop", {})


class MotionDomain(_Domain):
    def move_to(
        self,
        *,
        pan_deg: int,
        tilt_deg: int,
        duration_ms: int,
        profile: str = "ease_in_out",
    ) -> Job:
        if (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, int)
            or not 1 <= duration_ms <= 65535
        ):
            raise ValueError("duration_ms must be an integer between 1 and 65535")
        return self._robot._start_job(
            "ctrl.motion.move_to",
            {
                "pan_deg": int(pan_deg),
                "tilt_deg": int(tilt_deg),
                "duration_ms": duration_ms,
                "profile": profile,
            },
        )

    def set_target(self, *, pan_deg: int | None = None, tilt_deg: int | None = None) -> None:
        if pan_deg is None and tilt_deg is None:
            raise ValueError("pan_deg or tilt_deg is required")
        data = {}
        if pan_deg is not None:
            data["pan_deg"] = int(pan_deg)
        if tilt_deg is not None:
            data["tilt_deg"] = int(tilt_deg)
        self._robot._command("ctrl.motion.set_target", data)

    def play_action(self, action_id: str) -> Job:
        if not action_id:
            raise ValueError("action_id is required")
        return self._robot._start_job("ctrl.motion.action.play", {"action_id": action_id})

    def stop(self) -> None:
        self._robot._command("ctrl.motion.stop", {})


class AudioDomain(_Domain):
    def play(self, sound_id: str) -> Job:
        if not sound_id:
            raise ValueError("sound_id is required")
        return self._robot._start_local_audio(sound_id)

    def play_file(self, path: str | Path) -> AudioPlayback:
        return self._robot._start_audio_playback(load_pcm_wave(path))

    def play_pcm(
        self,
        data: bytes,
        *,
        sample_rate_hz: int = 24000,
        channels: int = 1,
        sample_width_bytes: int = 2,
    ) -> AudioPlayback:
        return self._robot._start_audio_playback(
            PCMAudio(
                bytes(data),
                AudioFormat(
                    sample_rate_hz=sample_rate_hz,
                    channels=channels,
                    sample_width_bytes=sample_width_bytes,
                    encoding="pcm_s16le",
                ),
            )
        )

    def stop(self) -> None:
        self._robot._stop_audio_playback()


class LightsDomain(_Domain):
    def set_color(self, color: str, *, brightness: float = 1.0, zone: str = "all") -> None:
        _validate_light(color, brightness)
        self._robot._command(
            "ctrl.light.set", {"color": color.upper(), "brightness": brightness, "zone": zone}
        )

    def play_effect(
        self,
        effect: str,
        *,
        color: str = "#FFFFFF",
        brightness: float = 1.0,
        zone: str = "all",
        period_ms: int = 500,
        repeat: int = 0,
    ) -> Job:
        _validate_light(color, brightness)
        if not effect or repeat < 0:
            raise ValueError("invalid light effect options")
        if (
            isinstance(period_ms, bool)
            or not isinstance(period_ms, int)
            or not 0 <= period_ms <= 65535
        ):
            raise ValueError("period_ms must be an integer between 0 and 65535")
        return self._robot._start_job(
            "ctrl.light.effect.play",
            {
                "effect": effect,
                "color": color.upper(),
                "brightness": brightness,
                "zone": zone,
                "period_ms": period_ms,
                "repeat": repeat,
            },
        )

    def off(self) -> None:
        self._robot._command("ctrl.light.off", {})


class MicrophoneDomain(_Domain):
    def open(self, *, queue_size: int = 32) -> MicrophoneSession:
        return self._robot._open_microphone(queue_size=queue_size)

    def record(
        self,
        *,
        duration: float,
        timeout: float | None = None,
        queue_size: int = 32,
    ) -> AudioRecording:
        if duration <= 0:
            raise ValueError("duration must be positive")
        if timeout is not None and timeout <= 0:
            raise ValueError("timeout must be positive")

        with self.open(queue_size=queue_size) as microphone:
            audio_format = microphone.format
            target_frames = max(1, round(audio_format.sample_rate_hz * duration))
            target_bytes = (
                target_frames
                * audio_format.channels
                * audio_format.sample_width_bytes
            )
            deadline = time.monotonic() + (timeout if timeout is not None else duration + 2.0)
            chunks: list[bytes] = []
            recorded_bytes = 0
            while recorded_bytes < target_bytes:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    frame = microphone.read(timeout=min(1.0, remaining))
                except TimeoutError:
                    continue
                chunks.append(frame.data)
                recorded_bytes += len(frame.data)
            dropped_frames = microphone.dropped_frames

        pcm = b"".join(chunks)[:target_bytes]
        if len(pcm) != target_bytes:
            raise TimeoutError(
                f"microphone recording incomplete: expected {target_bytes} bytes, got {len(pcm)}"
            )
        return AudioRecording(
            data=pcm,
            format=audio_format,
            dropped_frames=dropped_frames,
        )


class CameraDomain(_Domain):
    def capture(
        self,
        *,
        width: int = 0,
        height: int = 0,
        quality: int = 0,
        timeout: float = 5.0,
    ) -> ImageFrame:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        return self._robot._capture_image(width=width, height=height, quality=quality, timeout=timeout)


def _validate_light(color: str, brightness: float) -> None:
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", color) is None:
        raise ValueError("color must use #RRGGBB")
    if not 0.0 <= brightness <= 1.0:
        raise ValueError("brightness must be between 0 and 1")


class WatcheRobot:
    def __init__(self, transport: Any) -> None:
        self._transport = transport
        self._jobs: dict[int, Job] = {}
        self._pending_job_events: dict[int, tuple[str, int | None, str | None]] = {}
        self._terminal_job_ids: dict[int, None] = {}
        self._jobs_lock = threading.Lock()
        self._microphone: MicrophoneSession | None = None
        self._microphone_opening = False
        self._pending_audio_frames: list[tuple[bytes, int, int, int]] = []
        self._media_lock = threading.Lock()
        self._image_queue: queue.Queue[ImageFrame] = queue.Queue(maxsize=1)
        self._image_assemblies: dict[int, _ImageAssembly] = {}
        self._image_assembly_lock = threading.Lock()
        self._camera_lock = threading.Lock()
        self._audio_playback_lock = threading.Lock()
        self._audio_api_lock = threading.Lock()
        self._audio_playback: AudioPlayback | None = None
        self._audio_send_future: Any | None = None
        self._audio_cleanup_future: Any | None = None
        self._audio_cleanup_required = False
        self._audio_transition_in_progress = False
        self._next_audio_stream_id = 1
        self._closed = False
        self._closing = False
        self.behavior = BehaviorDomain(self)
        self.animation = AnimationDomain(self)
        self.motion = MotionDomain(self)
        self.audio = AudioDomain(self)
        self.lights = LightsDomain(self)
        self.microphone = MicrophoneDomain(self)
        self.camera = CameraDomain(self)
        transport.set_callbacks(self._on_message, self._on_binary, self._on_disconnect)

    @classmethod
    def connect(
        cls,
        *,
        pairing_code: str,
        discovery_port: int = DISCOVERY_PORT,
        websocket_port: int = WEBSOCKET_PORT,
        timeout: float = 15.0,
        host: str = "0.0.0.0",
    ) -> WatcheRobot:
        transport = BackgroundTransport(
            discovery_port=discovery_port,
            websocket_port=websocket_port,
            host=host,
        )
        robot = cls(transport)
        try:
            transport.start(pairing_code, timeout=timeout)
        except Exception:
            robot.close()
            raise
        return robot

    @classmethod
    def _from_transport(cls, transport: Any) -> WatcheRobot:
        return cls(transport)

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(self._transport.capabilities)

    @property
    def device_info(self) -> dict[str, Any]:
        return dict(self._transport.device_info)

    def supports(self, capability: str) -> bool:
        if not isinstance(capability, str) or not capability:
            raise ValueError("capability must be a non-empty string")
        return capability in self.capabilities

    def close(self) -> None:
        with self._media_lock:
            if self._closed or self._closing:
                return
            self._closing = True
            microphone = self._microphone
        if microphone is not None and not microphone.closed:
            try:
                microphone.close()
            except Exception:
                pass
        with self._media_lock:
            self._closed = True
            self._closing = False
        self._transport.close()
        self._fail_all_jobs(reason="connection_closed")

    def __enter__(self) -> WatcheRobot:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _command(self, message_type: str, data: dict, timeout: float | None = None) -> dict:
        if self._closed:
            raise WatcheRobotError("robot connection is closed")
        return self._transport.send_command(message_type, data, timeout=timeout)

    def _start_job(self, message_type: str, data: dict) -> Job:
        response = self._command(message_type, data)
        operation_id = response.get("data", {}).get("operation_id")
        if not isinstance(operation_id, int) or operation_id <= 0:
            raise WatcheRobotError(f"{message_type} ACK did not include operation_id")
        job = Job(operation_id, self._transport)
        with self._jobs_lock:
            self._jobs[operation_id] = job
            pending_event = self._pending_job_events.pop(operation_id, None)
        if pending_event is not None:
            job._update(pending_event[0], pending_event[1], pending_event[2])
            if job.state.terminal:
                with self._jobs_lock:
                    if self._jobs.get(operation_id) is job:
                        self._jobs.pop(operation_id, None)
                    self._remember_terminal_job_locked(operation_id)
        return job

    def _remember_terminal_job_locked(self, operation_id: int) -> None:
        if len(self._terminal_job_ids) >= 64:
            self._terminal_job_ids.pop(next(iter(self._terminal_job_ids)))
        self._terminal_job_ids[operation_id] = None

    def _replace_audio_playback(self) -> None:
        with self._audio_playback_lock:
            playback = self._audio_playback
            send_future = self._audio_send_future
            self._audio_playback = None
            self._audio_send_future = None
        if playback is not None and not playback.state.terminal:
            playback._update(JobState.CANCELLED)
        if send_future is not None and not send_future.done():
            send_future.cancel()

    def _cancel_audio_sender(self) -> None:
        with self._audio_playback_lock:
            send_future = self._audio_send_future
            self._audio_send_future = None
        if send_future is not None and not send_future.done():
            send_future.cancel()

    def _start_local_audio(self, sound_id: str) -> Job:
        with self._audio_api_lock:
            self._begin_audio_transition()
            try:
                job = self._start_job("ctrl.audio.play", {"sound_id": sound_id})
            except Exception:
                self._end_audio_transition(command_succeeded=False)
                raise
            self._replace_audio_playback()
            self._end_audio_transition(command_succeeded=True)
            return job

    def _start_audio_playback(self, audio: PCMAudio) -> AudioPlayback:
        if "audio.stream" not in self.capabilities:
            raise WatcheRobotError("robot firmware does not advertise audio.stream")
        with self._audio_api_lock:
            self._begin_audio_transition()
            with self._audio_playback_lock:
                stream_id = self._next_audio_stream_id
                self._next_audio_stream_id = 1 if stream_id >= 0xFFFF else stream_id + 1
            try:
                self._command(
                    "ctrl.audio.stream.begin",
                    {
                        "stream_id": stream_id,
                        "total_bytes": len(audio.data),
                        "sample_rate_hz": audio.audio_format.sample_rate_hz,
                        "channels": audio.audio_format.channels,
                        "sample_width_bytes": audio.audio_format.sample_width_bytes,
                        "audio_sha256": audio.sha256,
                    },
                )
            except Exception:
                self._end_audio_transition(command_succeeded=False)
                raise
            self._replace_audio_playback()
            playback = AudioPlayback(
                stream_id,
                self._transport,
                audio.sha256,
                self._cancel_audio_playback,
            )
            with self._audio_playback_lock:
                self._audio_playback = playback
            try:
                send_future = self._transport.send_audio_stream(audio.data, stream_id=stream_id)
                with self._audio_playback_lock:
                    if self._audio_playback is playback:
                        self._audio_send_future = send_future
            except Exception:
                self._handle_audio_sender_failure(playback)
                self._end_audio_transition(command_succeeded=False)
                raise

            self._end_audio_transition(command_succeeded=True)

            def finish_send(future: Future[None]) -> None:
                if future.cancelled():
                    return
                try:
                    future.result()
                except Exception:
                    self._handle_audio_sender_failure(playback)

            send_future.add_done_callback(finish_send)
            return playback

    def _begin_audio_transition(self) -> None:
        while True:
            with self._audio_playback_lock:
                if self._audio_cleanup_required and self._audio_cleanup_future is None:
                    self._schedule_audio_cleanup_locked()
                cleanup_future = self._audio_cleanup_future
                if cleanup_future is None:
                    self._audio_transition_in_progress = True
                    return
            self._wait_for_audio_cleanup(cleanup_future)

    def _end_audio_transition(self, *, command_succeeded: bool) -> None:
        cleanup_future = None
        with self._audio_playback_lock:
            self._audio_transition_in_progress = False
            if self._audio_cleanup_required:
                if command_succeeded:
                    self._audio_cleanup_required = False
                else:
                    self._schedule_audio_cleanup_locked()
                    cleanup_future = self._audio_cleanup_future
        if cleanup_future is not None:
            self._wait_for_audio_cleanup(cleanup_future)

    def _schedule_audio_cleanup_locked(self) -> None:
        self._audio_cleanup_required = False
        try:
            self._audio_cleanup_future = self._transport.send_command_nowait(
                "ctrl.audio.stop",
                {},
            )
        except Exception:
            self._audio_cleanup_future = None

    def _wait_for_audio_cleanup(self, cleanup_future: Any) -> None:
        timeout = getattr(self._transport, "command_timeout", 5.0) + 1.0
        try:
            cleanup_future.result(timeout=timeout)
        except Exception:
            cleanup_future.cancel()
        finally:
            with self._audio_playback_lock:
                if self._audio_cleanup_future is cleanup_future:
                    self._audio_cleanup_future = None

    def _handle_audio_sender_failure(self, playback: AudioPlayback) -> None:
        with self._audio_playback_lock:
            if self._audio_playback is not playback:
                return
            self._audio_playback = None
            self._audio_send_future = None
            self._audio_cleanup_required = True
            if not self._audio_transition_in_progress:
                self._schedule_audio_cleanup_locked()
        playback._update(JobState.FAILED, reason="audio_send_failed")

    def _cancel_audio_playback(self, playback: AudioPlayback) -> None:
        with self._audio_api_lock:
            with self._audio_playback_lock:
                if self._audio_playback is not playback or playback.state.terminal:
                    return
            self._begin_audio_transition()
            try:
                self._command("ctrl.audio.stop", {})
            except Exception:
                self._end_audio_transition(command_succeeded=False)
                raise
            self._cancel_audio_sender()
            playback._update(JobState.CANCELLED)
            with self._audio_playback_lock:
                if self._audio_playback is playback:
                    self._audio_playback = None
            self._end_audio_transition(command_succeeded=True)

    def _stop_audio_playback(self) -> None:
        with self._audio_api_lock:
            self._begin_audio_transition()
            try:
                self._command("ctrl.audio.stop", {})
            except Exception:
                self._end_audio_transition(command_succeeded=False)
                raise
            self._replace_audio_playback()
            self._end_audio_transition(command_succeeded=True)

    def _open_microphone(self, *, queue_size: int) -> MicrophoneSession:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        with self._media_lock:
            if self._closed or self._closing:
                raise WatcheRobotError("robot connection is closed")
            if (
                self._microphone_opening
                or self._microphone is not None
                and not self._microphone.closed
            ):
                raise WatcheRobotError("microphone session is already open or opening")
            self._microphone_opening = True
            self._pending_audio_frames.clear()
        try:
            response = self._command("ctrl.microphone.open", {"sample_rate_hz": 16000})
        except Exception:
            with self._media_lock:
                self._microphone_opening = False
                self._pending_audio_frames.clear()
            raise
        session_id = response.get("data", {}).get("session_id")
        if not isinstance(session_id, int) or session_id <= 0:
            with self._media_lock:
                self._microphone_opening = False
                self._pending_audio_frames.clear()
            raise WatcheRobotError("microphone ACK did not include session_id")
        session = MicrophoneSession(self, session_id, audio_format=AudioFormat(), queue_size=queue_size)
        with self._media_lock:
            if self._closed or self._closing:
                self._microphone_opening = False
                self._pending_audio_frames.clear()
                raise WatcheRobotError("robot connection closed while opening microphone")
            self._microphone = session
            buffered_frames = self._pending_audio_frames
            self._pending_audio_frames = []
            self._microphone_opening = False
        expected_stream_id = session_id & 0xFFFF
        for payload, sequence, flags, stream_id in buffered_frames:
            if stream_id not in (0, expected_stream_id):
                continue
            if payload:
                session._push(payload, sequence)
            if flags & FLAG_LAST:
                session._mark_remote_closed()
        return session

    def _close_microphone(self, session_id: int) -> None:
        try:
            self._command("ctrl.microphone.close", {"session_id": session_id})
        finally:
            with self._media_lock:
                if self._microphone is not None and self._microphone.id == session_id:
                    self._microphone = None

    def _capture_image(self, *, width: int, height: int, quality: int, timeout: float) -> ImageFrame:
        with self._camera_lock:
            with self._image_assembly_lock:
                self._image_assemblies.clear()
            while True:
                try:
                    self._image_queue.get_nowait()
                except queue.Empty:
                    break
            deadline = time.monotonic() + max(timeout, 0)
            first_attempt = True
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 and not first_attempt:
                    raise TimeoutError("camera remained busy before capture timeout")
                try:
                    response = self._command(
                        "ctrl.camera.capture",
                        {"width": int(width), "height": int(height), "quality": int(quality)},
                        timeout=max(remaining, 0),
                    )
                    break
                except TimeoutError as error:
                    raise TimeoutError("camera capture command was not acknowledged before timeout") from error
                except CommandError as error:
                    first_attempt = False
                    if error.reason != "busy":
                        raise
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("camera remained busy before capture timeout") from error
                    time.sleep(min(0.1, remaining))
            session_id = response.get("data", {}).get("session_id")
            if not isinstance(session_id, int) or session_id <= 0:
                raise WatcheRobotError("camera ACK did not include session_id")
            expected_stream_id = session_id & 0xFFFF
            while True:
                try:
                    image = self._image_queue.get_nowait()
                except queue.Empty:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("camera did not return a JPEG before timeout")
                    try:
                        image = self._image_queue.get(timeout=remaining)
                    except queue.Empty as error:
                        raise TimeoutError("camera did not return a JPEG before timeout") from error
                if image.session_id in (0, expected_stream_id):
                    return image

    def _on_message(self, message: dict[str, Any]) -> None:
        if message.get("type") == "evt.audio.buffer_status":
            self._on_audio_buffer_status(message.get("data", {}))
            return
        if message.get("type") != "evt.sdk.operation":
            return
        data = message.get("data", {})
        operation_id = data.get("operation_id")
        if not isinstance(operation_id, int):
            return
        state = data.get("state", "failed")
        error_code = data.get("error_code")
        reason = data.get("reason")
        if not isinstance(reason, str):
            reason = None
        with self._jobs_lock:
            if operation_id in self._terminal_job_ids:
                return
            job = self._jobs.get(operation_id)
            if job is None:
                if len(self._pending_job_events) >= 32:
                    self._pending_job_events.pop(next(iter(self._pending_job_events)))
                self._pending_job_events[operation_id] = (state, error_code, reason)
        if job is None:
            return
        try:
            job._update(state, error_code, reason)
        except ValueError:
            job._update(JobState.FAILED, reason="invalid_operation_state")
        if job.state.terminal:
            with self._jobs_lock:
                if self._jobs.get(operation_id) is job:
                    self._jobs.pop(operation_id, None)
                self._remember_terminal_job_locked(operation_id)

    def _on_audio_buffer_status(self, data: dict[str, Any]) -> None:
        with self._audio_playback_lock:
            playback = self._audio_playback
        if playback is None or playback.state.terminal:
            return
        stream_id = data.get("stream_id", 0)
        if stream_id != playback.id:
            return
        reason = data.get("reason", "")
        status = classify_audio_status(reason)
        if status is AudioStatusKind.COMPLETED:
            actual_sha256 = data.get("audio_sha256")
            if isinstance(actual_sha256, str) and actual_sha256 != playback.expected_sha256:
                playback._update(JobState.FAILED, reason="checksum_mismatch")
            else:
                playback._update(JobState.COMPLETED)
        elif status is AudioStatusKind.FAILED:
            playback._update(JobState.FAILED, reason=str(reason) or "audio_failed")
        elif status is AudioStatusKind.CANCELLED:
            playback._update(JobState.CANCELLED, reason=str(reason) or "cancelled")
        else:
            playback._update(JobState.RUNNING)
        if playback.state.terminal:
            send_future = None
            with self._audio_playback_lock:
                if self._audio_playback is playback:
                    send_future = self._audio_send_future
                    self._audio_playback = None
                    self._audio_send_future = None
            if send_future is not None and not send_future.done():
                send_future.cancel()

    def _on_binary(self, frame: BinaryFrame) -> None:
        if frame.frame_type == FRAME_AUDIO:
            with self._media_lock:
                microphone = self._microphone
                if microphone is None and self._microphone_opening:
                    if len(self._pending_audio_frames) >= 4:
                        self._pending_audio_frames.pop(0)
                    self._pending_audio_frames.append(
                        (frame.payload, frame.sequence, frame.flags, frame.stream_id)
                    )
                    return
            if microphone is not None:
                if frame.stream_id not in (0, microphone.id & 0xFFFF):
                    return
                if frame.payload:
                    microphone._push(frame.payload, frame.sequence)
                if frame.flags & FLAG_LAST:
                    microphone._mark_remote_closed()
            return
        if frame.frame_type == FRAME_IMAGE:
            self._on_image_frame(frame)

    def _on_image_frame(self, frame: BinaryFrame) -> None:
        if not frame.flags & FLAG_FRAGMENT:
            if not frame.payload:
                return
            self._enqueue_image(
                ImageFrame(frame.payload, frame.sequence, time.time(), session_id=frame.stream_id)
            )
            return

        with self._image_assembly_lock:
            if frame.flags & FLAG_FIRST:
                if len(self._image_assemblies) >= 2:
                    self._image_assemblies.pop(next(iter(self._image_assemblies)))
                self._image_assemblies[frame.stream_id] = _ImageAssembly(
                    next_sequence=frame.sequence,
                    data=bytearray(),
                )
            assembly = self._image_assemblies.get(frame.stream_id)
            if assembly is None or frame.sequence != assembly.next_sequence:
                self._image_assemblies.pop(frame.stream_id, None)
                return
            if len(assembly.data) + len(frame.payload) > MAX_IMAGE_BYTES:
                self._image_assemblies.pop(frame.stream_id, None)
                return
            assembly.data.extend(frame.payload)
            assembly.next_sequence = (frame.sequence + 1) & 0xFFFFFFFF
            if not frame.flags & FLAG_LAST:
                return
            payload = bytes(assembly.data)
            self._image_assemblies.pop(frame.stream_id, None)

        self._enqueue_image(
            ImageFrame(payload, frame.sequence, time.time(), session_id=frame.stream_id)
        )

    def _enqueue_image(self, image: ImageFrame) -> None:
        try:
            self._image_queue.put_nowait(image)
        except queue.Full:
            try:
                self._image_queue.get_nowait()
            except queue.Empty:
                pass
            self._image_queue.put_nowait(image)

    def _on_disconnect(self) -> None:
        with self._media_lock:
            was_closed = self._closed
            self._closed = True
            self._closing = False
            self._microphone_opening = False
            self._pending_audio_frames.clear()
            microphone = self._microphone
        self._fail_all_jobs(reason="disconnected")
        if microphone is not None:
            microphone._mark_remote_closed()
        if not was_closed:
            self._transport.close()

    def _fail_all_jobs(self, *, reason: str) -> None:
        with self._jobs_lock:
            jobs = list(self._jobs.values())
            self._jobs.clear()
            self._pending_job_events.clear()
            self._terminal_job_ids.clear()
        for job in jobs:
            if not job.state.terminal:
                job._update(JobState.FAILED, reason=reason)
        with self._audio_playback_lock:
            playback = self._audio_playback
            send_future = self._audio_send_future
            cleanup_future = self._audio_cleanup_future
            self._audio_playback = None
            self._audio_send_future = None
            self._audio_cleanup_future = None
            self._audio_cleanup_required = False
            self._audio_transition_in_progress = False
        if send_future is not None and not send_future.done():
            send_future.cancel()
        if cleanup_future is not None and not cleanup_future.done():
            cleanup_future.cancel()
        if playback is not None and not playback.state.terminal:
            playback._update(JobState.FAILED, reason=reason)
