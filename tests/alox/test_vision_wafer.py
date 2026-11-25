"""Tests of alox.vision.wafer: bowing/parallelism, tilted coordinates, tilt angle, XY recentring move."""

from __future__ import annotations

import math

import numpy as np
import pytest

from alox.vision.wafer import (
    BOWING_ROWS,
    INVALID_CALIBRATION,
    CalibrationError,
    ChuckProperties,
    PixelUnit,
    StageCoordinateSystem,
    bowing_z_values,
    compute_bowing,
    straight_edge_tilt,
    tilt_from_objects,
    tilted_coordinates,
    tracking_move,
)

CHUCK = ChuckProperties(wafer_diameter_mm=200.0, max_parallelism_um=30.0)


def z_rows(**values: float) -> list[float]:
    """Nine Z values (mm) in LabVIEW row order, 0 unless given."""
    return [values.get(name, 0.0) for name in BOWING_ROWS]


# ---------------------------------------------------------------------------------------------------------------------
# Bowing and parallelism
# ---------------------------------------------------------------------------------------------------------------------


def test_row_order_is_the_labview_numbering():
    assert BOWING_ROWS == ("ZW2", "ZW3", "ZC3", "ZW1", "ZC1", "ZW4", "ZC4", "ZW5", "ZC5")
    z = bowing_z_values(list(range(9)))
    assert z["ZW2"] == 0 and z["ZW1"] == 3 and z["ZC5"] == 8


def test_flat_wafer_on_flat_chuck():
    res = compute_bowing(z_rows(), measure_distance_mm=90.0, max_bowing_um=50.0, chuck=CHUCK)
    assert (res.bow_a_um, res.bow_b_um, res.para_a_um, res.para_b_um) == (0.0, 0.0, 0.0, 0.0)
    assert res.bow_valid and res.para_valid


def test_bowing_formula_units_and_scaling():
    # centre 10 µm above the measured points at 90 mm: sagitta -10 µm scaled by 200/(2*90)
    z = z_rows(ZW2=0.010, ZW1=0.0, ZW3=0.0, ZW4=0.002, ZW5=0.004)
    res = compute_bowing(z, measure_distance_mm=90.0, max_bowing_um=50.0, chuck=CHUCK)
    assert res.bow_a_um == pytest.approx(-0.010 * 200.0 / 180.0 * 1000.0)
    assert res.bow_b_um == pytest.approx((0.003 - 0.010) * 200.0 / 180.0 * 1000.0)


def test_bowing_validity_is_signed_as_wired():
    up = compute_bowing(z_rows(ZW1=0.1, ZW3=0.1), measure_distance_mm=100.0, max_bowing_um=50.0, chuck=CHUCK)
    assert up.bow_a_um == pytest.approx(100.0)
    assert not up.bow_valid_a and up.bow_valid_b and not up.bow_valid
    down = compute_bowing(z_rows(ZW1=-0.1, ZW3=-0.1), measure_distance_mm=100.0, max_bowing_um=50.0, chuck=CHUCK)
    assert down.bow_a_um == pytest.approx(-100.0)
    assert down.bow_valid_a  # "Bow <= max Bowing": a large negative bow passes (as read)


def test_parallelism_formula_with_chuck_offsets():
    chuck = ChuckProperties(wafer_diameter_mm=150.0, dz1=0.01, dz3=0.0, dz4=-0.005, dz5=0.005, max_parallelism_um=30.0)
    z = z_rows(ZC1=0.05, ZW1=0.0, ZC3=0.02, ZW3=0.0, ZC4=0.03, ZW4=0.01, ZC5=0.01, ZW5=0.0)
    res = compute_bowing(z, measure_distance_mm=60.0, max_bowing_um=50.0, chuck=chuck)
    assert res.para_a_um == pytest.approx(((0.05 + 0.01 - 0.0) - (0.02 + 0.0 - 0.0)) * 1000.0)  # 40 µm
    assert res.para_b_um == pytest.approx(((0.03 - 0.005 - 0.01) - (0.01 + 0.005 - 0.0)) * 1000.0)  # 0 µm
    assert not res.para_valid_a and res.para_valid_b and not res.para_valid
    neg = compute_bowing(z_rows(ZC3=0.04), measure_distance_mm=60.0, max_bowing_um=50.0, chuck=chuck)
    assert neg.para_a_um == pytest.approx(-30.0)
    assert neg.para_valid_a  # In Range: limits included


