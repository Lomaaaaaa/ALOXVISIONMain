"""Points, formats, statistics, table and CSV of the thermal map (``alox.thermal.table``)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from pathlib import Path

import pytest

from alox.thermal.table import (
    COLUMN_HEADERS,
    PointResult,
    ResultTable,
    default_csv_name,
    global_statistics,
    lv_exponential,
    lv_fixed,
    measurement_points,
    ordinal,
    sample_statistics,
)


def test_points_follow_the_positions_vi_and_are_sorted_on_the_angle() -> None:
    points = measurement_points((0.0, 20.0), (1, 4), 0)
    assert [(p.circle, round(p.theta)) for p in points] == [(0, 0), (1, 0), (1, 90), (1, 180), (1, 270)]
    assert [p.order for p in points] == [0, 1, 2, 3, 4]
    assert (points[0].x, points[0].y, points[0].rho) == (0.0, 0.0, 0.0)
    assert points[1].x == pytest.approx(10.0) and points[1].y == pytest.approx(0.0)
    assert points[2].x == pytest.approx(0.0, abs=1e-12) and points[2].y == pytest.approx(10.0)
    assert points[3].x == pytest.approx(-10.0) and points[4].y == pytest.approx(-10.0)
    assert all(p.rho == 10.0 for p in points[1:])


def test_the_sort_mixes_circles_and_keeps_their_order_on_equal_angles() -> None:
    points = measurement_points((20.0, 40.0), (4, 2), 45)
    assert [(p.circle, p.theta) for p in points] == [(0, 45.0), (1, 45.0), (0, 135.0), (0, 225.0), (1, 225.0),
                                                     (0, 315.0)]
    first = points[0]
    assert first.x == pytest.approx(10 * math.cos(math.radians(45))) and first.y == pytest.approx(first.x)


def test_points_of_missing_or_empty_counts_and_of_a_zero_diameter() -> None:
    assert measurement_points((10.0, 20.0, 30.0), (3, 0)) == measurement_points((10.0,), (3,))
    centre = measurement_points((0.0,), (3,), 10)
    assert [(p.x, p.y, p.theta) for p in centre] == [(0.0, 0.0, 10.0), (0.0, 0.0, 130.0), (0.0, 0.0, 250.0)]
    assert measurement_points((), ()) == []


@pytest.mark.parametrize(("value", "text"), [(25.4, "25.40"), (-0.001, "-0.00"), (1.005, "1.00"), (math.nan, "NaN"),
                                             (math.inf, "Inf"), (-math.inf, "-Inf"), (0.0, "0.00")])
def test_lv_fixed(value: float, text: str) -> None:
    assert lv_fixed(value) == text


@pytest.mark.parametrize(("value", "text"), [(0.1234, "1.23E-1"), (0.0, "0.00E+0"), (1234.5, "1.23E+3"),
                                             (12.0, "1.20E+1"), (math.nan, "NaN"), (-0.5, "-5.00E-1")])
def test_lv_exponential_has_no_leading_zeros_in_the_exponent(value: float, text: str) -> None:
    assert lv_exponential(value) == text


def test_ordinals() -> None:
    assert [ordinal(n) for n in (1, 2, 3, 4, 9, 11, 12, 13, 21, 22)] == [
        "1st", "2nd", "3rd", "4th", "9th", "11th", "12th", "13th", "21st", "22nd"]


def test_sample_statistics_leave_failed_samples_out() -> None:
    assert sample_statistics([1.0, 2.0, 3.0]) == (2.0, 1.0, 0)
    mean, std, failed = sample_statistics([1.0, math.nan, 3.0])
    assert (mean, std, failed) == (2.0, pytest.approx(math.sqrt(2.0)), 1)
    assert sample_statistics([5.0]) == (5.0, 0.0, 0)
    mean, std, failed = sample_statistics([math.nan, math.nan])
    assert math.isnan(mean) and math.isnan(std) and failed == 2


def test_global_statistics() -> None:
    g = global_statistics([4.0, 1.0, math.nan, 3.0, 2.0])
    assert (g.count, g.mean, g.median, g.max, g.min, g.delta) == (4, 2.5, 2.5, 4.0, 1.0, 3.0)
    assert g.std == pytest.approx(1.2909944)
    empty = global_statistics([math.nan])
    assert empty.count == 0 and math.isnan(empty.mean)


def _filled_table() -> ResultTable:
    points = measurement_points((0.0, 20.0), (1, 4), 0)
    table = ResultTable(points)
    temps = [300.0, 310.0, 301.0, 290.0, 299.0]
    for point, temp in zip(points, temps, strict=True):
        table.set_result(PointResult(point, temp, 0.5, (temp,) * 3))
    return table


def test_table_headers_cells_and_statistics() -> None:
    table = _filled_table()
    headers = table.row_headers()
    assert headers[:7] == ["Point 1", "Point 2", "Point 3", "Point 4", "Point 5", "Global", "Mean"]
    assert headers[5:12] == ["Global", "Mean", "Median", "Std.Dev", "Max", "Min", "Delta Max-Min"]
    assert headers[12:16] == ["1st circle", "Mean", "Max", "Min"]
    assert headers[-4:] == ["8th circle", "Mean", "Max", "Min"]
    assert len(headers) == 5 + 7 + 8 * 4
    cells = table.cells()
    assert len(cells) == len(headers) and all(len(row) == 6 for row in cells)
    assert cells[1] == ["10.00", "0.00", "10.00", "0.00", "310.00", "5.00E-1"]
    assert cells[2][:2] == ["0.00", "10.00"] and cells[3][0] == "-10.00"
    stats = {headers[i]: cells[i][4] for i in range(5, 12)}
    assert stats == {"Global": "", "Mean": "300.00", "Median": "300.00", "Std.Dev": "7.11", "Max": "310.00",
                     "Min": "290.00", "Delta Max-Min": "20.00"}
    assert [cells[i][4] for i in range(12, 16)] == ["", "300.00", "300.00", "300.00"]
    assert [cells[i][4] for i in range(16, 20)] == ["", "300.00", "310.00", "290.00"]
    assert all(cells[i] == [""] * 6 for i in range(20, len(cells)))           # circles 3..8: no point
    assert all(c == "" for i in range(5, 12) for j, c in enumerate(cells[i]) if j != 4)


def test_table_before_any_result_and_with_a_ninth_circle() -> None:
    points = measurement_points((10.0,) * 9, (1,) * 9, 0)
    table = ResultTable(points)
    assert table.circle_groups == 9 and table.row_headers()[-4] == "9th circle"
    assert all(cell == "" for row in table.cells() for cell in row)
    with pytest.raises(ValueError, match="not a point"):
        table.set_result(PointResult(measurement_points((5.0,), (1,))[0], 1.0, 0.0, ()))


def test_csv_is_tab_separated_with_crlf_in_cp1252(tmp_path: Path) -> None:
    table = _filled_table()
    lines = table.to_text().split("\r\n")
    assert lines[0] == "\t" + "\t".join(COLUMN_HEADERS)
    assert lines[1] == "Point 1\t0.00\t0.00\t0.00\t0.00\t300.00\t5.00E-1"
    assert lines[7] == "Mean\t\t\t\t\t300.00\t"
    assert lines[-1] == "" and len(lines) == 1 + 44 + 1
    path = table.write_csv(tmp_path / "out.csv")
    data = path.read_bytes()
    assert data.startswith(b"\tX\tY\tRho\tTeta\tTemp. (\xb0C)\tStd Dev Temp\r\n")


def test_default_csv_name() -> None:
    assert default_csv_name(datetime(2026, 11, 26, 14, 5, 9, tzinfo=UTC)) == "Thermal_measurement_261126_140509.csv"
    assert default_csv_name().startswith("Thermal_measurement_")
