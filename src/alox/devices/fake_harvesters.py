"""A fake Harvesters / GenICam stack: tests and rehearsals of :mod:`alox.devices.camera_genicam` without a camera, a
GenTL producer or the ``harvesters`` package (as :mod:`aloxcam.zstage.fake_c663` is for the Z stage).

It mimics what Harvesters 1.4 and the genicam package return: :class:`FakeHarvester` (``add_file``, ``update`` — which
destroys the open image acquirers, as Harvesters does —, ``device_info_list`` with ``property_dict``,
``create(index)``, ``reset``), :class:`FakeAcquirer` (``remote_device.node_map.get_node``, ``num_buffers``,
``start`` / ``stop`` / ``is_acquiring``, ``try_fetch(timeout=…)`` returning context-managed buffers with
``payload.components[0]``, ``destroy``) and :class:`FakeNode` (GenApi nodes: ``node.principal_interface_type``,
``get_access_mode()``, ``value``, ``min`` / ``max`` / ``unit``, ``symbolics`` / ``entries`` / ``get_int_value`` /
``set_int_value``, ``execute``), selectors included. :func:`fake_cameras` gives the two cameras of the machine
("HighMag" #4103000001 and "LowMag" #4103000002) with a GV-528x-M-like node map (:func:`make_nodes`: SFNC names, the
LabVIEW integer values of the I/O enumerations).

Frames: queued with :meth:`FakeAcquirer.emit`, or produced every ``free_run_period`` seconds while acquiring, either
filled with the frame index (8 × 6 Mono8) or made by ``image`` (:func:`gradient_image`: the node map's size, pixel
format and exposure). ``incomplete=True`` discards every free-run frame as Harvesters does with an incomplete buffer
(a warning on the ``harvesters.core`` logger): the symptom of a packet size the network card cannot carry.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import numpy as np

__all__ = [
    "BOOLEAN",
    "COMMAND",
    "ENUM",
    "FAKE_MODEL",
    "FAKE_PRODUCER",
    "FAKE_VENDOR",
    "FLOAT",
    "INT",
    "STR",
    "FakeAcquirer",
    "FakeCamera",
    "FakeGenApiError",
    "FakeGenTLError",
    "FakeHarvester",
    "FakeNode",
    "fake_cameras",
    "gradient_image",
    "make_nodes",
]

INT, BOOLEAN, COMMAND, FLOAT, STR, ENUM = 2, 3, 4, 5, 6, 9          # genicam.genapi.EInterfaceType values
_RO, _RW, _WO = 3, 4, 2                                             # genicam.genapi.EAccessMode values
FAKE_VENDOR = "IDS Imaging Development Systems GmbH"
FAKE_MODEL = "GV-5280CP-M"
#: Stands for the GenTL producer file (it exists, which is all the driver checks).
FAKE_PRODUCER = Path(__file__)
#: (data, pixel format, width, height) of one frame, from the node map and the frame index.
ImageSource = Callable[[dict[str, "FakeNode"], int], tuple[np.ndarray, str, int, int]]

_harvesters_log = logging.getLogger("harvesters.core")


class FakeGenApiError(Exception):
    """Stands in for the genicam.genapi exceptions (AccessException, OutOfRangeException, LogicalErrorException…)."""


class FakeGenTLError(Exception):
    """Stands in for the genicam.gentl exceptions (AccessDeniedException, IoException…)."""


class FakeNode:
    """A GenApi node as genicam's NodeMap returns it."""

    def __init__(self, nodes: dict[str, FakeNode], name: str, itype: int, value: Any = None, *,
                 minimum: float | None = None, maximum: float | None = None, entries: dict[str, int] | None = None,
                 readonly: bool = False, selector: str | None = None, unit: str = "") -> None:
        self._nodes = nodes
        self.name = name
        self.node = SimpleNamespace(principal_interface_type=itype, name=name)
        self._value = value
        self.per: dict[int, Any] = {}                     # values per selector value
        self._min, self._max = minimum, maximum
        self._entries = dict(entries or {})
        self.readonly = readonly
        self.selector = selector
        self.unit = unit
        self.executed = 0
        self.fail: Exception | None = None

    @property
    def itype(self) -> int:
        return self.node.principal_interface_type

    def get_access_mode(self) -> int:
        return _WO if self.itype == COMMAND else _RO if self.readonly else _RW

    def _selected(self) -> int | None:
        return None if self.selector is None else self._nodes[self.selector].get_int_value()

    @property
    def value(self) -> Any:
        if self.fail is not None:
            raise self.fail
        if self.itype == COMMAND:
            raise FakeGenApiError("AccessException: a command has no value")
        sel = self._selected()
        return self._value if sel is None else self.per.get(sel, self._value)

    @value.setter
    def value(self, v: Any) -> None:
        if self.fail is not None:
            raise self.fail
        if self.readonly:
            raise FakeGenApiError(f"AccessException: node {self.name} is not writable")
        if self.itype == ENUM and v not in self._entries:
            raise FakeGenApiError(f"InvalidArgumentException: {v} is not an entry of {self.name}")
        if self.itype in (INT, FLOAT):
            if (self._min is not None and v < self._min) or (self._max is not None and v > self._max):
                raise FakeGenApiError(f"OutOfRangeException: {v}")
            if self.itype == INT and not isinstance(v, int):
                raise FakeGenApiError(f"InvalidArgumentException: {v!r} is not an integer")
        sel = self._selected()
        if sel is None:
            self._value = v
        else:
            self.per[sel] = v

    @property
    def min(self) -> float | None:
        if self.itype not in (INT, FLOAT):
            raise FakeGenApiError("LogicalErrorException: no Min")
        return self._min

    @property
    def max(self) -> float | None:
        if self.itype not in (INT, FLOAT):
            raise FakeGenApiError("LogicalErrorException: no Max")
        return self._max

    @property
    def symbolics(self) -> list[str]:
        return list(self._entries)

    @property
    def entries(self) -> list[SimpleNamespace]:
        """``IEnumEntry`` objects: ``symbolic`` and integer ``value``."""
        return [SimpleNamespace(symbolic=s, value=v) for s, v in self._entries.items()]

    def get_int_value(self) -> int:
        return self._entries[self.value]

    def set_int_value(self, n: int) -> None:
        for symbolic, value in self._entries.items():
            if value == n:
                self.value = symbolic
                return
        raise FakeGenApiError(f"OutOfRangeException: no entry with value {n}")

    def execute(self) -> None:
        if self.fail is not None:
            raise self.fail
        self.executed += 1


