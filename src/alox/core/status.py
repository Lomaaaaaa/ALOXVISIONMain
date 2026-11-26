"""Status IDs: the system status and the status of the three measurements (map Part 1 §4.5, §8 note 13; Part 3 §2.1
note 4; Part 5 Appendix A/B).

LabVIEW keeps each status in an FGV: ``alox_manager_system_status_ID`` (methods Init, Exit, Set, Get, Set Language,
Status timer) and ``alox_manager_mesa_status_ID`` / ``_tilt_`` / ``_bowing_`` (Init, Exit, Set, Get, Set Language,
Get Description). "Set" stores the code and, **only when it differs from the current one**, sends it to the websocket
notifier (``status``, ``mesa_status``...; ``alox_manager_system_status_ID d8``, ``alox_manager_mesa_status_ID d0``).
Here each change is published as an :class:`alox.events.Event` on ``status.system``, ``status.mesa``, ``status.tilt``
or ``status.bowing`` with ``data = {"code": new, "previous": old}``; the API turns it into the websocket event
(:func:`alox.api.reports.status_event`, which also maps 1001..1999 to 500).

Code ranges, common to every status (diagram note "SPECS AET POUR CODES DE STATUS"): 0 initial state; 10..99 stopped,
waiting for a condition; 100 stopped, condition OK; 200 starting (command accepted); 201..499 sequenced steps; 500
finished OK / idle; 600..799 waiting, paused; 800..899 stop actions; 1000..1999 error. 501..599 and 900..999 are
unassigned ("Dispo").

System hardware errors (``tools_update_system_status_from_{cameras_z,xy,monochromator}_state d0..d16``, texts in
``system_en.lang``): the code is ``1000 + bits`` with a 9-bit field, bit 0 high-mag camera, 1 low-mag camera, 2 X stage,
3 Y stage, 4 Z stage, 5 monochromator ("WL"), 6 cameras/Z module, 7 XY module, 8 monochromator module (so 1001..1511;
``system_en.lang`` names 1001..1063 one by one and 1064, 1128, 1256 for the modules). "Get" describes 1064..1127 with
the text of 1064, 1128..1255 with 1128 and 1256..1512 with 1256 (``alox_manager_system_status_ID d11..d15``).
"""

from __future__ import annotations

import threading
from enum import IntEnum, IntFlag

from alox.core.translations import Translations
from alox.events import Event, Publish

__all__ = ["FUNCTION_CODES", "BowingStatus", "FunctionStatusID", "HardwareBit", "MesaStatus", "StatusID", "StatusRange",
           "SystemCode", "SystemStatus", "TiltStatus", "hardware_bits_of", "status_range", "system_description_code"]

#: Highest status code of the specification (1000..1999 = error).
MAX_CODE = 1999
#: Base of the hardware error codes (``Base Error`` in ``tools_update_system_status_from_xy_state d0``).
HARDWARE_BASE = 1000


class StatusRange(IntEnum):
    """The meaning of a code range; the value is the first code of the range."""

    INITIAL = 0                     # 0
    WAITING_CONDITION = 10          # 10..99: stopped, waiting for a condition
    CONDITION_OK = 100              # 100: stopped, ready
    STARTING = 200                  # 200: command accepted
    RUNNING = 201                   # 201..499: sequenced steps
    FINISHED = 500                  # 500: finished OK / idle
    PAUSED = 600                    # 600..799: waiting, paused
    STOPPING = 800                  # 800..899: stop actions
    ERROR = 1000                    # 1000..1999
    UNASSIGNED = -1                 # 1..9, 501..599, 900..999


def status_range(code: int) -> StatusRange:
    """The range of ``code`` (module docstring). Raises ValueError outside 0..1999."""
    code = _check_code(code)
    if code == 0:
        return StatusRange.INITIAL
    if 10 <= code <= 99:
        return StatusRange.WAITING_CONDITION
    if code == 100:
        return StatusRange.CONDITION_OK
    if code == 200:
        return StatusRange.STARTING
    if 201 <= code <= 499:
        return StatusRange.RUNNING
    if code == 500:
        return StatusRange.FINISHED
    if 600 <= code <= 799:
        return StatusRange.PAUSED
    if 800 <= code <= 899:
        return StatusRange.STOPPING
    if code >= 1000:
        return StatusRange.ERROR
    return StatusRange.UNASSIGNED


