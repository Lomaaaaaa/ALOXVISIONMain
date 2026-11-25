r"""Wavelength → exposure table: the replacement of ``alox_manager_exposure_wl_lut`` and its tools.

Spec: map Part 1 §4.2 (the table is the profile property ``general.exposures``, a 2-D array; methods; CSV columns),
§2.3 (init frame 26: "Create default table if none available"), Part 4 §3.9 (``tools_alox_interpolate_data_from_
wavelength``, ``tools_alox_extrapolate_data_from_wavelength``), §4.6 (globals ``Alox Def WL Table``, ``Alox Def Exp
Table (µs)``); typedefs ``alox_typedef_exposures_wl_lut_method``, ``alox_typedef_auto_exposure_data``,
``alox_typedef_auto_exposure_theoric_data``.

The table has one row per wavelength and 7 columns (diagram note, ``alox_manager_exposure_wl_lut d0``): 0 WL (nm),
1 ExpoTh (µs), 2 ExpoMin, 3 ExpoMax (µs), 4 BMin, 5 BMax (grey levels), 6 ExpoAuto (µs).

Observed in ``alox_manager_exposure_wl_lut`` (d0..d27):

- "Create Default" (d3, d6): the globals ``Alox Def WL Table`` and ``Alox Def Exp Table (µs)`` (front panel of
  ``alox_core_global_variables p``: :data:`DEFAULT_WAVELENGTHS`, :data:`DEFAULT_EXPOSURES_US`, 12 values each, the first
  wavelength being **0 nm**); ExpoMin/Max = ExpoTh ∓ ExpoTh·20/100 ("Def. Variation %" 20, coerced to 0..100); BMin,
  BMax, ExpoAuto = NaN.
- "Import CSV" (d4, d5): ``Read Delimited Spreadsheet (DBL)`` with a **tab** delimiter; columns 1 and 2 mandatory;
  ExpoMin/Max from columns 3 and 4 when the file has at least 4 columns, else ExpoTh ∓ 20 % (constant ``0,2``); BMin,
  BMax, ExpoAuto from columns 5..7 when it has at least 7 columns, else NaN. "Export CSV" (d7): ``Write Delimited
  Spreadsheet`` with ``%.0f`` and a tab delimiter (the whole 7-column table).
- "Get Exposure(WL)" (d8): interpolation of ExpoTh (:func:`interpolate`). The wavelength input is a U32.
- "Get Fixed Expo" / "Set Fixed Expo" (d9..d14): exact search of the wavelength in column 0; not found → error 9093
  "AE Error (Invalid wavelength)"; "Set" replaces ExpoTh only.
- "Get Auto Expo" (d15..d17): when the table holds at least one row, the interpolations of ExpoAuto, ExpoMin, ExpoMax,
  BMin, BMax (and of ExpoTh for "Exposure (µs)"); else error 9092 "AE Error (AE data not available)".
- "Set Auto Expo" (d18): ``tools_alox_extrapolate_data_from_wavelength`` (:func:`upsert_auto_expo`). "Reset Auto Expo"
  (d19): BMin, BMax, ExpoAuto = NaN. "Save calib values" (d20, comment copied from Reset): ExpoTh ← the last column
  (ExpoAuto).
- "Has Auto Expo ?" (d21): at least one row, every column as long as column 0, no NaN in BMin, BMax, ExpoAuto and no
  ±Inf in ExpoAuto. "Has Fixed Expo ?" (d22): at least one row and ExpoTh as long as column 0.
- "Get Wavelengths" (d23): column 0 converted to U32. "Set Wavelengths" (d24..d26): when the table is not (n × 7), it is
  replaced by an n × 7 table of NaN; then column 0 ← the wavelengths.
- TODO in d0 (kept): "REVOIR LA TABLE D'EXPOS THEORIQUES PAR DEFAUT INADAPTEE AUX CAMERAS DU MODULE OPTO".

A second default table is a constant of ``alox_manager_profile d0`` (:data:`PROFILE_DEFAULT_TABLE`, used by profile
"New"/"Check"); the init cycle only calls "Create Default" when the profile has no wavelength.
"""

