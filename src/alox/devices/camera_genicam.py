"""GenICam camera backend on Harvesters: the NI-IMAQdx replacement for the real IDS GigE Vision cameras.

Hardware: two IDS GigE Vision cameras, monochrome (firmware ``GV-528x-M_1.6.1739.guf`` in the LabVIEW sources; Sony
IMX264, 2448 × 2048, Mono8 per docs/answers/owner-answers.md §4). :class:`GenICamBackend` implements
:class:`aloxcam.camera.backend.CameraBackend` on a GenTL producer (``.cti`` file, e.g. the IDS peak GigE Vision
producer) through the Harvesters library; :class:`GenICamDevice` implements
:class:`~aloxcam.camera.backend.CameraDevice` with the semantics of the simulator (:mod:`aloxcam.camera.sim`): same
buffer numbering, timeouts, error codes and attribute typing. Spec: docs/05 §2, §4, §5, §6.4, §9, §12.3–§12.5.

Opening by name (``IMAQdx Open Camera``)
    ``alox.ini`` ``name_STR`` holds an IMAQdx camera name (``HighMag`` / ``LowMag``, or an NI MAX alias such as
    ``cam0``). It is resolved in this order (:func:`match_device`):

    1. ``name_map[name]`` (configuration, {INI name → serial number}): the camera with that serial number;
    2. the GenTL device information, field by field (:data:`MATCH_FIELDS`): ``user_defined_name`` (the camera's
       ``DeviceUserID``, settable in IDS peak Cockpit: naming the cameras ``HighMag`` / ``LowMag`` is the simplest
       set-up), ``serial_number``, ``display_name``, ``model``, ``id``; exact comparison first, then case-insensitive.

    The first field that matches decides; a name matching two cameras (e.g. by model: both cameras are the same model)
    is an error. A camera listed by two producers (IDS GEV and GEVK) counts once (same serial number). NI MAX aliases
    (``cam0``) are not device properties: they need ``name_map`` or a ``DeviceUserID``. A camera is opened exclusively.
    Harvesters' ``update()`` destroys every open image acquirer, so the device list is refreshed only while no camera
    of this backend is open (open both cameras before using either, or open them in any order: the list read at the
    first open serves the second one).

Attributes
    ``CameraAttributes::<Category>::<Feature>`` → GenICam feature ``<Feature>`` (:func:`genicam_feature_names`);
    :data:`DEFAULT_ALIASES` covers the known differences (IIDC-shaped ``Exposure::Value`` / ``Gain::Value``, the session
    property ``PacketSize``) and is extended or overridden by ``aliases``; :data:`FEATURE_FALLBACKS` lists pre-SFNC 2.0
    names tried when the SFNC name is missing. Typed access mirrors the simulator: ``DBL`` / ``I64`` / ``U32`` on float
    and integer features (``I64`` / ``U32`` reads rounded half to even, ``U32`` floored at 0; writes range-checked),
    ``BOOL`` on booleans, ``STRING`` on strings and enumerations (entry names). Enumerations are also accessed **by
    integer value** with ``I64`` / ``U32`` / ``DBL``, as IMAQdx does for ``LineSelector`` / ``LineSource`` /
    ``UserOutputSelector`` (docs/05 §2.1, §9.1), through ``enum_values`` {feature → {integer → entry}} when the camera's
    entry values differ from the LabVIEW ones. ``BOOL True`` executes a command (``TriggerSoftware``), ``False`` does
    nothing. Selectors (``LineSelector`` then ``LineInverter``) need no emulation: they are features of the camera.

Acquisition
    ``configure`` sets the number of GenTL buffers and resets a :class:`~aloxcam.camera.backend.FrameRing`; ``start``
    starts the image acquirer and the grab thread ``alox-genicam-<name>``, which fetches buffers (``try_fetch``, polled
    every ``poll_s``), copies the first payload component to NumPy (Mono8 → uint8; Mono10/12/14/16, packed or not →
    uint16; :func:`mono_pixels`), requeues the buffer and pushes the frame into the ring. One-shot mode (``continuous``
    FALSE, IMAQdx "Sequence") keeps ``num_buffers`` frames per start, then the counter stops while the acquisition stays
    in progress (as the simulator). ``stop`` / ``unconfigure`` / ``close`` stop the thread first, then the acquirer
    (Harvesters wants every fetched buffer queued back before ``stop``). Driver errors in the grab thread are logged and
    retried; the last one is quoted in the next get-image timeout. ``pixel_format`` (default Mono8, the format LabVIEW
    used) is written at open.

Errors
    Every failure raises :class:`~aloxcam.errors.CameraError`: code 9502 (``Code.CAMERA_SDK``) with the IMAQdx operation
    and the driver text (Harvesters / GenApi / GenTL exceptions included); a closed device raises -1074360316 ("invalid
    camera session"), as the simulator.

Adapter layer and threading
    Only :class:`HarvestersDriver`, :class:`HarvestersAcquirer` and :class:`GenICamFeature` touch Harvesters / GenApi
    objects; ``harvesters`` is imported on first use. Tests inject a fake Harvester with ``harvester_factory`` (or a
    whole driver with ``driver``). Devices are called from the camera worker only, plus ``close()`` at shutdown; one
    lock guards the session state and the grab thread's pushes, and the ring's ``alive`` check is lock-free.

Installation
    ``pip install harvesters`` (1.4.x; it installs the ``genicam`` package, wheels for 64-bit CPython on Windows and
    Linux) and IDS peak with the GigE Vision transport layer. IDS peak offers two GigE Vision GenTL producers: "Kernel
    (GEVK)", the producer plus a kernel driver for Ethernet performance (recommended), and "Socket (GEV)" for PCs where
    no kernel driver can be installed [1]. The ``.cti`` files are in the IDS peak installation folder, typically
    ``C:\\Program Files\\IDS\\ids_peak\\...\\ids_gevgentlk.cti`` (GEVK) or ``ids_gevgentl.cti`` (GEV); the exact
    sub-folder depends on the IDS peak version and was not confirmed from the IDS documentation: check it on the PC
    with ``dir /s "C:\\Program Files\\IDS\\ids_peak\\*.cti"``. The IDS peak setup puts the producer folder in
    ``GENICAM_GENTL64_PATH``, which this backend scans when no ``.cti`` is given (:func:`find_gentl_producers`); give
    the GEVK file explicitly to load one GigE producer only. On Linux the producers are in
    ``/opt/ids_peak_<version>_<architecture>/lib/ids/cti`` (``ids_gevgentl.cti``, ``ids_u3vgentl.cti``) and the IDS
    scripts set ``GENICAM_GENTL64_PATH`` [2].

    [1] IDS peak readme for Windows (components "Kernel (GEVK)" / "Socket (GEV)"; installation under
        ``C:\\Program Files\\IDS\\ids_peak``),
        https://en.ids-imaging.com/files/downloads/ids-peak/readme/ids-peak-windows-readme-2.6_EN.html
        (also 2.5.1 / 2.17.0 under the same path).
    [2] IDS peak readme for Linux (``cti`` folder, ``GENICAM_GENTL64_PATH``),
        https://en.ids-imaging.com/files/downloads/ids-peak/readme/ids-peak-linux-readme-2.15.0_EN.html
        and ids-peak-linux-readme-2.5_EN.html (list of the ``.cti`` files).

What must be checked on the real cameras (IDS peak Cockpit shows every feature, its type and enumeration entries)
    - Feature names and units on the GV-528x-M firmware: ``ExposureTime`` (µs), ``Gain`` (IDS: a factor, with
      ``GainSelector``; ``alox.ini`` ``gain_STR`` / gain type should become ``Gain`` / ``DBL``, the default ``I64``
      rounds it), ``AcquisitionFrameRate`` (and whether a frame-rate enable feature must be set first; its ``max``
      feeds ``Cam Set Exposure`` on HM), ``DeviceTemperature`` (with ``DeviceTemperatureSelector``), ``TriggerMode`` /
      ``TriggerSoftware`` (``TriggerSelector`` = FrameStart, ``TriggerSource`` = Software). Unknown names → ``aliases``.
    - Pixel format: Mono8 is written at open; check the images are 2448 × 2048 uint8 and that no ROI / binning is left
      in the camera (the NI MAX camera file that IMAQdx loaded at open is not applied: save the needed settings in a
      camera UserSet and make it the default).
    - Packet size: ``GevSCPSPacketSize`` = 8996 needs jumbo frames (9014 bytes) on the NIC; without them every frame is
      incomplete, dropped by the producer, and get-image times out. ``packet_size=1500`` forces a smaller size, ``0``
      keeps the producer's value; with both cameras on one NIC check the inter-packet delay (``GevSCPD``).
    - I/O lines: LabVIEW writes ``LineSelector`` 1, ``LineSource`` 1 (``UserOutput0``) and ``UserOutputSelector`` 0 by
      integer value (docs/05 §9.2); check the entries' integer values on the GV-528x-M, map differences with
      ``enum_values`` (e.g. ``{"LineSource": {1: "UserOutput0"}}``), check that the LED / bulb are wired to that line
      and that its ``LineMode`` is Output (``LineSource`` is only writable on an output line).
    - Serial number: ``SerialNumberHigh/Low`` are derived from the GenTL serial string (decimal or hexadecimal number,
      else a CRC); a camera that disappears shows only as get-image timeouts.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import threading
import time
import zlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

import numpy as np

from aloxcam.camera.attributes import NUM_BUFFERS, PROPERTY_NODE
from aloxcam.camera.backend import (
    CameraBackend,
    CameraDevice,
    Frame,
    FrameRing,
    ValueType,
)
from aloxcam.clock import U32_MASK
from aloxcam.errors import CameraError, Code

__all__ = [
    "DEFAULT_ALIASES",
    "DEFAULT_PIXEL_FORMAT",
    "FEATURE_FALLBACKS",
    "MATCH_FIELDS",
    "Acquirer",
    "DeviceInfo",
    "GenICamBackend",
    "GenICamDevice",
    "GenICamFeature",
    "GenTLDriver",
    "HarvestersAcquirer",
    "HarvestersDriver",
    "RawImage",
    "find_gentl_producers",
    "genicam_feature_names",
    "match_device",
    "mono_pixels",
    "serial_number_words",
]

log = logging.getLogger(__name__)

#: IMAQdx attribute → GenICam feature for the names that are not ``CameraAttributes::<Category>::<Feature>``.
DEFAULT_ALIASES: Mapping[str, str | tuple[str, ...]] = MappingProxyType({
    "CameraAttributes::Exposure::Value": "ExposureTime",
    "CameraAttributes::Gain::Value": "Gain",
    "CameraAttributes::FrameRate::Value": "AcquisitionFrameRate",
    "PacketSize": "GevSCPSPacketSize",
})
#: Pre-SFNC 2.0 GigE Vision names tried, in order, when the SFNC feature is not in the node map.
FEATURE_FALLBACKS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "ExposureTime": ("ExposureTimeAbs",),
    "AcquisitionFrameRate": ("AcquisitionFrameRateAbs",),
})
#: Device-information fields compared with an IMAQdx camera name, in this order (after ``name_map``).
MATCH_FIELDS = ("user_defined_name", "serial_number", "display_name", "model", "id")
#: Pixel format written at open: LabVIEW acquired into ``Grayscale (U8)`` images (docs/05 §2).
DEFAULT_PIXEL_FORMAT = "Mono8"

_OPEN = "IMAQdx Open Camera"
#: CameraDevice method → IMAQdx operation named in error sources (as :mod:`aloxcam.camera.sim`).
_OPERATIONS: Mapping[str, str] = MappingProxyType({
    "bus_type": "IMAQdx Property Node (BusType)",
    "serial_number": "IMAQdx Property Node (SerialNumber)",
    "set_packet_size": "IMAQdx Property Node (PacketSize)",
    "configure": "IMAQdx Configure Acquisition",
    "unconfigure": "IMAQdx Unconfigure Acquisition",
    "close": "IMAQdx Close Camera",
    "start": "IMAQdx Start Acquisition",
    "stop": "IMAQdx Stop Acquisition",
    "acquisition_in_progress": "IMAQdx Property Node (AcquisitionInProgress)",
    "last_buffer_number": "IMAQdx Property Node (LastBufferNumber)",
    "buffer_count": "IMAQdx Property Node (BufferCount)",
    "get_buffer": "IMAQdx Get Image2",
    "get_next": "IMAQdx Get Image2",
    "get_last_new": "IMAQdx Get Image2",
    "read_attribute": PROPERTY_NODE,
    "write_attribute": PROPERTY_NODE,
    "read_attribute_max": PROPERTY_NODE,
})
#: GenTL transport-layer type → IMAQdx ``BusType`` text.
_BUS_TYPES: Mapping[str, str] = MappingProxyType({"GEV": "Ethernet", "U3V": "USB3Vision"})
#: ``genicam.genapi.EInterfaceType`` value → feature kind.
_INTERFACE_KINDS: Mapping[int, str] = MappingProxyType({2: "int", 3: "bool", 4: "command", 5: "float", 6: "string",
                                                         9: "enum"})
_NUMERIC = (ValueType.DBL, ValueType.I64, ValueType.U32)
_MONO16_PREFIXES = ("Mono10", "Mono12", "Mono14", "Mono16")


# ================================================================================================== pure helpers
def _describe(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


@contextmanager
def _sdk(operation: str) -> Iterator[None]:
    """Driver exception → ``CameraError(9502, "<operation>: <type>: <text>")``; CameraError passes through."""
    try:
        yield
    except CameraError:
        raise
    except Exception as exc:
        raise CameraError(Code.CAMERA_SDK, f"{operation}: {_describe(exc)}") from exc


def genicam_feature_names(attribute: str,
                          aliases: Mapping[str, str | Sequence[str]] | None = None) -> tuple[str, ...]:
    """The GenICam feature names tried, in order, for an IMAQdx attribute path.

    ``aliases`` (default :data:`DEFAULT_ALIASES`) wins; otherwise the last ``::`` component
    (``CameraAttributes::AcquisitionControl::TriggerMode`` → ``TriggerMode``; a bare name is used as it is). Each name
    is followed by its :data:`FEATURE_FALLBACKS`. An IIDC-shaped ``…::<Feature>::Value`` without an alias raises
    ``CameraError(9502, …)``.
    """
    table = DEFAULT_ALIASES if aliases is None else aliases
    alias = table.get(attribute)
    if alias is not None:
        names: tuple[str, ...] = (alias,) if isinstance(alias, str) else tuple(alias)
    else:
        feature = attribute.rsplit("::", 1)[-1].strip()
        if not feature or (feature == "Value" and "::" in attribute):
            raise CameraError(Code.CAMERA_SDK, f"{attribute}: no GenICam feature for this IMAQdx attribute "
                                               f"(add it to the backend's aliases)")
        names = (feature,)
    out: list[str] = []
    for name in names:
        out.append(name)
        out.extend(FEATURE_FALLBACKS.get(name, ()))
    return tuple(dict.fromkeys(out))


def find_gentl_producers(environ: Mapping[str, str] | None = None) -> list[Path]:
    """The ``.cti`` files in the folders of ``GENICAM_GENTL64_PATH`` (``GENICAM_GENTL32_PATH`` for a 32-bit Python),
    as the GenTL standard defines them; sorted per folder, folders in variable order."""
    env = os.environ if environ is None else environ
    var = "GENICAM_GENTL64_PATH" if sys.maxsize > 2**32 else "GENICAM_GENTL32_PATH"
    found: list[Path] = []
    for folder in env.get(var, "").split(os.pathsep):
        path = Path(folder.strip().strip('"')) if folder.strip() else None
        if path is not None and path.is_dir():
            found.extend(sorted(p for p in path.iterdir() if p.suffix.lower() == ".cti" and p.is_file()))
    return list(dict.fromkeys(found))


def serial_number_words(serial: str) -> tuple[int, int]:
    """``(SerialNumberHigh, SerialNumberLow)`` from a GenTL serial string: a decimal or hexadecimal number as 64 bits,
    anything else its CRC-32 (display only)."""
    s = serial.strip()
    if not s:
        return 0, 0
    if s.isascii() and s.isdigit():
        n = int(s)
    else:
        try:
            n = int(s, 16)
        except ValueError:
            n = zlib.crc32(s.encode("utf-8", "replace"))
    n &= 0xFFFF_FFFF_FFFF_FFFF
    return (n >> 32) & U32_MASK, n & U32_MASK


@dataclass(frozen=True)
class DeviceInfo:
    """GenTL device information of one camera (``harvesters.core.DeviceInfo.property_dict``)."""

    index: int                           # position in the Harvester's device_info_list
    id: str = ""
    vendor: str = ""
    model: str = ""
    tl_type: str = ""
    display_name: str = ""
    user_defined_name: str = ""
    serial_number: str = ""

    @property
    def key(self) -> str:
        """Identity of the physical camera: serial number, else GenTL id, else list index."""
        return self.serial_number or self.id or f"#{self.index}"

    def describe(self) -> str:
        user = f" '{self.user_defined_name}'" if self.user_defined_name else ""
        return f"{self.model or self.display_name or self.id or '?'} #{self.serial_number or '?'}{user}"


def _unique(infos: Sequence[DeviceInfo]) -> list[DeviceInfo]:
    seen: dict[str, DeviceInfo] = {}
    for info in infos:
        seen.setdefault(info.key, info)
    return list(seen.values())


def _open_error(message: str, infos: Sequence[DeviceInfo]) -> CameraError:
    listing = ", ".join(i.describe() for i in infos) or "none"
    return CameraError(Code.CAMERA_SDK, f"{_OPEN}: {message} (GenICam cameras: {listing})")


def match_device(name: str, infos: Sequence[DeviceInfo],
                 name_map: Mapping[str, str] | None = None) -> DeviceInfo:
    """The camera an IMAQdx name designates (order in the module docstring); ``CameraError(9502, …)`` if none or
    several match."""
    unique = _unique(infos)
    if name_map and name in name_map:
        serial = str(name_map[name]).strip()
        for info in unique:
            if info.serial_number.strip() == serial:
                return info
        raise _open_error(f"camera '{name}' (serial number {serial} in name_map) not found", unique)
    for fold in (False, True):
        wanted = name.casefold() if fold else name
        for field in MATCH_FIELDS:
            hits = [i for i in unique if getattr(i, field)
                    and (getattr(i, field).casefold() if fold else getattr(i, field)) == wanted]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise _open_error(f"camera name '{name}' matches {len(hits)} cameras by {field}; set a DeviceUserID "
                                  f"or a name_map entry", unique)
    raise _open_error(f"camera '{name}' not found", unique)


@dataclass(frozen=True)
class RawImage:
    """One fetched buffer, copied out of the GenTL buffer before it was requeued."""

    width: int
    height: int
    pixel_format: str                    # PFNC name, e.g. "Mono8"
    data: np.ndarray                     # owned copy (1-D or 2-D), as Harvesters expands the pixel format


def mono_pixels(image: RawImage) -> np.ndarray:
    """A 2-D ``height × width`` array: Mono8 → uint8, Mono10/12/14/16 (unpacked or already unpacked by Harvesters) →
    uint16; line padding is dropped. ``CameraError(9502, …)`` for other formats or a short buffer."""
    fmt = image.pixel_format.strip()
    h, w = int(image.height), int(image.width)
    data = np.asarray(image.data).reshape(-1)
    if fmt == "Mono8":
        dtype = np.dtype(np.uint8)
    elif fmt.startswith(_MONO16_PREFIXES):
        dtype = np.dtype(np.uint16)
        if data.dtype == np.uint8:                         # raw bytes: only unpacked little-endian 16-bit pixels
            if fmt.endswith(("p", "Packed")) or data.size % 2:
                raise CameraError(Code.CAMERA_SDK, f"pixel format {fmt}: packed data delivered as raw bytes")
            data = np.ascontiguousarray(data).view("<u2")
    else:
        raise CameraError(Code.CAMERA_SDK, f"pixel format {fmt!r} not supported (monochrome Mono8 / Mono10–16 only)")
    if h <= 0 or w <= 0 or data.size < h * w:
        raise CameraError(Code.CAMERA_SDK, f"{fmt} buffer of {data.size} pixels does not hold a {w} x {h} image")
    if data.size != h * w and data.size % h == 0:
        pixels = data.reshape(h, -1)[:, :w]                # padding at the end of each line
    else:
        pixels = data[: h * w].reshape(h, w)
    return np.ascontiguousarray(pixels, dtype=dtype)


# ================================================================================================== adapter layer
def _kind_of(node: Any) -> str:
    """``"float" | "int" | "bool" | "enum" | "string" | "command" | "other"`` from the GenApi interface type."""
    itype = getattr(getattr(node, "node", None), "principal_interface_type", None)
    if isinstance(itype, int):                             # genicam.genapi.EInterfaceType is an IntEnum
        return _INTERFACE_KINDS.get(int(itype), "other")
    if callable(getattr(node, "execute", None)):
        return "command"
    if hasattr(node, "symbolics"):
        return "enum"
    value = getattr(node, "value", None)
    for kind, types in (("bool", (bool, np.bool_)), ("int", (int, np.integer)), ("float", (float, np.floating)),
                        ("string", (str,))):
        if isinstance(value, types):
            return kind
    return "other"


class GenICamFeature:
    """Adapter for one GenApi node as Harvesters returns it (``IFloat``, ``IInteger``, ``IBoolean``,
    ``IEnumeration``, ``IString``, ``ICommand``). Exceptions are the driver's own."""

    def __init__(self, name: str, node: Any) -> None:
        self.name = name
        self._node = node
        self.kind = _kind_of(node)

    @property
    def value(self) -> Any:
        """Current value (an enumeration's entry name)."""
        return self._node.value

    @value.setter
    def value(self, value: Any) -> None:
        self._node.value = value

    @property
    def minimum(self) -> float | int:
        return self._node.min

    @property
    def maximum(self) -> float | int:
        return self._node.max

    def symbolics(self) -> tuple[str, ...]:
        """Entry names of an enumeration."""
        return tuple(str(s) for s in self._node.symbolics)

    def get_int(self) -> int:
        """Integer value of an enumeration's current entry (GenApi ``GetIntValue``)."""
        return int(self._node.get_int_value())

    def set_int(self, value: int) -> None:
        """Select an enumeration entry by integer value (GenApi ``SetIntValue``)."""
        self._node.set_int_value(int(value))

    def execute(self) -> None:
        self._node.execute()


