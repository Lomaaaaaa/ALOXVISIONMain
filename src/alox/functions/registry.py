"""Register the measurement functions on the core's runner (LabVIEW init cycle frame 30, "Init Low-Level + High-Level
functions": every ``alox_function_<x>`` "Init").

:func:`build_services` gives the functions the core's data and the application's devices; :func:`register_functions`
builds every function whose devices are present and registers it. A function that cannot be built (its devices are
missing, or its module is not written yet) is skipped and reported, as LabVIEW leaves a function "Off" when its
hardware is down: a request for it then fails with 9002 "Invalid function" instead of stopping the start-up.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Mapping
from typing import Any

from alox.core.functions import FunctionName, FunctionRunner, MeasurementFunction
from alox.functions.services import Services

__all__ = ["FACTORIES", "build_services", "register_functions"]

log = logging.getLogger(__name__)


def build_services(core: Any, images: Any = None) -> Services:
    """The :class:`Services` of a :class:`alox.core.core.Core` (its devices, active profile, settings, exposure table,
    folders, event bus, log and the status IDs of the high-level measurements); ``images`` defaults to the core's
    image manager."""
    kwargs: dict[str, Any] = {"devices": core.devices, "profile": core.profile_manager, "settings": core.properties,
                              "exposures": core.exposures, "folders": core.folders, "publish": core.events.publish,
                              "log": core.log,
                              "statuses": {m.name.lower(): holder for m, holder in getattr(core, "measures", {}).items()}}
    if hasattr(core, "tilt"):
        kwargs["tilt"] = core.tilt
    sink = images if images is not None else getattr(core, "images", None)
    if sink is not None and hasattr(sink, "put"):
        kwargs["images"] = sink
    return Services(**kwargs)


def _focus(kind: str) -> Callable[[Services], MeasurementFunction]:
    def build(services: Services) -> MeasurementFunction:
        from alox.functions.focus import Autofocus, CameraType, Refocus

        found = {CameraType.HIGH_MAG: services.devices.get("highmag"), CameraType.LOW_MAG: services.devices.get("lowmag")}
        cameras = {key: camera for key, camera in found.items() if camera is not None}
        if not cameras:
            raise LookupError("no camera")
        if kind == "autofocus":
            from alox.functions.af_latency import LATENCY_SETTING
            from alox.functions.focus import FocusOptions

            latency = float(services.settings.get(LATENCY_SETTING, 0.0) or 0.0)       # owner B8, measured on site
            return Autofocus(services.device("z"), cameras, options=FocusOptions(fast_latency_s=max(0.0, latency)))
        return Refocus(services.device("z"), cameras)
    return build


def _by_name(module: str, cls: str) -> Callable[[Services], MeasurementFunction]:
    """A function class built with ``cls(services)`` (the convention of the functions written after the focus ones)."""
    def build(services: Services) -> MeasurementFunction:
        found = getattr(importlib.import_module(module), cls)
        return found(services)
    return build


def _search_wavelength(services: Services) -> MeasurementFunction:
    from alox.functions.mesa import locate_mesa
    from alox.functions.search_wavelength import SearchWavelength

    return SearchWavelength(services, locate=locate_mesa)       # the mesa is re-detected at each wavelength


def _mesa(services: Services) -> MeasurementFunction:
    from alox.functions.mesa import MesaMeasurement

    return MesaMeasurement(services, tilt=services.tilt)        # Tilt Correction uses the last measured tilt


#: How each function is built, in LabVIEW's init order where known.
FACTORIES: dict[FunctionName, Callable[[Services], MeasurementFunction]] = {
    FunctionName.GET_IMAGE: _by_name("alox.functions.get_image", "GetImage"),
    FunctionName.AUTOFOCUS_LARGE: _focus("autofocus"),
    FunctionName.REFOCUS: _focus("refocus"),
    FunctionName.REFOCUS_OXYD: _by_name("alox.functions.refocus_oxyd", "RefocusOxyd"),
    FunctionName.DETECT_CIRCLE: _by_name("alox.functions.detect_circle", "DetectCircle"),
    FunctionName.MEASURE_OXYD: _by_name("alox.functions.measure_oxyd", "MeasureOxyd"),
    FunctionName.SEARCH_OBJECT: _by_name("alox.functions.search_object", "SearchObject"),
    FunctionName.TRACKING: _by_name("alox.functions.tracking", "Tracking"),
    FunctionName.AUTO_EXPOSURE: _by_name("alox.functions.auto_exposure", "AutoExposure"),
    FunctionName.SEARCH_WAVELENGTH: _search_wavelength,
    FunctionName.MESA_MEASURE: _mesa,
    FunctionName.TILT_MEASURE: _by_name("alox.functions.tilt", "TiltMeasurement"),
    FunctionName.BOWING_MEASURE: _by_name("alox.functions.bowing", "BowingMeasurement"),
}


def register_functions(runner: FunctionRunner, services: Services,
                       factories: Mapping[FunctionName, Callable[[Services], MeasurementFunction]] | None = None,
                       ) -> dict[FunctionName, str]:
    """Build and register every function; returns ``{function: "registered" | reason it was skipped}``."""
    outcome: dict[FunctionName, str] = {}
    for name, factory in (factories or FACTORIES).items():
        try:
            function = factory(services)
        except ModuleNotFoundError as exc:
            outcome[name] = f"not available ({exc.name})"
            log.info("function %s not available yet", name.name)
            continue
        except Exception as exc:                          # noqa: BLE001 - a function that cannot be built stays off
            outcome[name] = f"skipped: {exc}"
            log.warning("function %s not registered: %s", name.name, exc)
            continue
        runner.register(function)
        outcome[name] = "registered"
    return outcome