from __future__ import annotations

import csv
import io
import math
import os
import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple, Protocol

from alox.core.properties import parse_number

__all__ = [
    "AE_DATA_NOT_AVAILABLE",
    "COLUMNS",
    "DEFAULT_EXPOSURES_US",
    "DEFAULT_VARIATION_PERCENT",
    "DEFAULT_WAVELENGTHS",
    "EXPOSURES_PROPERTY",
    "INVALID_WAVELENGTH",
    "PROFILE_DEFAULT_TABLE",
    "AutoExposureData",
    "ExposureError",
    "ExposureRow",
    "ExposureTable",
    "Interpolation",
    "default_table",
    "interpolate",
    "read_csv",
    "table_from_columns",
    "upsert_auto_expo",
    "write_csv",
]

#: The profile property holding the table (``alox_manager_exposure_wl_lut d0``).
EXPOSURES_PROPERTY = "general.exposures"
COLUMNS = ("WL", "ExpoTh", "ExpoMin", "ExpoMax", "BMin", "BMax", "ExpoAuto")
WL, EXPO_TH, EXPO_MIN, EXPO_MAX, B_MIN, B_MAX, EXPO_AUTO = range(7)

AE_DATA_NOT_AVAILABLE = 9092
INVALID_WAVELENGTH = 9093

#: ``Alox Def WL Table`` (nm), front panel of ``alox_core_global_variables p``: 12 elements, the first one is 0.
DEFAULT_WAVELENGTHS: tuple[int, ...] = (0, 700, 725, 750, 775, 800, 825, 850, 875, 900, 925, 950)
#: ``Alox Def Exp Table (µs)``, same front panel, paired element by element with :data:`DEFAULT_WAVELENGTHS`.
DEFAULT_EXPOSURES_US: tuple[int, ...] = (27000, 18000, 20000, 24000, 36000, 55000, 87100, 132000, 189000, 277000,
                                         414000, 628000)
#: "Def. Variation %" of "Create Default" (d3) and the ``0,2`` of "Import CSV" (d5).
DEFAULT_VARIATION_PERCENT = 20.0
_IMPORT_FACTOR = 0.2

#: The 12 × 7 constant of ``alox_manager_profile d0`` (default of the profile property ``general.exposures``).
PROFILE_DEFAULT_TABLE: tuple[tuple[float, ...], ...] = tuple(
    (float(wl), float(th), 10000.0, 40000.0, 50.0, 100.0, float(th))
    for wl, th in ((0, 20000), (700, 25000), (725, 30000), (750, 45000), (775, 70000), (800, 125000), (825, 200000),
                   (850, 300000), (875, 450000), (900, 500000), (925, 600000), (950, 750000)))


