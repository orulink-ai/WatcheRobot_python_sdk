"""WRU1 contract tests; packets are assembled independently of the codec."""
from __future__ import annotations

import importlib
import struct
import unittest
import zlib


def packet(kind=2, flags=3, index=0, frame_id=7, payload=b"", crc=None):
    return struct.pack(
        "<4sBBHIII", b"WRU1", kind, flags, index, frame_id,
        len(payload), zlib.crc32(payload) if crc is None else crc,
    ) + payload


class UsbWireTests(unittest.TestCase):
    def wire(self):
        name = "watcherobot.runtime.daemon.connections.usb_wire"
        self.assertIsNotNone(importlib.util.find_spec(name), "WRU1 codec is missing")
        return importlib.import_module(name)

    def test_encoder_matches_literal_header_and_ack(self):
        wire = self.wire()
        self.assertEqual(
            wire.encode_frame(b"abc", frame_id=7),
            [b"WRU1\x02\x03\x00\x00\x07\x00\x00\x00"
             b"\x03\x00\x00\x00\xc2\x41\x24\x35abc"],
        )
        self.assertEqual(wire.encode_ack(7, 4), packet(3, 0, 4, 7))

    def test_encoder_fragments_utf8_bytes_without_decoding_each_fragment(self):
        wire = self.wire()
        payload = "a" * 1023 + "\u4f60\u597d"
        fragments = wire.encode_frame(payload, frame_id=9)
        self.assertEqual(len(fragments), 2)
        self.assertEqual(fragments[0], packet(1, 1, 0, 9, payload.encode()[:1024]))
        self.assertEqual(fragments[1], packet(1, 2, 1, 9, payload.encode()[1024:]))
        decoded = wire.UsbWireDecoder().feed(b"".join(fragments))
        self.assertEqual([(f.frame_id, f.payload) for f in decoded], [(9, payload)])

    def test_empty_text_and_binary_remain_distinct(self):
        wire = self.wire()
        decoder = wire.UsbWireDecoder()
        events = decoder.feed(packet(1, frame_id=1) + packet(2, frame_id=2))
        self.assertEqual([e.payload for e in events], ["", b""])
        self.assertEqual(wire.encode_frame("", frame_id=1), [packet(1, frame_id=1)])

    def test_encoder_rejects_oversize_invalid_utf8_and_invalid_ids(self):
        wire = self.wire()
        for value in (b"x" * 131073, "\u4f60" * 43691, "\ud800"):
            with self.subTest(value_type=type(value)):
                with self.assertRaises((ValueError, UnicodeError)):
                    wire.encode_frame(value, frame_id=1)
        for value in (0, -1, 2**32, True):
            with self.subTest(frame_id=value):
                with self.assertRaises(ValueError):
                    wire.encode_frame(b"x", frame_id=value)
        with self.assertRaises(TypeError):
            wire.encode_frame({"type": "new"}, frame_id=1)

    def test_maximum_frame_round_trip(self):
        wire = self.wire()
        payload = bytes(range(256)) * 512
        fragments = wire.encode_frame(payload, frame_id=0xFFFFFFFF)
        self.assertEqual(len(fragments), 128)
        self.assertEqual(wire.UsbWireDecoder().feed(b"".join(fragments))[0].payload, payload)

    def test_incremental_byte_feeds_resynchronize_after_boot_noise(self):
        wire = self.wire()
        decoder = wire.UsbWireDecoder()
        output = []
        for byte in b"boot: ready\r\nWjunkWR" + packet(payload=b"\x00WRU1\xff"):
            output.extend(decoder.feed(bytes([byte])))
        self.assertEqual([e.payload for e in output], [b"\x00WRU1\xff"])
        self.assertLessEqual(decoder.buffered_bytes, 1044)

    def test_ack_is_separate_from_logical_payload_and_can_interleave(self):
        wire = self.wire()
        decoder = wire.UsbWireDecoder()
        events = decoder.feed(
            packet(1, 1, 0, 8, b"hel") + packet(3, 0, 2, 90)
            + packet(1, 2, 1, 8, b"lo")
        )
        self.assertIsInstance(events[0], wire.UsbAck)
        self.assertEqual((events[0].frame_id, events[0].fragment_index), (90, 2))
        self.assertEqual(events[1].payload, "hello")

    def test_bad_crc_discards_whole_packet_including_embedded_magic(self):
        wire = self.wire()
        decoder = wire.UsbWireDecoder()
        nested = packet(payload=b"must not escape")
        events = decoder.feed(
            packet(payload=nested, crc=1) + packet(frame_id=8, payload=b"good")
        )
        self.assertEqual([e.payload for e in events], [b"good"])

    def test_crc_failure_aborts_entire_assembly(self):
        wire = self.wire()
        decoder = wire.UsbWireDecoder()
        self.assertEqual(decoder.feed(packet(2, 1, 0, 1, b"a")), [])
        self.assertEqual(decoder.feed(packet(2, 0, 1, 1, b"b", crc=0)), [])
        self.assertEqual(decoder.feed(packet(2, 2, 2, 1, b"c")), [])
        self.assertEqual(decoder.feed(packet(frame_id=2, payload=b"new"))[0].payload, b"new")

    def test_malformed_headers_never_dispatch(self):
        wire = self.wire()
        invalid = [
            packet(kind=9), packet(flags=4), packet(frame_id=0),
            packet(index=1), packet(flags=2), packet(3, 1),
            packet(3, 0, payload=b"x"), packet(3, 0, crc=2),
            struct.pack("<4sBBHIII", b"WRU1", 2, 3, 0, 4, 1025, 0),
        ]
        for bad in invalid:
            with self.subTest(header=bad[:20]):
                events = wire.UsbWireDecoder().feed(bad + packet(frame_id=90, payload=b"ok"))
                self.assertEqual([e.payload for e in events], [b"ok"])

    def test_wrong_sequence_kind_or_id_aborts_without_dispatch(self):
        wire = self.wire()
        for continuation in (
            packet(2, 2, 2, 1, b"b"), packet(1, 2, 1, 1, b"b"),
            packet(2, 2, 1, 2, b"b"), packet(2, 3, 0, 1, b"b"),
        ):
            with self.subTest(continuation=continuation[:20]):
                decoder = wire.UsbWireDecoder()
                self.assertEqual(decoder.feed(packet(2, 1, 0, 1, b"a") + continuation), [])

    def test_new_frame_can_replace_an_abandoned_assembly(self):
        wire = self.wire()
        events = wire.UsbWireDecoder().feed(
            packet(2, 1, 0, 1, b"a") + packet(frame_id=2, payload=b"new")
        )
        self.assertEqual([e.payload for e in events], [b"new"])

    def test_strict_utf8_is_checked_only_after_reassembly(self):
        wire = self.wire()
        decoder = wire.UsbWireDecoder()
        self.assertEqual(decoder.feed(packet(1, payload=b"\xff")), [])
        self.assertEqual(decoder.feed(packet(1, frame_id=8, payload=b"\xed\xa0\x80")), [])
        events = decoder.feed(packet(1, frame_id=9, payload=b"plain"))
        self.assertEqual([e.payload for e in events], ["plain"])

    def test_oversize_reassembly_never_dispatches(self):
        wire = self.wire()
        decoder = wire.UsbWireDecoder()
        output = []
        for index in range(129):
            output.extend(decoder.feed(packet(
                2, 1 if index == 0 else (2 if index == 128 else 0),
                index, 1, b"x" * 1024,
            )))
        self.assertEqual(output, [])
        self.assertEqual(decoder.feed(packet(frame_id=2, payload=b"ok"))[0].payload, b"ok")

    def test_duplicate_fragments_and_completed_frames_never_dispatch_twice(self):
        wire = self.wire()
        decoder = wire.UsbWireDecoder()
        first = packet(2, 1, 0, 1, b"a")
        last = packet(2, 2, 1, 1, b"b")
        self.assertEqual(decoder.feed(first + first + last), [])
        complete = packet(frame_id=2, payload=b"once")
        self.assertEqual([e.payload for e in decoder.feed(complete + complete)], [b"once"])
        self.assertEqual(decoder.feed(first + last), [])

    def test_fragment_deadline_expires_even_without_new_bytes(self):
        wire = self.wire()
        decoder = wire.UsbWireDecoder(fragment_timeout=1)
        self.assertEqual(decoder.feed(packet(2, 1, 0, 1, b"a"), now=0), [])
        decoder.feed(b"", now=1.01)
        self.assertEqual(decoder.feed(packet(2, 2, 1, 1, b"b"), now=1.02), [])
        self.assertEqual(decoder.feed(packet(frame_id=2, payload=b"ok"), now=1.03)[0].payload, b"ok")

    def test_partial_wire_packet_deadline_does_not_hold_next_frame_hostage(self):
        wire = self.wire()
        decoder = wire.UsbWireDecoder(fragment_timeout=1)
        self.assertEqual(decoder.feed(packet(payload=b"x" * 1024)[:21], now=0), [])
        decoder.feed(b"", now=1.01)
        events = decoder.feed(packet(frame_id=8, payload=b"ok"), now=1.02)
        self.assertEqual([e.payload for e in events], [b"ok"])

    def test_boot_noise_budget_is_bounded(self):
        wire = self.wire()
        decoder = wire.UsbWireDecoder(max_noise=32)
        with self.assertRaises(wire.UsbWireError):
            for _ in range(40):
                decoder.feed(b"x")
        self.assertLessEqual(decoder.buffered_bytes, 1044)


if __name__ == "__main__":
    unittest.main()
