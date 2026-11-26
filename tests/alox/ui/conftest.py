"""Fixtures of the operator UI tests: a booted core on temporary folders, fake functions and devices; Qt runs
offscreen (``QT_QPA_PLATFORM``), set before any Qt import. The helper classes are given by the ``fakes`` fixture
(the test modules of this folder cannot import each other)."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from alox.core.core import Core, CoreConfig  # noqa: E402 - after the Qt platform choice
from alox.core.folders import Folders  # noqa: E402
from alox.core.functions import FunctionContext, FunctionName, MeasurementFunction  # noqa: E402
from alox.devices.base import DeviceState  # noqa: E402

SYSTEM_LANG = "000100;System initialized\n000200;Starting\n000203;Mesa running\n000500;Idle\n"
ALOX_LANG = ("000039;OFF;   function status 'OFF'\n000040;OFF OK;  function status 'OFF COND OK'\n"
             "000041;RUNNING\n000042;FINISHED\n000043;ERROR\n001651;stop current function\n")
#: Operator: may run Get Image, Autofocus, the measurements and change profiles; not Refocus, nor Autofocus Setup.
DEFAULTS_INI = """[general]
profile_STR = DEFAULT
mode_I32 = 0
core_I32 = 1
camera_I32 = 1
displaytimer_I32 = 200
language_STR = en
[hardware.xy]
waferxorg_DBL = 10,0
waferyorg_DBL = 20,0
stagescoordsys_U32 = 3
[user.rights.operator]
getimage_BOOL = TRUE
getimagesetup_BOOL = TRUE
autofocus_BOOL = TRUE
autofocussetup_BOOL = FALSE
refocus_BOOL = FALSE
detectcircle_BOOL = TRUE
mesa_BOOL = TRUE
mesasetup_BOOL = TRUE
tilt_BOOL = TRUE
bowing_BOOL = TRUE
changeprofile_BOOL = TRUE
editprofile_BOOL = FALSE
editproperties_BOOL = TRUE
exitapp_BOOL = TRUE
[user.rights.engineer]
refocus_BOOL = TRUE
editprofile_BOOL = TRUE
"""


def make_folders(tmp_path: Path) -> Folders:
    exe = tmp_path / "exe"
    (exe / "translations").mkdir(parents=True)
    (exe / "translations" / "system_en.lang").write_text(SYSTEM_LANG, encoding="cp1252")
    (exe / "translations" / "alox_en.lang").write_text(ALOX_LANG, encoding="cp1252")
    (exe / "alox_default.ini").write_text(DEFAULTS_INI, encoding="cp1252")
    root = tmp_path / "alox"
    return Folders(exe=exe, settings=root / "settings", vision=root / "vision", data=root / "data",
                   profiles=root / "profiles", exposures=root / "exposures", jobhistory=root / "jobhistory")


@dataclass(frozen=True)
class ProbeParams:
    """A params class in the style of the functions: ``from_profile`` reads ``probe.value``."""

    camera_type: int = 0
    method: int = 0
    value: float = 0.0

    @classmethod
    def from_profile(cls, profile: Any) -> ProbeParams:
        return cls(value=float(profile.get("probe.value", 1.5)))


class Probe(MeasurementFunction):
    """A function that records its parameters and runs until ``release`` (or a stop)."""

    Params = ProbeParams

    def __init__(self, name: FunctionName, *, block: bool = True) -> None:
        self.name = name
        self.block = block
        self.release = threading.Event()
        self.seen: list[Any] = []
        self.started = threading.Event()

    def run(self, params: Any, ctx: FunctionContext) -> Any:
        self.seen.append(params)
        self.started.set()
        while self.block and not self.release.wait(0.01):
            ctx.check()
        return "done"


class FakeStage:
    """XY or Z stage as the controller uses it (state, joystick, position, absolute moves)."""

    def __init__(self, name: str, position: Any = (0.0, 0.0)) -> None:
        self.name = name
        self.state = DeviceState.READY
        self.joystick = True
        self.pos = position
        self.moves: list[dict[str, Any]] = []

    def joystick_enabled(self, joystick_id: int = 1, *, timeout: float | None = None) -> bool:
        return self.joystick

    def set_joystick(self, enabled: bool, *, timeout: float | None = None, **_kw: Any) -> None:
        self.joystick = bool(enabled)

    def position(self, *, timeout: float | None = None) -> Any:
        return self.pos

    def move_absolute(self, *args: Any, **kwargs: Any) -> Any:
        self.moves.append({"args": args, **kwargs})
        return self.pos

    def status(self) -> Any:
        return None


class FakeMonochromator:
    def __init__(self) -> None:
        self.name = "wl"
        self.state = DeviceState.READY
        self.last_position: int | None = 650
        self.targets: list[int] = []

    def goto(self, nm: int, *, wait: bool = True, timeout: float | None = None) -> int:
        self.targets.append(nm)
        self.last_position = nm
        return nm


def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError("condition not reached")
        time.sleep(0.01)


@pytest.fixture
def core(tmp_path: Path) -> Iterator[Core]:
    c = Core(make_folders(tmp_path), config=CoreConfig(hostname="alox-pc", supervise_hardware=False,
                                                       heartbeat=False))
    c.boot()
    c.start()
    yield c
    c.stop()


@pytest.fixture
def devices(core: Core) -> dict[str, Any]:
    """Fake XY (stage position (9, 18)), Z and monochromator on the core."""
    found = {"xy": FakeStage("xy", (9.0, 18.0)), "z": FakeStage("z", 1.0), "wl": FakeMonochromator()}
    core.devices.update(found)
    return found


@pytest.fixture
def fakes() -> SimpleNamespace:
    return SimpleNamespace(Probe=Probe, ProbeParams=ProbeParams, FakeStage=FakeStage, wait_until=wait_until)
