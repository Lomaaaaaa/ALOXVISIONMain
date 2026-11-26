"""The thermal map measurement (LabVIEW ``Pyro/Bandit``: ``alox_function_ll_thermal_measurement_state_machine`` and its
9 helpers; ``docs/vision-core/map.md`` Part 6 §0, §2.1).

- :mod:`alox.thermal.controller`: :class:`~alox.thermal.controller.ThermalController`, the tool's logic for a panel
  (commands, events, the measurement thread; the "Reproduced" / "Fixed" lists);
- :mod:`alox.thermal.table`: measurement points, result table, statistics, CSV;
- :mod:`alox.thermal.settings`: the ``alox.ini`` keys, read at opening and written back at closing;
- :mod:`alox.thermal.optris` (+ :mod:`~alox.thermal.optris_sim`): the Optris pyrometer (serial binary protocol) as a
  device, and its emulator;
- :mod:`alox.thermal.bandit`, :mod:`alox.thermal.modbus` (+ :mod:`~alox.thermal.modbus_sim`): the kSA BandiT reading
  over Modbus TCP as a device, the minimal Modbus TCP client and a fake server;
- :mod:`alox.thermal.plot`: the external 3D-map script hook and an optional matplotlib plot.
"""

from .bandit import (
    BanditProbe,
    BanditSettings,
    load_bandit_settings,
    voltage_to_temperature,
)
from .controller import (
    ChuckFrame,
    MeasurementSummary,
    Positions,
    Step,
    ThermalBusy,
    ThermalController,
    ThermalError,
    ThermalState,
    Tool,
)
from .errors import ProbeError
from .optris import (
    OptrisCt,
    OptrisPyrometer,
    PyroSettings,
    build_pyrometer_backend,
    load_pyro_settings,
)
from .settings import (
    MeasureOptions,
    ThermalConfig,
    ThermalSettings,
    load_thermal_config,
    write_back,
)
from .table import (
    MeasurePoint,
    PointResult,
    ResultTable,
    default_csv_name,
    measurement_points,
)

__all__ = [
    "BanditProbe",
    "BanditSettings",
    "ChuckFrame",
    "MeasureOptions",
    "MeasurePoint",
    "MeasurementSummary",
    "OptrisCt",
    "OptrisPyrometer",
    "PointResult",
    "Positions",
    "ProbeError",
    "PyroSettings",
    "ResultTable",
    "Step",
    "ThermalBusy",
    "ThermalConfig",
    "ThermalController",
    "ThermalError",
    "ThermalSettings",
    "ThermalState",
    "Tool",
    "build_pyrometer_backend",
    "default_csv_name",
    "load_bandit_settings",
    "load_pyro_settings",
    "load_thermal_config",
    "measurement_points",
    "voltage_to_temperature",
    "write_back",
]
