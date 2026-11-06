"""Fake-serial tests of the public asynchronous USB connection contract."""
from __future__ import annotations

import asyncio
import importlib
import struct
import threading
import time
import unittest
import zlib


def packet(kind=2, flags=3, index=0, frame_id=77, payload=b"", crc=None):
    return struct.pack(
        "<4sBBHIII", b"WRU1", kind, flags, index, frame_id,
        len(payload), zlib.crc32(payload) if crc is None else crc,
    ) + payload


class FakeSerial:
    def __init__(self, *, auto_ack=True, read_chunk=1044, short_write=None):
        self.auto_ack = auto_ack
        self.read_chunk = read_chunk
        self.short_write = short_write
        self.timeout = 0.01
        self.is_open = True
        self.incoming = bytearray()
        self.outgoing = bytearray()
        self.packets = []
        self.packet_times = []
        self.io_threads = []
        self.close_count = 0
        self.read_error = None
        self.write_error = None
        self.condition = threading.Condition()

    @property
    def in_waiting(self):
        self.io_threads.append(threading.get_ident())
        with self.condition:
            return len(self.incoming)

    def inject(self, data):
        with self.condition:
            self.incoming.extend(data)
            self.condition.notify_all()

    def read(self, size):
        self.io_threads.append(threading.get_ident())
        with self.condition:
            if not self.incoming and self.is_open and self.read_error is None:
                self.condition.wait(self.timeout)
            if self.read_error is not None:
                raise self.read_error
            result = bytes(self.incoming[:min(size, self.read_chunk)])
            del self.incoming[:len(result)]
            return result

    def write(self, data):
        self.io_threads.append(threading.get_ident())
        if not self.is_open:
            raise OSError("port closed")
        if self.write_error:
            raise self.write_error
        size = min(len(data), self.short_write) if self.short_write else len(data)
        self.outgoing.extend(data[:size])
        while len(self.outgoing) >= 20:
            header = struct.unpack_from("<4sBBHIII", self.outgoing)
            end = 20 + header[5]
            if len(self.outgoing) < end:
                break
            raw = bytes(self.outgoing[:end])
            del self.outgoing[:end]
            self.packets.append(raw)
            self.packet_times.append(time.perf_counter())
            if self.auto_ack:
                self.inject(packet(3, 0, header[3], header[4]))
        return size

    def close(self):
        self.io_threads.append(threading.get_ident())
        with self.condition:
            self.close_count += 1
            self.is_open = False
            self.condition.notify_all()


class UsbConnectionTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        importlib.import_module("watcherobot.runtime.daemon.connections.usb_connection")

    def module(self):
        name = "watcherobot.runtime.daemon.connections.usb_connection"
        self.assertIsNotNone(importlib.util.find_spec(name), "USB connection is missing")
        return importlib.import_module(name)

    def make_connection(self, serial=None, **options):
        module = self.module()
        serial = FakeSerial() if serial is None else serial
        self.frames = []
        self.disconnects = []
        self.factory_calls = []
        self.callback_threads = []

        async def on_frame(frame):
            self.callback_threads.append(threading.get_ident())
            self.frames.append(frame)

        async def on_disconnect(error):
            self.disconnects.append(error)

        def factory(**kwargs):
            self.factory_calls.append((kwargs, threading.get_ident()))
            serial.timeout = kwargs["timeout"]
            return serial

        connection = module.UsbConnection(
            "FAKE-PORT", on_frame=options.pop("on_frame", on_frame),
            on_disconnect=options.pop("on_disconnect", on_disconnect),
            serial_factory=options.pop("serial_factory", factory),
            read_timeout=options.pop("read_timeout", 0.01),
            **options,
        )
        async def cleanup():
            if not connection.closed:
                await connection.close()
        self.addAsyncCleanup(cleanup)
        return connection, serial

    async def eventually(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.001)
        await asyncio.wait_for(wait(), 1)

    async def test_constructor_is_inert_and_start_waits_for_ready(self):
        connection, serial = self.make_connection(management_identity={"device_id": "usb-test"})
        self.assertEqual(self.factory_calls, [])
        with self.assertRaises(ConnectionError):
            await connection.send("before start")
        await connection.start()
        await connection.open()
        self.assertTrue(connection.is_open)
        self.assertEqual(len(self.factory_calls), 1)
        kwargs, worker = self.factory_calls[0]
        self.assertEqual((kwargs["port"], kwargs["baudrate"]), ("FAKE-PORT", 1000000))
        self.assertGreater(kwargs["timeout"], 0)
        self.assertGreater(kwargs["write_timeout"], 0)
        self.assertNotEqual(worker, threading.get_ident())
        self.assertEqual(connection.management_identity, {"device_id": "usb-test"})
        await connection.send("ready")
        await connection.close()
        self.assertEqual(serial.close_count, 1)
        self.assertEqual(self.disconnects, [None])
        self.assertTrue(all(t != threading.get_ident() for t in serial.io_threads))

    async def test_each_fragment_waits_for_exact_ack_and_configured_gap(self):
        connection, serial = self.make_connection(
            FakeSerial(auto_ack=False), ack_timeout=0.4, fragment_gap=0.01,
        )
        await connection.start()
        sending = asyncio.create_task(connection.send(b"x" * 1025))
        await self.eventually(lambda: len(serial.packets) == 1)
        first = struct.unpack("<4sBBHIII", serial.packets[0][:20])
        self.assertEqual((first[1], first[2], first[3], first[5]), (2, 1, 0, 1024))
        serial.inject(packet(3, 0, 1, first[4]) + packet(3, 0, 0, first[4] + 1))
        await asyncio.sleep(0.015)
        self.assertEqual(len(serial.packets), 1)
        ack_time = time.perf_counter()
        serial.inject(packet(3, 0, 0, first[4]))
        await self.eventually(lambda: len(serial.packets) == 2)
        self.assertGreaterEqual(serial.packet_times[1] - ack_time, 0.008)
        second = struct.unpack("<4sBBHIII", serial.packets[1][:20])
        self.assertEqual((second[2], second[3], second[4], second[5]), (2, 1, first[4], 1))
        serial.inject(packet(3, 0, 1, first[4]))
        await sending

    async def test_text_and_binary_are_opaque_in_both_directions(self):
        connection, serial = self.make_connection(FakeSerial(read_chunk=7), fragment_gap=0)
        await connection.start()
        text = ' { "type": "future.business.message.v999", "data": [1, null] } \n'
        binary = b"\xff\x00WRU1\x01\x02"
        await connection.send(text)
        await connection.send(binary)
        self.assertEqual(serial.packets[0][20:], text.encode())
        self.assertEqual(serial.packets[1][20:], binary)
        serial.inject(b"boot log\r\n" + packet(1, frame_id=1, payload=text.encode())
                      + packet(2, frame_id=2, payload=binary)
                      + packet(2, frame_id=2, payload=binary))
        await self.eventually(lambda: len(self.frames) == 2)
        self.assertEqual(self.frames, [text, binary])
        self.assertEqual(self.callback_threads, [threading.get_ident()] * 2)
        self.assertEqual(len(serial.packets), 2, "device data must not generate host ACKs")

    async def test_corrupt_crc_and_invalid_utf8_never_reach_callback(self):
        connection, serial = self.make_connection()
        await connection.start()
        serial.inject(packet(frame_id=1, payload=b"bad", crc=0)
                      + packet(1, frame_id=2, payload=b"\xff")
                      + packet(frame_id=3, payload=b"good"))
        await self.eventually(lambda: len(self.frames) == 1)
        self.assertEqual(self.frames, [b"good"])

    async def test_ack_timeout_closes_without_retransmission(self):
        connection, serial = self.make_connection(FakeSerial(auto_ack=False), ack_timeout=0.03)
        await connection.start()
        with self.assertRaises(TimeoutError):
            await connection.send(b"one")
        self.assertTrue(connection.closed)
        self.assertFalse(serial.is_open)
        self.assertEqual(len(serial.packets), 1)
        self.assertEqual(len(self.disconnects), 1)
        self.assertIsInstance(self.disconnects[0], TimeoutError)
        with self.assertRaises(ConnectionError):
            await connection.send(b"later")

    async def test_close_wakes_ack_waiter_and_serialized_senders(self):
        connection, serial = self.make_connection(FakeSerial(auto_ack=False), ack_timeout=5)
        await connection.start()
        first = asyncio.create_task(connection.send(b"x" * 2048))
        second = asyncio.create_task(connection.send("queued"))
        await self.eventually(lambda: len(serial.packets) == 1)
        await asyncio.wait_for(connection.close(code=1001, reason="test shutdown"), 0.5)
        results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 0.2)
        self.assertTrue(all(isinstance(result, ConnectionError) for result in results))
        self.assertEqual((connection.close_code, connection.close_reason), (1001, "test shutdown"))
        self.assertEqual(len(serial.packets), 1)
        self.assertEqual(self.disconnects, [None])

    async def test_concurrent_sends_do_not_interleave_logical_frames(self):
        connection, serial = self.make_connection(fragment_gap=0)
        await connection.start()
        await asyncio.gather(connection.send(b"a" * 2500), connection.send(b"b" * 2500))
        ids = [struct.unpack_from("<I", raw, 8)[0] for raw in serial.packets]
        self.assertEqual(len(set(ids)), 2)
        self.assertEqual(ids, [ids[0]] * 3 + [ids[3]] * 3)
        self.assertEqual(b"".join(raw[20:] for raw in serial.packets[:3]), b"a" * 2500)

    async def test_partial_writes_are_completed_off_the_event_loop(self):
        connection, serial = self.make_connection(FakeSerial(short_write=3), fragment_gap=0)
        await connection.start()
        await connection.send(b"unaltered")
        self.assertEqual(len(serial.packets), 1)
        self.assertEqual(serial.packets[0][20:], b"unaltered")

    async def test_read_failure_notifies_and_wait_closed_raises_original(self):
        connection, serial = self.make_connection()
        await connection.start()
        error = OSError("unplugged")
        serial.read_error = error
        serial.inject(b"")
        with self.assertRaisesRegex(OSError, "unplugged"):
            await asyncio.wait_for(connection.wait_closed(), 1)
        self.assertIs(connection.error, error)
        self.assertEqual(self.disconnects, [error])
        self.assertFalse(serial.is_open)

    async def test_frame_callback_failure_is_observable_and_closes_port(self):
        async def broken(_frame):
            raise ValueError("callback failed")
        connection, serial = self.make_connection(on_frame=broken)
        await connection.start()
        serial.inject(packet(payload=b"x"))
        with self.assertRaisesRegex(ValueError, "callback failed"):
            await asyncio.wait_for(connection.wait_closed(), 1)
        self.assertIs(self.disconnects[0], connection.error)
        self.assertFalse(serial.is_open)

    async def test_disconnect_callback_failure_is_not_swallowed(self):
        async def broken(_error):
            raise ValueError("disconnect callback failed")
        connection, serial = self.make_connection(on_disconnect=broken)
        await connection.start()
        with self.assertRaisesRegex(ValueError, "disconnect callback failed"):
            await connection.close()
        self.assertFalse(serial.is_open)

    async def test_frame_callback_can_send_and_close_without_deadlock(self):
        async def reply_and_close(frame):
            self.frames.append(frame)
            await connection.send("reply")
            await connection.close()
        connection, serial = self.make_connection(on_frame=reply_and_close, fragment_gap=0)
        await connection.start()
        serial.inject(packet(1, payload=b"request"))
        await asyncio.wait_for(connection.wait_closed(), 1)
        self.assertEqual(self.frames, ["request"])
        self.assertEqual(serial.packets[0][20:], b"reply")

    async def test_invalid_payload_rejected_before_io_keeps_connection_usable(self):
        connection, serial = self.make_connection(fragment_gap=0)
        await connection.start()
        for value in (b"x" * 131073, "\ud800", {"type": "new"}):
            with self.assertRaises((ValueError, UnicodeError, TypeError)):
                await connection.send(value)
        self.assertEqual(serial.packets, [])
        await connection.send("valid")
        self.assertEqual(serial.packets[0][20:], b"valid")

    async def test_cancelling_inflight_send_closes_ambiguous_session(self):
        connection, serial = self.make_connection(FakeSerial(auto_ack=False), ack_timeout=5)
        await connection.start()
        sending = asyncio.create_task(connection.send("ambiguous"))
        await self.eventually(lambda: bool(serial.packets))
        sending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await sending
        self.assertFalse(serial.is_open)
        self.assertEqual(len(serial.packets), 1)

    async def test_write_failure_closes_and_surfaces_original_error(self):
        connection, serial = self.make_connection()
        await connection.start()
        serial.write_error = OSError("write failed")
        with self.assertRaisesRegex(OSError, "write failed"):
            await connection.send("x")
        self.assertFalse(serial.is_open)
        self.assertEqual(serial.packets, [])

    async def test_factory_failure_is_reported_without_a_reader(self):
        failure = OSError("cannot open selected port")
        def factory(**_kwargs):
            raise failure
        connection, serial = self.make_connection(serial_factory=factory)
        with self.assertRaisesRegex(OSError, "cannot open"):
            await connection.start()
        self.assertTrue(connection.closed)
        self.assertIs(connection.error, failure)
        self.assertEqual(self.disconnects, [failure])
        self.assertEqual(serial.close_count, 0)

    async def test_concurrent_start_opens_only_once(self):
        connection, _serial = self.make_connection()
        await asyncio.gather(connection.start(), connection.open(), connection.start())
        self.assertEqual(len(self.factory_calls), 1)
        self.assertTrue(connection.is_open)

    async def test_close_while_factory_is_opening_releases_returned_handle(self):
        entered, release = threading.Event(), threading.Event()
        serial = FakeSerial()
        def factory(**_kwargs):
            entered.set()
            if not release.wait(1):
                raise TimeoutError("test did not release factory")
            return serial
        connection, _ = self.make_connection(serial, serial_factory=factory)
        starting = asyncio.create_task(connection.start())
        await self.eventually(entered.is_set)
        closing = asyncio.create_task(connection.close())
        await self.eventually(lambda: connection.closed)
        release.set()
        await asyncio.wait_for(closing, 1)
        result = await asyncio.gather(starting, return_exceptions=True)
        self.assertIsInstance(result[0], ConnectionError)
        self.assertEqual(serial.close_count, 1)
        self.assertFalse(serial.is_open)
        self.assertEqual(self.disconnects, [None])

    async def test_cancelled_start_closes_late_factory_handle(self):
        entered, release = threading.Event(), threading.Event()
        serial = FakeSerial()
        def factory(**_kwargs):
            entered.set()
            if not release.wait(1):
                raise TimeoutError("test did not release factory")
            return serial
        connection, _ = self.make_connection(serial, serial_factory=factory)
        starting = asyncio.create_task(connection.start())
        await self.eventually(entered.is_set)
        starting.cancel()
        await self.eventually(lambda: connection.closed)
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(starting, 1)
        self.assertEqual(serial.close_count, 1)
        self.assertIsInstance(self.disconnects[0], asyncio.CancelledError)

    async def test_disconnect_callback_cancellation_is_recorded(self):
        async def cancelled(_error):
            raise asyncio.CancelledError("disconnect cancelled")
        connection, serial = self.make_connection(on_disconnect=cancelled)
        await connection.start()
        with self.assertRaises(asyncio.CancelledError):
            await connection.close()
        self.assertFalse(serial.is_open)
        self.assertIsInstance(connection.error, asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await connection.wait_closed()

    async def test_blocked_write_hits_deadline_and_close_unblocks_worker(self):
        class BlockedSerial(FakeSerial):
            def write(self, _data):
                with self.condition:
                    while self.is_open:
                        self.condition.wait(0.1)
                raise OSError("write unblocked by port close")
        connection, serial = self.make_connection(BlockedSerial(), ack_timeout=0.03)
        await connection.start()
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(connection.send("blocked"), 0.5)
        self.assertFalse(serial.is_open)
        self.assertEqual(serial.close_count, 1)

    async def test_missing_second_fragment_ack_never_retries_or_sends_third(self):
        connection, serial = self.make_connection(FakeSerial(auto_ack=False), ack_timeout=0.06)
        await connection.start()
        sending = asyncio.create_task(connection.send(b"x" * 2500))
        await self.eventually(lambda: len(serial.packets) == 1)
        frame_id = struct.unpack_from("<I", serial.packets[0], 8)[0]
        serial.inject(packet(3, 0, 0, frame_id))
        with self.assertRaises(TimeoutError):
            await sending
        self.assertEqual(len(serial.packets), 2)
        self.assertFalse(serial.is_open)

    async def test_idle_read_ticks_expire_abandoned_fragment(self):
        connection, serial = self.make_connection(fragment_timeout=0.03)
        await connection.start()
        serial.inject(packet(2, 1, 0, 1, b"abandoned"))
        await asyncio.sleep(0.08)
        serial.inject(packet(2, 2, 1, 1, b"tail") + packet(frame_id=2, payload=b"fresh"))
        await self.eventually(lambda: len(self.frames) == 1)
        self.assertEqual(self.frames, [b"fresh"])

    async def test_noise_budget_failure_closes_connection(self):
        connection, serial = self.make_connection(max_noise=16)
        await connection.start()
        serial.inject(b"x" * 100)
        with self.assertRaisesRegex(ValueError, "noise budget"):
            await asyncio.wait_for(connection.wait_closed(), 1)
        self.assertFalse(serial.is_open)
        self.assertEqual(self.frames, [])

    async def test_slow_frame_callback_does_not_block_outbound_ack(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(frame):
            self.frames.append(frame)
            entered.set()
            await release.wait()
        connection, serial = self.make_connection(on_frame=slow, ack_timeout=0.1)
        await connection.start()
        serial.inject(packet(payload=b"slow callback"))
        await asyncio.wait_for(entered.wait(), 1)
        await connection.send("send while callback waits")
        self.assertEqual(serial.packets[0][20:], b"send while callback waits")
        release.set()

    async def test_invalid_timing_options_fail_without_opening_port(self):
        for option in (
            {"ack_timeout": 0}, {"ack_timeout": float("nan")},
            {"read_timeout": float("inf")}, {"fragment_gap": -1},
            {"fragment_timeout": 0}, {"max_noise": -1},
        ):
            with self.subTest(option=option):
                with self.assertRaises(ValueError):
                    self.make_connection(**option)


    async def test_sparse_ack_does_not_wait_for_full_serial_read_timeout(self):
        class FillingSerial(FakeSerial):
            """Model pyserial: read(size) waits for size bytes or its deadline."""
            def __init__(self):
                super().__init__(auto_ack=False)
                self.read_sizes = []
                self.read_entered = threading.Event()

            def read(self, size):
                self.io_threads.append(threading.get_ident())
                self.read_sizes.append(size)
                deadline = time.perf_counter() + self.timeout
                with self.condition:
                    self.read_entered.set()
                    while len(self.incoming) < size and self.is_open:
                        if self.read_error is not None:
                            raise self.read_error
                        remaining = deadline - time.perf_counter()
                        if remaining <= 0:
                            break
                        self.condition.wait(remaining)
                    result = bytes(self.incoming[:size])
                    del self.incoming[:len(result)]
                    return result

        connection, serial = self.make_connection(
            FillingSerial(), read_timeout=0.5, ack_timeout=0.2, fragment_gap=0,
        )
        await connection.start()
        await self.eventually(serial.read_entered.is_set)
        sending = asyncio.create_task(connection.send(b"sparse ACK"))
        await self.eventually(lambda: bool(serial.packets))
        frame_id = struct.unpack_from("<I", serial.packets[0], 8)[0]
        ack = packet(3, 0, 0, frame_id)

        def emit_sparse_ack():
            for chunk in (ack[:1], ack[1:7], ack[7:]):
                serial.inject(chunk)
                time.sleep(0.001)

        started = time.perf_counter()
        await asyncio.to_thread(emit_sparse_ack)
        await sending
        self.assertLess(time.perf_counter() - started, 0.15)
        self.assertEqual(serial.read_sizes[0], 1, "idle read must wait for one byte")
        self.assertTrue(all(1 <= size <= 1044 for size in serial.read_sizes))

    async def test_one_ms_gap_uses_worker_sleep_despite_coarse_asyncio_timers(self):
        from unittest.mock import patch
        connection, serial = self.make_connection(fragment_gap=0.001)
        await connection.start()
        original_sleep = asyncio.sleep

        async def coarse_sleep(delay, result=None):
            # Reproduce Windows' roughly 15 ms timer latency with margin.
            return await original_sleep(max(delay, 0.025), result)

        started = time.perf_counter()
        with patch("asyncio.sleep", new=coarse_sleep):
            await connection.send(b"x" * (6 * 1024))
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 0.12, "six 1 ms gaps must not incur six coarse timer ticks")
        self.assertEqual(len(serial.packets), 6)
        for previous, current in zip(serial.packet_times, serial.packet_times[1:]):
            self.assertGreaterEqual(current - previous, 0.001)


    async def test_default_factory_deasserts_control_lines_before_open(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch

        ports = []

        class DeferredSerial(FakeSerial):
            def __init__(self, *, port=None, **kwargs):
                super().__init__()
                self.port = port
                self.timeout = kwargs["timeout"]
                self.is_open = False
                self.dtr = self.rts = True  # pyserial defaults before opening
                self.settings = kwargs
                self.open_states = []
                ports.append(self)
                if port is not None:
                    self.open()

            def open(self):
                self.open_states.append((self.port, self.dtr, self.rts))
                self.is_open = True

        with patch.dict(sys.modules, {"serial": SimpleNamespace(Serial=DeferredSerial)}):
            connection, _unused = self.make_connection(serial_factory=None, fragment_gap=0)
            self.assertEqual(ports, [], "default factory must remain inert until start")
            await connection.start()
            self.assertEqual(len(ports), 1)
            port = ports[0]
            self.assertEqual(port.open_states, [("FAKE-PORT", False, False)])
            self.assertEqual(port.settings["baudrate"], 1000000)
            self.assertEqual(port.settings["timeout"], 0.01)
            self.assertEqual(port.settings["write_timeout"], 1.0)
            await connection.send("ready without reset")
            self.assertEqual(port.packets[0][20:], b"ready without reset")
            await connection.close()
            self.assertEqual(port.close_count, 1)


    async def test_default_factory_closes_handle_when_explicit_open_fails(self):
        import sys
        from types import SimpleNamespace
        from unittest.mock import patch

        ports = []
        failure = OSError("selected port failed to open")

        class FailedSerial:
            def __init__(self, *, port=None, **_kwargs):
                self.port = port
                self.close_count = 0
                self.dtr = self.rts = True
                ports.append(self)
                if port is not None:
                    self.open()

            def open(self):
                raise failure

            def close(self):
                self.close_count += 1

        with patch.dict(sys.modules, {"serial": SimpleNamespace(Serial=FailedSerial)}):
            connection, _unused = self.make_connection(serial_factory=None)
            with self.assertRaisesRegex(OSError, "selected port failed"):
                await connection.start()
            self.assertEqual(ports[0].close_count, 1)
            self.assertIs(connection.error, failure)
            self.assertEqual(self.disconnects, [failure])


    async def test_close_before_start_never_opens_port_or_allows_reopen(self):
        connection, serial = self.make_connection()
        await connection.close()
        await connection.close()
        with self.assertRaises(ConnectionError):
            await connection.start()
        self.assertEqual(self.factory_calls, [])
        self.assertEqual(serial.close_count, 0)


    def observe_handoff(self, connection, serial):
        """Observe a real reader while deliberately holding its owning loop."""
        settled = threading.Event()
        state = {"armed": False, "decoded": 0, "notifications": 0}
        original_read = serial.read
        original_worker = connection._read_worker
        original_feed = connection._decoder.feed
        original_schedule = asyncio.get_running_loop().call_soon_threadsafe

        def read(size):
            data = original_read(size)
            if state["armed"] and state["decoded"] and not data:
                settled.set()  # All preceding events have passed through the worker.
            return data

        def worker(port):
            try:
                original_worker(port)
            finally:
                settled.set()  # Overflow must stop the reader without the loop.

        def feed(data):
            events = original_feed(data)
            if state["armed"]:
                state["decoded"] += len(events)
            return events

        def schedule(callback, *args, **kwargs):
            if getattr(callback, "__self__", None) is connection:
                state["notifications"] += 1
            return original_schedule(callback, *args, **kwargs)

        serial.read = read
        connection._read_worker = worker
        connection._decoder.feed = feed
        return settled, state, schedule

    def stall_for_burst(self, connection, serial, observer, wire):
        from unittest.mock import patch
        settled, state, schedule = observer
        state.update(armed=True, decoded=0, notifications=0)
        settled.clear()
        with patch.object(asyncio.get_running_loop(), "call_soon_threadsafe", schedule):
            serial.inject(wire)
            # Intentionally block this loop, rather than simulating a slow callback.
            self.assertTrue(settled.wait(2), "reader neither drained nor failed closed")
            return state["notifications"]

    async def test_stalled_loop_handoff_bounds_empty_frames_before_scheduling(self):
        connection, serial = self.make_connection()
        observer = self.observe_handoff(connection, serial)
        await connection.start()
        count = self.stall_for_burst(connection, serial, observer, b"".join(
            packet(frame_id=i, payload=b"") for i in range(1, 513)
        ))
        self.assertLessEqual(count, 1, "one callback per frame bypasses the receive bound")
        self.assertTrue(connection._stop.is_set(), "overflow must stop reading while loop is stalled")
        self.assertLessEqual(connection._receive_count, 128)
        self.assertLessEqual(connection._receive_bytes, 1024 * 1024)
        with self.assertRaises(BufferError):
            await asyncio.wait_for(connection.wait_closed(), 1)
        self.assertEqual(self.frames, [], "overflow must not deliver a partial buffered burst")
        self.assertEqual((connection._receive_count, connection._receive_bytes), (0, 0))
        self.assertFalse(connection._reader.is_alive())
        self.assertEqual(serial.close_count, 1)
        self.assertEqual(self.disconnects, [connection.error])

    async def test_stalled_loop_handoff_has_a_payload_byte_limit(self):
        from watcherobot.runtime.daemon.connections.usb_wire import encode_frame
        connection, serial = self.make_connection()
        observer = self.observe_handoff(connection, serial)
        await connection.start()
        wire = b"".join(
            raw for i in range(1, 11)
            for raw in encode_frame(b"x" * 131072, frame_id=i)
        )
        count = self.stall_for_burst(connection, serial, observer, wire)
        self.assertTrue(connection._stop.is_set(), "fewer than 128 large frames must hit a byte bound")
        self.assertLessEqual(count, 1)
        self.assertLessEqual(connection._receive_bytes, 1024 * 1024)
        self.assertLessEqual(connection._receive_count, 128)
        with self.assertRaises(BufferError):
            await asyncio.wait_for(connection.wait_closed(), 1)
        self.assertEqual((connection._receive_count, connection._receive_bytes), (0, 0))

    async def test_handoff_byte_limit_includes_active_unicode_callback(self):
        from watcherobot.runtime.daemon.connections.usb_wire import encode_frame
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(_frame):
            entered.set()
            await release.wait()

        connection, serial = self.make_connection(on_frame=slow)
        observer = self.observe_handoff(connection, serial)
        await connection.start()
        # A single non-BMP character makes Python retain four bytes per character.
        text = "\U0001f916" + "x" * (131072 - 4)
        serial.inject(b"".join(encode_frame(text, frame_id=1)))
        await asyncio.wait_for(entered.wait(), 1)
        wire = b"".join(raw for i in range(2, 5) for raw in encode_frame(text, frame_id=i))
        self.stall_for_burst(connection, serial, observer, wire)
        self.assertTrue(connection._stop.is_set(), "queued and active Python text memory must be bounded")
        self.assertLessEqual(connection._receive_bytes, 1024 * 1024)
        with self.assertRaises(BufferError):
            await asyncio.wait_for(connection.wait_closed(), 1)
        self.assertEqual((connection._receive_count, connection._receive_bytes), (0, 0))

    async def test_stalled_loop_ack_flood_is_coalesced_without_losing_matching_ack(self):
        connection, serial = self.make_connection(FakeSerial(auto_ack=False), ack_timeout=3, fragment_gap=0)
        observer = self.observe_handoff(connection, serial)
        await connection.start()
        sending = asyncio.create_task(connection.send(b"one ACK is sufficient"))
        await self.eventually(lambda: bool(serial.packets))
        frame_id = struct.unpack_from("<I", serial.packets[0], 8)[0]
        wire = (packet(3, 0, 1, frame_id) * 500
                + packet(3, 0, 0, frame_id) * 500
                + packet(3, 0, 0, frame_id + 1) * 500)
        count = self.stall_for_burst(connection, serial, observer, wire)
        self.assertLessEqual(count, 1, "ACK traffic must not allocate one loop handle per ACK")
        self.assertFalse(connection._pending[2].done(), "reader must not complete an asyncio future off-loop")
        await asyncio.wait_for(sending, 1)
        self.assertFalse(connection.closed)
        self.assertEqual(self.frames, [])

    async def test_coalesced_handoff_rearms_and_reclaims_receive_budget(self):
        connection, serial = self.make_connection()
        observer = self.observe_handoff(connection, serial)
        await connection.start()
        for burst in range(4):
            first = burst * 64 + 1
            expected = [str(i).encode() for i in range(first, first + 64)]
            count = self.stall_for_burst(connection, serial, observer, b"".join(
                packet(frame_id=i, payload=str(i).encode()) for i in range(first, first + 64)
            ))
            self.assertLessEqual(count, 1)
            await self.eventually(lambda: len(self.frames) == first + 63)
            self.assertEqual(self.frames[-64:], expected)
            self.assertEqual((connection._receive_count, connection._receive_bytes), (0, 0))

    async def test_handoff_overflow_wakes_ack_waiter_and_queued_sender(self):
        connection, serial = self.make_connection(FakeSerial(auto_ack=False), ack_timeout=5)
        observer = self.observe_handoff(connection, serial)
        await connection.start()
        first = asyncio.create_task(connection.send(b"in flight"))
        second = asyncio.create_task(connection.send(b"queued"))
        await self.eventually(lambda: len(serial.packets) == 1)
        self.stall_for_burst(connection, serial, observer, b"".join(
            packet(frame_id=i) for i in range(1, 513)
        ))
        self.assertTrue(connection._stop.is_set(), "reader-side overflow must be terminal")
        with self.assertRaises(BufferError):
            await asyncio.wait_for(connection.wait_closed(), 1)
        results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 1)
        self.assertTrue(all(isinstance(result, ConnectionError) for result in results))
        self.assertEqual(len(serial.packets), 1)
        self.assertEqual(serial.close_count, 1)

    async def test_explicit_close_discards_stalled_handoff_without_callbacks(self):
        connection, serial = self.make_connection()
        observer = self.observe_handoff(connection, serial)
        await connection.start()
        count = self.stall_for_burst(connection, serial, observer, b"".join(
            packet(frame_id=i, payload=b"discard") for i in range(1, 65)
        ))
        self.assertLessEqual(count, 1)
        await asyncio.wait_for(connection.close(), 1)
        self.assertEqual(self.frames, [])
        self.assertEqual((connection._receive_count, connection._receive_bytes), (0, 0))
        self.assertEqual(self.disconnects, [None])
        self.assertFalse(connection._reader.is_alive())



if __name__ == "__main__":
    unittest.main()