class Acquirer(Protocol):
    """What a device needs from one opened camera (:class:`HarvestersAcquirer`)."""

    def feature(self, name: str) -> GenICamFeature | None: ...
    def set_num_buffers(self, num_buffers: int) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def is_acquiring(self) -> bool: ...
    def fetch(self, timeout_s: float) -> RawImage | None: ...
    def destroy(self) -> None: ...


class GenTLDriver(Protocol):
    """What the backend needs from the GenTL stack (:class:`HarvestersDriver`)."""

    def update(self) -> list[DeviceInfo]: ...
    def create(self, index: int) -> Acquirer: ...
    def reset(self) -> None: ...


class HarvestersAcquirer:
    """Adapter for one ``harvesters.core.ImageAcquirer``."""

    def __init__(self, image_acquirer: Any) -> None:
        self._ia = image_acquirer

    def feature(self, name: str) -> GenICamFeature | None:
        """The remote device's feature ``name``, None when the node map has no such node."""
        node_map = self._ia.remote_device.node_map
        get_node = getattr(node_map, "get_node", None)
        try:
            node = get_node(name) if callable(get_node) else getattr(node_map, name)
        except Exception:                                   # noqa: BLE001 - GenApi LogicalErrorException: no node
            return None
        return None if node is None else GenICamFeature(name, node)

    def set_num_buffers(self, num_buffers: int) -> None:
        minimum = int(getattr(self._ia, "min_num_buffers", 1) or 1)
        self._ia.num_buffers = max(int(num_buffers), minimum)

    def start(self) -> None:
        self._ia.start()

    def stop(self) -> None:
        self._ia.stop()

    def is_acquiring(self) -> bool:
        return bool(self._ia.is_acquiring())

    def fetch(self, timeout_s: float) -> RawImage | None:
        """The next complete buffer copied out (None after ``timeout_s``); the buffer is queued back at once."""
        buffer = self._ia.try_fetch(timeout=timeout_s)
        if buffer is None:
            return None
        with buffer:                                        # Buffer.__exit__ queues it back to the producer
            component = buffer.payload.components[0]
            return RawImage(int(component.width), int(component.height), str(component.data_format),
                            np.array(component.data, copy=True))

    def destroy(self) -> None:
        self._ia.destroy()


