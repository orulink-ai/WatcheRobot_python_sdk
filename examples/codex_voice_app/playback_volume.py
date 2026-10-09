"""Application-lifetime playback attenuation after enhancement and limiting."""
import math
import struct


class PlaybackVolume:
    def __init__(self):
        self._level = 1.0

    @property
    def level(self):
        return self._level

    def set_level(self, level):
        if type(level) not in (int, float) or not math.isfinite(level) or not 0 <= level <= 1:
            raise ValueError('播放音量必须是 0–1 之间的有限数值')
        self._level = float(level)

    def process(self, pcm: bytes) -> bytes:
        if self._level == 1:
            return pcm  # Normal enhancement stays bit-identical.
        if self._level == 0:
            return bytes(len(pcm))
        values = [round(sample * self._level) for (sample,) in struct.iter_unpack('<h', pcm)]
        return struct.pack(f'<{len(values)}h', *values)

    def diagnostics(self):
        return dict(level=self._level, percent=round(self._level * 100),
                    gainDb=round(20 * math.log10(self._level), 2) if self._level else None)
