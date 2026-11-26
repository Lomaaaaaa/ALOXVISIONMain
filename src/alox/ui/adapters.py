"""What the UI needs from the core that the core does not offer as such (no Qt here).

- :class:`ImageProvider`: the image manager as the display reads it. :class:`alox.core.images.ImageStore` satisfies it
  (``current()``, ``merged()``); it has no change notification, so :func:`image_key` gives the display a cheap key
  that changes with the image, its overlays and their switches (the display polls it at its own rate and renders
  only on a change).
- :func:`build_params`: the parameters of a local start (LabVIEW's ``alox_function_<x>_bundle_profile_parameters``
  VIs): the function's params class built ``from_profile(profile)`` when it has one, else its defaults, then the
  button's overrides for the fields the class has.
- :func:`wafer_to_stage` and :func:`stage_to_wafer`: ``alox_manager_xy_coordinate_system`` "Convert" both ways (the
  X/Y controls of the main window are wafer coordinates; the XY device works in stage coordinates).
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import logging
import sys
from collections.abc import Callable, Mapping
from typing import Any, Protocol, runtime_checkable

import numpy as np

__all__ = ["ImageProvider", "build_params", "image_key", "params_class", "stage_to_wafer", "wafer_to_stage"]

log = logging.getLogger(__name__)


# ================================================================================================== images
@runtime_checkable
class ImageProvider(Protocol):
    """The display's source: :class:`alox.core.images.ImageStore` or any object with these two methods."""

    def current(self) -> tuple[str, np.ndarray, Mapping[str, Any]] | None:
        """``(camera, pixels, metadata)`` of the image on display, None before the first image."""

    def merged(self) -> Any:
        """The current image with its enabled overlays drawn (a Pillow image), None without image."""


def image_key(provider: ImageProvider | None) -> tuple[Any, ...] | None:
    """A value that changes when the displayed image changes: the pixels object, plus (when the provider exposes
    them) the overlay shapes and switches of the known overlay names. None without image."""
    if provider is None:
        return None
    current = provider.current()
    if current is None:
        return None
    camera, pixels, _meta = current
    key: list[Any] = [camera, id(pixels), pixels.shape]
    shapes = getattr(provider, "shapes", None)
    overlay = getattr(provider, "overlay", None)
    if callable(shapes):
        for name in _overlay_names():
            key.append(shapes(name))
            if callable(overlay):
                try:
                    key.append(bool(overlay(name)))
                except Exception:                                   # noqa: BLE001 - a switch read must not break the display
                    key.append(None)
    return tuple(key)


def _overlay_names() -> tuple[str, ...]:
    try:
        from alox.core.images import OVERLAY_PROPERTIES
    except ImportError:                                             # Pillow missing: no overlay names
        return ()
    return tuple(OVERLAY_PROPERTIES)


# ================================================================================================== parameters
def params_class(function: Any) -> type | None:
    """The parameters dataclass of a measurement function: its ``Params`` attribute, else ``<ClassName>Params`` in
    its module (``Autofocus`` → ``AutofocusParams``, ``DetectCircle`` → ``DetectCircleParams``); None if neither."""
    found = getattr(type(function), "Params", None)
    if inspect.isclass(found) and dataclasses.is_dataclass(found):
        return found
    name = type(function).__module__
    module = sys.modules.get(name)
    if module is None:
        try:
            module = importlib.import_module(name)
        except ImportError:
            return None
    found = getattr(module, f"{type(function).__name__}Params", None)
    if inspect.isclass(found) and dataclasses.is_dataclass(found):
        return found
    return None


def build_params(function: Any, profile: Any, overrides: Mapping[str, Any] | None = None, *,
                 warn: Callable[[str], None] = log.warning) -> Any:
    """The parameters of a local start (module docstring). Overrides naming a field the class does not have are
    ignored (with a warning). Returns None when the function has no parameters class (it then reads its profile
    itself). Errors of the params class (invalid profile values) propagate: the start is refused with them."""
    cls = params_class(function)
    if cls is None:
        if overrides:
            warn(f"{type(function).__name__}: no parameters class, overrides {sorted(overrides)} ignored")
        return None
    from_profile = getattr(cls, "from_profile", None)
    params = from_profile(profile) if callable(from_profile) and profile is not None else cls()
    if overrides:
        names = {f.name for f in dataclasses.fields(cls)}
        known = {key: value for key, value in overrides.items() if key in names}
        unknown = sorted(set(overrides) - names)
        if unknown:
            warn(f"{cls.__name__}: no field {', '.join(unknown)}; override ignored")
        if known:
            params = dataclasses.replace(params, **known)
    return params


# ================================================================================================== coordinates
def stage_to_wafer(x: float, y: float, origin: tuple[float, float], system: int) -> tuple[float, float]:
    """Stages → Wafer (map Part 2 §2.9): systems 0..3 = #1..#4; X indirect for #2 and #4, Y indirect for #3 and #4
    (indirect ``w = O − p``, direct ``w = p − O``). Same formula as :func:`alox.functions.get_image.stage_to_wafer`."""
    ox, oy = origin
    wx = ox - x if system in (1, 3) else x - ox
    wy = oy - y if system in (2, 3) else y - oy
    return wx, wy


def wafer_to_stage(x: float, y: float, origin: tuple[float, float], system: int) -> tuple[float, float]:
    """Wafer → Stages, the inverse of :func:`stage_to_wafer` (indirect ``p = O − w``, direct ``p = w + O``)."""
    ox, oy = origin
    sx = ox - x if system in (1, 3) else x + ox
    sy = oy - y if system in (2, 3) else y + oy
    return sx, sy
