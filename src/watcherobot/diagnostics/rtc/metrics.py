"""Small deterministic metric helpers shared by tests and report tooling."""

from __future__ import annotations

import math
from collections.abc import Iterable


def percentile(samples: Iterable[float], rank: float) -> float:
    """Return a nearest-rank percentile without external dependencies."""

    values = sorted(float(sample) for sample in samples)
    if not values:
        return 0.0
    if not 0.0 <= rank <= 100.0:
        raise ValueError("rank must be within 0..100")
    index = max(0, math.ceil((rank / 100.0) * len(values)) - 1)
    return values[index]