def test_bowing_from_labview_2d_array_and_nan_rows():
    rows = np.zeros((9, 3))
    rows[:, 0] = np.arange(9)  # X, Y columns are ignored
    rows[:, 2] = z_rows(ZW2=-0.005)
    res = compute_bowing(rows, measure_distance_mm=50.0, max_bowing_um=100.0, chuck=CHUCK)
    assert res.bow_a_um == pytest.approx(0.005 * 2.0 * 1000.0)
    rows[3, 2] = math.nan  # ZW1 not measured yet
    partial = compute_bowing(rows, measure_distance_mm=50.0, max_bowing_um=100.0, chuck=CHUCK)
    assert math.isnan(partial.bow_a_um) and not partial.bow_valid_a
    assert math.isnan(partial.para_a_um) and not partial.para_valid_a
    assert partial.bow_valid_b
    short = compute_bowing([0.0] * 4, measure_distance_mm=50.0, max_bowing_um=100.0, chuck=CHUCK)
    assert math.isnan(short.bow_b_um)


# ---------------------------------------------------------------------------------------------------------------------
# Tilt
# ---------------------------------------------------------------------------------------------------------------------


def test_tilted_coordinates_rotation():
    assert tilted_coordinates((1.0, 0.0), 90.0) == pytest.approx((0.0, 1.0))
    assert tilted_coordinates((10.0, 5.0), 0.0) == pytest.approx((10.0, 5.0))
    p = tilted_coordinates(tilted_coordinates((12.0, -7.0), 1.3), -1.3)
    assert p == pytest.approx((12.0, -7.0))


def test_tilt_from_objects_angle_and_folding():
    res = tilt_from_objects((0.0, 0.0), (10.0, 1.0), expected_deg=0.0, tolerance_deg=10.0)
    assert res.angle_deg == pytest.approx(math.degrees(math.atan(0.1)))
    assert res.valid
    assert res.message == "TILT FOUND: 5.7°"
    back = tilt_from_objects((10.0, 1.0), (0.0, 0.0), expected_deg=0.0, tolerance_deg=10.0)
    assert back.angle_deg == pytest.approx(res.angle_deg)  # folded modulo 180
    vertical = tilt_from_objects((0.0, 0.0), (0.0, -5.0), expected_deg=90.0, tolerance_deg=0.0)
    assert vertical.angle_deg == pytest.approx(90.0) and vertical.valid


def test_tilt_from_objects_validity_limits_and_degenerate():
    edge = tilt_from_objects((0.0, 0.0), (1.0, 1.0), expected_deg=40.0, tolerance_deg=5.0)
    assert edge.valid  # 45 = expected + tolerance, included
    out = tilt_from_objects((0.0, 0.0), (1.0, 1.0), expected_deg=0.0, tolerance_deg=5.0)
    assert not out.valid and out.message == "TILT NOT FOUND/INVALID"
    same = tilt_from_objects((2.0, 2.0), (2.0, 2.0), expected_deg=0.0, tolerance_deg=180.0)
    assert math.isnan(same.angle_deg) and not same.valid


