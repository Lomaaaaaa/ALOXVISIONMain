"""The monochromator: CM11x protocol (``alox.devices.cm11x``), emulator (``alox.devices.cm11x_sim``) and device
(``alox.devices.monochromator``).

Spec: ``docs/vision-core/monochromator.md``. Most tests run the protocol on the in-process emulator with a fake clock
(moves take no real time); the ``socket`` tests run pyserial's ``socket://`` transport against the emulator server, as
on the COM port.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from alox.config import TypedIni
from alox.devices.base import DeviceError, DeviceState
from alox.devices.cm11x import (
    BAD_ANSWER,
    INVALID_INPUT,
    NOT_OPEN,
    OPEN_FAILED,
    TIMEOUT,
    Cm11x,
    Cm11xError,
    Query,
    SerialLink,
    Units,
    command_text,
    decode_grooves,
    normal_port,
    speed_table,
    split16,
)
from alox.devices.cm11x_sim import (
    STATUS_TOO_LARGE,
    Cm11xEmulator,
    Cm11xEmulatorServer,
    Grating,
    SimOptions,
    build_sim_cm11x,
)
from alox.devices.monochromator import (
    NOT_REACHED,
    Monochromator,
    MonochromatorSettings,
    build_monochromator_backend,
    load_monochromator_settings,
)
from alox.events import Event


class FakeTime:
    """``sleep`` advances the clock at once; shared by the protocol and the emulator."""

    def __init__(self) -> None:
        self.now = 1000.0
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self.now

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.now += max(0.0, seconds)


@pytest.fixture
def clock() -> FakeTime:
    return FakeTime()


@pytest.fixture
def sim(clock: FakeTime) -> Iterator[tuple[Cm11x, Cm11xEmulator]]:
    cm, emulator = build_sim_cm11x(sleep=clock.sleep, monotonic=clock.monotonic)
    cm.open("COM2", 9600, 2.0)
    yield cm, emulator
    cm.close()


def sent(cm: Cm11x) -> list[bytes]:
    return [data for _t, direction, data in cm.frames if direction == "tx"]


# ============================================================================================== protocol
def test_query_sends_56_code_and_reads_data_status_end(sim: tuple[Cm11x, Cm11xEmulator]) -> None:
    cm, _ = sim
    assert cm.query(Query.SERIAL) == 12345
    assert sent(cm) == [bytes((56, 19))]
    assert [d for _t, direction, d in cm.frames if direction == "rx"] == [bytes((0x30, 0x39, 0, 24))]
    assert cm.last_status == 0


def test_goto_sends_16_high_low_and_waits_for_the_end_of_the_move(sim: tuple[Cm11x, Cm11xEmulator],
                                                                   clock: FakeTime) -> None:
    cm, _ = sim
    t0 = clock.now
    cm.goto(778)                                       # 0x030A: the low byte is a line feed
    assert sent(cm)[-1] == bytes((16, 0x03, 0x0A))
    assert clock.now - t0 == pytest.approx(778 / 500.0)   # emulator slew 500 nm/s
    assert cm.query(Query.POSITION) == 778


def test_commands_bytes() -> None:
    assert command_text(bytes((12, 0x02, 0xBC, 0x03, 0xB6))) == "SCAN 700 950"
    assert command_text(bytes((26, 2))) == "SELECT 2"
    assert command_text(bytes((255, 255, 255))) == "RESET"
    assert command_text(bytes((56, 2))) == "QUERY 2 (GROOVES)"
    assert split16(750) == (2, 238)
    with pytest.raises(Cm11xError) as info:
        split16(70000)                                 # LabVIEW wrapped it to 4464 (D9)
    assert info.value.code == INVALID_INPUT
    assert normal_port("4") == "COM4" and normal_port("com4") == "COM4"
    assert normal_port("socket://127.0.0.1:5556") == "socket://127.0.0.1:5556"


def test_every_command_byte_sequence(sim: tuple[Cm11x, Cm11xEmulator]) -> None:
    cm, _ = sim
    cm.set_units(Units.NANOMETERS)
    cm.set_speed(500)
    cm.select_grating(2)
    cm.select_grating(1)
    cm.calibrate(5)
    cm.scan(700, 710)
    cm.echo()
    assert sent(cm) == [bytes((50, 1)), bytes((13, 1, 0xF4)), bytes((26, 2)), bytes((26, 1)), bytes((18, 0, 5)),
                        bytes((12, 2, 0xBC, 2, 0xC6)), bytes((27,))]


def test_select_sends_one_data_byte(sim: tuple[Cm11x, Cm11xEmulator]) -> None:
    """D3: LabVIEW sent 26, high, low; the manual has 26, grating."""
    cm, emulator = sim
    cm.select_grating(2)
    assert emulator.received[-1] == bytes((26, 2))
    assert cm.query(Query.GROOVES) == 600 and cm.query(Query.GRATING) == 2


def test_status_bit_7_only_on_the_status_byte(clock: FakeTime) -> None:
    """D1: a serial number ≥ 32768 (data high byte ≥ 128) is not a refusal."""
    cm, _ = build_sim_cm11x(SimOptions(serial_number=40000), sleep=clock.sleep, monotonic=clock.monotonic)
    cm.open("COM2")
    assert cm.query(Query.SERIAL) == 40000


def test_a_refused_command_raises_with_the_status_byte(sim: tuple[Cm11x, Cm11xEmulator]) -> None:
    cm, emulator = sim
    with pytest.raises(Cm11xError) as info:
        cm.goto(1600)                                  # above the emulator's range at 1200 gr/mm
    assert info.value.code == STATUS_TOO_LARGE and info.value.status == 0xC0
    assert not info.value.link_failure
    emulator.fail_next(56, 0x80)
    with pytest.raises(Cm11xError) as info:
        cm.query(Query.POSITION)
    assert info.value.code == 0x80
    assert cm.query(Query.POSITION) == 0               # the link is still in step


def test_status_with_bit_7_clear_is_accepted_even_when_it_is_a_line_feed(sim: tuple[Cm11x, Cm11xEmulator]) -> None:
    """D2: a status byte 0x0A no longer ends the read early (no termination character)."""
    cm, emulator = sim
    emulator.status_next(0x0A)
    cm.goto(700)
    assert cm.last_status == 0x0A
    assert cm.query(Query.POSITION) == 700


def test_a_wrong_end_byte_is_a_bad_answer(sim: tuple[Cm11x, Cm11xEmulator]) -> None:
    cm, emulator = sim
    emulator.bad_end_next()
    with pytest.raises(Cm11xError) as info:
        cm.query(Query.SERIAL)
    assert info.value.code == BAD_ANSWER and info.value.link_failure


def test_no_answer_is_a_timeout(sim: tuple[Cm11x, Cm11xEmulator], clock: FakeTime) -> None:
    cm, emulator = sim
    emulator.drop_next_answer()
    t0 = clock.now
    with pytest.raises(Cm11xError) as info:
        cm.query(Query.SERIAL, timeout_s=1.5)
    assert info.value.code == TIMEOUT and info.value.link_failure
    assert clock.now - t0 == pytest.approx(1.5)


def test_input_is_flushed_before_each_command(sim: tuple[Cm11x, Cm11xEmulator]) -> None:
    cm, emulator = sim
    emulator.inject(b"\x00\x18\x0a")                   # a late answer / noise
    assert cm.query(Query.GRATING) == 1
    assert [d for _t, direction, d in cm.frames if direction == "drop"] == [b"\x00\x18\x0a"]


def test_not_open() -> None:
    with pytest.raises(Cm11xError) as info:
        Cm11x().query(0)
    assert info.value.code == NOT_OPEN


def test_groove_codes_of_query_15_and_17(sim: tuple[Cm11x, Cm11xEmulator]) -> None:
    cm, _ = sim
    assert cm.query_raw(Query.GROOVES_G1) == 3 and cm.query(Query.GROOVES_G1) == 1200
    assert cm.query(Query.GROOVES_G2) == 600
    assert [decode_grooves(c) for c in range(9)] == [3600, 2400, 1800, 1200, 600, 300, 150, 75, 75]


def test_speed_table() -> None:
    """D4: one rule for every grating (LabVIEW divided by 3 at 1800 gr/mm), levels 0..9 only."""
    assert speed_table(1200) == [1000, 500, 250, 125, 62, 31, 16, 8, 4, 2]
    assert speed_table(600) == [2000, 1000, 500, 250, 125, 62, 31, 16, 8, 4]   # 62 Å/s at level 5: the manual
    assert speed_table(1800)[:3] == [667, 333, 167]
    assert len(speed_table(2400)) == 10


def test_reset_polls_echo_until_the_instrument_answers(sim: tuple[Cm11x, Cm11xEmulator]) -> None:
    """D5: the loop ends on the echo (LabVIEW stopped when it did not come)."""
    cm, emulator = sim
    cm.goto(500)
    seconds = cm.reset(echo_timeout_s=30.0)
    assert 2.0 <= seconds < 4.0                        # emulator RESET lasts 2 s
    assert emulator.commands.count("RESET") == 1
    assert cm.query(Query.POSITION) == 0


def test_reset_without_echo_times_out(clock: FakeTime) -> None:
    cm, _ = build_sim_cm11x(SimOptions(reset_s=100.0), sleep=clock.sleep, monotonic=clock.monotonic)
    cm.open("COM2")
    with pytest.raises(Cm11xError) as info:
        cm.reset(echo_timeout_s=5.0)
    assert info.value.code == TIMEOUT


def test_reset_ignores_a_reply_after_reset(clock: FakeTime) -> None:
    cm, _ = build_sim_cm11x(SimOptions(reset_reply=b"\x00\x18"), sleep=clock.sleep, monotonic=clock.monotonic)
    cm.open("COM2")
    cm.reset()
    assert cm.query(Query.SERIAL) == 12345


# ============================================================================================== device
@pytest.fixture
def events() -> list[Event]:
    return []


@pytest.fixture
def make_wl(clock: FakeTime, events: list[Event]) -> Iterator[Callable[..., tuple[Monochromator, Cm11xEmulator]]]:
    devices: list[Monochromator] = []

    def factory(*, options: SimOptions | None = None, settings: MonochromatorSettings | None = None,
                open_: bool = True, **kwargs: Any) -> tuple[Monochromator, Cm11xEmulator]:
        backend = build_monochromator_backend(simulate=True, sim_options=options, sleep=clock.sleep,
                                              monotonic=clock.monotonic)
        wl = Monochromator(backend, settings, publish=events.append, reconnect_delay_s=0.0, sleep=clock.sleep,
                           **kwargs)
        devices.append(wl)
        if open_:
            wl.open(timeout=10)
        return wl, backend.emulator                     # type: ignore[attr-defined]

    yield factory
    for wl in devices:
        wl.shutdown(timeout=5)


def topics(events: list[Event], prefix: str = "device.wl.") -> list[str]:
    return [e.topic[len(prefix):] for e in events if e.topic.startswith(prefix)]


def test_open_checks_echo_serial_units_grating_and_position(make_wl: Callable[..., Any],
                                                             events: list[Event]) -> None:
    wl, emulator = make_wl(options=SimOptions(position_nm=650.0))
    assert wl.state == DeviceState.READY
    assert emulator.commands == ["ECHO", "QUERY 19 (SERIAL)", "QUERY 14 (UNITS)", "QUERY 2 (GROOVES)",
                                 "QUERY 3 (BLAZE)", "QUERY 4 (GRATING)", "QUERY 0 (POSITION)"]
    assert wl.serial_number == "12345" and wl.last_position == 650 and wl.range_nm == (0, 1500)
    opened = next(e for e in events if e.topic == "device.wl.opened")
    assert opened.data["grooves"] == 1200 and opened.data["position_nm"] == 650


def test_open_puts_the_instrument_in_nanometres(make_wl: Callable[..., Any], events: list[Event]) -> None:
    wl, emulator = make_wl(options=SimOptions(units=2, position_nm=700.0))
    assert "UNITS 1" in emulator.commands and emulator.units == 1
    assert wl.last_position == 700
    assert "units_set" in topics(events)


def test_open_fails_when_nothing_answers(make_wl: Callable[..., Any], clock: FakeTime) -> None:
    """D8: not ready (and no serial number) when the ECHO is not answered."""
    wl, emulator = make_wl(open_=False)
    emulator.drop_next_answer()
    with pytest.raises(DeviceError) as info:
        wl.open(timeout=10)
    assert info.value.code == TIMEOUT
    assert wl.state == DeviceState.FAULT and wl.serial_number == ""


def test_open_fails_on_a_missing_port(make_wl: Callable[..., Any]) -> None:
    wl, emulator = make_wl(open_=False)
    emulator.port_missing = True
    with pytest.raises(DeviceError) as info:
        wl.open(timeout=10)
    assert info.value.code == OPEN_FAILED


def test_goto_moves_confirms_and_publishes(make_wl: Callable[..., Any], events: list[Event]) -> None:
    wl, emulator = make_wl()
    assert wl.goto(750) == 750
    assert emulator.commands[-2:] == ["GOTO 750", "QUERY 0 (POSITION)"]
    assert wl.last_position == 750
    moved = [e for e in events if e.topic == "device.wl.position"]
    assert moved[-1].data == {"position_nm": 750, "target_nm": 750}
    assert topics(events)[-2:] == ["move_started", "position"]


@pytest.mark.parametrize("target", [1501, -1, 750.5, float("nan"), "750", True])
def test_goto_refuses_bad_targets_before_sending(make_wl: Callable[..., Any], target: Any) -> None:
    wl, emulator = make_wl()
    before = len(emulator.received)
    with pytest.raises(DeviceError) as info:
        wl.goto(target)
    assert info.value.code == INVALID_INPUT
    assert len(emulator.received) == before


def test_goto_accepts_a_whole_float_and_the_range_follows_the_grating(make_wl: Callable[..., Any]) -> None:
    wl, emulator = make_wl()
    assert wl.goto(800.0) == 800
    wl.select_grating(2)                               # 600 gr/mm: range doubles
    assert wl.range_nm == (0, 3000) and wl.grating() == 2
    assert bytes((26, 2)) in emulator.received
    assert wl.goto(2000) == 2000


def test_max_nm_setting_overrides_the_grating_range(make_wl: Callable[..., Any]) -> None:
    wl, _ = make_wl(settings=MonochromatorSettings(max_nm=1000, min_nm=400))
    assert wl.range_nm == (400, 1000)
    with pytest.raises(DeviceError):
        wl.goto(1100)
    with pytest.raises(DeviceError):
        wl.goto(300)


def test_a_move_that_stops_short_raises_instead_of_looping(make_wl: Callable[..., Any]) -> None:
    """D11: one check after the move, error 9041 (LabVIEW polled until equal, forever)."""
    wl, emulator = make_wl()
    emulator.stop_short_next(2)
    with pytest.raises(DeviceError) as info:
        wl.goto(900)
    assert info.value.code == NOT_REACHED
    assert wl.last_position == 898                     # the confirmed position, not the target
    assert emulator.commands.count("GOTO 900") == 1
    assert wl.state == DeviceState.READY


def test_tolerance_accepts_a_close_position(make_wl: Callable[..., Any]) -> None:
    wl, emulator = make_wl(settings=MonochromatorSettings(on_target_tolerance_nm=1))
    emulator.stop_short_next(1)
    assert wl.goto(900) == 899


def test_groove_blaze_grating_do_not_touch_the_last_position(make_wl: Callable[..., Any]) -> None:
    """D6: LabVIEW's manager stored them as the last known position."""
    wl, _ = make_wl()
    wl.goto(760)
    assert (wl.groove(), wl.blaze(), wl.grating(), wl.speed(), wl.units()) == (1200, 750, 1, 1000, Units.NANOMETERS)
    assert wl.last_position == 760