def _import_harvester() -> Callable[[], Any]:
    try:
        from harvesters.core import Harvester
    except ImportError as exc:
        raise CameraError(Code.CAMERA_SDK, f"GenICam camera backend: the 'harvesters' package is not installed "
                                           f"(pip install harvesters): {exc}") from exc
    return Harvester


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _device_info(index: int, info: Any) -> DeviceInfo:
    props = getattr(info, "property_dict", None)
    props = props if isinstance(props, Mapping) else {}

    def get(key: str) -> str:
        if key in props:
            return _text(props[key])
        try:
            return _text(getattr(info, key, None))
        except Exception:                                   # noqa: BLE001 - GenTL NotAvailableException etc.
            return ""
    return DeviceInfo(index, id=get("id_"), vendor=get("vendor"), model=get("model"), tl_type=get("tl_type"),
                      display_name=get("display_name"), user_defined_name=get("user_defined_name"),
                      serial_number=get("serial_number"))


class HarvestersDriver:
    """Adapter for ``harvesters.core.Harvester`` with the GenTL producers loaded (created on first use)."""

    def __init__(self, cti_files: Sequence[str | os.PathLike[str]], *,
                 harvester_factory: Callable[[], Any] | None = None, update_timeout_ms: int | None = None) -> None:
        self.cti_files = [str(p) for p in cti_files]
        self._factory = harvester_factory
        self._update_timeout_ms = update_timeout_ms
        self._harvester: Any = None

    def _ensure(self) -> Any:
        if self._harvester is None:
            factory = self._factory or _import_harvester()
            if not self.cti_files:
                raise CameraError(Code.CAMERA_SDK, "GenICam camera backend: no GenTL producer (.cti): give its path "
                                                   "or set GENICAM_GENTL64_PATH (IDS peak setup)")
            missing = [p for p in self.cti_files if not Path(p).is_file()]
            if missing:
                raise CameraError(Code.CAMERA_SDK, f"GenICam camera backend: GenTL producer not found: "
                                                   f"{', '.join(missing)}")
            harvester = factory()
            for path in self.cti_files:
                harvester.add_file(path)
            if self._update_timeout_ms is not None:
                harvester.timeout_for_update = int(self._update_timeout_ms)
            self._harvester = harvester
        return self._harvester

    def update(self) -> list[DeviceInfo]:
        """Rescan the cameras (destroys every image acquirer of this Harvester)."""
        harvester = self._ensure()
        harvester.update()
        return [_device_info(i, info) for i, info in enumerate(harvester.device_info_list)]

    def create(self, index: int) -> HarvestersAcquirer:
        return HarvestersAcquirer(self._ensure().create(int(index)))

    def reset(self) -> None:
        harvester, self._harvester = self._harvester, None
        if harvester is not None:
            harvester.reset()