def _check_code(code: int) -> int:
    if isinstance(code, bool) or not isinstance(code, int):
        raise TypeError(f"a status code is an int, not {type(code).__name__}")
    if not 0 <= code <= MAX_CODE:
        raise ValueError(f"status code {code} outside 0..{MAX_CODE}")
    return int(code)


# ==================================================================================================== system status


class SystemCode(IntEnum):
    """The named system status codes of ``system_en.lang`` (where they are set: map Part 1 §2.3..§2.6)."""

    INITIAL = 0
    BOOTING = 10
    CHECKING_DONGLE = 11
    INIT_HEARTBEAT = 12
    INIT_WEB_SERVER = 13
    INIT_WEBSOCKET = 14
    INIT_CAMERAS_Z_MODULE = 50
    INIT_HIGH_MAG_CAMERA = 51
    INIT_LOW_MAG_CAMERA = 52
    INIT_Z_STAGE = 53
    INIT_XY_MODULE = 54
    INIT_XY_STAGES = 55
    INIT_MONOCHROMATOR_MODULE = 56
    INIT_MONOCHROMATOR = 57
    INIT_PROFILE = 58
    INIT_WATCHDOG = 59
    INITIALIZED = 100
    STARTING = 200
    BOWING_RUNNING = 201
    TILT_RUNNING = 202
    MESA_RUNNING = 203
    LOW_LEVEL_RUNNING = 204
    IDLE = 500
    PAUSED = 600
    EXIT = 800
    RESET = 801
    STOP = 802
    ABORT = 803
    CAMERAS_Z_MODULE_ERROR = 1064
    XY_MODULE_ERROR = 1128
    MONOCHROMATOR_MODULE_ERROR = 1256
    NO_DONGLE = 1513
    INVALID_DONGLE = 1514
    EXPIRED_DONGLE = 1515
    MISSING_RESOURCE = 1516
    CAMERAS_Z_START_ERROR = 1601
    XY_START_ERROR = 1602
    MONOCHROMATOR_START_ERROR = 1603
    PROFILE_ERROR = 1604
    UNKNOWN_ERROR = 1701


class HardwareBit(IntFlag):
    """The hardware error bit field of the system status (labels of ``tools_update_system_status_from_*_state``)."""

    HIGH_MAG = 1                    # "HI Mag cam bit"
    LOW_MAG = 2                     # "LO Mag cam bit"
    X = 4                           # "X stage bit"
    Y = 8                           # "Y stage bit"
    Z = 16                          # "z stage bit"
    MONOCHROMATOR = 32              # "WL. bit"
    CAMERAS_Z_MODULE = 64           # "Cameras/Z mod. bit"
    XY_MODULE = 128                 # "XY mod. bit"
    MONOCHROMATOR_MODULE = 256      # "WL mod. bit"


_ALL_BITS = int(HardwareBit(511))
#: Highest hardware error code (all nine bits).
HARDWARE_MAX = HARDWARE_BASE + _ALL_BITS


def hardware_bits_of(code: int) -> HardwareBit:
    """The failed-hardware bits of a system code (none outside 1001..1511)."""
    if HARDWARE_BASE < code <= HARDWARE_MAX:
        return HardwareBit(code - HARDWARE_BASE)
    return HardwareBit(0)


def system_description_code(code: int) -> int:
    """The ``system_en.lang`` ID describing a system code: the module error text for 1064..1127 / 1128..1255 /
    1256..1512, the code itself otherwise (``alox_manager_system_status_ID d11..d15``)."""
    if 1064 <= code <= 1127:
        return 1064
    if 1128 <= code <= 1255:
        return 1128
    if 1256 <= code <= 1512:
        return 1256
    return code


# ==================================================================================================== holders