class ExposureError(ValueError):
    """An ``alox_manager_exposure_wl_lut`` error: 9093 "AE Error (Invalid wavelength)", 9092 "AE Error (AE data not
    available)", or None for a Python-only check."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class ExposureRow(NamedTuple):
    """One row of the table (:data:`COLUMNS`); exposures in µs, brightness in grey levels."""

    wl: float
    expo_th: float
    expo_min: float
    expo_max: float
    b_min: float
    b_max: float
    expo_auto: float


@dataclass(frozen=True)
class AutoExposureData:
    """``alox_typedef_auto_exposure_data`` ("AE Data"), in the cluster order."""

    auto_expo: float
    expo_min: float
    expo_max: float
    brightness_min: float
    brightness_max: float


@dataclass(frozen=True)
class Interpolation:
    """Outputs of ``tools_alox_interpolate_data_from_wavelength``: ``Data Out``, ``Interpolated``, ``Array Index``."""

    value: float
    interpolated: bool
    index: int


class ProfileLike(Protocol):
    """What the table needs from a profile: :class:`alox.core.profiles.ProfileManager` or a profile store."""

    def get(self, dotted: str, default: Any = None) -> Any: ...

    def set(self, dotted: str, value: Any) -> None: ...


# ---------------------------------------------------------------------------------------------- pure functions
def _at(values: Sequence[float], index: int) -> float:
    """LabVIEW ``Index Array``: the default value 0 out of range (no negative wrap-around)."""
    return float(values[index]) if 0 <= index < len(values) else 0.0


def interpolate(wavelength: float, wavelengths: Sequence[float], data: Sequence[float]) -> Interpolation:
    """``tools_alox_interpolate_data_from_wavelength`` (d0..d10, map Part 4 §3.9).

    Empty or different sizes → (NaN, False, −1) ("Invalid input arrays", d9). ``wavelength`` ≤ the first wavelength →
    the first value, index 0 ("Get First Value", d8); ≥ the last → the last value, index n−1 ("Get Last Value", d7);
    an exact match → that value and index ("Value found: No Interpolation", d5); otherwise the first ``i`` with
    ``wavelength ≤ WL[i]`` and ``D[i−1] + (wavelength − WL[i−1])·(D[i] − D[i−1])/(WL[i] − WL[i−1])``, index ``i``,
    ``interpolated`` True (d6). The table is assumed sorted, as in LabVIEW (no check); an out-of-range index reads 0.
    """
    n = len(wavelengths)
    if n == 0 or n != len(data):
        return Interpolation(math.nan, False, -1)
    wl = float(wavelength)
    if wl <= wavelengths[0]:
        return Interpolation(float(data[0]), False, 0)
    if wl >= wavelengths[-1]:
        return Interpolation(float(data[-1]), False, n - 1)
    for i, w in enumerate(wavelengths):
        if float(w) == wl:
            return Interpolation(float(data[i]), False, i)
    i = next(k for k, w in enumerate(wavelengths) if wl <= w)
    a, b = _at(wavelengths, i - 1), float(wavelengths[i])
    c, d = _at(data, i - 1), float(data[i])
    value = math.nan if b == a else c + (wl - a) * (d - c) / (b - a)
    return Interpolation(value, True, i)


def _column(table: Sequence[Sequence[float]], index: int) -> list[float]:
    """Column ``index`` (LabVIEW ``Index Array`` on a column: empty when the table is narrower)."""
    if not table or index >= len(table[0]):
        return []
    return [float(row[index]) for row in table]


def table_from_columns(columns: Sequence[Sequence[float]]) -> list[list[float]]:
    """``Build Array`` of the columns + ``Transpose 2D Array`` (d3, d4): one row per index of the shortest column."""
    n = min((len(c) for c in columns), default=0)
    return [[float(c[i]) for c in columns] for i in range(n)]


def default_table(wavelengths: Sequence[float] = DEFAULT_WAVELENGTHS,
                  exposures: Sequence[float] = DEFAULT_EXPOSURES_US,
                  variation_percent: float = DEFAULT_VARIATION_PERCENT) -> list[list[float]]:
    """"Create Default" (d3): rows (WL, ExpoTh, ExpoTh − v, ExpoTh + v, NaN, NaN, NaN) with ``v = ExpoTh·p/100`` and
    ``p`` coerced to [0, 100]."""
    p = min(max(float(variation_percent), 0.0), 100.0)
    th = [float(e) for e in exposures]
    delta = [e * p / 100.0 for e in th]
    nan = [math.nan] * len(th)
    return table_from_columns([wavelengths, th, [e - v for e, v in zip(th, delta, strict=True)],
                               [e + v for e, v in zip(th, delta, strict=True)], nan, nan, nan])


def upsert_auto_expo(table: Sequence[Sequence[float]], wavelength: float,
                     data: AutoExposureData) -> list[list[float]]:
    """``tools_alox_extrapolate_data_from_wavelength`` (d0..d10), used by "Set Auto Expo".

    The table must hold at least one row and 7 full columns, else :class:`ExposureError` 9093 ("Input wavelength is
    out of the table range", a misnomer). A wavelength already in column 0 (exact) gets ExpoMin, ExpoMax, BMin, BMax,
    ExpoAuto replaced, ExpoTh unchanged ("Wavelength already in table => replace row existing values", d9). Another
    wavelength gets a new row (WL, AutoExpo, ExpoMin, ExpoMax, BMin, BMax, AutoExpo) inserted at its place, the table
    being then sorted on column 0 (the panel example of ``… p`` shows this row). Returns a new table.
    """
    rows = [[float(v) for v in row] for row in table]
    n = len(rows)
    if n == 0 or any(len(row) < 7 for row in rows):
        raise ExposureError("the exposure table is empty or has fewer than 7 columns", INVALID_WAVELENGTH)
    wl = float(wavelength)
    for row in rows:
        if row[WL] == wl:
            row[EXPO_MIN:EXPO_AUTO + 1] = [data.expo_min, data.expo_max, data.brightness_min, data.brightness_max,
                                           data.auto_expo]
            return rows
    extra = [math.nan] * (len(rows[0]) - 7)
    rows.append([wl, data.auto_expo, data.expo_min, data.expo_max, data.brightness_min, data.brightness_max,
                 data.auto_expo, *extra])
    rows.sort(key=lambda row: row[WL])
    return rows


# ---------------------------------------------------------------------------------------------- CSV
def read_csv(path: str | os.PathLike[str], delimiter: str | None = None) -> list[list[float]]:
    """"Import CSV" (d4, d5) → a 7-column table.

    LabVIEW reads the file with a **tab** delimiter; ``delimiter`` None picks tab when a line holds one, else ``;``
    when a line holds one, else ``,``. Numbers take a decimal point or comma (``,`` only when it is not the
    delimiter). Missing cells read 0, as ``Read Delimited Spreadsheet`` pads short rows. Column rules: 1 (WL) and 2
    (ExpoTh) mandatory; ExpoMin/Max from columns 3 and 4 when the file has at least 4 columns, else ExpoTh ∓ 20 %;
    BMin, BMax, ExpoAuto from columns 5..7 when it has at least 7 columns, else NaN.

    Deviations: blank lines and lines whose first cell is not a number (a header) are skipped (LabVIEW read them as
    rows of 0); any other unreadable cell raises :class:`ValueError` (LabVIEW read 0).
    """
    text = Path(path).read_text(encoding="cp1252", errors="replace")
    lines = [line for line in text.splitlines() if line.strip()]
    if delimiter is None:
        delimiter = "\t" if any("\t" in line for line in lines) else ";" if any(";" in line for line in lines) \
            else ","
    rows: list[list[float]] = []
    for number, raw in enumerate(csv.reader(lines, delimiter=delimiter), start=1):       # non-blank rows
        cells = [cell.strip() for cell in raw]
        while cells and not cells[-1]:                      # a trailing delimiter adds no column
            cells.pop()
        try:
            parse_number(cells[0])
        except (ValueError, IndexError):
            continue
        try:
            rows.append([parse_number(cell) if cell else 0.0 for cell in cells])
        except ValueError as exc:
            raise ValueError(f"{path}: row {number}: {exc}") from None
    cols = max((len(row) for row in rows), default=0)
    if cols < 2:
        raise ExposureError(f"{path}: columns 1 (wavelength) and 2 (exposure) are mandatory")
    rows = [row + [0.0] * (cols - len(row)) for row in rows]
    wl, th = _column(rows, WL), _column(rows, EXPO_TH)
    if cols >= 4:
        lo, hi = _column(rows, EXPO_MIN), _column(rows, EXPO_MAX)
    else:
        lo = [e - e * _IMPORT_FACTOR for e in th]                 # "0,2" × ExpoTh, then − and + (d5)
        hi = [e + e * _IMPORT_FACTOR for e in th]
    if cols >= 7:
        b_min, b_max, auto = _column(rows, B_MIN), _column(rows, B_MAX), _column(rows, EXPO_AUTO)
    else:
        b_min = b_max = auto = [math.nan] * len(rows)
    return table_from_columns([wl, th, lo, hi, b_min, b_max, auto])


def _format0(value: float) -> str:
    """LabVIEW ``%.0f``: no decimals, ``NaN``, ``Inf``, ``-Inf``."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Inf" if value > 0 else "-Inf"
    return f"{value:.0f}"


def write_csv(table: Iterable[Sequence[float]], path: str | os.PathLike[str], delimiter: str = "\t") -> Path:
    """"Export CSV" (d7): every row with ``%.0f`` (no decimals) and a tab delimiter, CRLF line ends as LabVIEW on
    Windows **(inferred)**."""
    buffer = io.StringIO()
    for row in table:
        buffer.write(delimiter.join(_format0(float(v)) for v in row) + "\r\n")
    target = Path(path)
    target.write_text(buffer.getvalue(), encoding="cp1252", newline="")
    return target


def _to_u32(value: float) -> int:
    """LabVIEW DBL → U32: round half to even, saturated to [0, 2³²−1]; NaN → 0 **(inferred)**."""
    if math.isnan(value):
        return 0
    if math.isinf(value):
        return 0 if value < 0 else 2**32 - 1
    return min(max(round(value), 0), 2**32 - 1)


# ---------------------------------------------------------------------------------------------- manager
class ExposureTable:
    """``alox_manager_exposure_wl_lut``: the table kept in the profile property ``general.exposures``.

    ``profile`` is the active profile (:class:`alox.core.profiles.ProfileManager`) or a profile store; every method
    reads the property, and the changing ones write it back with ``set`` (saved with the profile). Thread-safe for
    the read-modify-write sequences of this object.
    """

    def __init__(self, profile: ProfileLike, key: str = EXPOSURES_PROPERTY) -> None:
        self.profile = profile
        self.key = key
        self._lock = threading.RLock()

    # -- storage
    def table(self) -> list[list[float]]:
        """The table (a copy; empty when the property is missing)."""
        value = self.profile.get(self.key, None)
        if not value:
            return []
        return [[float(v) for v in row] for row in value]

    def rows(self) -> list[ExposureRow]:
        """The rows as :class:`ExposureRow` (rows narrower than 7 columns padded with NaN)."""
        return [ExposureRow(*(list(row[:7]) + [math.nan] * (7 - len(row)))) for row in self.table()]

    def store(self, table: Iterable[Sequence[float]]) -> None:
        """Write ``table`` to the profile property."""
        self.profile.set(self.key, [[float(v) for v in row] for row in table])

    # -- methods
    def create_default(self) -> list[int]:
        """"Create Default": :func:`default_table`; returns the wavelengths."""
        with self._lock:
            self.store(default_table())
            return self.wavelengths()

    def import_csv(self, path: str | os.PathLike[str], delimiter: str | None = None) -> list[int]:
        """"Import CSV": :func:`read_csv`; returns the wavelengths."""
        table = read_csv(path, delimiter)
        with self._lock:
            self.store(table)
            return self.wavelengths()

    def export_csv(self, path: str | os.PathLike[str], delimiter: str = "\t") -> Path:
        """"Export CSV": :func:`write_csv` of the whole table."""
        return write_csv(self.table(), path, delimiter)

    def exposure_for(self, wavelength: float) -> float:
        """"Get Exposure(WL)": ExpoTh interpolated at ``wavelength`` (:func:`interpolate`). :class:`ExposureError`
        when the table is empty (LabVIEW returned NaN)."""
        table = self.table()
        result = interpolate(wavelength, _column(table, WL), _column(table, EXPO_TH))
        if result.index < 0:
            raise ExposureError(f"no exposure table in {self.key}")
        return result.value

    def _find(self, table: list[list[float]], wavelength: float) -> int:
        wl = float(wavelength)
        for i, row in enumerate(table):
            if row and row[WL] == wl:
                return i
        raise ExposureError(f"AE Error (Invalid wavelength): {wavelength} nm is not in the table",
                            INVALID_WAVELENGTH)

    def fixed_expo(self, wavelength: float) -> float:
        """"Get Fixed Expo": ExpoTh of the row of exactly ``wavelength``; :class:`ExposureError` 9093 otherwise."""
        table = self.table()
        return table[self._find(table, wavelength)][EXPO_TH]

    def set_fixed_expo(self, wavelength: float, exposure: float) -> None:
        """"Set Fixed Expo": ExpoTh of the row of exactly ``wavelength`` (the other columns are kept);
        :class:`ExposureError` 9093 when the wavelength is not in the table."""
        with self._lock:
            table = self.table()
            table[self._find(table, wavelength)][EXPO_TH] = float(exposure)
            self.store(table)

    def auto_expo(self, wavelength: float) -> tuple[float, AutoExposureData]:
        """"Get Auto Expo": ``(ExpoTh, AE data)`` interpolated at ``wavelength`` (values may be NaN after a reset);
        :class:`ExposureError` 9092 when the table is empty."""
        table = self.table()
        wls = _column(table, WL)
        n = len(wls)
        if n == 0 or len(_column(table, EXPO_MIN)) < n or len(_column(table, EXPO_MAX)) < n:
            raise ExposureError("AE Error (AE data not available)", AE_DATA_NOT_AVAILABLE)

        def at(col: int) -> float:
            return interpolate(wavelength, wls, _column(table, col)).value

        return at(EXPO_TH), AutoExposureData(at(EXPO_AUTO), at(EXPO_MIN), at(EXPO_MAX), at(B_MIN), at(B_MAX))

    def set_auto_expo(self, wavelength: float, data: AutoExposureData) -> None:
        """"Set Auto Expo": :func:`upsert_auto_expo`."""
        with self._lock:
            self.store(upsert_auto_expo(self.table(), wavelength, data))

    def reset_auto_expo(self) -> None:
        """"Reset Auto Expo": BMin, BMax, ExpoAuto ← NaN (ExpoMin/Max kept)."""
        with self._lock:
            table = self.table()
            for row in table:
                for col in (B_MIN, B_MAX, EXPO_AUTO):
                    if col < len(row):
                        row[col] = math.nan
            self.store(table)

    def save_calib_values(self) -> None:
        """"Save calib values": ExpoTh ← the last column (ExpoAuto in a 7-column table)."""
        with self._lock:
            table = self.table()
            for row in table:
                if len(row) > EXPO_TH:
                    row[EXPO_TH] = row[-1]
            self.store(table)

    def has_auto_expo(self) -> bool:
        """"Has Auto Expo ?"."""
        table = self.table()
        n = len(_column(table, WL))
        if n == 0 or any(len(_column(table, col)) < n for col in range(1, 7)):
            return False
        no_nan = all(not math.isnan(v) for col in (B_MIN, B_MAX, EXPO_AUTO) for v in _column(table, col))
        return no_nan and all(not math.isinf(v) for v in _column(table, EXPO_AUTO))

    def has_fixed_expo(self) -> bool:
        """"Has Fixed Expo ?"."""
        table = self.table()
        n = len(_column(table, WL))
        return n > 0 and len(_column(table, EXPO_TH)) >= n

    def wavelengths(self) -> list[int]:
        """"Get Wavelengths": column 0 as U32."""
        return [_to_u32(v) for v in _column(self.table(), WL)]

    def set_wavelengths(self, wavelengths: Sequence[float]) -> None:
        """"Set Wavelengths": a table that is not (len(wavelengths) × 7) is replaced by NaN rows; then column 0 ←
        ``wavelengths``."""
        with self._lock:
            table = self.table()
            n = len(wavelengths)
            if len(table) != n or any(len(row) != 7 for row in table):
                table = [[math.nan] * 7 for _ in range(n)]
            for row, wl in zip(table, wavelengths, strict=True):
                row[WL] = float(wl)
            self.store(table)