# ================================================================================================== device
class GenICamDevice(CameraDevice):
    """One opened GenICam camera, ~ one IMAQdx session (semantics of :class:`aloxcam.camera.sim.SimCameraDevice`)."""

    def __init__(self, name: str, acquirer: Acquirer, info: DeviceInfo, *,
                 aliases: Mapping[str, str | Sequence[str]] = DEFAULT_ALIASES,
                 enum_values: Mapping[str, Mapping[int, str]] | None = None, packet_size: int | None = None,
                 clock: Callable[[], float] = time.monotonic, poll_s: float = 0.1,
                 on_close: Callable[[GenICamDevice], None] | None = None) -> None:
        self.name = name
        self.info = info
        self._acq = acquirer
        self._aliases = aliases
        self._enum_values = {str(k): {int(n): str(s) for n, s in v.items()} for k, v in (enum_values or {}).items()}
        self._packet_size_override = packet_size
        self._clock = clock
        self._poll_s = max(0.001, float(poll_s))
        self._on_close = on_close
        self._lock = threading.RLock()
        self._features: dict[str, GenICamFeature] = {}
        self._ring = FrameRing(NUM_BUFFERS)
        self._configured = False
        self._continuous = True
        self._num_buffers = 0
        self._sequence_count = 0
        self._acquiring = False
        self._closed = False
        self._packet_size = 0
        self._grab: threading.Thread | None = None
        self._grab_stop = threading.Event()
        self._grab_error: str | None = None
        self._bad_formats: set[str] = set()

    # ------------------------------------------------------------------ introspection
    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def configured(self) -> bool:
        return self._configured

    @property
    def continuous(self) -> bool:
        return self._continuous

    @property
    def packet_size(self) -> int:
        """The last packet size written (0 = never written or left to the producer)."""
        return self._packet_size

    @property
    def frames_acquired(self) -> int:
        """Frames pushed since the last :meth:`configure`."""
        return self._ring.frames_acquired

    @property
    def last_grab_error(self) -> str | None:
        """The last driver error seen by the grab thread (None if none since :meth:`start`)."""
        return self._grab_error

    def feature_name(self, attribute: str) -> str:
        """The GenICam feature an IMAQdx attribute resolves to on this camera."""
        with self._lock:
            self._check("read_attribute")
            return self._feature(attribute).name

    # ------------------------------------------------------------------ CameraDevice: session
    def bus_type(self) -> str:
        with self._lock:
            self._check("bus_type")
            return _BUS_TYPES.get(self.info.tl_type.upper(), self.info.tl_type or "Unknown")

    def serial_number(self) -> tuple[int, int]:
        with self._lock:
            self._check("serial_number")
            return serial_number_words(self.info.serial_number)

    def set_packet_size(self, size: int) -> None:
        op = _OPERATIONS["set_packet_size"]
        with self._lock:
            self._check("set_packet_size")
            target = int(size) if self._packet_size_override is None else int(self._packet_size_override)
            if target == 0:
                log.info("%s: packet size left to the camera / GenTL producer", self.name)
                return
            feature = self._feature("PacketSize")
            if feature.kind != "int":
                raise CameraError(Code.CAMERA_SDK, f"{op}: {feature.name} is not an integer feature ({feature.kind})")
            self._check_range("PacketSize", feature, target)
            with _sdk(f"{op} ({feature.name})"):
                feature.value = target
            self._packet_size = target

    def configure(self, continuous: bool, num_buffers: int) -> None:
        op = _OPERATIONS["configure"]
        with self._lock:
            self._check("configure")
            if self._acquiring:
                raise CameraError(Code.CAMERA_SDK, f"{op}: acquisition in progress")
            if num_buffers < 1:
                raise CameraError(Code.CAMERA_SDK, f"{op}: invalid number of buffers {num_buffers}")
            with _sdk(op):
                self._acq.set_num_buffers(int(num_buffers))
            self._configured = True
            self._continuous = bool(continuous)
            self._num_buffers = int(num_buffers)
            self._ring.reset(self._num_buffers)

    def unconfigure(self) -> None:
        with self._lock:
            self._check("unconfigure")
            was_acquiring, thread = self._halt_locked()
            self._configured = False
        if was_acquiring:
            self._stop_acquirer(thread, _OPERATIONS["unconfigure"])

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            _, thread = self._halt_locked()
            self._closed = True
            self._configured = False
        self._join(thread)
        error: CameraError | None = None
        try:
            with _sdk(_OPERATIONS["close"]):
                self._acq.destroy()                         # also stops a running acquisition
        except CameraError as exc:
            error = exc
        self._ring.interrupt()
        if self._on_close is not None:
            self._on_close(self)
        if error is not None:
            raise error

    def start(self) -> None:
        op = _OPERATIONS["start"]
        with self._lock:
            self._check("start")
            if not self._configured:
                raise CameraError(Code.CAMERA_SDK, f"{op}: acquisition not configured")
            if self._acquiring:
                return
            with _sdk(op):
                self._acq.start()
            self._acquiring = True
            self._sequence_count = 0
            self._grab_error = None
            self._grab_stop = threading.Event()
            self._grab = threading.Thread(target=self._grab_loop, args=(self._grab_stop,),
                                          name=f"alox-genicam-{self.name}", daemon=True)
            self._grab.start()

    def stop(self) -> None:
        with self._lock:
            self._check("stop")
            was_acquiring, thread = self._halt_locked()
        if was_acquiring:
            self._stop_acquirer(thread, _OPERATIONS["stop"])

    def acquisition_in_progress(self) -> bool:
        with self._lock:
            self._check("acquisition_in_progress")
            with _sdk(_OPERATIONS["acquisition_in_progress"]):
                return self._acq.is_acquiring()

    def last_buffer_number(self) -> int | None:
        with self._lock:
            self._check("last_buffer_number")
            return self._ring.last_number

    def buffer_count(self) -> int:
        with self._lock:
            self._check("buffer_count")
            return self._num_buffers if self._configured else 0

    # ------------------------------------------------------------------ CameraDevice: images
    def get_buffer(self, number: int | None, timeout_ms: int) -> Frame:
        self._before_get("get_buffer")
        return self._after_get(self._ring.get_buffer(number, timeout_ms / 1000.0, alive=self._alive), timeout_ms)

    def get_next(self, timeout_ms: int) -> Frame:
        self._before_get("get_next")
        return self._after_get(self._ring.get_next(timeout_ms / 1000.0, alive=self._alive), timeout_ms)

    def get_last_new(self, timeout_ms: int) -> Frame:
        self._before_get("get_last_new")
        return self._after_get(self._ring.get_last_new(timeout_ms / 1000.0, alive=self._alive), timeout_ms)

    # ------------------------------------------------------------------ CameraDevice: attributes
    def read_attribute(self, name: str, vtype: ValueType) -> float | int | bool | str:
        with self._lock:
            self._check("read_attribute")
            feature = self._feature(name)
            kind = feature.kind
            if kind == "command":
                raise CameraError(Code.CAMERA_SDK, f"{name}: command attribute is not readable")
            with _sdk(self._op(name, feature)):
                if kind in ("float", "int"):
                    if vtype is ValueType.DBL:
                        return float(feature.value)
                    if vtype in (ValueType.I64, ValueType.U32):
                        return self._read_int(feature.value, vtype)
                elif kind == "enum":
                    if vtype is ValueType.STRING:
                        return str(feature.value)
                    if vtype in _NUMERIC:
                        n = self._enum_get_int(feature)
                        return float(n) if vtype is ValueType.DBL else self._read_int(n, vtype)
                elif kind == "bool" and vtype is ValueType.BOOL:
                    return bool(feature.value)
                elif kind == "string" and vtype is ValueType.STRING:
                    return str(feature.value)
            raise self._wrong_type(name, feature, vtype)

    def write_attribute(self, name: str, vtype: ValueType, value: float | bool | str) -> None:
        with self._lock:
            self._check("write_attribute")
            feature = self._feature(name)
            kind = feature.kind
            op = self._op(name, feature)
            if kind == "command":
                if vtype is not ValueType.BOOL or not isinstance(value, (bool, np.bool_)):
                    raise self._wrong_type(name, feature, vtype)
                if value:
                    with _sdk(op):
                        feature.execute()
                return
            if kind in ("float", "int"):
                x = self._number(name, feature, vtype, value)
                v: Any = (x if isinstance(x, int) else round(x)) if kind == "int" else float(x)
                self._check_range(name, feature, v)
                with _sdk(op):
                    feature.value = v
            elif kind == "enum":
                if vtype is ValueType.STRING and isinstance(value, str):
                    with _sdk(op):
                        choices = feature.symbolics()
                    if choices and value not in choices:
                        raise CameraError(Code.CAMERA_SDK, f"{name}: invalid value {value!r} "
                                                           f"(one of {', '.join(choices)})")
                    with _sdk(op):
                        feature.value = value
                else:
                    x = self._number(name, feature, vtype, value)
                    self._enum_set_int(name, feature, x if isinstance(x, int) else round(x))
            elif kind == "bool":
                if vtype is not ValueType.BOOL or not isinstance(value, (bool, np.bool_)):
                    raise self._wrong_type(name, feature, vtype)
                with _sdk(op):
                    feature.value = bool(value)
            elif kind == "string":
                if vtype is not ValueType.STRING or not isinstance(value, str):
                    raise self._wrong_type(name, feature, vtype)
                with _sdk(op):
                    feature.value = value
            else:
                raise self._wrong_type(name, feature, vtype)

    def read_attribute_max(self, name: str) -> float:
        with self._lock:
            self._check("read_attribute_max")
            feature = self._feature(name)
            if feature.kind not in ("float", "int"):
                raise CameraError(Code.CAMERA_SDK, f"{name}: attribute has no maximum ({feature.kind} feature)")
            with _sdk(self._op(name, feature)):
                return float(feature.maximum)

    # ------------------------------------------------------------------ internals (lock held unless stated)
    def _check(self, method: str) -> None:
        if self._closed:
            raise CameraError(Code.IMAQDX_INVALID_SESSION, f"{_OPERATIONS[method]}: invalid camera session")

    @staticmethod
    def _op(name: str, feature: GenICamFeature) -> str:
        return f"{PROPERTY_NODE} ({name} -> {feature.name})"

    def _feature(self, attribute: str) -> GenICamFeature:
        feature = self._features.get(attribute)
        if feature is None:
            names = genicam_feature_names(attribute, self._aliases)
            with _sdk(f"{PROPERTY_NODE} ({attribute})"):
                for candidate in names:
                    feature = self._acq.feature(candidate)
                    if feature is not None:
                        break
            if feature is None:
                raise CameraError(Code.CAMERA_SDK, f"{attribute}: unknown attribute (GenICam feature "
                                                   f"{' / '.join(names)} not in the node map of {self.name})")
            self._features[attribute] = feature
        return feature

    @staticmethod
    def _wrong_type(name: str, feature: GenICamFeature, vtype: ValueType) -> CameraError:
        return CameraError(Code.CAMERA_SDK, f"{name}: Value{vtype.value} does not match the {feature.kind} feature "
                                            f"{feature.name}")

    @staticmethod
    def _read_int(value: Any, vtype: ValueType) -> int:
        n = int(value) if isinstance(value, (int, np.integer)) else round(float(value))
        return max(n, 0) if vtype is ValueType.U32 else n

    def _number(self, name: str, feature: GenICamFeature, vtype: ValueType, value: Any) -> float:
        if vtype not in _NUMERIC or isinstance(value, (bool, np.bool_, str)):
            raise self._wrong_type(name, feature, vtype)
        if isinstance(value, (int, np.integer)):
            return int(value)
        x = float(value)
        if not math.isfinite(x):
            raise CameraError(Code.CAMERA_SDK, f"{name}: invalid value {x}")
        return x

    def _check_range(self, name: str, feature: GenICamFeature, value: float) -> None:
        try:
            lo, hi = feature.minimum, feature.maximum
        except Exception:                                   # noqa: BLE001 - no readable bounds: the driver checks
            return
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise CameraError(Code.CAMERA_SDK, f"{name}: value {value} out of range [{lo}, {hi}] ({feature.name})")

    def _enum_get_int(self, feature: GenICamFeature) -> int:
        table = self._enum_values.get(feature.name)
        if table:
            symbolic = str(feature.value)
            for n, entry in table.items():
                if entry == symbolic:
                    return n
        return feature.get_int()

    def _enum_set_int(self, name: str, feature: GenICamFeature, n: int) -> None:
        table = self._enum_values.get(feature.name)
        if table and n in table:
            with _sdk(self._op(name, feature)):
                feature.value = table[n]
            return
        try:
            feature.set_int(n)
        except Exception as exc:
            try:
                entries = f"entries {', '.join(feature.symbolics())}; "
            except Exception:                               # noqa: BLE001 - the entry list is only a hint
                entries = ""
            raise CameraError(Code.CAMERA_SDK, f"{self._op(name, feature)}: no entry with integer value {n} "
                                               f"({entries}map it with enum_values): {_describe(exc)}") from exc

    def _alive(self) -> bool:                                # lock-free: called under the ring's lock
        return self._acquiring and not self._closed

    def _before_get(self, method: str) -> None:
        with self._lock:
            self._check(method)
            if not self._acquiring:
                raise CameraError(Code.CAMERA_SDK, f"{_OPERATIONS[method]}: acquisition not in progress")

    def _after_get(self, frame: Frame | None, timeout_ms: int) -> Frame:   # without the lock
        if frame is not None:
            return frame
        if not self._alive():
            raise CameraError(Code.CAMERA_SDK, "IMAQdx Get Image2: acquisition stopped while waiting")
        last = self._grab_error
        detail = f" (last driver error: {last})" if last else ""
        raise CameraError(Code.CAMERA_SDK, f"IMAQdx Get Image2: timeout after {timeout_ms} ms{detail}")

    def _halt_locked(self) -> tuple[bool, threading.Thread | None]:
        """Flag the acquisition stopped and wake the waiters (no join: the grab thread needs the lock to finish)."""
        if not self._acquiring:
            return False, None
        self._acquiring = False
        self._grab_stop.set()
        thread, self._grab = self._grab, None
        self._ring.interrupt()
        return True, thread

    def _join(self, thread: threading.Thread | None) -> None:         # without the lock
        if thread is None or thread is threading.current_thread():
            return
        thread.join(timeout=max(2.0, 10 * self._poll_s))
        if thread.is_alive():
            log.warning("%s: grab thread did not stop in time", self.name)

    def _stop_acquirer(self, thread: threading.Thread | None, operation: str) -> None:   # without the lock
        self._join(thread)                                  # every fetched buffer is queued back before stop()
        with self._lock, _sdk(operation):
            self._acq.stop()

    def _grab_loop(self, stop: threading.Event) -> None:
        failing = False
        while not stop.is_set():
            try:
                image = self._acq.fetch(self._poll_s)
            except Exception as exc:                        # noqa: BLE001 - a driver failure must not end the thread
                if stop.is_set():
                    return
                self._grab_error = _describe(exc)
                if not failing:
                    log.warning("%s: fetching a buffer failed: %s", self.name, self._grab_error)
                failing = True
                stop.wait(self._poll_s)
                continue
            if failing:
                log.info("%s: buffers are fetched again", self.name)
                failing = False
            if image is None:
                continue
            try:
                pixels = mono_pixels(image)
            except CameraError as exc:
                self._grab_error = exc.source
                if image.pixel_format not in self._bad_formats:
                    self._bad_formats.add(image.pixel_format)
                    log.warning("%s: frame dropped: %s", self.name, exc.source)
                continue
            with self._lock:
                if stop.is_set() or not self._acquiring or self._closed:
                    return
                if self._continuous or self._sequence_count < self._num_buffers:
                    self._ring.push(pixels, self._clock())
                    self._sequence_count += 1


