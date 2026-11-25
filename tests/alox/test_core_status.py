"""Status IDs (``alox.core.status``): ranges, change events, the hardware error field and the measurement codes."""

from __future__ import annotations

import threading
from enum import IntEnum

import pytest

from alox.core.status import (
    FUNCTION_CODES,
    BowingStatus,
    FunctionStatusID,
    HardwareBit,
    MesaStatus,
    StatusRange,
    SystemCode,
    SystemStatus,
    TiltStatus,
    hardware_bits_of,
    status_range,
    system_description_code,
)
from alox.core.translations import Translations
from alox.events import Event

SYSTEM_EN = """;; synthetic system file
000000;System initial state
000500;System idle, ready to start a function
000802;System is going to stop running function
001001;ERROR (High mag)
001016;ERROR (Z)
001017;ERROR (High mag, Z)
001064;ERROR (Cameras/Z module)
001128;ERROR (XY stages module)
001256;ERROR (Monochromator module)
"""


@pytest.fixture
def translations() -> Translations:
    tr = Translations()
    tr.load_text("system", SYSTEM_EN)
    tr.load_text("mesa", "000000;Function initial state\n000404;Search Object\n")
    return tr


# ============================================================================================== ranges


@pytest.mark.parametrize(("code", "expected"), [
    (0, StatusRange.INITIAL), (5, StatusRange.UNASSIGNED), (10, StatusRange.WAITING_CONDITION),
    (59, StatusRange.WAITING_CONDITION), (100, StatusRange.CONDITION_OK), (150, StatusRange.UNASSIGNED),
    (200, StatusRange.STARTING), (201, StatusRange.RUNNING), (499, StatusRange.RUNNING),
    (500, StatusRange.FINISHED), (550, StatusRange.UNASSIGNED), (600, StatusRange.PAUSED), (799, StatusRange.PAUSED),
    (800, StatusRange.STOPPING), (899, StatusRange.STOPPING), (950, StatusRange.UNASSIGNED),
    (1000, StatusRange.ERROR), (1999, StatusRange.ERROR),
])
def test_status_range(code: int, expected: StatusRange):
    assert status_range(code) is expected


def test_invalid_codes():
    for bad in (-1, 2000):
        with pytest.raises(ValueError):
            status_range(bad)
    with pytest.raises(TypeError):
        SystemStatus().set("500")                                 # type: ignore[arg-type]
    with pytest.raises(TypeError):
        SystemStatus().set(True)


# ============================================================================================== system status


def test_set_publishes_only_changes():
    events: list[Event] = []
    status = SystemStatus(publish=events.append)
    assert status.code == SystemCode.INITIAL
    assert status.set(SystemCode.BOOTING) is True
    assert status.set(10) is False
    assert status.set(SystemCode.IDLE) is True
    assert status.get() == 500 and status.range() is StatusRange.FINISHED
    assert [(e.topic, e.source, dict(e.data)) for e in events] == [
        ("status.system", "core", {"code": 10, "previous": 0}),
        ("status.system", "core", {"code": 500, "previous": 10}),
    ]


def test_system_codes_match_the_language_file():
    assert (SystemCode.CHECKING_DONGLE, SystemCode.INIT_WATCHDOG, SystemCode.INITIALIZED) == (11, 59, 100)
    assert (SystemCode.MESA_RUNNING, SystemCode.LOW_LEVEL_RUNNING, SystemCode.IDLE) == (203, 204, 500)
    assert (SystemCode.EXIT, SystemCode.RESET, SystemCode.STOP, SystemCode.ABORT) == (800, 801, 802, 803)
    assert (SystemCode.NO_DONGLE, SystemCode.EXPIRED_DONGLE, SystemCode.PROFILE_ERROR) == (1513, 1515, 1604)


def test_description(translations: Translations):
    status = SystemStatus()
    assert status.description(translations) == "System initial state"
    status.set(SystemCode.STOP)
    assert status.description(translations) == "System is going to stop running function"
    assert status.description(translations, 1017) == "ERROR (High mag, Z)"
    assert status.description(translations, 1700) == "001700"     # missing: the code itself


def test_module_errors_are_described_by_the_module_text(translations: Translations):
    assert [system_description_code(c) for c in (1063, 1064, 1127, 1128, 1255, 1256, 1511, 1512, 1513)] == [
        1063, 1064, 1064, 1128, 1128, 1256, 1256, 1256, 1513]
    assert SystemStatus().description(translations, 1064 + 1 + 16) == "ERROR (Cameras/Z module)"
    assert SystemStatus().description(translations, 1128 + 64 + 4) == "ERROR (XY stages module)"


def test_hardware_bits_follow_the_diagram():
    assert [int(b) for b in HardwareBit] == [1, 2, 4, 8, 16, 32, 64, 128, 256]
    assert hardware_bits_of(1017) == HardwareBit.HIGH_MAG | HardwareBit.Z
    assert hardware_bits_of(1063) == (HardwareBit.HIGH_MAG | HardwareBit.LOW_MAG | HardwareBit.X | HardwareBit.Y
                                      | HardwareBit.Z | HardwareBit.MONOCHROMATOR)
    assert hardware_bits_of(500) == HardwareBit(0) and hardware_bits_of(1000) == HardwareBit(0)
    assert hardware_bits_of(1513) == HardwareBit(0)                # boot error, not a bit field


