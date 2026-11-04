"""Deterministic external-close races with a frame callback's send()."""
from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import patch

from tests.test_usb_connection import FakeSerial, packet
from watcherobot.runtime.daemon.connections.usb_connection import UsbConnection


class ControlledWriteSerial(FakeSerial):
    """A write stays in progress until close unblocks the serial worker."""

    def __init__(self, *, close_error=None):
        super().__init__(auto_ack=False)
        self.write_entered = threading.Event()
        self.write_exited = threading.Event()
        self.close_error = close_error

    def write(self, data):
        self.write_entered.set()
        try:
            with self.condition:
                if not self.condition.wait_for(lambda: not self.is_open, timeout=2):
                    raise AssertionError("close did not unblock the controlled write")
            raise OSError("write interrupted by serial close")
        finally:
            self.write_exited.set()

    def close(self):
        super().close()
        if self.close_error is not None:
            raise self.close_error


class UsbCloseCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def exercise_external_close(self, stage, *, close_error=None, disconnect_error=None):
        serial = (ControlledWriteSerial(close_error=close_error) if stage == "write"
                  else FakeSerial(auto_ack=False))
        entered, release = asyncio.Event(), asyncio.Event()
        callback_finished = asyncio.Event()
        disconnects, snapshots, send_errors = [], [], []
        real_wait = asyncio.wait

        async def on_frame(_frame):
            try:
                await connection.send(b"callback response")
            except BaseException as exc:
                send_errors.append(exc)
                raise
            finally:
                callback_finished.set()

        async def on_disconnect(error):
            disconnects.append(error)
            # The disconnect callback releases the owner's USB slot. It must
            # only run once the callback and its send have relinquished state.
            snapshots.append((
                callback_finished.is_set(), connection._dispatch_task.done(),
                connection._pending, connection._expected_ack, connection._received_ack,
                connection._receive_count, connection._receive_bytes,
                connection._tx_lock.locked(), connection._reader.is_alive(),
            ))
            if disconnect_error is not None:
                raise disconnect_error

        connection = UsbConnection(
            "FAKE-CALLBACK-CLOSE", on_frame=on_frame, on_disconnect=on_disconnect,
            serial_factory=lambda **_kwargs: serial,
            read_timeout=0.01, ack_timeout=5, fragment_gap=0,
        )

        async def controlled_wait(futures, **kwargs):
            # Complete real I/O/ACK waits, but hold their resumption until the
            # close task cancels the dispatcher. This fixes a legal scheduling
            # window without relying on sleeps or a lucky executor ordering.
            pending = connection._pending
            is_ack = pending is not None and pending[2] in futures
            target = stage == ("ack" if is_ack else "write")
            if asyncio.current_task() is connection._dispatch_task and target:
                entered.set()
                result = await real_wait(futures, **kwargs)
                await release.wait()
                return result
            return await real_wait(futures, **kwargs)

        closing = waiting = queued = None
        try:
            await connection.start()
            with patch("asyncio.wait", new=controlled_wait):
                serial.inject(packet(payload=b"request"))
                await asyncio.wait_for(entered.wait(), 1)
                if stage == "write":
                    self.assertTrue(await asyncio.to_thread(serial.write_entered.wait, 1))
                else:
                    self.assertEqual(len(serial.packets), 1)
                    self.assertFalse(connection._pending[2].done())

                queued_entered = asyncio.Event()

                async def queued_send():
                    queued_entered.set()
                    await connection.send(b"must never be written")

                queued = asyncio.create_task(queued_send())
                await asyncio.wait_for(queued_entered.wait(), 1)
                closing = asyncio.create_task(connection.close())
                waiting = asyncio.create_task(connection.wait_closed())
                done, _ = await real_wait({closing, waiting}, timeout=0.5)
                self.assertEqual(
                    done, {closing, waiting},
                    "external close and callback send formed a circular wait",
                )
                expected_error = disconnect_error or close_error
                results = await asyncio.gather(closing, waiting, return_exceptions=True)
                if expected_error is None:
                    self.assertEqual(results, [None, None])
                else:
                    self.assertTrue(all(result is expected_error for result in results))
                self.assertEqual(disconnects, [close_error])
                self.assertEqual(snapshots, [(True, True, None, None, None, 0, 0, False, False)])
                self.assertEqual(len(send_errors), 1)
                self.assertIsInstance(send_errors[0], asyncio.CancelledError)
                self.assertTrue(connection._close_task.done())
                self.assertEqual(serial.close_count, 1)
                if stage == "write":
                    self.assertTrue(serial.write_exited.is_set())
                else:
                    self.assertEqual(len(serial.packets), 1)
                queued_result = await asyncio.wait_for(
                    asyncio.gather(queued, return_exceptions=True), 1,
                )
                self.assertIsInstance(queued_result[0], ConnectionError)
                with self.assertRaises(ConnectionError):
                    await connection.send(b"closed connection")
                for _ in range(2):
                    if expected_error is None:
                        await asyncio.wait_for(connection.close(), 1)
                    else:
                        with self.assertRaises(type(expected_error)) as caught:
                            await asyncio.wait_for(connection.close(), 1)
                        self.assertIs(caught.exception, expected_error)
                self.assertEqual(serial.close_count, 1)
                self.assertEqual(len(disconnects), 1)
        finally:
            # Rescue only a failing implementation, so RED cannot strand an
            # executor or a cyclic close task during unittest's loop teardown.
            release.set()
            if connection._close_task is None:
                connection._request_close(None, 1000, "test cleanup")
            if not connection._close_task.done():
                dispatcher = connection._dispatch_task
                if dispatcher is not None and not dispatcher.done():
                    dispatcher.cancel()
            tasks = [task for task in (closing, waiting, queued, connection._close_task)
                     if task is not None]
            done, pending = await real_wait(tasks, timeout=2)
            self.assertFalse(pending, "test rescue could not finish connection cleanup")
            await asyncio.gather(*done, return_exceptions=True)

    async def test_external_close_cancels_callback_waiting_for_ack(self):
        await self.exercise_external_close("ack")

    async def test_external_close_cancels_callback_during_controlled_write(self):
        await self.exercise_external_close("write")

    async def test_close_failure_still_cleans_callback_before_disconnect(self):
        await self.exercise_external_close("write", close_error=OSError("close failed"))

    async def test_disconnect_failure_is_reported_once_after_callback_cleanup(self):
        await self.exercise_external_close("ack", disconnect_error=RuntimeError("slot cleanup failed"))