def test_straight_edge_tilt_checks():
    ok = straight_edge_tilt(-1.5, used_edges=8, search_lines=10, min_points_percent=80.0, angle_range_deg=5.0)
    assert ok.valid and ok.points_percent == 80.0 and ok.message == "TILT ANGLE: -1.5°"
    few = straight_edge_tilt(-1.5, used_edges=7, search_lines=10, min_points_percent=80.0, angle_range_deg=5.0)
    assert few.edge_found and not few.min_points_ok and not few.valid
    steep = straight_edge_tilt(-7.0, used_edges=10, search_lines=10, min_points_percent=50.0, angle_range_deg=5.0)
    assert not steep.angle_range_ok  # |angle| tested against [orientation - range, orientation + range]
    none = straight_edge_tilt(None, used_edges=0, search_lines=10, min_points_percent=0.0)
    assert not none.edge_found and not none.valid and none.message == "TILT NOT FOUND/INVALID"
    no_lines = straight_edge_tilt(1.0, used_edges=0, search_lines=0, min_points_percent=0.0)
    assert not no_lines.min_points_ok


# ---------------------------------------------------------------------------------------------------------------------
# XY recentring (tools_tracking_move_relative)
# ---------------------------------------------------------------------------------------------------------------------

SENSOR = (2448, 2048)
OBJECT = (1224.0 + 100.0, 1024.0 - 50.0)  # 100 px right of and 50 px above the image centre


def move(**kwargs):
    params = {
        "pixel_size": 2.0,
        "pixel_unit": "um",
        "min_distance_mm": 0.15,
        "coordinate_system": StageCoordinateSystem.X_RIGHT_Y_DOWN,
    }
    params.update(kwargs)
    return tracking_move(OBJECT, SENSOR, **params)


def test_tracking_move_production_defaults():
    res = move()
    assert res.move_px == pytest.approx((-100.0, 50.0))  # centre - object
    # stage invert OFF (AET, XY moves the cameras): move = -(centre - object) in mm
    assert res.move_mm == pytest.approx((0.2, -0.1))
    assert res.min_move == (True, False)
    assert res.needed


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        (StageCoordinateSystem.X_RIGHT_Y_UP, (0.2, 0.1)),
        (StageCoordinateSystem.X_LEFT_Y_UP, (-0.2, 0.1)),
        (StageCoordinateSystem.X_RIGHT_Y_DOWN, (0.2, -0.1)),
        (StageCoordinateSystem.X_LEFT_Y_DOWN, (-0.2, -0.1)),
        (3, (-0.2, -0.1)),
    ],
)
def test_tracking_move_coordinate_systems(system, expected):
    res = move(coordinate_system=system)
    assert res.move_mm == pytest.approx(expected)
    assert res.move_px == pytest.approx((-100.0, 50.0))  # the pixel move ignores the stage axes


def test_tracking_move_debug_stage_invert_globals():
    assert move(x_stage_invert=True).move_mm == pytest.approx((-0.2, -0.1))
    assert move(y_stage_invert=True).move_mm == pytest.approx((0.2, 0.1))


def test_tracking_move_flips():
    h = move(flip_horizontal=True)  # top <-> bottom: Y components negated
    assert h.move_mm == pytest.approx((0.2, 0.1))
    assert h.move_px == pytest.approx((-100.0, -50.0))
    v = move(flip_vertical=True)
    assert v.move_mm == pytest.approx((-0.2, -0.1))
    assert v.move_px == pytest.approx((100.0, 50.0))


def test_tracking_move_units_and_min_distance():
    mm = move(pixel_size=0.002, pixel_unit=PixelUnit.MILLIMETER)
    assert mm.move_mm == pytest.approx(move().move_mm)
    exact = move(min_distance_mm=0.1)
    assert exact.min_move == (True, True)  # |0.1| >= 0.1, limit included
    centred = tracking_move((1224.0, 1024.0), SENSOR, pixel_size=2.0, pixel_unit="µm", min_distance_mm=0.0,
                            coordinate_system=0)
    assert centred.move_mm == (0.0, 0.0)
    assert centred.min_move == (True, True)


def test_tracking_move_invalid_calibration_and_system():
    with pytest.raises(CalibrationError) as exc:
        move(pixel_unit="pixels")
    assert exc.value.code == INVALID_CALIBRATION
    with pytest.raises(ValueError):
        move(coordinate_system=7)