def test_hardware_failed_sets_and_clears_bits(translations: Translations):
    events: list[Event] = []
    status = SystemStatus(publish=events.append, initial=SystemCode.IDLE)
    assert status.hardware_failed(HardwareBit.HIGH_MAG) == 1001
    assert status.hardware_failed(HardwareBit.Z, True) == 1017
    assert status.hardware_failed(HardwareBit.Z, True) == 1017      # no change, no event
    assert status.hardware_bits == HardwareBit.HIGH_MAG | HardwareBit.Z
    assert status.description(translations) == "ERROR (High mag, Z)"
    assert status.hardware_failed(HardwareBit.HIGH_MAG, False) == 1016
    assert status.hardware_failed(HardwareBit.Z, False) == 500      # back to the code before the first failure
    assert status.hardware_failed(HardwareBit.X, False) == 500      # nothing to clear
    assert [e.data["code"] for e in events] == [1001, 1017, 1016, 500]


def test_module_bits_and_set_while_failed():
    status = SystemStatus(initial=SystemCode.IDLE)
    assert status.hardware_failed(HardwareBit.XY_MODULE | HardwareBit.X | HardwareBit.Y) == 1140
    assert status.hardware_failed(HardwareBit.MONOCHROMATOR_MODULE) == 1396
    status.set(SystemCode.RESET)                                    # a Set replaces the field, as in LabVIEW
    assert status.hardware_bits == HardwareBit(0)
    assert status.hardware_failed(HardwareBit.CAMERAS_Z_MODULE) == 1064
    status.set(SystemCode.INITIALIZED)
    status.set(1064)                                                # set directly: no saved code to restore
    assert status.hardware_failed(HardwareBit.CAMERAS_Z_MODULE, False) == SystemCode.IDLE
    assert status.hardware_failed(HardwareBit(511)) == 1511


def test_events_follow_the_order_of_changes_across_threads():
    events: list[Event] = []
    status = SystemStatus(publish=events.append)

    def worker(base: int) -> None:
        for i in range(200):
            status.set(base + i % 50)

    threads = [threading.Thread(target=worker, args=(b,)) for b in (10, 201, 600, 800)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    previous = 0
    for event in events:
        assert event.data["previous"] == previous
        previous = event.data["code"]
    assert previous == status.code


# ============================================================================================== measurement statuses


def test_function_status_ids(translations: Translations):
    events: list[Event] = []
    mesa = FunctionStatusID("mesa", publish=events.append)
    assert mesa.codes is MesaStatus and mesa.domain == "mesa" and mesa.topic == "status.mesa"
    mesa.set(MesaStatus.SEARCH_OBJECT)
    assert mesa.description(translations) == "Search Object"
    assert mesa.description(translations, MesaStatus.INITIAL) == "Function initial state"
    assert [(e.topic, e.data["code"]) for e in events] == [("status.mesa", 404)]
    assert FunctionStatusID("tilt").topic == "status.tilt" and FunctionStatusID("bowing").codes is BowingStatus
    with pytest.raises(ValueError):
        FunctionStatusID("autofocus")


@pytest.mark.parametrize("enum", [MesaStatus, TiltStatus, BowingStatus, SystemCode])
def test_code_enums_have_no_aliases_and_valid_codes(enum: type[IntEnum]):
    assert len(list(enum)) == len(enum.__members__)                # an IntEnum alias would hide a typo
    for member in enum:
        status_range(int(member))


def test_measurement_codes_match_the_language_files():
    assert FUNCTION_CODES == {"mesa": MesaStatus, "tilt": TiltStatus, "bowing": BowingStatus}
    assert (MesaStatus.INITIALIZING, MesaStatus.DETECT_MESA_5, MesaStatus.DATA_LOGGING, MesaStatus.EXITING) == (
        401, 418, 436, 499)
    assert (MesaStatus.ERROR, MesaStatus.ERROR_MEASURE_MESA) == (1500, 1511)
    assert (TiltStatus.MEASURE_DUAL_OBJECTS, TiltStatus.EXITING, TiltStatus.ERROR_TILT_NOT_FOUND) == (308, 400, 1499)
    assert (BowingStatus.MOVE_ZW2, BowingStatus.STORE_ZC5, BowingStatus.DATA_LOGGING, BowingStatus.EXITING) == (
        203, 247, 248, 300)
    assert (BowingStatus.ERROR_AUTOFOCUS_ZC3, BowingStatus.ERROR_PARALLELISM_B_RANGE) == (1012, 1033)
    for enum in (MesaStatus, TiltStatus, BowingStatus):
        assert (enum.READY, enum.STARTING, enum.FINISHED, enum.STOPPING, enum.STOPPED) == (100, 200, 500, 800, 850)
