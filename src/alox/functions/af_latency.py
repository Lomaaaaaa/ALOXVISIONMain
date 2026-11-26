"""Measure the latency of the Fast autofocus (owner's decision B8: "do better if you can, or give a procedure to
measure it").

The Fast autofocus moves Z continuously from ``Z Min`` to ``Z Max`` in ``Duration`` seconds and dates each image by
the time it was grabbed: ``Z = Z Min + v·(t − T0 − latency)`` with ``v = (Z Max − Z Min) / Duration``
(:mod:`alox.functions.focus`, D16). An image is exposed *before* it is grabbed, so without the right ``latency`` every
Z is overestimated by ``v·latency`` and so is the best Z. The Slow autofocus has no such bias (the stage stands still
during each exposure). Hence the measurement, on the machine and on a sharp sample:

1. a Slow autofocus over ``[Z Min, Z Max]`` gives the reference peak ``Z_slow``;
2. a Fast autofocus over the same range, with ``latency = 0``, gives ``Z_fast``;
3. ``latency = (Z_fast − Z_slow) / v``; repeated ``runs`` times, the mean is the setting and the spread says how
   reliable it is.

Each peak is refined by a parabola through the best score and its two neighbours (sub-step resolution), so the result
does not depend on the Slow step. The result goes to ``alox.ini`` ``[hardware.z] fastaflatency_DBL`` (seconds), which
the application gives to the autofocus (:class:`alox.functions.focus.FocusOptions` ``fast_latency_s``).
``tools/af_latency_check.py`` runs this on the machine.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from alox.core.functions import FunctionName
from alox.functions.focus import AutofocusParams, AutofocusSpeed, FocusResult

__all__ = ["LATENCY_SETTING", "LatencyEstimate", "LatencyRun", "measure_fast_latency", "peak_z"]

#: The ``alox.ini`` key of the result (seconds).
LATENCY_SETTING = "hardware.z.fastaflatency"


def peak_z(table: Sequence[tuple[float, float]]) -> float:
    """The Z of the score peak of an autofocus table ``[(Z, score)]``: the vertex of the parabola through the best
    point and its two neighbours (in Z order), the best point itself at an end of the scan or when the three points do
    not make a maximum. NaN for an empty table or no finite score."""
    rows = sorted((float(z), float(s)) for z, s in table if math.isfinite(z) and math.isfinite(s))
    if not rows:
        return math.nan
    best = max(range(len(rows)), key=lambda i: rows[i][1])
    if best == 0 or best == len(rows) - 1:
        return rows[best][0]
    (z0, s0), (z1, s1), (z2, s2) = rows[best - 1], rows[best], rows[best + 1]
    denom = (z0 - z1) * (z0 - z2) * (z1 - z2)
    if denom == 0:
        return z1
    a = (z2 * (s1 - s0) + z1 * (s0 - s2) + z0 * (s2 - s1)) / denom
    b = (z2 * z2 * (s0 - s1) + z1 * z1 * (s2 - s0) + z0 * z0 * (s1 - s2)) / denom
    if a >= 0:
        return z1
    vertex = -b / (2 * a)
    return vertex if z0 <= vertex <= z2 else z1


@dataclass(frozen=True)
class LatencyRun:
    z_slow: float
    z_fast: float
    latency_s: float


@dataclass(frozen=True)
class LatencyEstimate:
    """``latency_s``: the mean over the runs (the value to set); ``spread_s``: their sample standard deviation (0 for
    one run); ``speed_mm_s``: the Fast scan speed."""

    runs: tuple[LatencyRun, ...]
    speed_mm_s: float
    latency_s: float
    spread_s: float

    def ini_line(self) -> str:
        """The ``alox.ini`` line to add under ``[hardware.z]`` (LabVIEW decimal comma)."""
        return f"fastaflatency_DBL = {self.latency_s:.6f}".replace(".", ",")


def measure_fast_latency(run_once: Callable[[FunctionName, Any], FocusResult], params: AutofocusParams, *,
                         step: float, duration_s: int, runs: int = 3,
                         check: Callable[[], None] | None = None) -> LatencyEstimate:
    """Run ``runs`` × (Slow, then Fast) autofocus with ``run_once(FunctionName.AUTOFOCUS_LARGE, params)`` (the runner's
    :meth:`alox.core.functions.FunctionRunner.run_once`; the registered autofocus must have ``fast_latency_s = 0``).
    ``params`` gives the camera, method, range and ROI; ``step`` the Slow step and ``duration_s`` the Fast duration."""
    if runs < 1:
        raise ValueError("runs must be at least 1")
    if not (params.z_max > params.z_min and step > 0 and duration_s > 0):
        raise ValueError("need Z Max > Z Min, a positive step and a positive duration")
    speed = (params.z_max - params.z_min) / duration_s
    slow = replace(params, speed=AutofocusSpeed.SLOW, step=step)
    fast = replace(params, speed=AutofocusSpeed.FAST, duration_s=int(duration_s))
    results: list[LatencyRun] = []
    for _ in range(runs):
        if check is not None:
            check()
        z_slow = peak_z(run_once(FunctionName.AUTOFOCUS_LARGE, slow).table)
        z_fast = peak_z(run_once(FunctionName.AUTOFOCUS_LARGE, fast).table)
        results.append(LatencyRun(z_slow, z_fast, (z_fast - z_slow) / speed))
    values = [r.latency_s for r in results]
    spread = statistics.stdev(values) if len(values) > 1 else 0.0
    return LatencyEstimate(tuple(results), speed, statistics.fmean(values), spread)