def test_a_timeout_raises_faults_and_reconnects(make_wl: Callable[..., Any], events: list[Event]) -> None:
    """D7: a timeout is an error (9403) and the next call re-opens the link."""
    wl, emulator = make_wl()
    wl.goto(700)
    emulator.drop_next_answer()
    with pytest.raises(DeviceError) as info:
        wl.position()
    assert info.value.code == TIMEOUT
    assert wl.state == DeviceState.FAULT and wl.last_position is None
    assert wl.position() == 700
    assert wl.state == DeviceState.READY
    assert "reconnected" in topics(events)


def test_a_lost_goto_answer_is_an_error_not_a_cached_target(make_wl: Callable[..., Any]) -> None:
    wl, emulator = make_wl(settings=MonochromatorSettings(move_timeout_ms=3000))
    emulator.drop_next_answer()
    with pytest.raises(DeviceError) as info:
        wl.goto(1200)
    assert info.value.code == TIMEOUT
    assert wl.last_position is None


def test_a_refused_goto_keeps_the_device_ready(make_wl: Callable[..., Any]) -> None:
    wl, emulator = make_wl()
    emulator.fail_next(16, 0x80)
    with pytest.raises(DeviceError) as info:
        wl.goto(700)
    assert info.value.code == 0x80
    assert wl.state == DeviceState.READY
    assert wl.goto(700) == 700


