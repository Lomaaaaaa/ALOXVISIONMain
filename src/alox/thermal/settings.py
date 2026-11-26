"""The ``alox.ini`` keys of the thermal map: read at opening (``SM d10``), written back at closing (``SM d171``).

``SM`` = ``alox_function_ll_thermal_measurement_state_machine``. Read (defaults as wired in ``SM d10``):

=====================================  ================  =================================================
Key                                    Default           Use
=====================================  ================  =================================================
``hardware.bandit.diam1`` … ``diam9``  10 (mm)           circle diameters
``hardware.bandit.nbpoints1`` … ``9``  10                points per circle
``hardware.bandit.numberMeasure``      10                samples per point (``NB Meas Pts``)
``hardware.bandit.offsetAngle``        10 (deg)          angle of the first point of every circle (integer)
``hardware.bandit.wait``               2000 (ms)         settle time after each move (``SM d154``)
``hardware.bandit.enableBandit``       TRUE              the tool selector is shown (else pyrometer only)
``hardware.bandit.ScriptPythonPath``   ""                command prefix of the external 3D-map script
``hardware.xy.chuckxorg/chuckyorg``    0 / 0             the thermal origin (stage mm), see below
``hardware.xy.stagescoordsys``         3 (#4)            stage coordinate system (``xy-stage.md`` §9.1)
=====================================  ================  =================================================

(the probe keys are read by :func:`alox.thermal.optris.load_pyro_settings` and
:func:`alox.thermal.bandit.load_bandit_settings`). Written back when the tool closes (``SM d171``): ``diam1..9``,
``nbpoints1..9``, ``numberMeasure``, ``offsetAngle``, ``ScriptPythonPath`` with ``Set Property`` (memory; the
application saves ``alox.ini`` at exit, ``alox.core.core`` "EXIT: Saving Calibration").

The thermal origin is the chuck origin of ``alox_manager_xy_coordinate_system``: ``SET DEVICE ORIGIN`` stores the
current stage position as the chuck origin and in ``hardware.xy.chuckxorg/chuckyorg`` (``SM d90``, ``d91``); every
measurement point is a chuck point converted Chuck → XY Stages.

Fixed: LabVIEW wrote ``diam9`` and ``nbpoints9`` as 0 at closing (constants, ``SM d171``) although the 9th circle
read from the INI was measured; here the values in use are written. LabVIEW never loaded ``chuckxorg/chuckyorg``
back into the coordinate system (only ``SET DEVICE ORIGIN`` set it), so after a restart the thermal origin was (0, 0)
until the operator set it again; here it is read from the INI. LabVIEW also wrote ``NB Meas Pts`` into
``offsetAngle`` every ~100 ms (``SM d47``); nothing is written periodically here.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from .bandit import BanditSettings, load_bandit_settings
from .optris import PyroSettings, load_pyro_settings

__all__ = [
    "MAX_CIRCLES",
    "MeasureOptions",
    "ThermalConfig",
    "ThermalSettings",
    "load_thermal_config",
    "load_thermal_settings",
    "write_back",
    "write_origin",
]

log = logging.getLogger(__name__)

#: ``diam1`` … ``diam9`` (the panel shows 8 rows; the arrays hold 9, ``SM d10``).
MAX_CIRCLES = 9


@dataclass(frozen=True)
class ThermalSettings:
    """What the operator edits on the panel, plus the INI values the tool uses."""

    diameters: tuple[float, ...] = (10.0,) * MAX_CIRCLES
    nb_points: tuple[int, ...] = (10,) * MAX_CIRCLES
    samples: int = 10
    offset_angle: int = 10
    wait_ms: int = 2000
    script_command: str = ""
    bandit_enabled: bool = True
    origin: tuple[float, float] = (0.0, 0.0)
    coordinate_system: int = 3

    def __post_init__(self) -> None:
        if len(self.diameters) > MAX_CIRCLES or len(self.nb_points) > MAX_CIRCLES:
            raise ValueError(f"at most {MAX_CIRCLES} circles")
        if any(not math.isfinite(d) or d < 0 for d in self.diameters):
            raise ValueError(f"diameters must be finite and ≥ 0: {self.diameters}")
        if any(n < 0 for n in self.nb_points):
            raise ValueError(f"point counts must be ≥ 0: {self.nb_points}")
        if self.samples < 1:
            raise ValueError(f"samples per point must be ≥ 1, not {self.samples}")
        if self.wait_ms < 0:
            raise ValueError(f"wait must be ≥ 0 ms, not {self.wait_ms}")
        if self.coordinate_system not in (0, 1, 2, 3):
            raise ValueError(f"stage coordinate system must be 0..3, not {self.coordinate_system}")

    @property
    def origin_defined(self) -> bool:
        """FALSE for a 0 coordinate (the "undefined" meaning of the XY origins, ``xy-stage.md`` X8)."""
        return self.origin[0] != 0.0 and self.origin[1] != 0.0

    def with_circles(self, diameters: Sequence[float], nb_points: Sequence[int]) -> ThermalSettings:
        return replace(self, diameters=tuple(float(d) for d in diameters), nb_points=tuple(int(n) for n in nb_points))


@dataclass(frozen=True)
class MeasureOptions:
    """Constants of the measurement state machine (``SM d151``–``d156``), as parameters."""

    reached_tolerance_mm: float = 0.01        # WAIT MOVE: |ΔX| and |ΔY| ≤ 0.01 mm (In Range, limits included)
    move_reissue_s: float = 30.0              # WAIT MOVE: MOVING again after 30 000 ms
    move_attempts: int = 3                    # new: moves issued before the point fails (LabVIEW: forever)
    poll_s: float = 0.05                      # new: between two Get Position XY (LabVIEW: as fast as they return)
    sample_period_s: float = 0.05             # MEASURING: 50 ms per sample, in parallel with the read
    live_period_s: float = 0.5                # idle refresh (LabVIEW's event timeout, ≈ 0.1 s)


@dataclass(frozen=True)
class ThermalConfig:
    settings: ThermalSettings = field(default_factory=ThermalSettings)
    pyro: PyroSettings = field(default_factory=PyroSettings)
    bandit: BanditSettings = field(default_factory=BanditSettings)


def _getter(ini: Any, warn: Callable[[str], None]) -> Callable[[str, Any], Any]:
    def get(name: str, default: Any) -> Any:
        try:
            value = ini.get(name, default)
        except (TypeError, ValueError) as exc:
            warn(f"alox.ini: {exc}; default {default!r} used")
            return default
        if isinstance(default, bool):
            return value if isinstance(value, bool) else default
        if isinstance(default, int | float) and (isinstance(value, bool) or not isinstance(value, int | float)
                                                 or not math.isfinite(value)):
            warn(f"alox.ini: {name} = {value!r} is not a number; {default!r} used")
            return default
        return value
    return get


def load_thermal_settings(ini: Any, *, warn: Callable[[str], None] = log.warning) -> ThermalSettings:
    """:class:`ThermalSettings` from ``alox.ini`` (any object with ``get(name, default)``); invalid values take
    their default with a warning."""
    get = _getter(ini, warn)
    diameters = []
    counts = []
    for i in range(1, MAX_CIRCLES + 1):
        d = float(get(f"hardware.bandit.diam{i}", 10.0))
        n = int(get(f"hardware.bandit.nbpoints{i}", 10))
        if d < 0:
            warn(f"alox.ini: hardware.bandit.diam{i} = {d} is negative; 0 used")
            d = 0.0
        if n < 0:
            warn(f"alox.ini: hardware.bandit.nbpoints{i} = {n} is negative; 0 used")
            n = 0
        diameters.append(d)
        counts.append(n)
    samples = int(get("hardware.bandit.numberMeasure", 10))
    if samples < 1:
        warn(f"alox.ini: hardware.bandit.numberMeasure = {samples}; 1 used")
        samples = 1
    wait = int(get("hardware.bandit.wait", 2000))
    if wait < 0:
        warn(f"alox.ini: hardware.bandit.wait = {wait} ms; 0 used")
        wait = 0
    system = int(get("hardware.xy.stagescoordsys", 3))
    if system not in (0, 1, 2, 3):
        warn(f"alox.ini: hardware.xy.stagescoordsys = {system} is not 0..3; 3 used")
        system = 3
    script = get("hardware.bandit.ScriptPythonPath", "")
    return ThermalSettings(
        diameters=tuple(diameters), nb_points=tuple(counts), samples=samples,
        offset_angle=int(round(float(get("hardware.bandit.offsetAngle", 10)))), wait_ms=wait,
        script_command=str(script) if script is not None else "",
        bandit_enabled=bool(get("hardware.bandit.enableBandit", True)),
        origin=(float(get("hardware.xy.chuckxorg", 0.0)), float(get("hardware.xy.chuckyorg", 0.0))),
        coordinate_system=system)


def load_thermal_config(ini: Any, *, warn: Callable[[str], None] = log.warning, pyro_port: str | None = None
                        ) -> ThermalConfig:
    """Everything the tool reads from ``alox.ini``."""
    return ThermalConfig(load_thermal_settings(ini, warn=warn), load_pyro_settings(ini, warn=warn, port=pyro_port),
                         load_bandit_settings(ini, warn=warn))


def write_back(store: Any, settings: ThermalSettings, *, warn: Callable[[str], None] = log.warning) -> list[str]:
    """``SM d171``: set ``diam1..9``, ``nbpoints1..9``, ``numberMeasure``, ``offsetAngle``, ``ScriptPythonPath`` on
    ``store`` (``set(name, value)``: :class:`alox.core.properties.Properties`). A value the store refuses (type or
    range of the existing key) is skipped with a warning. Returns the names written."""
    values: list[tuple[str, Any]] = []
    for i in range(MAX_CIRCLES):
        diameter = settings.diameters[i] if i < len(settings.diameters) else 0.0
        count = settings.nb_points[i] if i < len(settings.nb_points) else 0
        values += [(f"hardware.bandit.diam{i + 1}", float(diameter)), (f"hardware.bandit.nbpoints{i + 1}", int(count))]
    values += [("hardware.bandit.numberMeasure", int(settings.samples)),
               ("hardware.bandit.offsetAngle", int(settings.offset_angle)),
               ("hardware.bandit.ScriptPythonPath", settings.script_command)]
    return _set_all(store, values, warn)


def write_origin(store: Any, origin: tuple[float, float], *, warn: Callable[[str], None] = log.warning) -> list[str]:
    """``SM d90``: ``hardware.xy.chuckxorg/chuckyorg`` (memory, as ``Set Property``)."""
    return _set_all(store, [("hardware.xy.chuckxorg", float(origin[0])), ("hardware.xy.chuckyorg", float(origin[1]))],
                    warn)


def _set_all(store: Any, values: list[tuple[str, Any]], warn: Callable[[str], None]) -> list[str]:
    written = []
    for name, value in values:
        try:
            store.set(name, value)
        except (TypeError, ValueError) as exc:
            warn(f"alox.ini: {name} not written ({exc})")
            continue
        written.append(name)
    return written

