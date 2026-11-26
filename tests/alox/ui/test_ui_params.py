"""Parameters of the window's local starts (``alox.ui.adapters.build_params``), Qt-free."""

from __future__ import annotations

from alox.functions.focus import Autofocus, CameraType, Refocus
from alox.ui.adapters import build_params


def test_autofocus_and_refocus_starts_read_the_recipe_and_the_settings():
    """They used the invalid typedef defaults (Z range 0..0, 0 steps) because their classes had no from_profile."""
    recipe = {"autofocus.method": 1, "refocus.nbsteps": 3, "refocus.step": 0.02}
    settings = {"hardware.z.zminautofocus": 30.0, "hardware.z.zmaxautofocus": 31.0}
    af = build_params(Autofocus(object(), object()), recipe, {"camera_type": CameraType.LOW_MAG}, settings=settings)
    assert (af.z_min, af.z_max, af.camera_type) == (30.0, 31.0, CameraType.LOW_MAG)
    rf = build_params(Refocus(object(), object()), recipe, {"camera_type": CameraType.HIGH_MAG})
    assert (rf.steps, rf.step_mm) == (3, 0.02)