def test_goto_without_wait_then_wait_end_move(make_wl: Callable[..., Any], events: list[Event]) -> None:
    wl, emulator = make_wl()
    assert wl.goto(820, wait=False) == 820
    wl.wait_end_move(timeout=10)
    assert wl.last_position == 820
    emulator.stop_short_next(5)
    wl.goto(830, wait=False)
    with pytest.raises(DeviceError) as info:
        wl.wait_end_move(timeout=10)
    assert info.value.code == NOT_REACHED
    assert "move_failed" in topics(events)
    wl.wait_end_move(timeout=10)                       # the failure is reported once


def test_status_snapshot(make_wl: Callable[..., Any]) -> None:
    wl, _ = make_wl()
    wl.goto(710)
    s = wl.status()
    assert (s.state, s.position_nm, s.units, s.grating, s.grooves, s.blaze_nm, s.speed) == \
        ("ready", 710, 1, 1, 1200, 750, 1000)
    assert s.serial_number == "12345" and s.range_nm == (0, 1500)


def test_status_when_closed(make_wl: Callable[..., Any]) -> None:
    wl, _ = make_wl(open_=False)
    wl.start()
    s = wl.status(timeout=2)
    assert s.state == "closed" and s.position_nm is None


def test_speed_levels(make_wl: Callable[..., Any]) -> None:
    wl, emulator = make_wl()
    assert wl.set_speed_level(0) == 1000
    assert wl.set_speed_level(9) == 2
    assert emulator.speed == 2
    with pytest.raises(DeviceError) as info:
        wl.set_speed_level(10)                         # D4: LabVIEW sent speed 0
    assert info.value.code == INVALID_INPUT


