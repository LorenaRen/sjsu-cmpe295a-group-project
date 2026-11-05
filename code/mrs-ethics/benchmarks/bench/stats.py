"""Statistics for the reports (pure Python)."""
from __future__ import annotations

import math
from typing import Sequence

Z95 = 1.959963984540054   # standard normal quantile for a 95 % interval


def percentile(values: Sequence[float], p: float) -> float:
    """Nearest-rank percentile: the smallest value with at least p % of the values at or below it,
    so always a value that was measured (no interpolation). p in (0, 100]."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    return ordered[max(1, math.ceil(p / 100 * len(ordered))) - 1]


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for k successes in n trials (95 % by default)."""
    if n <= 0:
        raise ValueError("no trials")
    p, z2 = k / n, z * z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / (1 + z2 / n)
    return (0.0 if k == 0 else centre - half, 1.0 if k == n else centre + half)
