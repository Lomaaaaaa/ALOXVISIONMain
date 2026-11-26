"""The 3D-map hooks (``alox.thermal.plot``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from alox.thermal.plot import (
    launch_plot_script,
    matplotlib_available,
    plot_command,
    plot_map,
    scatter_data,
)
from alox.thermal.table import PointResult, measurement_points


def test_the_csv_path_is_quoted() -> None:
    prefix = 'py "D:/ALOX/software/Device Software/Python/readdata_arg.py" --path '
    assert plot_command(prefix, Path("C:/data/Thermal measurement.csv")) == (
        f'{prefix}"{Path("C:/data/Thermal measurement.csv")}"')
    assert plot_command("plot", "a.csv") == 'plot "a.csv"'


def test_launch_without_waiting() -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []

    def popen(args: Any, **kwargs: Any) -> str:
        calls.append((args, kwargs))
        return "process"

    assert launch_plot_script("", "a.csv", popen=popen) is None and calls == []
    assert launch_plot_script("py s.py --path ", "x y.csv", popen=popen, platform="win32") == "process"
    assert calls[-1][0] == 'py s.py --path "x y.csv"'
    launch_plot_script("python3 s.py --path ", "x y.csv", popen=popen, platform="linux")
    assert calls[-1][0] == ["python3", "s.py", "--path", "x y.csv"]


def test_scatter_data_and_plot(tmp_path: Path) -> None:
    points = measurement_points((0.0, 20.0), (1, 2), 0)
    results = [PointResult(p, 300.0 + i, 0.0, ()) for i, p in enumerate(points)]
    xs, ys, temps = scatter_data(results)
    assert xs == pytest.approx([0.0, 10.0, -10.0]) and ys == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)
    assert temps == [300.0, 301.0, 302.0]
    if not matplotlib_available():
        with pytest.raises(ImportError):
            plot_map(results, tmp_path / "map.png")
        return
    path = plot_map(results, tmp_path / "map.png")                          # pragma: no cover - optional dependency
    assert path.stat().st_size > 0                                          # pragma: no cover