class GatedCloseSerial(FakeSerial):
    """Hold physical cleanup so callback/external close ordering is explicit."""

    def __init__(self, *, close_error=None):
        super().__init__()
        self.close_entered = threading.Event()
        self.close_release = threading.Event()
        self.close_error = close_error

    def close(self):
        self.close_entered.set()
        if not self.close_release.wait(3):
            raise AssertionError("test did not release serial cleanup")
        super().close()
        if self.close_error is not None:
            raise self.close_error


class UsbPublicCloseCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def exercise_public_close(self, order, *, close_error=None, disconnect_error=None):
        serial = GatedCloseSerial(close_error=close_error)
        callback_entered, callback_close = asyncio.Event(), asyncio.Event()
        callback_returned, callback_finished = asyncio.Event(), asyncio.Event()
        all_received = asyncio.Event()
        disconnect_entered, disconnect_release = asyncio.Event(), asyncio.Event()
        disconnects, cleanup_states = [], []
        slot = {"connection": None}

        async def on_frame(_frame):
            try:
                callback_entered.set()
                if order == "cancel_finally":
                    try:
                        await callback_close.wait()
                    finally:
                        await connection.close()
                else:
                    await callback_close.wait()
                    await connection.close()
                callback_returned.set()
            finally:
                callback_finished.set()

        async def on_disconnect(error):
            disconnects.append(error)
            cleanup_states.append((
                callback_finished.is_set(), connection._reader.is_alive(),
                connection._pending, connection._expected_ack, connection._received_ack,
                connection._receive_count, connection._receive_bytes,
            ))
            disconnect_entered.set()
            await disconnect_release.wait()
            slot["connection"] = None
            # Public close is also allowed from its own disconnect callback.
            await connection.close()
            if disconnect_error is not None:
                raise disconnect_error

        connection = UsbConnection(
            "FAKE-PUBLIC-CLOSE", on_frame=on_frame, on_disconnect=on_disconnect,
            serial_factory=lambda **_kwargs: serial, read_timeout=0.01,
        )
        slot["connection"] = connection
        received = 0
        original_receive = connection._receive_event
        loop = asyncio.get_running_loop()

        def receive(event):
            nonlocal received
            result = original_receive(event)
            received += 1
            if received == 3:
                loop.call_soon_threadsafe(all_received.set)
            return result

        connection._receive_event = receive
        callers = []
        waiting = None

        async def start_caller():
            started = asyncio.Event()

            async def external_close():
                started.set()
                await connection.close()

            task = asyncio.create_task(external_close())
            callers.append(task)
            await asyncio.wait_for(started.wait(), 1)
            return task

        try:
            await connection.start()
            serial.inject(b"".join(packet(frame_id=i, payload=b"pending callback")
                                   for i in range(1, 4)))
            await asyncio.wait_for(callback_entered.wait(), 1)
            await asyncio.wait_for(all_received.wait(), 1)
            self.assertEqual(connection._receive_count, 3)
            self.assertGreater(connection._receive_bytes, 0)

            if order == "callback_first":
                callback_close.set()
                self.assertTrue(await asyncio.to_thread(serial.close_entered.wait, 1))
                self.assertIs(connection._close_caller, connection._dispatch_task)
            else:
                await start_caller()
                self.assertTrue(await asyncio.to_thread(serial.close_entered.wait, 1))
                self.assertIsNot(connection._close_caller, connection._dispatch_task)

            await start_caller()
            cancelled_caller = await start_caller()
            waiting = asyncio.create_task(connection.wait_closed())
            self.assertTrue(all(not task.done() for task in callers))
            if order == "external_first":
                callback_close.set()
                await asyncio.wait_for(callback_returned.wait(), 0.5)
                self.assertTrue(connection._dispatch_task.done())
                self.assertEqual((connection._receive_count, connection._receive_bytes), (0, 0))
                self.assertTrue(all(not task.done() for task in callers))
            else:
                self.assertFalse(callback_finished.is_set())

            serial.close_release.set()
            await asyncio.wait_for(disconnect_entered.wait(), 0.5)
            self.assertEqual(disconnects, [close_error])
            self.assertIs(slot["connection"], connection)
            self.assertTrue(all(not task.done() for task in callers))
            self.assertFalse(waiting.done())
            cancelled_caller.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await cancelled_caller
            self.assertFalse(connection._close_task.done())
            self.assertFalse(connection._close_task.cancelled())
            if order != "callback_first":
                self.assertEqual(cleanup_states, [(True, False, None, None, None, 0, 0)])

            disconnect_release.set()
            remaining = [task for task in callers if task is not cancelled_caller]
            results = await asyncio.wait_for(
                asyncio.gather(*remaining, waiting, return_exceptions=True), 1,
            )
            expected_error = disconnect_error or close_error
            if expected_error is None:
                self.assertEqual(results, [None] * len(results))
            else:
                self.assertTrue(all(result is expected_error for result in results))
            await asyncio.wait_for(connection._dispatch_task, 1)
            self.assertTrue(callback_finished.is_set())
            self.assertIsNone(slot["connection"])
            self.assertEqual((connection._receive_count, connection._receive_bytes), (0, 0))
            self.assertEqual(list(connection._frames), [])
            self.assertEqual((connection._pending, connection._expected_ack, connection._received_ack),
                             (None, None, None))
            self.assertFalse(connection._reader.is_alive())
            self.assertFalse(connection._tx_lock.locked())
            for _ in range(2):
                if expected_error is None:
                    await asyncio.wait_for(connection.close(), 1)
                else:
                    with self.assertRaises(type(expected_error)) as caught:
                        await asyncio.wait_for(connection.close(), 1)
                    self.assertIs(caught.exception, expected_error)
            self.assertEqual(serial.close_count, 1)
            self.assertEqual(len(disconnects), 1)
        finally:
            # Release the physical gate and, on RED only, break the collector
            # cycle with one additional cancellation before loop teardown.
            serial.close_release.set()
            disconnect_release.set()
            if connection._close_task is None:
                connection._request_close(None, 1000, "test cleanup")
            dispatcher = connection._dispatch_task
            if not connection._close_task.done() and dispatcher is not None and not dispatcher.done():
                dispatcher.cancel()
            tasks = [task for task in [*callers, waiting, dispatcher, connection._close_task]
                     if task is not None]
            done, pending = await asyncio.wait(tasks, timeout=2)
            self.assertFalse(pending, "public-close test rescue could not finish cleanup")
            await asyncio.gather(*done, return_exceptions=True)

    async def test_external_cancel_finally_can_await_public_close(self):
        await self.exercise_public_close("cancel_finally")

    async def test_callback_public_close_after_external_close_does_not_wait_for_collector(self):
        await self.exercise_public_close("external_first")

    async def test_callback_initiated_close_keeps_external_callers_waiting_for_cleanup(self):
        await self.exercise_public_close("callback_first")

    async def test_finally_public_close_preserves_serial_cleanup_failure(self):
        await self.exercise_public_close("cancel_finally", close_error=OSError("serial cleanup failed"))

    async def test_finally_public_close_preserves_disconnect_failure(self):
        await self.exercise_public_close("cancel_finally", disconnect_error=RuntimeError("disconnect failed"))


class UsbDispatcherCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def exercise_dispatcher_completion(
        self, order, *, cancel_caller=False, cancel_dispatcher=False,
        close_error=None, disconnect_error=None,
    ):
        serial = GatedCloseSerial(close_error=close_error)
        entered, request_close, all_received = asyncio.Event(), asyncio.Event(), asyncio.Event()
        finishing, finish_release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        offline_entered, offline_release = asyncio.Event(), asyncio.Event()
        disconnects, callback_close_errors = [], []

        async def callback_close():
            # This remains a direct dispatcher await, including repeated close.
            try:
                await connection.close()
            except Exception as exc:
                callback_close_errors.append(exc)

        async def on_frame(_frame):
            try:
                entered.set()
                await request_close.wait()
                await callback_close()
            finally:
                try:
                    await callback_close()
                    finishing.set()
                    await finish_release.wait()
                    await callback_close()
                finally:
                    finished.set()

        async def on_disconnect(error):
            disconnects.append(error)
            offline_entered.set()
            await offline_release.wait()
            await connection.close()  # The collector must not await itself.
            if disconnect_error is not None:
                raise disconnect_error

        connection = UsbConnection(
            "FAKE-DISPATCHER-COMPLETION", on_frame=on_frame, on_disconnect=on_disconnect,
            serial_factory=lambda **_kwargs: serial, read_timeout=0.01,
        )
        if cancel_dispatcher:
            original_dispatch = connection._dispatch_frames

            async def cancelled_dispatcher():
                await original_dispatch()
                raise asyncio.CancelledError("dispatcher ended cancelled")

            connection._dispatch_frames = cancelled_dispatcher
        loop = asyncio.get_running_loop()
        original_receive = connection._receive_event
        received = 0

        def receive(event):
            nonlocal received
            result = original_receive(event)
            received += 1
            if received == 3:
                loop.call_soon_threadsafe(all_received.set)
            return result

        connection._receive_event = receive
        callers = []

        async def start_external_close():
            started = asyncio.Event()

            async def close():
                started.set()
                await connection.close()

            task = asyncio.create_task(close())
            callers.append(task)
            await asyncio.wait_for(started.wait(), 1)
            return task

        try:
            await connection.start()
            serial.inject(b"".join(packet(frame_id=i, payload=b"active and queued")
                                   for i in range(1, 4)))
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.wait_for(all_received.wait(), 1)
            self.assertEqual(connection._receive_count, 3)
            if order == "external_first":
                await start_external_close()
            else:
                request_close.set()
            self.assertTrue(await asyncio.to_thread(serial.close_entered.wait, 1))
            if order == "before_port_cleanup":
                await start_external_close()
            serial.close_release.set()

            if order == "external_first":
                # External cancellation enters callback finally before offline.
                await asyncio.wait_for(finishing.wait(), 1)
                self.assertFalse(connection._close_task.done())
                self.assertEqual(disconnects, [])
            else:
                await asyncio.wait_for(offline_entered.wait(), 1)
                offline_release.set()
                await asyncio.wait_for(finishing.wait(), 1)
                self.assertTrue(connection._close_task.done())
                self.assertEqual(disconnects, [close_error])
                self.assertIs(connection._close_caller, connection._dispatch_task)

            self.assertFalse(connection._dispatch_task.done())
            self.assertFalse(finished.is_set())
            self.assertEqual(connection._receive_count, 1)
            self.assertGreater(connection._receive_bytes, 0)
            self.assertEqual(list(connection._frames), [])
            late_caller = await start_external_close()
            self.assertFalse(
                late_caller.done(),
                "external close returned while callback async finally still owns the payload",
            )
            repeated_caller = await start_external_close()
            self.assertTrue(all(not task.done() for task in callers))
            if cancel_caller:
                late_caller.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await late_caller
                self.assertFalse(connection._dispatch_task.done())
                self.assertFalse(finished.is_set())
                self.assertFalse(connection._close_task.cancelled())
                self.assertFalse(repeated_caller.done())

            finish_release.set()
            offline_release.set()
            remaining = [task for task in callers if not task.cancelled()]
            results = await asyncio.wait_for(
                asyncio.gather(*remaining, return_exceptions=True), 1,
            )
            expected_error = disconnect_error or close_error
            if expected_error is None:
                self.assertEqual(results, [None] * len(results))
            else:
                self.assertTrue(all(result is expected_error for result in results))
                self.assertTrue(all(exc is expected_error for exc in callback_close_errors))
            self.assertTrue(finished.is_set())
            self.assertTrue(connection._dispatch_task.done())
            self.assertEqual(connection._dispatch_task.cancelled(), cancel_dispatcher)
            self.assertEqual((connection._receive_count, connection._receive_bytes), (0, 0))
            self.assertEqual((connection._pending, connection._expected_ack, connection._received_ack),
                             (None, None, None))
            self.assertFalse(connection._reader.is_alive())
            for _ in range(2):
                if expected_error is None:
                    await asyncio.wait_for(connection.close(), 1)
                else:
                    with self.assertRaises(type(expected_error)) as caught:
                        await asyncio.wait_for(connection.close(), 1)
                    self.assertIs(caught.exception, expected_error)
            self.assertEqual(serial.close_count, 1)
            self.assertEqual(disconnects, [close_error])
        finally:
            serial.close_release.set()
            offline_release.set()
            finish_release.set()
            request_close.set()
            if connection._close_task is None:
                connection._request_close(None, 1000, "test cleanup")
            tasks = [task for task in [*callers, connection._dispatch_task, connection._close_task]
                     if task is not None]
            done, pending = await asyncio.wait(tasks, timeout=2)
            self.assertFalse(pending, "dispatcher-completion test cleanup did not finish")
            await asyncio.gather(*done, return_exceptions=True)

    async def test_external_close_after_callback_port_cleanup_waits_for_async_finally(self):
        await self.exercise_dispatcher_completion("after_port_cleanup")

    async def test_external_close_before_callback_port_cleanup_waits_for_async_finally(self):
        await self.exercise_dispatcher_completion("before_port_cleanup")

    async def test_cancelling_external_dispatcher_wait_leaves_shared_cleanup_running(self):
        await self.exercise_dispatcher_completion("after_port_cleanup", cancel_caller=True)

    async def test_external_collection_precedes_serial_cleanup_error(self):
        await self.exercise_dispatcher_completion("after_port_cleanup", close_error=OSError("port cleanup failed"))

    async def test_external_collection_precedes_disconnect_error(self):
        await self.exercise_dispatcher_completion("before_port_cleanup", disconnect_error=RuntimeError("offline failed"))

    async def test_external_first_still_collects_cancelled_callback_async_finally(self):
        await self.exercise_dispatcher_completion("external_first", cancel_caller=True)

    async def test_cancelled_dispatcher_does_not_mimic_external_caller_cancellation(self):
        await self.exercise_dispatcher_completion("after_port_cleanup", cancel_dispatcher=True)


