"""Measurement points, result table, statistics and CSV of the thermal map (no I/O besides :meth:`ResultTable.write_csv`).

Spec: ``docs/vision-core/map.md`` Part 6 §2.1. Diagrams (``SM`` = ``alox_function_ll_thermal_measurement_state_machine``):

- points: ``get_pyrometer_measures_positions_tablesorted_v2 d`` — for circle ``i`` (diameter ``Dᵢ``, ``nᵢ`` points) and
  point ``j``: ``r = Dᵢ · 0.5``, ``angle = 360 / nᵢ · j + Offset_Angle`` (degrees, the offset is an integer),
  ``X = r · cos(angle · π / 180)``, ``Y = r · sin(…)``, plus the "Susceptor Pos" (``SM`` passes ``ZERO POS`` = (0, 0, 0),
  ``SM d15``, and subtracts it again before moving and before writing the table, ``SM d148``, ``d150``,
  ``update_pyrometer_measures_table_v5 d4``: the points are relative to the thermal origin). Every circle of the arrays
  is used (the outer loop runs on the size of the diameters array; a missing ``# POINTS`` entry gives no point); a
  diameter of 0 puts all its points at the origin. Rows ``[X, Y, Z, Rho, Teta]`` of all circles are then sorted on
  column 4 (``Teta``, ascending, NI ``Sort 2D Array``): the measurement order sweeps the angles, all circles mixed.
  The sort here is stable (equal angles keep the circle order) **(inferred** from NI's sort on (key, index) pairs).
- table: ``update_pyrometer_measures_table_v5 d4`` writes one row per point in the ``MEASURES`` table, columns ``X``,
  ``Y``, ``Rho``, ``Teta``, ``Temp. (°C)``, ``Std Dev Temp`` (``SM d15``), formats ``%.;%3.2f`` (decimal point, 2
  decimals) and ``%.;%3.2e`` for the standard deviation (LabVIEW's exponent has no leading zeros: ``1.23E-1``).
  Row headers (``SM d15``, ``MeasureArrayBundle d``): ``Point 1`` … ``Point N``, then ``Global``, ``Mean``,
  ``Median``, ``Std.Dev``, ``Max``, ``Min``, ``Delta Max-Min``, then for the 1st … 8th circle ``<n>th circle``,
  ``Mean``, ``Max``, ``Min``. Statistics are written in the temperature column, ``%.;%3.2f``, recomputed after each
  point (``…table_v5 d1``): unparsable or NaN temperatures are left out.
- CSV: ``SM d161`` (end of a measurement) and ``SM d100`` (EXPORT): the table ``Value`` with the column headers
  inserted as row 0 and ``["", row headers…]`` as column 0, written by NI ``Write Delimited Spreadsheet`` with format
  ``%s`` and no delimiter wired, i.e. **tab-separated** despite the ``.csv`` name; default file name
  ``Thermal_measurement_%y%m%d_%H%M%S.csv``. Line ends ``\\r\\n`` and the cp1252 encoding are what LabVIEW writes on
  Windows **(inferred)**.

Deviations (see :mod:`alox.thermal.controller` for the full list): the per-circle rows of LabVIEW's v5 table were
computed from a column subset whose selection could not be decoded with certainty (``…table_v5 d1``: ``Array Max &
Min`` on an extract of column 2, then ``Min`` and ``Max − Min`` written); here each circle's ``Mean``/``Max``/``Min`` are
the statistics of that circle's point temperatures. The CSV always lists every row (LabVIEW's ``Insert Into Array``
truncated the row headers to the rows already written). A 9th circle with points gets a 9th header group.
"""

from __future__ import annotations

import math
import os
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

__all__ = [
    "CIRCLE_ROWS",
    "COLUMN_HEADERS",
    "GLOBAL_ROWS",
    "LABVIEW_CIRCLE_GROUPS",
    "CircleStatistics",
    "GlobalStatistics",
    "MeasurePoint",
    "PointResult",
    "ResultTable",
    "default_csv_name",
    "global_statistics",
    "lv_exponential",
    "lv_fixed",
    "measurement_points",
    "ordinal",
    "sample_statistics",
]

COLUMN_HEADERS = ("X", "Y", "Rho", "Teta", "Temp. (°C)", "Std Dev Temp")
TEMP_COLUMN = 4
GLOBAL_ROWS = ("Global", "Mean", "Median", "Std.Dev", "Max", "Min", "Delta Max-Min")
CIRCLE_ROWS = ("Mean", "Max", "Min")
#: ``MeasureArrayBundle d``: the row headers name eight circles.
LABVIEW_CIRCLE_GROUPS = 8
CSV_NAME_FORMAT = "Thermal_measurement_%y%m%d_%H%M%S"