class StatusID:
    """One status-ID manager: the current code, its description and the change events. Thread-safe.

    ``publish`` receives one event per change, in the order of the changes; it is called with the holder's lock held,
    so it must be quick and must not wait for another thread (``EventBus.publish`` and ``list.append`` fit).
    """

    def __init__(self, domain: str, topic: str, *, publish: Publish | None = None, source: str = "core",
                 initial: int = 0) -> None:
        self.domain = domain
        self.topic = topic
        self._publish = publish
        self._source = source
        self._lock = threading.RLock()
        self._code = _check_code(initial)

    @property
    def code(self) -> int:
        """The current code (LabVIEW "Get")."""
        with self._lock:
            return self._code

    def get(self) -> int:
        return self.code

    def set(self, code: int) -> bool:
        """Store ``code`` (LabVIEW "Set"); publish and return True when it changed."""
        code = _check_code(code)
        with self._lock:
            return self._store(code)

    def _store(self, code: int) -> bool:
        previous = self._code
        if code == previous:
            return False
        self._code = code
        if self._publish is not None:
            self._publish(Event(self.topic, self._source, {"code": code, "previous": previous}))
        return True

    def range(self, code: int | None = None) -> StatusRange:
        """:func:`status_range` of ``code`` (default: the current code)."""
        return status_range(self.code if code is None else code)

    def description_code(self, code: int) -> int:
        """The language-file ID that describes ``code``."""
        return code

    def description(self, translations: Translations, code: int | None = None) -> str:
        """The localized text of ``code`` (default: the current code; LabVIEW "Get Description" / the ``Description``
        output of "Get"), from the ``<domain>_<lang>.lang`` table."""
        code = self.code if code is None else _check_code(code)
        return translations.text(self.description_code(code), domain=self.domain)


class SystemStatus(StatusID):
    """The system status (``alox_manager_system_status_ID``; topic ``status.system``, texts of ``system_<lang>.lang``).

    :meth:`hardware_failed` maintains the hardware error field. LabVIEW only ever *sets* bits (the update tools run
    when a module's error count reaches ``hardware.<module>.maxfails``) and a later "Set" (e.g. 500 after a reset)
    replaces the whole code; here a bit can also be cleared, and when the last bit goes the code that was current
    before the first failure comes back (**design choice**, to confirm with the owner). Any :meth:`set` replaces the
    code and forgets that saved code, as in LabVIEW.
    """

    def __init__(self, *, publish: Publish | None = None, source: str = "core", initial: int = 0) -> None:
        super().__init__("system", "status.system", publish=publish, source=source, initial=initial)
        self._before_hardware: int | None = None

    def set(self, code: int) -> bool:
        code = _check_code(code)
        with self._lock:
            self._before_hardware = None
            return self._store(code)

    @property
    def hardware_bits(self) -> HardwareBit:
        """The failed-hardware bits of the current code (none when it is not a hardware error)."""
        return hardware_bits_of(self.code)

    def hardware_failed(self, module: HardwareBit, failed: bool = True) -> int:
        """Set (``failed``) or clear the bits of ``module`` (one or several :class:`HardwareBit`) and return the new
        code. The bits start from the current code when it is a hardware error (1001..1511), from zero otherwise."""
        module = HardwareBit(module)
        with self._lock:
            bits = hardware_bits_of(self._code)
            new = (bits | module) if failed else (bits & ~module)
            if new == bits:
                return self._code
            if new:
                if not bits:
                    self._before_hardware = self._code
                self._store(HARDWARE_BASE + int(new))
            else:
                restore = self._before_hardware if self._before_hardware is not None else SystemCode.IDLE
                self._before_hardware = None
                self._store(int(restore))
            return self._code

    def description_code(self, code: int) -> int:
        return system_description_code(code)


class FunctionStatusID(StatusID):
    """The status ID of a high-level measurement: ``FunctionStatusID("mesa")`` (``alox_manager_mesa_status_ID``),
    ``"tilt"`` or ``"bowing"``; topic ``status.<name>``, texts of ``<name>_<lang>.lang``, codes in
    :class:`MesaStatus`, :class:`TiltStatus`, :class:`BowingStatus`."""

    def __init__(self, name: str, *, publish: Publish | None = None, source: str = "core", initial: int = 0) -> None:
        if name not in FUNCTION_CODES:
            raise ValueError(f"unknown measurement {name!r} (known: {', '.join(FUNCTION_CODES)})")
        super().__init__(name, f"status.{name}", publish=publish, source=source, initial=initial)
        self.name = name
        self.codes = FUNCTION_CODES[name]


# ==================================================================================================== function codes
# Common codes of the three files: 0 initial, 10 waiting for a low-level function, 100 ready, 200 starting,
# 500 finished, 600 paused, 800 stopping, 850 stopped (mesa_en.lang says "Function stopping (command accepted)" twice).