class UsbCallbackCleanupErrorTests(unittest.IsolatedAsyncioTestCase):
    async def exercise_cleanup_error(
        self, order, *, serial_error=None, disconnect_error=None, late_send=False,
    ):
        serial = GatedCloseSerial(close_error=serial_error)
        entered, start_close = asyncio.Event(), asyncio.Event()
        finishing, release = asyncio.Event(), asyncio.Event()
        offline_entered, offline_release = asyncio.Event(), asyncio.Event()
        failure = RuntimeError("callback asynchronous cleanup failed")
        notifications = []

        async def on_frame(_frame):
            try:
                entered.set()
                await start_close.wait()
                await connection.close()
            finally:
                finishing.set()
                await release.wait()
                if late_send:
                    await connection.send(b"normal close rejects this late send")
                else:
                    raise failure

        async def on_disconnect(error):
            notifications.append(error)
            offline_entered.set()
            await offline_release.wait()
            if disconnect_error is not None:
                raise disconnect_error

        connection = UsbConnection(
            "FAKE-CALLBACK-ERROR", on_frame=on_frame, on_disconnect=on_disconnect,
            serial_factory=lambda **_kwargs: serial, read_timeout=0.01,
        )
        callers = []

        async def start_external():
            started = asyncio.Event()

            async def close():
                started.set()
                await connection.close()

            task = asyncio.create_task(close())
            callers.append(task)
            await asyncio.wait_for(started.wait(), 1)
            return task

        try:
            await connection.start()
            serial.inject(packet(payload=b"retained until callback cleanup"))
            await asyncio.wait_for(entered.wait(), 1)
            if order == "callback_first":
                start_close.set()
            else:
                await start_external()
            self.assertTrue(await asyncio.to_thread(serial.close_entered.wait, 1))
            serial.close_release.set()
            if order == "callback_first":
                await asyncio.wait_for(offline_entered.wait(), 1)
                offline_release.set()
            await asyncio.wait_for(finishing.wait(), 1)
            await start_external()
            await start_external()
            self.assertTrue(all(not task.done() for task in callers))
            self.assertEqual(connection._receive_count, 1)
            release.set()
            if order != "callback_first":
                # The callback fails before a subsequent disconnect failure;
                # the existing disconnect error must retain its priority.
                await asyncio.wait_for(offline_entered.wait(), 1)
                offline_release.set()
            results = await asyncio.wait_for(
                asyncio.gather(*callers, return_exceptions=True), 1,
            )
            expected = disconnect_error or serial_error or (None if late_send else failure)
            if expected is None:
                self.assertEqual(results, [None] * len(results))
            else:
                self.assertTrue(all(result is expected for result in results),
                                "external close lost or replaced the original cleanup failure")
            first_error = serial_error or disconnect_error or (None if late_send else failure)
            self.assertIs(connection.error, first_error)
            for _ in range(2):
                if expected is None:
                    await asyncio.wait_for(connection.close(), 1)
                    await asyncio.wait_for(connection.wait_closed(), 1)
                else:
                    with self.assertRaises(type(expected)) as caught:
                        await asyncio.wait_for(connection.close(), 1)
                    self.assertIs(caught.exception, expected)
                    with self.assertRaises(type(expected)) as caught:
                        await asyncio.wait_for(connection.wait_closed(), 1)
                    self.assertIs(caught.exception, expected)
            self.assertTrue(connection._dispatch_task.done())
            self.assertEqual((connection._receive_count, connection._receive_bytes), (0, 0))
            self.assertEqual((connection._pending, connection._expected_ack, connection._received_ack),
                             (None, None, None))
            self.assertFalse(connection._reader.is_alive())
            self.assertEqual(serial.close_count, 1)
            self.assertEqual(notifications, [serial_error])
        finally:
            serial.close_release.set()
            start_close.set()
            release.set()
            offline_release.set()
            if connection._close_task is None:
                connection._request_close(None, 1000, "test cleanup")
            tasks = [task for task in [*callers, connection._dispatch_task, connection._close_task]
                     if task is not None]
            done, pending = await asyncio.wait(tasks, timeout=2)
            self.assertFalse(pending, "callback-error cleanup remained blocked")
            await asyncio.gather(*done, return_exceptions=True)

    async def test_callback_first_async_cleanup_error_is_retained_after_join(self):
        await self.exercise_cleanup_error("callback_first")

    async def test_external_first_async_cleanup_error_is_retained_after_join(self):
        await self.exercise_cleanup_error("external_first")

    async def test_prior_serial_failure_keeps_original_identity(self):
        await self.exercise_cleanup_error("callback_first", serial_error=OSError("original serial failure"))

    async def test_prior_disconnect_failure_keeps_original_identity(self):
        await self.exercise_cleanup_error("callback_first", disconnect_error=ValueError("original offline failure"))

    async def test_subsequent_disconnect_failure_keeps_existing_priority(self):
        await self.exercise_cleanup_error("external_first", disconnect_error=ValueError("offline failure"))

    async def test_serial_and_disconnect_failure_priorities_remain_unchanged(self):
        await self.exercise_cleanup_error("callback_first", serial_error=OSError("serial first"),
                                          disconnect_error=ValueError("offline second"))

    async def test_late_callback_send_does_not_turn_normal_close_into_failure(self):
        for order in ("callback_first", "external_first"):
            with self.subTest(order=order):
                await self.exercise_cleanup_error(order, late_send=True)

    async def test_interrupted_callback_write_does_not_become_cleanup_failure(self):
        close_entered, close_release = threading.Event(), threading.Event()
        callback_entered = asyncio.Event()
        notifications = []

        class InterruptedWriteSerial(ControlledWriteSerial):
            def close(self):
                FakeSerial.close(self)  # Unblock write before collector returns.
                close_entered.set()
                if not close_release.wait(2):
                    raise AssertionError("test did not release physical close")

        serial = InterruptedWriteSerial()

        async def on_frame(_frame):
            callback_entered.set()
            await connection.send(b"interrupted by normal external close")

        async def on_disconnect(error):
            notifications.append(error)

        connection = UsbConnection(
            "FAKE-CLOSE-WRITE-ERROR", on_frame=on_frame, on_disconnect=on_disconnect,
            serial_factory=lambda **_kwargs: serial, read_timeout=0.01,
        )
        closing = None
        try:
            await connection.start()
            serial.inject(packet(payload=b"request"))
            await asyncio.wait_for(callback_entered.wait(), 1)
            self.assertTrue(await asyncio.to_thread(serial.write_entered.wait, 1))
            closing = asyncio.create_task(connection.close())
            self.assertTrue(await asyncio.to_thread(close_entered.wait, 1))
            await asyncio.wait_for(asyncio.shield(connection._dispatch_task), 1)
            self.assertIsNone(connection.error)
            self.assertIsNone(connection._close_error)
            close_release.set()
            await asyncio.wait_for(closing, 1)
            await asyncio.wait_for(connection.wait_closed(), 1)
            self.assertEqual(notifications, [None])
            self.assertEqual((connection._receive_count, connection._receive_bytes), (0, 0))
            self.assertTrue(serial.write_exited.is_set())
            self.assertEqual(serial.close_count, 1)
        finally:
            close_release.set()
            tasks = [asyncio.create_task(connection.close())]
            if closing is not None:
                tasks.append(closing)
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