# ---------------------------------------------------------------------------------------------- formats
def _special(value: float) -> str | None:
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Inf" if value > 0 else "-Inf"
    return None


def lv_fixed(value: float, precision: int = 2, width: int = 3) -> str:
    """LabVIEW ``%.;<width>.<precision>f``: decimal point, ``NaN``/``Inf``/``-Inf`` as LabVIEW writes them."""
    text = _special(value)
    if text is None:
        text = f"{value:.{precision}f}"
    return text.rjust(width)


def lv_exponential(value: float, precision: int = 2) -> str:
    """LabVIEW ``%.;%3.<precision>e``: ``1.23E-1``, ``4.56E+2`` (no leading zeros in the exponent)."""
    text = _special(value)
    if text is not None:
        return text
    mantissa, _, exponent = f"{value:.{precision}e}".partition("e")
    power = int(exponent)
    return f"{mantissa}E{'+' if power >= 0 else '-'}{abs(power)}"


def ordinal(n: int) -> str:
    """``1st``, ``2nd``, ``3rd``, ``4th`` … (``MeasureArrayBundle d``)."""
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def default_csv_name(now: datetime | None = None) -> str:
    """``Thermal_measurement_%y%m%d_%H%M%S.csv`` at ``now`` (local time; ``SM d161``)."""
    return (now or datetime.now().astimezone()).strftime(CSV_NAME_FORMAT) + ".csv"


# ---------------------------------------------------------------------------------------------- points
@dataclass(frozen=True)
class MeasurePoint:
    """One measurement point. ``x``/``y`` in mm in the chuck coordinate system (relative to the thermal origin),
    ``rho`` the circle radius (mm), ``theta`` the angle in degrees (offset included), ``circle`` the 0-based circle
    and ``order`` the 0-based position in the measurement order."""

    order: int
    circle: int
    x: float
    y: float
    rho: float
    theta: float


def measurement_points(diameters: Sequence[float], nb_points: Sequence[int], offset_angle: float = 0.0
                       ) -> list[MeasurePoint]:
    """The points of ``get_pyrometer_measures_positions_tablesorted_v2`` in measurement order (see the module
    docstring). ``nb_points`` shorter than ``diameters`` gives no point for the circles without a count; negative
    counts count as 0."""
    rows: list[tuple[float, int, float, float, float]] = []
    for circle, diameter in enumerate(diameters):
        count = int(nb_points[circle]) if circle < len(nb_points) else 0
        if count <= 0:
            continue
        radius = float(diameter) * 0.5
        step = 360.0 / count
        for j in range(count):
            angle = step * j + offset_angle
            rad = angle * math.pi / 180.0
            rows.append((angle, circle, radius * math.cos(rad), radius * math.sin(rad), radius))
    rows.sort(key=lambda row: row[0])                       # stable: equal angles keep the circle order
    return [MeasurePoint(order, circle, x, y, rho, angle)
            for order, (angle, circle, x, y, rho) in enumerate(rows)]


# ---------------------------------------------------------------------------------------------- statistics
def sample_statistics(samples: Sequence[float]) -> tuple[float, float, int]:
    """``(mean, sample standard deviation, failed)`` of the probe samples of one point (NI ``Std Deviation and
    Variance``, weighting "sample", ``SM d155``). Non-finite samples (failed reads) are left out and counted
    (LabVIEW let a NaN into the mean, which made the whole point NaN); one valid sample gives a deviation of 0
    **(choice)**; none gives ``(NaN, NaN)``."""
    valid = [float(v) for v in samples if math.isfinite(v)]
    failed = len(samples) - len(valid)
    if not valid:
        return math.nan, math.nan, failed
    mean = math.fsum(valid) / len(valid)
    std = statistics.stdev(valid) if len(valid) > 1 else 0.0
    return mean, std, failed


@dataclass(frozen=True)
class GlobalStatistics:
    """The ``Global`` rows: over the valid point temperatures."""

    count: int
    mean: float
    median: float
    std: float
    max: float
    min: float

    @property
    def delta(self) -> float:
        return self.max - self.min


@dataclass(frozen=True)
class CircleStatistics:
    """The rows of one circle."""

    circle: int
    count: int
    mean: float
    max: float
    min: float


def global_statistics(temperatures: Sequence[float]) -> GlobalStatistics:
    """Mean, median, sample standard deviation, max and min of the finite values (NaN when there are none; the
    deviation of a single value is 0)."""
    valid = [float(v) for v in temperatures if math.isfinite(v)]
    if not valid:
        return GlobalStatistics(0, math.nan, math.nan, math.nan, math.nan, math.nan)
    std = statistics.stdev(valid) if len(valid) > 1 else 0.0
    return GlobalStatistics(len(valid), math.fsum(valid) / len(valid), statistics.median(valid), std, max(valid),
                            min(valid))