class MesaStatus(IntEnum):
    """``mesa_en.lang`` (running 401..499, errors 1500..1511)."""

    INITIAL = 0
    WAITING_LOW_LEVEL = 10
    READY = 100
    STARTING = 200
    INITIALIZING = 401
    MOVE_START = 402
    WAIT_MOVE_START = 403
    SEARCH_OBJECT = 404
    AUTOFOCUS_1 = 405
    AUTOFOCUS_2 = 406
    AUTOFOCUS_3 = 407
    REFOCUS_1 = 408
    REFOCUS_2 = 409
    REFOCUS_3 = 410
    REFOCUS_OXYD_1 = 411
    REFOCUS_OXYD_2 = 412
    REFOCUS_OXYD_3 = 413
    DETECT_MESA_1 = 414
    DETECT_MESA_2 = 415
    DETECT_MESA_3 = 416
    DETECT_MESA_4 = 417
    DETECT_MESA_5 = 418
    TRACK_MESA_1 = 419
    TRACK_MESA_2 = 420
    TRACK_MESA_3 = 421
    AUTO_EXPOSURE_1 = 422
    AUTO_EXPOSURE_2 = 423
    AUTO_EXPOSURE_3 = 424
    SEARCH_WAVELENGTH_1 = 425
    SEARCH_WAVELENGTH_2 = 426
    SEARCH_WAVELENGTH_3 = 427
    SET_WAVELENGTH_1 = 428
    SET_WAVELENGTH_2 = 429
    SET_WAVELENGTH_3 = 430
    MEASURE_MESA_1 = 431
    MEASURE_MESA_2 = 432
    MEASURE_MESA_3 = 433
    MEASURE_MESA_4 = 434
    MEASURE_MESA_5 = 435
    DATA_LOGGING = 436
    EXITING = 499
    FINISHED = 500
    PAUSED = 600
    STOPPING = 800
    STOPPED = 850
    ERROR = 1500
    ERROR_MOVE_START = 1501
    ERROR_WAIT_MOVE_START = 1502
    ERROR_SEARCH_OBJECT = 1503
    ERROR_AUTOFOCUS = 1504
    ERROR_REFOCUS = 1505
    ERROR_DETECT_CIRCLE = 1506
    ERROR_TRACK_MESA = 1507
    ERROR_AUTO_EXPOSURE = 1508
    ERROR_SEARCH_WAVELENGTH = 1509
    ERROR_INVALID_EXPOSURES = 1510
    ERROR_MEASURE_MESA = 1511


class TiltStatus(IntEnum):
    """``tilt_en.lang`` (running 301..400, errors 1300..1499)."""

    INITIAL = 0
    WAITING_LOW_LEVEL = 10
    READY = 100
    STARTING = 200
    INITIALIZING = 301
    INITIALIZED = 302
    MOVE = 303
    WAIT_MOVE = 304
    AUTOFOCUS = 305
    REFOCUS = 306
    MEASURE_STRAIGHT_EDGE = 307
    MEASURE_DUAL_OBJECTS = 308
    DATA_LOGGING = 309
    # Python only (Tilt Methods Long Baseline Edge / Lattice, not in tilt_en.lang)
    MEASURE_LONG_BASELINE_EDGE = 310
    MEASURE_LATTICE = 311
    MOVE_BASELINE = 312
    WAIT_MOVE_BASELINE = 313
    EXITING = 400
    FINISHED = 500
    PAUSED = 600
    STOPPING = 800
    STOPPED = 850
    ERROR = 1300
    ERROR_MOVE = 1301
    ERROR_WAIT_MOVE = 1302
    ERROR_AUTOFOCUS = 1303
    ERROR_REFOCUS = 1304
    ERROR_TILT_NOT_FOUND = 1499