class FakeNodeMap:
    def __init__(self, nodes: dict[str, FakeNode]) -> None:
        self._nodes = nodes

    def get_node(self, name: str) -> FakeNode:
        try:
            return self._nodes[name]
        except KeyError:
            raise FakeGenApiError(f"LogicalErrorException: node {name} does not exist") from None


def _ip(text: str) -> int:
    a, b, c, d = (int(x) for x in text.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d


def make_nodes(*, serial_number: str = "4103000001", user_id: str = "HighMag", model: str = FAKE_MODEL,
               ip: str = "192.168.10.11") -> dict[str, FakeNode]:
    """A GV-528x-M-like node map (SFNC names; ``LineSource`` / ``LineSelector`` / ``UserOutputSelector`` integer
    values as in the LabVIEW comments, docs/05 §9.1; Line1 is an output)."""
    nodes: dict[str, FakeNode] = {}

    def add(name: str, itype: int, value: Any = None, **kwargs: Any) -> None:
        nodes[name] = FakeNode(nodes, name, itype, value, **kwargs)
    add("DeviceVendorName", STR, FAKE_VENDOR, readonly=True)
    add("DeviceModelName", STR, model, readonly=True)
    add("DeviceFirmwareVersion", STR, "1.6.1739", readonly=True)
    add("DeviceSerialNumber", STR, serial_number, readonly=True)
    add("DeviceUserID", STR, user_id)
    add("GevCurrentIPAddress", INT, _ip(ip), readonly=True)
    add("GevCurrentSubnetMask", INT, _ip("255.255.255.0"), readonly=True)
    add("GevMACAddress", INT, 0x000C_DF04_0000 + int(serial_number[-4:] or 0), readonly=True)
    add("ExposureTime", FLOAT, 10000.0, minimum=28.0, maximum=2_000_000.0, unit="us")
    add("ExposureAuto", ENUM, "Off", entries={"Off": 0, "Continuous": 2})
    add("GainSelector", ENUM, "AnalogAll", entries={"AnalogAll": 0, "DigitalAll": 1})
    add("Gain", FLOAT, 1.0, minimum=1.0, maximum=16.0)
    add("AcquisitionMode", ENUM, "Continuous", entries={"SingleFrame": 0, "MultiFrame": 1, "Continuous": 2})
    add("AcquisitionFrameRate", FLOAT, 20.0, minimum=0.5, maximum=60.0, unit="Hz")
    add("DeviceTemperatureSelector", ENUM, "Mainboard", entries={"Mainboard": 0, "Sensor": 1})
    add("DeviceTemperature", FLOAT, 41.5, readonly=True, unit="C")
    add("PixelFormat", ENUM, "Mono12", entries={"Mono8": 0x01080001, "Mono12": 0x01100005})
    add("Width", INT, 2448, minimum=16, maximum=2448)
    add("Height", INT, 2048, minimum=2, maximum=2048)
    add("OffsetX", INT, 0, minimum=0, maximum=2432)
    add("OffsetY", INT, 0, minimum=0, maximum=2046)
    add("GevSCPSPacketSize", INT, 1440, minimum=576, maximum=9000)
    add("GevSCPD", INT, 0, minimum=0, maximum=100000)
    add("TriggerSelector", ENUM, "FrameStart", entries={"FrameStart": 0})
    add("TriggerMode", ENUM, "Off", entries={"Off": 0, "On": 1})
    add("TriggerSource", ENUM, "Software", entries={"Software": 0, "Line0": 1})
    add("TriggerSoftware", COMMAND)
    add("LineSelector", ENUM, "Line0", entries={"Line0": 0, "Line1": 1, "Line2": 2, "Line3": 3})
    add("LineMode", ENUM, "Input", entries={"Input": 0, "Output": 1}, selector="LineSelector")
    add("LineSource", ENUM, "Off", entries={"Off": 0, "UserOutput0": 1, "UserOutput1": 2}, selector="LineSelector")
    add("LineInverter", BOOLEAN, False, selector="LineSelector")
    add("LineStatus", BOOLEAN, False, readonly=True, selector="LineSelector")
    add("UserOutputSelector", ENUM, "UserOutput0", entries={"UserOutput0": 0, "UserOutput1": 1})
    add("UserOutputValue", BOOLEAN, False, selector="UserOutputSelector")
    add("UserSetSelector", ENUM, "Default", entries={"Default": 0, "UserSet0": 1})
    add("UserSetDefault", ENUM, "Default", entries={"Default": 0, "UserSet0": 1})
    nodes["LineMode"].per[1] = "Output"                                 # Line1: the opto-coupled output
    return nodes


def gradient_image(nodes: dict[str, FakeNode], index: int) -> tuple[np.ndarray, str, int, int]:
    """A horizontal gradient of the node map's Width × Height in its PixelFormat, full scale at 20 ms exposure."""
    width, height = int(nodes["Width"].value), int(nodes["Height"].value)
    fmt = str(nodes["PixelFormat"].value)
    top = 255 if fmt == "Mono8" else 4095
    row = np.clip(np.linspace(0.0, top, width) * float(nodes["ExposureTime"].value) / 20_000.0, 0, top)
    data = np.repeat(row[np.newaxis, :], height, axis=0).astype(np.uint8 if fmt == "Mono8" else np.uint16)
    return data.reshape(-1), fmt, width, height


class FakeBuffer:
    def __init__(self, acquirer: FakeAcquirer, component: SimpleNamespace) -> None:
        self._acquirer = acquirer
        self.payload = SimpleNamespace(components=[component])

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self._acquirer.requeue()


class FakeAcquirer:
    """``harvesters.core.ImageAcquirer`` (frames: see the module docstring)."""

    def __init__(self, nodes: dict[str, FakeNode], *, free_run_period: float | None = None,
                 image: ImageSource | None = None, incomplete: bool = False) -> None:
        self.nodes = nodes
        self.remote_device = SimpleNamespace(node_map=FakeNodeMap(nodes))
        self.num_buffers = 1
        self.min_num_buffers = 1
        self.free_run_period = free_run_period
        self.image = image
        self.incomplete = incomplete
        self._cond = threading.Condition()
        self._pending: deque[SimpleNamespace] = deque()
        self._acquiring = False
        self.starts = self.stops = self.produced = self.outstanding = self.requeued = self.discarded = 0
        self.stopped_with_outstanding = False
        self.destroyed = False
        self.fail_start: Exception | None = None
        self.fail_stop: Exception | None = None
        self.fetch_errors: deque[Exception] = deque()

    def start(self) -> None:
        if self.fail_start is not None:
            raise self.fail_start
        with self._cond:
            self._acquiring = True
            self.starts += 1

    def stop(self) -> None:
        if self.fail_stop is not None:
            error, self.fail_stop = self.fail_stop, None
            raise error
        with self._cond:
            if self._acquiring:
                self.stopped_with_outstanding |= self.outstanding > 0
                self._acquiring = False
                self.stops += 1
            self._pending.clear()

    def is_acquiring(self) -> bool:
        return self._acquiring

    def destroy(self) -> None:
        self.stop()
        self.destroyed = True

    def emit(self, data: np.ndarray | None = None, *, fmt: str = "Mono8", width: int = 8, height: int = 6) -> None:
        """Queue one frame (default: every pixel holds the frame index, mod 256)."""
        with self._cond:
            self._pending.append(self._component(data, fmt, width, height))
            self._cond.notify_all()

    def _component(self, data: np.ndarray | None, fmt: str, width: int, height: int) -> SimpleNamespace:
        if data is None:
            dtype = np.uint8 if fmt == "Mono8" else np.uint16
            data = np.full(width * height, self.produced % 256, dtype)
        self.produced += 1
        return SimpleNamespace(data=data, width=width, height=height, data_format=fmt)

    def _free_run_frame(self) -> SimpleNamespace | None:
        if self.incomplete:
            self.discarded += 1
            _harvesters_log.warning("incomplete or not available; discarded: fake buffer #%d", self.discarded)
            return None
        if self.image is None:
            return self._component(None, "Mono8", 8, 6)
        data, fmt, width, height = self.image(self.nodes, self.produced)
        return self._component(data, fmt, width, height)

    def try_fetch(self, *, timeout: float) -> FakeBuffer | None:
        with self._cond:
            if self.fetch_errors:
                raise self.fetch_errors.popleft()
            if self.free_run_period is not None and not self._pending:
                self._cond.wait(min(timeout, self.free_run_period))
                if self._acquiring:
                    frame = self._free_run_frame()
                    if frame is not None:
                        self._pending.append(frame)
            elif not self._cond.wait_for(lambda: bool(self._pending), timeout):
                return None
            if not self._pending:
                return None
            self.outstanding += 1
            return FakeBuffer(self, self._pending.popleft())

    def requeue(self) -> None:
        with self._cond:
            self.outstanding -= 1
            self.requeued += 1


@dataclass
class FakeCamera:
    """One camera seen by the fake producer; its node map is made by :func:`make_nodes` unless given."""

    user_defined_name: str
    serial_number: str
    model: str = FAKE_MODEL
    ip: str = "192.168.10.11"
    free_run_period: float | None = None
    image: ImageSource | None = None
    incomplete: bool = False
    in_use: bool = False
    nodes: dict[str, FakeNode] | None = None
    acquirer: FakeAcquirer | None = None

    def __post_init__(self) -> None:
        if self.nodes is None:
            self.nodes = make_nodes(serial_number=self.serial_number, user_id=self.user_defined_name,
                                    model=self.model, ip=self.ip)

    def property_dict(self) -> dict[str, str]:
        return {"id_": f"GEV-{self.serial_number}", "vendor": FAKE_VENDOR, "model": self.model, "tl_type": "GEV",
                "display_name": f"IDS {self.model} {self.serial_number}",
                "user_defined_name": self.user_defined_name, "serial_number": self.serial_number}


def fake_cameras(*, free_run_period: float | None = None, image: ImageSource | None = None) -> list[FakeCamera]:
    """The machine's two cameras: "HighMag" #4103000001 (192.168.10.11) and "LowMag" #4103000002 (192.168.11.11)."""
    return [FakeCamera("HighMag", "4103000001", ip="192.168.10.11", free_run_period=free_run_period, image=image),
            FakeCamera("LowMag", "4103000002", ip="192.168.11.11", free_run_period=free_run_period, image=image)]


class FakeHarvester:
    """``harvesters.core.Harvester``."""

    def __init__(self, cameras: list[FakeCamera]) -> None:
        self.cameras = cameras
        self.files: list[str] = []
        self.device_info_list: list[SimpleNamespace] = []
        self._listed: list[FakeCamera] = []
        self.acquirers: list[FakeAcquirer] = []
        self.updates = self.resets = 0
        self.timeout_for_update = 1000

    def add_file(self, path: str) -> None:
        self.files.append(path)

    def update(self) -> None:
        self.updates += 1
        for acquirer in self.acquirers:                   # as Harvesters: update() makes the acquirers obsolete
            acquirer.destroy()
        self.acquirers.clear()
        self._listed = list(self.cameras)
        self.device_info_list = [SimpleNamespace(property_dict=c.property_dict()) for c in self._listed]

    def create(self, search_key: int) -> FakeAcquirer:
        assert type(search_key) is int
        camera = self._listed[search_key]
        if camera.in_use:
            raise FakeGenTLError("AccessDeniedException: the device is opened by another application")
        assert camera.nodes is not None
        camera.acquirer = FakeAcquirer(camera.nodes, free_run_period=camera.free_run_period, image=camera.image,
                                       incomplete=camera.incomplete)
        self.acquirers.append(camera.acquirer)
        return camera.acquirer

    def reset(self) -> None:
        self.resets += 1
        for acquirer in self.acquirers:
            acquirer.destroy()
        self.acquirers.clear()
        self.device_info_list = []
