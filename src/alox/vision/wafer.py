"""Wafer-level formulas: bowing and chuck parallelism, tilt angle, tilted coordinates, XY recentring move.

Spec: ``docs/vision-core/map.md`` Part 4 §3.8 and Part 3 §3.7 (tracking), §3.12 (bowing), §3.13 (tilt). LabVIEW VIs
covered: ``tools_alox_bowing_computation``, ``tools_alox_get_tilted_coordinates``,
``tools_tilt_function_object_detection_status``, the checks of ``alox_vision_measure_tilt_straight_edge``,
``tools_tracking_move_relative``.

Units: Z values, distances and wafer diameter in mm; bowing and parallelism in µm (``×1000``); angles in degrees;
object positions in pixels; moves in mm and pixels.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum

import numpy as np

from aloxcam.errors import AloxError

from .geometry import fold_angle_180, segment_angle_deg

__all__ = [
    "BOWING_ROWS",
    "BOWING_ROW_LABELS",
    "INVALID_CALIBRATION",
    "BowingResult",
    "CalibrationError",
    "ChuckProperties",
    "PixelUnit",
    "StageCoordinateSystem",
    "StraightEdgeTilt",
    "TiltResult",
    "TrackingMove",
    "bowing_z_values",
    "compute_bowing",
    "straight_edge_tilt",
    "tilt_from_objects",
    "tilted_coordinates",
    "tracking_move",
]

#: ``009080`` "invalid calibration error" (``tools_tracking_move_relative d3``, case "Undefined"/Default).
INVALID_CALIBRATION = 9080


class CalibrationError(AloxError):
    """The image calibration is not in mm or µm (LabVIEW error 9080)."""


# --------------------------------------------------------------------------------------------------------------------
# Bowing and parallelism
# --------------------------------------------------------------------------------------------------------------------

#: Row order of ``Z Measures`` (``tools_alox_bowing_computation d``: Index Array rows 0…8, column 2).
BOWING_ROWS = ("ZW2", "ZW3", "ZC3", "ZW1", "ZC1", "ZW4", "ZC4", "ZW5", "ZC5")
#: W = wafer, C = chuck; wafer compass names from ``bowing_en.lang`` (map Part 3 §3.12); the chuck labels are inferred:
#: chuck points sit beyond the wafer edge (radius ``wafersize/2 + general.bowingOffset``,
#: ``…bowing_measurement_state_machine d24``); move status codes 203…211 follow this order (d25…d84).
BOWING_ROW_LABELS = {
    "ZW2": "wafer centre",
    "ZW3": "wafer NE",
    "ZC3": "chuck NE",
    "ZW1": "wafer SW",
    "ZC1": "chuck SW",
    "ZW4": "wafer SE",
    "ZC4": "chuck SE",
    "ZW5": "wafer NW",
    "ZC5": "chuck NW",
}


@dataclass(frozen=True)
class ChuckProperties:
    """``alox_typedef_bowing_measurement_chuck_properties`` (INI ``[hardware.chuck]``); lengths in mm."""

    wafer_diameter_mm: float
    dz1: float = 0.0
    dz2: float = 0.0
    dz3: float = 0.0
    dz4: float = 0.0
    dz5: float = 0.0
    max_parallelism_um: float = math.inf
    bow_chuck_a: float = 0.0  # wired into the formula node but unused
    bow_chuck_b: float = 0.0  # wired into the formula node but unused


@dataclass(frozen=True)
class BowingResult:
    """``BOW RESULTS`` and ``PARA RESULTS`` (``alox_typedef_bowing_computation_results``), in µm."""

    bow_a_um: float
    bow_b_um: float
    bow_valid_a: bool
    bow_valid_b: bool
    para_a_um: float
    para_b_um: float
    para_valid_a: bool
    para_valid_b: bool

    @property
    def bow_valid(self) -> bool:
        return self.bow_valid_a and self.bow_valid_b

    @property
    def para_valid(self) -> bool:
        return self.para_valid_a and self.para_valid_b


def bowing_z_values(
    z_measures: Sequence[float] | Sequence[Sequence[float]] | np.ndarray, *, z_column: int = 2
) -> dict[str, float]:
    """Name the nine Z values (mm): a 1-D sequence of 9 values, or LabVIEW's 2-D ``Z Measures`` (column
    ``z_column``, 2 as wired). Rows not measured yet are NaN (the bowing state machine stores NaN with each position,
    ``…bowing_measurement_state_machine d24``); missing rows are NaN too."""
    arr = np.asarray(z_measures, dtype=np.float64)
    z = arr[:, z_column] if arr.ndim == 2 else arr.ravel()
    values = [float(z[i]) if i < z.size else math.nan for i in range(len(BOWING_ROWS))]
    return dict(zip(BOWING_ROWS, values, strict=True))


def compute_bowing(
    z_measures: Sequence[float] | Sequence[Sequence[float]] | np.ndarray,
    *,
    measure_distance_mm: float,
    max_bowing_um: float,
    chuck: ChuckProperties,
    z_column: int = 2,
) -> BowingResult:
    """Wafer bowing and wafer/chuck parallelism — formula node of ``tools_alox_bowing_computation d``::

        BowA  = ((ZW1 + ZW3)/2 − ZW2) · waferDiam/(2·Dist) · 1000
        BowB  = ((ZW4 + ZW5)/2 − ZW2) · waferDiam/(2·Dist) · 1000
        ParaA = ((ZC1 + DZ1 − ZW1) − (ZC3 + DZ3 − ZW3)) · 1000
        ParaB = ((ZC4 + DZ4 − ZW4) − (ZC5 + DZ5 − ZW5)) · 1000

    ``Dist`` = ``measure Distance`` (mm, centre to the measured wafer points), ``waferDiam`` = the chuck property
    ``Wafer Diameter (mm)``: the sagitta measured over ``Dist`` is scaled linearly to the wafer radius ("Estimation
    linéaire"). Validity as wired: ``bow valid A/B = Bow ≤ max Bowing`` (signed: a large negative bow is "valid"),
    ``para valid A/B = −Max Parallelism ≤ Para ≤ Max Parallelism`` (In Range, limits included). NaN → not valid.
    Not implemented, as in LabVIEW: the commented chuck-corrected formula and the "parabolic" variant
    (``wafersize²/(2·dx²)``); ``DZ2``, ``bowChuckA/B`` are unused; the ``ChuckCalibration`` case selects between
    two identical outputs.
    """
    z = bowing_z_values(z_measures, z_column=z_column)
    scale = chuck.wafer_diameter_mm / (2.0 * measure_distance_mm) if measure_distance_mm else math.nan
    bow_a = ((z["ZW1"] + z["ZW3"]) / 2.0 - z["ZW2"]) * scale * 1000.0
    bow_b = ((z["ZW4"] + z["ZW5"]) / 2.0 - z["ZW2"]) * scale * 1000.0
    para_a = ((z["ZC1"] + chuck.dz1 - z["ZW1"]) - (z["ZC3"] + chuck.dz3 - z["ZW3"])) * 1000.0
    para_b = ((z["ZC4"] + chuck.dz4 - z["ZW4"]) - (z["ZC5"] + chuck.dz5 - z["ZW5"])) * 1000.0
    max_para = chuck.max_parallelism_um
    return BowingResult(
        bow_a_um=bow_a,
        bow_b_um=bow_b,
        bow_valid_a=bool(bow_a <= max_bowing_um),
        bow_valid_b=bool(bow_b <= max_bowing_um),
        para_a_um=para_a,
        para_b_um=para_b,
        para_valid_a=bool(-max_para <= para_a <= max_para),
        para_valid_b=bool(-max_para <= para_b <= max_para),
    )


# --------------------------------------------------------------------------------------------------------------------
# Tilt
# --------------------------------------------------------------------------------------------------------------------


def tilted_coordinates(point: Sequence[float], tilt_deg: float) -> tuple[float, float]:
    """Rotate a wafer point about the wafer centre (origin) — ``tools_alox_get_tilted_coordinates d``.

    ``θ = tilt·π/180``, ``x' = x·cosθ − y·sinθ``, ``y' = x·sinθ + y·cosθ`` (counter-clockwise for θ > 0 with Y up). Used
    by HL mesa to correct the start position by the measured tilt. LabVIEW outputs SGL; float64 here.
    """
    theta = math.radians(float(tilt_deg))
    x, y = float(point[0]), float(point[1])
    c, s = math.cos(theta), math.sin(theta)
    return (x * c - y * s, x * s + y * c)


@dataclass(frozen=True)
class TiltResult:
    """Outputs of ``tools_tilt_function_object_detection_status`` (dual-objects tilt)."""

    angle_deg: float  # in (−90, 90]
    valid: bool
    message: str  # overlay text
    line: tuple[tuple[float, float], tuple[float, float]]  # Match1 → Match2


def tilt_from_objects(
    match1_mm: Sequence[float], match2_mm: Sequence[float], *, expected_deg: float, tolerance_deg: float
) -> TiltResult:
    """Tilt angle of the line Match1 → Match2 (wafer mm) — ``tools_tilt_function_object_detection_status d``.

    Angle against the X axis (``IMAQ Get Angles`` with the reference line (0,0)–(1,0); see
    :func:`~alox.vision.geometry.segment_angle_deg` for the sign convention), folded into (−90°, 90°]
    (:func:`~alox.vision.geometry.fold_angle_180`); ``valid`` when it lies in ``[expected − tol, expected + tol]``
    (limits included). Message ``TILT FOUND: %.1f°`` or ``TILT NOT FOUND/INVALID``. Coincident points → NaN, invalid.
    """
    p1 = (float(match1_mm[0]), float(match1_mm[1]))
    p2 = (float(match2_mm[0]), float(match2_mm[1]))
    angle = math.nan if p1 == p2 else fold_angle_180(segment_angle_deg(p1, p2))
    valid = bool(expected_deg - tolerance_deg <= angle <= expected_deg + tolerance_deg)
    message = f"TILT FOUND: {angle:.1f}°" if valid else "TILT NOT FOUND/INVALID"
    return TiltResult(angle, valid, message, (p1, p2))


@dataclass(frozen=True)
class StraightEdgeTilt:
    """Checks of ``alox_vision_measure_tilt_straight_edge`` (``…_straight_edge_report`` subset)."""

    angle_deg: float  # "Angle (Pixel)" of the first straight edge, NaN if none
    edge_found: bool  # EDGE FOUND?
    points_percent: float  # used edges / search lines · 100
    min_points_ok: bool  # MIN POINTS OK?
    angle_range_ok: bool  # ANGLE RANGE OK?
    message: str

    @property
    def valid(self) -> bool:
        """``TILT OK`` = EDGE FOUND AND MIN POINTS OK AND ANGLE RANGE OK."""
        return self.edge_found and self.min_points_ok and self.angle_range_ok


def straight_edge_tilt(
    angle_deg: float | None,
    *,
    used_edges: int,
    search_lines: int,
    min_points_percent: float,
    orientation_deg: float = 0.0,
    angle_range_deg: float = 360.0,
) -> StraightEdgeTilt:
    """Validity of a straight-edge tilt — ``alox_vision_measure_tilt_straight_edge d``.

    ``angle_deg`` is the angle of the first straight edge found (``None`` = no edge: ``EDGE FOUND?`` FALSE). Wired
    checks: ``used_edges / search_lines · 100 ≥ Min. Points (%)``; ``|angle|`` in ``[Orientation − Angle Range,
    Orientation + Angle Range]`` (limits included; the absolute value is taken before the test). Overlay
    ``TILT ANGLE: %.1f°`` or ``TILT NOT FOUND/INVALID``. Finding the edge itself (``IMAQ Find Straight Edges 3``)
    is not part of this module; a robust fit of the edge points is :func:`~alox.vision.geometry.fit_line_ransac`.
    """
    found = angle_deg is not None and math.isfinite(angle_deg)
    angle = float(angle_deg) if found else math.nan
    pct = 100.0 * used_edges / search_lines if search_lines > 0 else math.nan
    min_ok = bool(pct >= min_points_percent)
    range_ok = bool(orientation_deg - angle_range_deg <= abs(angle) <= orientation_deg + angle_range_deg)
    valid = found and min_ok and range_ok
    message = f"TILT ANGLE: {angle:.1f}°" if valid else "TILT NOT FOUND/INVALID"
    return StraightEdgeTilt(angle, found, pct, min_ok, range_ok, message)


# --------------------------------------------------------------------------------------------------------------------
# XY recentring (tracking)
# --------------------------------------------------------------------------------------------------------------------


class PixelUnit(IntEnum):
    """Unit of the image calibration pixel size (NI ``Get Calibration Info`` frames)."""

    MILLIMETER = 1
    MICROMETER = 2


class StageCoordinateSystem(IntEnum):
    """XY stage axes as drawn in the cases of ``tools_tracking_move_relative d5…d8, d14…d17`` (image: X right, Y
    down); the "Inverse axis" negations follow from the drawings."""

    X_RIGHT_Y_UP = 0  # case "0, Default" ("#1"): Y inverted
    X_LEFT_Y_UP = 1  # case "1" ("#2"): X and Y inverted
    X_RIGHT_Y_DOWN = 2  # case "2" ("#3"): same axes as the image
    X_LEFT_Y_DOWN = 3  # case "3" (labelled "#3" too): X inverted


_AXIS_SIGNS = {
    StageCoordinateSystem.X_RIGHT_Y_UP: (1.0, -1.0),
    StageCoordinateSystem.X_LEFT_Y_UP: (-1.0, -1.0),
    StageCoordinateSystem.X_RIGHT_Y_DOWN: (1.0, 1.0),
    StageCoordinateSystem.X_LEFT_Y_DOWN: (-1.0, 1.0),
}


@dataclass(frozen=True)
class TrackingMove:
    """Outputs of ``tools_tracking_move_relative``."""

    move_mm: tuple[float, float]  # "Relative Move (mm)", stage axes
    move_px: tuple[float, float]  # "Relative Move (pixels)", image centre − object (flips applied)
    min_move: tuple[bool, bool]  # "Min. Move": |dx|, |dy| ≥ Min. Distance (mm)

    @property
    def needed(self) -> bool:
        return self.min_move[0] or self.min_move[1]


def tracking_move(
    object_px: Sequence[float],
    image_size: Sequence[int],
    *,
    pixel_size: float,
    pixel_unit: PixelUnit | str,
    min_distance_mm: float,
    coordinate_system: StageCoordinateSystem | int,
    flip_horizontal: bool = False,
    flip_vertical: bool = False,
    x_stage_invert: bool = False,
    y_stage_invert: bool = False,
) -> TrackingMove:
    """Relative XY move bringing the object at ``object_px`` to the image centre — ``tools_tracking_move_relative d``.

    1. pixel size in mm: as is for ``mm``, ``/1000`` for ``µm``; any other calibration → :class:`CalibrationError`
       9080 (case "Undefined", Default; d3, d4, d13);
    2. ``dx_px = W/2 − x``, ``dy_px = H/2 − y`` ("XY move to recenter (image coord. sys)"); ``dx_mm = dx_px·px``;
       ``Min. Move = (|dx_mm| ≥ Min. Distance, |dy_mm| ≥ Min. Distance)``;
    3. "Inverse XY move (we move the stages, not the camera)": ``(−dx_mm, −dy_mm)``, unless the debug global
       ``X Stage Invert`` / ``Y Stage Invert`` is ON ("FOR DEBUG ONLY (SHOULD BE OFF FOR AET) – OFF: XY moves cameras
       (AET), ON: XY moves wafer (OPTO)"). The globals are explicit parameters here, default OFF (production AET);
    4. axis signs of the stage coordinate system (``alox_manager_xy_coordinate_system``, :class:`StageCoordinateSystem`;
       required — which value each delivered machine uses is an open question, map Part 4 §9 Q8);
    5. ``Flip Options`` (d9…d12, d18…d21): Horizontal (top↔bottom) negates the Y components, Vertical negates the X
       components, of both the mm and the pixel move.

    The pixel move is not affected by steps 3–4. ``image_size`` is ``(width, height)`` in pixels.
    """
    unit = _pixel_unit(pixel_unit)
    px_mm = float(pixel_size) if unit is PixelUnit.MILLIMETER else float(pixel_size) / 1000.0
    width, height = float(image_size[0]), float(image_size[1])
    dx_px = width / 2.0 - float(object_px[0])
    dy_px = height / 2.0 - float(object_px[1])
    dx_mm, dy_mm = dx_px * px_mm, dy_px * px_mm
    min_move = (bool(abs(dx_mm) >= min_distance_mm), bool(abs(dy_mm) >= min_distance_mm))
    sx = dx_mm if x_stage_invert else -dx_mm
    sy = dy_mm if y_stage_invert else -dy_mm
    ax, ay = _AXIS_SIGNS[StageCoordinateSystem(coordinate_system)]
    mx, my = ax * sx, ay * sy
    if flip_horizontal:
        my, dy_px = -my, -dy_px
    if flip_vertical:
        mx, dx_px = -mx, -dx_px
    return TrackingMove((mx + 0.0, my + 0.0), (dx_px + 0.0, dy_px + 0.0), min_move)


def _pixel_unit(unit: PixelUnit | str) -> PixelUnit:
    if isinstance(unit, PixelUnit):
        return unit
    key = str(unit).strip().lower()
    if key in ("mm", "millimeter", "millimetre"):
        return PixelUnit.MILLIMETER
    if key in ("um", "µm", "μm", "micrometer", "micrometre", "micron"):
        return PixelUnit.MICROMETER
    raise CalibrationError(INVALID_CALIBRATION, f"invalid calibration error (pixel unit {unit!r})")