def test_reset_engineering(make_wl: Callable[..., Any], events: list[Event]) -> None:
    wl, _ = make_wl()
    wl.goto(900)
    seconds = wl.reset()
    assert seconds >= 2.0 and wl.last_position == 0
    assert "reset" in topics(events)


def test_scan_and_calibrate_engineering(make_wl: Callable[..., Any]) -> None:
    wl, emulator = make_wl()
    assert wl.scan(700, 720) == 720
    assert "SCAN 700 720" in emulator.commands
    wl.calibrate(725)
    assert wl.last_position == 725 and "CALIBRATE 725" in emulator.commands
    assert wl.query(Query.GRATINGS) == 2


def test_serial_and_echo(make_wl: Callable[..., Any]) -> None:
    wl, emulator = make_wl(options=SimOptions(serial_number=50000))
    assert wl.serial() == "50000"
    wl.echo()
    assert emulator.commands[-2:] == ["QUERY 19 (SERIAL)", "ECHO"]


def test_load_settings_from_alox_ini() -> None:
    ini = TypedIni.parse("[hardware.monochromator]\nport_I32 = 505\nbaudrate_I32 = 0\ncomport_I32 = 1\n"
                         "heartbeat_I32 = 60000\ntimeout_I32 = 5000\nmaxfails_U32 = 10\nwaitposition_U32 = 500\n"
                         "watchdog_BOOL = TRUE\n")
    s = load_monochromator_settings(ini)
    assert (s.com, s.baudrate, s.timeout_ms, s.wait_position_ms, s.move_timeout_ms) == (1, 9600, 5000, 500, 30000)
    assert s.port_name == "COM1"
    warnings: list[str] = []
    s = load_monochromator_settings(TypedIni.parse("[hardware.monochromator]\nbaudrate_I32 = 7\n"),
                                    warn=warnings.append, port="socket://127.0.0.1:1")
    assert s.baudrate == 9600 and warnings and s.port_name == "socket://127.0.0.1:1"
    s = load_monochromator_settings(TypedIni.parse(""))
    assert (s.com, s.baudrate, s.timeout_ms, s.wait_position_ms) == (2, 9600, 5000, 0)