# ---------------------------------------------------------------------------------------------- table
@dataclass(frozen=True)
class PointResult:
    """A measured point: its temperature (mean of the samples, °C), sample deviation, the samples and the number of
    failed reads."""

    point: MeasurePoint
    temperature: float
    std: float
    samples: tuple[float, ...]
    failed: int = 0


class ResultTable:
    """The ``MEASURES`` table. Not thread-safe: the controller owns it and hands out copies (:meth:`copy`)."""

    def __init__(self, points: Sequence[MeasurePoint]) -> None:
        self.points: tuple[MeasurePoint, ...] = tuple(points)
        self._results: list[PointResult | None] = [None] * len(self.points)

    def copy(self) -> ResultTable:
        other = ResultTable(self.points)
        other._results = list(self._results)
        return other

    # -- results
    def set_result(self, result: PointResult) -> None:
        order = result.point.order
        if not 0 <= order < len(self.points) or self.points[order] != result.point:
            raise ValueError(f"point {order} is not a point of this table")
        self._results[order] = result

    @property
    def results(self) -> list[PointResult]:
        """The measured points, in measurement order."""
        return [r for r in self._results if r is not None]

    @property
    def measured(self) -> int:
        return sum(r is not None for r in self._results)

    def result(self, order: int) -> PointResult | None:
        return self._results[order]

    # -- statistics
    @property
    def circle_groups(self) -> int:
        """Header groups of circles: 8 as LabVIEW, more when the points use a 9th circle."""
        used = max((p.circle + 1 for p in self.points), default=0)
        return max(LABVIEW_CIRCLE_GROUPS, used)

    def global_statistics(self) -> GlobalStatistics:
        return global_statistics([r.temperature for r in self.results])

    def circle_statistics(self, circle: int) -> CircleStatistics:
        stats = global_statistics([r.temperature for r in self.results if r.point.circle == circle])
        return CircleStatistics(circle, stats.count, stats.mean, stats.max, stats.min)

    # -- cells
    def row_headers(self) -> list[str]:
        headers = [f"Point {k + 1}" for k in range(len(self.points))] + list(GLOBAL_ROWS)
        for circle in range(self.circle_groups):
            headers += [f"{ordinal(circle + 1)} circle", *CIRCLE_ROWS]
        return headers

    def cells(self) -> list[list[str]]:
        """The table ``Value``: one row per header of :meth:`row_headers`, six columns; empty strings where nothing
        was written (points not measured yet, statistics before the first point, circles without points)."""
        width = len(COLUMN_HEADERS)
        rows: list[list[str]] = []
        for result in self._results:
            if result is None:
                rows.append([""] * width)
                continue
            p = result.point
            rows.append([lv_fixed(p.x), lv_fixed(p.y), lv_fixed(p.rho), lv_fixed(p.theta),
                         lv_fixed(result.temperature), lv_exponential(result.std)])

        def stat_row(value: float | None) -> list[str]:
            row = [""] * width
            if value is not None:
                row[TEMP_COLUMN] = lv_fixed(value)
            return row

        written = bool(self.results)
        g = self.global_statistics()
        rows.append(stat_row(None))                                                  # "Global"
        for value in (g.mean, g.median, g.std, g.max, g.min, g.delta):
            rows.append(stat_row(value if written else None))
        for circle in range(self.circle_groups):
            c = self.circle_statistics(circle)
            rows.append(stat_row(None))                                              # "<n>th circle"
            for value in (c.mean, c.max, c.min):
                rows.append(stat_row(value if c.count else None))
        return rows

    def spreadsheet(self) -> list[list[str]]:
        """What LabVIEW exports: ``["", column headers…]`` then ``[row header, cells…]`` per row."""
        return [["", *COLUMN_HEADERS]] + [[h, *row] for h, row in zip(self.row_headers(), self.cells(), strict=True)]

    def to_text(self, *, delimiter: str = "\t", newline: str = "\r\n") -> str:
        return "".join(delimiter.join(row) + newline for row in self.spreadsheet())

    def write_csv(self, path: str | os.PathLike[str], *, delimiter: str = "\t", newline: str = "\r\n",
                  encoding: str = "cp1252") -> Path:
        """Write :meth:`spreadsheet` (tab-separated as LabVIEW, see the module docstring); returns the path.
        Characters the encoding cannot hold are replaced."""
        target = Path(path)
        data = self.to_text(delimiter=delimiter, newline=newline).encode(encoding, errors="replace")
        target.write_bytes(data)
        return target