class BowingStatus(IntEnum):
    """``bowing_en.lang`` (running 201..300, errors 1000..1033). The nine Z points in the file's order are ZW2 (wafer
    center), ZW3 (wafer NE), ZC3 (chuck NE), ZW1 (wafer SW), ZC1 (chuck SW), ZW4 (wafer SE), ZC4 (chuck SE), ZW5
    (wafer NW), ZC5 (chuck NW). The file writes "ZC3" for the chuck SW/SE/NW moves, waits and autofocus (207, 209,
    211, 216, 218, 220, 225, 227, 229); the names here follow the position and the refocus/store/error codes (ZC1, ZC4,
    ZC5): a typo of the file (**inferred**). The autofocus errors 1011..1019 use their own order (from the file)."""

    INITIAL = 0
    WAITING_LOW_LEVEL = 10
    READY = 100
    STARTING = 200
    INITIALIZING = 201
    INITIALIZED = 202
    MOVE_ZW2 = 203
    MOVE_ZW3 = 204
    MOVE_ZC3 = 205
    MOVE_ZW1 = 206
    MOVE_ZC1 = 207
    MOVE_ZW4 = 208
    MOVE_ZC4 = 209
    MOVE_ZW5 = 210
    MOVE_ZC5 = 211
    WAIT_MOVE_ZW2 = 212
    WAIT_MOVE_ZW3 = 213
    WAIT_MOVE_ZC3 = 214
    WAIT_MOVE_ZW1 = 215
    WAIT_MOVE_ZC1 = 216
    WAIT_MOVE_ZW4 = 217
    WAIT_MOVE_ZC4 = 218
    WAIT_MOVE_ZW5 = 219
    WAIT_MOVE_ZC5 = 220
    AUTOFOCUS_ZW2 = 221
    AUTOFOCUS_ZW3 = 222
    AUTOFOCUS_ZC3 = 223
    AUTOFOCUS_ZW1 = 224
    AUTOFOCUS_ZC1 = 225
    AUTOFOCUS_ZW4 = 226
    AUTOFOCUS_ZC4 = 227
    AUTOFOCUS_ZW5 = 228
    AUTOFOCUS_ZC5 = 229
    REFOCUS_ZW2 = 230
    REFOCUS_ZW3 = 231
    REFOCUS_ZC3 = 232
    REFOCUS_ZW1 = 233
    REFOCUS_ZC1 = 234
    REFOCUS_ZW4 = 235
    REFOCUS_ZC4 = 236
    REFOCUS_ZW5 = 237
    REFOCUS_ZC5 = 238
    STORE_ZW2 = 239
    STORE_ZW3 = 240
    STORE_ZC3 = 241
    STORE_ZW1 = 242
    STORE_ZC1 = 243
    STORE_ZW4 = 244
    STORE_ZC4 = 245
    STORE_ZW5 = 246
    STORE_ZC5 = 247
    DATA_LOGGING = 248
    EXITING = 300
    FINISHED = 500
    PAUSED = 600
    STOPPING = 800
    STOPPED = 850
    ERROR = 1000
    ERROR_RESET_WAVELENGTH = 1001
    ERROR_MOVE_ZW2 = 1002
    ERROR_MOVE_ZW3 = 1003
    ERROR_MOVE_ZC3 = 1004
    ERROR_MOVE_ZW1 = 1005
    ERROR_MOVE_ZC1 = 1006
    ERROR_MOVE_ZW4 = 1007
    ERROR_MOVE_ZC4 = 1008
    ERROR_MOVE_ZW5 = 1009
    ERROR_MOVE_ZC5 = 1010
    ERROR_AUTOFOCUS_ZW2 = 1011
    ERROR_AUTOFOCUS_ZC3 = 1012
    ERROR_AUTOFOCUS_ZW3 = 1013
    ERROR_AUTOFOCUS_ZC1 = 1014
    ERROR_AUTOFOCUS_ZW1 = 1015
    ERROR_AUTOFOCUS_ZC4 = 1016
    ERROR_AUTOFOCUS_ZW4 = 1017
    ERROR_AUTOFOCUS_ZC5 = 1018
    ERROR_AUTOFOCUS_ZW5 = 1019
    ERROR_REFOCUS_ZW2 = 1020
    ERROR_REFOCUS_ZW3 = 1021
    ERROR_REFOCUS_ZC3 = 1022
    ERROR_REFOCUS_ZW1 = 1023
    ERROR_REFOCUS_ZC1 = 1024
    ERROR_REFOCUS_ZW4 = 1025
    ERROR_REFOCUS_ZC4 = 1026
    ERROR_REFOCUS_ZW5 = 1027
    ERROR_REFOCUS_ZC5 = 1028
    ERROR_BOWING_RANGE = 1029
    ERROR_PARALLELISM_RANGE = 1030
    ERROR_BOWING_AND_PARALLELISM_RANGE = 1031
    ERROR_PARALLELISM_A_RANGE = 1032
    ERROR_PARALLELISM_B_RANGE = 1033


#: The measurement status families: name → code enum (the name is also the translation domain and the topic suffix).
FUNCTION_CODES: dict[str, type[IntEnum]] = {"mesa": MesaStatus, "tilt": TiltStatus, "bowing": BowingStatus}
