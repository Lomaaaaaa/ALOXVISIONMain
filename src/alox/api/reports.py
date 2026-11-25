"""The websocket event payloads, with LabVIEW's exact key names (``docs/vision-core/map.md`` Part 5, Appendix B).

The core publishes ``Event("ws.<event>", source, data)`` built with these helpers; the websocket server sends
``{"event": "<event>", **data}``. Numbers are strings formatted as LabVIEW's ``%f`` (6 decimals, ``.``), as the
supervisor receives them today.

One deliberate difference (owner's decision, map §3.1): the bowing payload uses ``bowinga``/``bowingb`` and
``parallelisma``/``parallelismb`` instead of LabVIEW's duplicated ``bowinga``/``bowinga`` and
``parallelismb``/``parallelismb`` (a JSON parser keeps only the last of two equal keys).
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any

from alox.core.functions import JobInfo
from alox.events import Event

__all__ = ["bowing_event", "error_event", "function_event", "hardware_event", "hardware_reset_event",
           "heartbeat_event", "image_event", "measure_status_event", "mesa_event", "status_event", "tilt_event"]

SOURCE = "core"


def _f(value: float) -> str:
    return f"{float(value):f}"


def _event(name: str, data: dict[str, Any]) -> Event:
    return Event(f"ws.{name}", SOURCE, data)


def _job_spaces(job: JobInfo | None) -> dict[str, str]:
    j = job or JobInfo()
    return {"Control Job ID": j.control_job_id, "Process Job ID": j.process_job_id, "Lot ID": j.lot_id,
            "Wafer ID": j.wafer_id, "Zone ID": j.zone_id}


def _job_underscores(job: JobInfo | None) -> dict[str, str]:
    j = job or JobInfo()
    return {"Control_Job_ID": j.control_job_id, "Process_Job_ID": j.process_job_id, "Wafer_ID": j.wafer_id,
            "Lot_ID": j.lot_id, "Zone_ID": j.zone_id}


def heartbeat_event(value: int) -> Event:
    return _event("heartbeat", {"timestamp": str(int(value))})


def status_event(code: int) -> Event:
    """System status; the hardware error codes 1001..1999 are sent as ``"500"`` (``alox_api_ws_send_status_event d``;
    the key ``State`` is the label of the status cluster, **inferred** for this event)."""
    sent = 500 if 1001 <= int(code) <= 1999 else int(code)
    return _event("status", {"State": str(sent)})


def measure_status_event(measure: str, code: int) -> Event:
    """``mesa_status`` / ``tilt_status`` / ``bowing_status``."""
    return _event(f"{measure}_status", {"State": str(int(code))})


def function_event(kind: str, function: str, job: JobInfo | None) -> Event:
    """``started``, ``finished`` or ``failed``: ``Function`` (e.g. ``"measure_mesa"``) and the SEMI job."""
    if kind not in ("started", "finished", "failed"):
        raise ValueError(f"not a function event: {kind!r}")
    return _event(kind, {"Function": function, "Job": _job_spaces(job)})


def error_event(source: str, code: int, description: str) -> Event:
    return _event("error", {"Source": source, "Code": int(code), "Description": description})


def hardware_event(kind: str, sources: Sequence[str], count: int) -> Event:
    """``hardware_warning`` or ``hardware_error`` (when ``count`` reaches the module's ``maxfails``)."""
    if kind not in ("hardware_warning", "hardware_error"):
        raise ValueError(f"not a hardware event: {kind!r}")
    return _event(kind, {"source": list(sources), "count": str(int(count))})


def hardware_reset_event(module: str) -> Event:
    return _event("hardware_reset", {"source": module})


def image_event(jpeg: bytes, width: int, height: int, quality: int) -> Event:
    return _event("image", {"width": int(width), "height": int(height), "quality": int(quality),
                            "jpeg": base64.b64encode(jpeg).decode("ascii")})


def tilt_event(job: JobInfo | None, angle_deg: float) -> Event:
    return _event("measure_tilt", {"Job": _job_underscores(job), "Tilt": {"Angle": _f(angle_deg), "Unit": "degrees"}})


def bowing_event(job: JobInfo | None, *, zwafer: Sequence[float], bowing_a: float, bowing_b: float,
                 zchuck: Sequence[float], parallelism_a: float, parallelism_b: float) -> Event:
    """``zwafer`` = Z of wafer points 1..5 (mm); ``zchuck`` = Z of chuck points 1, 3, 4, 5 (mm)."""
    if len(zwafer) != 5 or len(zchuck) != 4:
        raise ValueError("zwafer needs 5 values and zchuck 4 (points 1, 3, 4, 5)")
    bowing = {"zwafer1": _f(zwafer[0]), "zwafer2": _f(zwafer[1]), "zwafer3": _f(zwafer[2]), "zwafer4": _f(zwafer[3]),
              "Zwafer5": _f(zwafer[4]), "bowinga": _f(bowing_a), "bowingb": _f(bowing_b), "bowingunit": "um",
              "zwafer Unit": "mm"}
    parallelism = {"zchuck1": _f(zchuck[0]), "zchuck3": _f(zchuck[1]), "zchuck4": _f(zchuck[2]),
                   "zchuck5": _f(zchuck[3]), "parallelisma": _f(parallelism_a), "parallelismb": _f(parallelism_b),
                   "parallelismunit": "um", "zchuck unit": "mm"}
    return _event("measure_bowing", {"Job": _job_underscores(job), "Bowing": bowing, "Parallelism": parallelism})


def mesa_event(job: JobInfo | None, *, unix_time: float, external_fitted: float, oxyd: dict[str, float],
               measure_type: str, stddev: float, rate: float, remaining: str, position: Sequence[float],
               wavelength: float) -> Event:
    """``measure_mesa``. ``oxyd`` keys: ``Fitted``, ``Median``, ``Average``, ``Surface``, ``Inscribed``,
    ``Circumscribed``, ``Circularity`` (µm, circularity unitless); ``remaining`` is ``"HH:MM:SS"``."""
    keys = ("Fitted", "Median", "Average", "Surface", "Inscribed", "Circumscribed", "Circularity")
    oxyd_out: dict[str, str] = {k: _f(oxyd[k]) for k in keys}
    oxyd_out.update({"Type_Measure_Rate": measure_type, "STDDEV FET/#": _f(stddev), "Unit": "um"})
    x, y, z = position
    return _event("measure_mesa", {
        "Job": _job_underscores(job),
        "Timestamp": {"Unix": str(int(unix_time))},
        "External_Diameter": {"Fitted": _f(external_fitted), "Unit": "um"},
        "Oxyd_Diameter": oxyd_out,
        "Oxyd_Rate": {"Rate": _f(rate), "Unit": "um/min", "Remaining_time": remaining},
        "Mesa_Positions": {"x": _f(x), "y": _f(y), "z": _f(z), "unit": "mm"},
        "Wavelength": {"wl": _f(wavelength), "unit": "nm"},
    })