# ============================================================================================== over socket://
@pytest.fixture
def server() -> Iterator[Cm11xEmulatorServer]:
    with Cm11xEmulatorServer(SimOptions(slew_nm_per_s=20000.0, reset_s=0.2,
                                        gratings=(Grating(1200, 750), Grating(600, 1250)))) as srv:
        yield srv


def test_serial_link_over_socket(server: Cm11xEmulatorServer) -> None:
    cm = Cm11x()
    cm.open(server.url, 9600, 2.0)
    try:
        cm.echo()
        assert cm.query(Query.SERIAL) == 12345
        cm.goto(778)                                   # low byte 0x0A over a real pyserial byte stream
        assert cm.query(Query.POSITION) == 778
        server.emulator.status_next(0x0A)
        cm.goto(266)                                   # 0x010A, and a status byte 0x0A
        assert cm.query(Query.POSITION) == 266
        assert cm.reset(poll_s=0.05, answer_s=0.1) >= 0.2
    finally:
        cm.close()


def test_device_over_socket_reconnects_after_a_disconnect(server: Cm11xEmulatorServer, events: list[Event]) -> None:
    settings = MonochromatorSettings(port=server.url, timeout_ms=500)
    wl = Monochromator(Cm11x(), settings, publish=events.append, reconnect_delay_s=0.0)
    try:
        wl.open(timeout=10)
        assert wl.goto(750) == 750
        server.disconnect()
        with pytest.raises(DeviceError) as info:
            wl.position(timeout=10)
        assert info.value.code in (TIMEOUT, -2, -3)
        assert wl.state == DeviceState.FAULT
        assert wl.position(timeout=10) == 750
        assert server.connections == 2
    finally:
        wl.shutdown(timeout=5)


def test_serial_link_open_failure() -> None:
    with pytest.raises(Cm11xError) as info:
        SerialLink("socket://127.0.0.1:1", 9600, 0.5)
    assert info.value.code == OPEN_FAILED