# ================================================================================================== backend
def _as_paths(cti_files: str | os.PathLike[str] | Sequence[str | os.PathLike[str]]) -> list[str]:
    if isinstance(cti_files, (str, os.PathLike)):
        return [os.fspath(cti_files)]
    return [os.fspath(p) for p in cti_files]


class GenICamBackend(CameraBackend):
    """GenICam / GenTL camera driver on Harvesters (~ NI-IMAQdx). Thread-safe.

    ``cti_files``: the GenTL producer(s) (default: the ``.cti`` files of ``GENICAM_GENTL64_PATH``); ``name_map``:
    {IMAQdx / INI camera name → serial number}; ``aliases``: extra {IMAQdx attribute → GenICam feature(s)} over
    :data:`DEFAULT_ALIASES`; ``enum_values``: {feature → {integer → entry name}} for enumerations written by integer;
    ``pixel_format``: written at open (None = the camera's); ``packet_size``: forces the GigE packet size (0 = keep the
    producer's); ``harvester_factory`` / ``driver``: test injection; ``poll_s``: grab-thread fetch timeout.
    """

    def __init__(self, cti_files: str | os.PathLike[str] | Sequence[str | os.PathLike[str]] | None = None, *,
                 name_map: Mapping[str, str | int] | None = None,
                 aliases: Mapping[str, str | Sequence[str]] | None = None,
                 enum_values: Mapping[str, Mapping[int, str]] | None = None,
                 pixel_format: str | None = DEFAULT_PIXEL_FORMAT, packet_size: int | None = None,
                 harvester_factory: Callable[[], Any] | None = None, driver: GenTLDriver | None = None,
                 clock: Callable[[], float] = time.monotonic, poll_s: float = 0.1,
                 update_timeout_ms: int | None = None) -> None:
        if driver is None:
            files = _as_paths(cti_files) if cti_files else [str(p) for p in find_gentl_producers()]
            driver = HarvestersDriver(files, harvester_factory=harvester_factory, update_timeout_ms=update_timeout_ms)
        self.driver: GenTLDriver = driver
        self.name_map = {str(k): str(v).strip() for k, v in (name_map or {}).items()}
        self.aliases: Mapping[str, str | Sequence[str]] = MappingProxyType({**DEFAULT_ALIASES, **(aliases or {})})
        self.enum_values = {str(k): dict(v) for k, v in (enum_values or {}).items()}
        self.pixel_format = pixel_format
        self.packet_size = packet_size
        self._clock = clock
        self._poll_s = poll_s
        self._lock = threading.Lock()
        self._open: dict[str, GenICamDevice] = {}          # by DeviceInfo.key
        self._infos: list[DeviceInfo] | None = None
        self._closed = False

    @property
    def devices(self) -> Mapping[str, GenICamDevice]:
        """The open devices, by camera name."""
        with self._lock:
            return MappingProxyType({d.name: d for d in self._open.values()})

    @property
    def closed(self) -> bool:
        return self._closed

    def device_infos(self) -> list[DeviceInfo]:
        """The cameras the producers see (rescanned only while no camera is open)."""
        with self._lock:
            return list(self._listing())

    def open_camera(self, name: str) -> GenICamDevice:
        """``IMAQdx Open Camera``: ``CameraError(9502, …)`` for an unknown or ambiguous name, a camera already open, a
        released driver or a driver failure (harvesters missing, producer not found, camera in use)."""
        with self._lock:
            if self._closed:
                raise CameraError(Code.CAMERA_SDK, f"{_OPEN}: camera driver released")
            infos = self._listing()
            try:
                info = match_device(name, infos, self.name_map)
            except CameraError as exc:
                if self._open:
                    raise CameraError(exc.code, exc.source + " (list not refreshed while a camera is open)") from exc
                raise
            other = self._open.get(info.key)
            if other is not None:
                raise CameraError(Code.CAMERA_SDK, f"{_OPEN}: camera '{name}' ({info.describe()}) is already open "
                                                   f"as '{other.name}'")
            with _sdk(f"{_OPEN} ({name}: {info.describe()})"):
                acquirer = self.driver.create(info.index)
            device = GenICamDevice(name, acquirer, info, aliases=self.aliases, enum_values=self.enum_values,
                                   packet_size=self.packet_size, clock=self._clock, poll_s=self._poll_s,
                                   on_close=self._forget)
            self._open[info.key] = device
        if self.pixel_format:
            try:
                device.write_attribute("PixelFormat", ValueType.STRING, self.pixel_format)
            except CameraError as exc:
                log.warning("%s: pixel format %s not set: %s", name, self.pixel_format, exc.source)
        log.info("camera '%s' opened: %s (%s)", name, info.describe(), info.tl_type or "?")
        return device

    def close(self) -> None:
        """Close every open device and release the producers; idempotent."""
        with self._lock:
            self._closed = True
            devices = list(self._open.values())
        for device in devices:
            try:
                device.close()
            except CameraError as exc:
                log.warning("closing camera %r failed: %s", device.name, exc.source)
        try:
            with _sdk("GenICam backend release"):
                self.driver.reset()
        except CameraError as exc:
            log.warning("%s", exc.source)

    def _listing(self) -> list[DeviceInfo]:              # lock held
        if self._infos is None or not self._open:          # update() would destroy the open acquirers
            with _sdk(f"{_OPEN} (device list)"):
                self._infos = list(self.driver.update())
        return self._infos

    def _forget(self, device: GenICamDevice) -> None:
        with self._lock:
            if self._open.get(device.info.key) is device:
                del self._open[device.info.key]
