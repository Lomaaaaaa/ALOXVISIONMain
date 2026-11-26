"""The 3D map of a thermal measurement: the external script hook of LabVIEW, and an optional matplotlib plot.

LabVIEW (``tool_plot_3D_map d``): ``System Exec`` of ``<[hardware.bandit] ScriptPythonPath><CSV path>`` without
waiting (the shipped value is ``py "D:/ALOX/software/Device Software/Python/readdata_arg.py" --path ``; the script is not
in the sources). It ran after a measurement when the table had more than 3 rows (``SM d162``), and after EXPORT even
when the file dialog was cancelled (``SM d100``). The panel itself showed a 3D scatter of ``(X, Y, temperature)``
(NI ``Plot Helper (Vector)``, ``SM d148``).

Fixed here: the CSV path is quoted (LabVIEW appended it unquoted, which broke on a path with spaces) and the script is
started only after a file was written. :func:`plot_map` draws the same scatter with matplotlib when it is installed
(optional dependency; :func:`matplotlib_available`).
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .table import PointResult

__all__ = ["launch_plot_script", "matplotlib_available", "plot_command", "plot_map", "scatter_data"]

log = logging.getLogger(__name__)


def plot_command(script_command: str, csv_path: str | os.PathLike[str]) -> str:
    """``<script command><"csv path">``: LabVIEW's concatenation, with the path quoted."""
    prefix = script_command if not script_command or script_command.endswith(" ") else script_command + " "
    return f'{prefix}"{os.fspath(csv_path)}"'


def launch_plot_script(script_command: str, csv_path: str | os.PathLike[str], *,
                       popen: Callable[..., Any] = subprocess.Popen, platform: str = sys.platform) -> Any | None:
    """Start the external 3D-map script without waiting (None when no script is configured). Windows gets the command
    line as one string (as ``System Exec``); other systems get it split with :func:`shlex.split`. Raises
    :class:`OSError` when it cannot start."""
    if not script_command.strip():
        return None
    command = plot_command(script_command, csv_path)
    log.info("3D map script: %s", command)
    args: Any = command if platform == "win32" else shlex.split(command)
    return popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def scatter_data(results: Sequence[PointResult]) -> tuple[list[float], list[float], list[float]]:
    """``(X, Y, temperature)`` of the measured points (``Xmeas``, ``Ymeas``, ``Zmeas`` of ``SM d148``)."""
    return ([r.point.x for r in results], [r.point.y for r in results], [r.temperature for r in results])


def matplotlib_available() -> bool:
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        return False
    return True


def plot_map(results: Sequence[PointResult], path: str | os.PathLike[str], *, title: str = "Thermal map") -> Path:
    """Save a 3D scatter of the points coloured by temperature to ``path`` (PNG, SVG… by extension). Needs matplotlib
    (:class:`ImportError` otherwise); uses the non-interactive Agg backend, so it is safe from any thread."""
    from matplotlib.figure import Figure

    xs, ys, temps = scatter_data(results)
    figure = Figure(figsize=(7.0, 5.5))
    axes = figure.add_subplot(projection="3d")
    points = axes.scatter(xs, ys, temps, c=temps, cmap="viridis")
    axes.set_xlabel("X (mm)")
    axes.set_ylabel("Y (mm)")
    axes.set_zlabel("Temp. (°C)")
    axes.set_title(title)
    figure.colorbar(points, ax=axes, shrink=0.7, label="°C")
    target = Path(path)
    figure.savefig(target)
    return target
