"""GcsChain / ChainLink against FakeDaisyChainServer: daisy-chain addressing, answer routing and checks, single-byte
command mapping, two PiSerialStage + ZController pairs on one port from two threads, the shared port's life cycle and
link errors (alox.devices.gcs_chain, alox.devices.fake_chain)."""

from __future__ import annotations

import collections
import itertools
import re
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace
from typing import Any

import pytest
from zstage.fake_probe import ScriptedPeer

from alox.devices.fake_chain import FakeDaisyChainServer, chain_identity
from alox.devices.gcs_chain import ChainLink, GcsChain, parse_register
from aloxcam.errors import Code, StageError
from aloxcam.settings import SimulationOptions, StageOptions, ZStageSettings
from aloxcam.zstage.controller import ZController
from aloxcam.zstage.fake_c663 import FakeC663
from aloxcam.zstage.gcs_serial import GcsSerialLink
from aloxcam.zstage.pi_serial import PiSerialStage

BAUD = 115200
TIMEOUT_S = 0.25
READY, BUSY = chr(0xB1), chr(0xB0)
FAST = SimulationOptions(stage_reference_s=0.01, stage_min_mm=0.0, stage_max_mm=25.0)


def device_options(**changes: Any) -> dict[int, SimulationOptions]:
    return {a: replace(FAST, stage_identity=chain_identity(a), **changes) for a in (1, 2)}


def unbounded_link(port: str, baudrate: int, timeout_s: float) -> GcsSerialLink:
    """A GcsSerialLink whose raw log keeps every frame (GcsSerialLink keeps 512)."""
    link = GcsSerialLink(port, baudrate, timeout_s)
    link.frames = collections.deque()
    return link


@pytest.fixture
def make_server() -> Iterator[Callable[..., FakeDaisyChainServer]]:
    servers: list[FakeDaisyChainServer] = []

    def factory(**kwargs: Any) -> FakeDaisyChainServer:
        kwargs.setdefault("options", device_options())
        kwargs.setdefault("dh_running", False)
        server = FakeDaisyChainServer(**kwargs).start()
        servers.append(server)
        return server

    yield factory
    for server in servers:
        server.close()


@pytest.fixture
def server(make_server: Callable[..., FakeDaisyChainServer]) -> FakeDaisyChainServer:
    return make_server()


@pytest.fixture
def make_chain() -> Iterator[Callable[..., GcsChain]]:
    chains: list[GcsChain] = []

    def factory(url: str, **kwargs: Any) -> GcsChain:
        kwargs.setdefault("link_factory", unbounded_link)
        chain = GcsChain(url, BAUD, TIMEOUT_S, **kwargs)
        chains.append(chain)
        return chain

    yield factory
    for chain in chains:
        chain.close()


@pytest.fixture
def chain(server: FakeDaisyChainServer, make_chain: Callable[..., GcsChain]) -> GcsChain:
    return make_chain(server.url)


def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def assert_not_interleaved(frames: Any) -> list[int]:
    """Every query on the raw log is followed by its own answer before any other frame; returns the device address
    of every frame sent."""
    pending: int | None = None
    senders: list[int] = []
    for direction, data in frames:
        if direction == "drop":
            continue
        if direction == "tx":
            assert pending is None, f"{data!r} sent before device {pending} answered"
            match = re.match(rb"(\d+) (?:0 )?(.*)", data, re.DOTALL)
            assert match is not None, f"unaddressed frame {data!r}"
            address, body = int(match.group(1)), match.group(2)
            senders.append(address)
            if body in (b"\x05", b"\x07") or body.split(b" ")[0].rstrip(b"\n").endswith(b"?"):
                pending = address
        else:
            assert pending is not None, f"answer {data!r} without a query"
            assert data.startswith(b"0 %d " % pending), f"answer {data!r} to a query of device {pending}"
            pending = None
    assert pending is None
    return senders


# ---------------------------------------------------------------------------------------------- addressing
@pytest.mark.parametrize(("mode", "sent"), [("target_only", b"2 POS? 1\n"), ("target_sender", b"2 0 POS? 1\n")])
def test_commands_are_addressed_and_answers_stripped(make_server: Callable[..., FakeDaisyChainServer],
                                                     make_chain: Callable[..., GcsChain], mode: str,
                                                     sent: bytes) -> None:
    server = make_server(address_mode=mode)                  # a fake that understands only this form
    chain = make_chain(server.url, address_mode=mode)
    with chain.view(2) as y:
        assert y.query("POS? 1") == "1=0.000000"
        y.send("VEL 1 3.0000")
        assert y.query("VEL? 1") == "1=3.000000"
        assert list(y.frames)[:2] == [("tx", sent), ("rx", b"0 2 1=0.000000\n")]
    assert server.chain.device(2).frames == ["POS? 1", "VEL 1 3.0000", "VEL? 1"]
    assert server.chain.device(1).frames == []
    assert list(chain.frames)[:2] == [("tx", sent), ("rx", b"0 2 1=0.000000\n")]


def test_address_mode_mismatch_times_out(make_server: Callable[..., FakeDaisyChainServer],
                                         make_chain: Callable[..., GcsChain]) -> None:
    server = make_server(address_mode="target_only")         # this controller does not take a sender address
    with make_chain(server.url, address_mode="target_sender").view(2) as y, pytest.raises(StageError) as info:
        y.query("POS? 1")
    assert info.value.code == Code.PI_TIMEOUT
    assert info.value.source.endswith(f"baud {BAUD}, device 2)")
    assert server.chain.device(2).model.error_register == 2   # "0" was taken for an unknown command


def test_answers_are_routed_to_the_right_controller(server: FakeDaisyChainServer, chain: GcsChain) -> None:
    x, y = chain.view(1), chain.view(2)
    assert x.query("*IDN?") == chain_identity(1)
    assert y.query("*IDN?") == chain_identity(2)
    y.send("VEL 1 3.0000")
    assert (x.query("VEL? 1"), y.query("VEL? 1")) == ("1=10.000000", "1=3.000000")
    assert server.chain.frames == ["1 *IDN?", "2 *IDN?", "2 VEL 1 3.0000", "1 VEL? 1", "2 VEL? 1"]
    assert [f for f in x.frames] == [("tx", b"1 *IDN?\n"), ("rx", f"0 1 {chain_identity(1)}\n".encode()),
                                     ("tx", b"1 VEL? 1\n"), ("rx", b"0 1 1=10.000000\n")]


@pytest.mark.parametrize(("sender", "text"), [(1, "answer from device 1, not from device 2"),
                                              (None, "answer without the address prefix")])
def test_an_answer_from_another_device_is_rejected(server: FakeDaisyChainServer, chain: GcsChain,
                                                   sender: int | None, text: str) -> None:
    y = chain.view(2)
    server.chain.answer_as(2, sender)
    with pytest.raises(StageError) as info:
        y.query("POS? 1")
    assert info.value.code == Code.PI_RECEIVE_ERROR
    assert text in info.value.source and "device 2" in info.value.source
    assert y.query("POS? 1") == "1=0.000000"                 # the next exchange is fine
    server.chain.answer_as(2, 1)
    with pytest.raises(StageError, match="answer from device 1"):
        y.query_char(0x07)


def test_multi_line_and_empty_answers(server: FakeDaisyChainServer, chain: GcsChain) -> None:
    y = chain.view(2)
    plain = FakeC663(FAST, dh_running=False).feed(b"MAC? DH\n")[:-1].decode("cp1252")
    assert y.query("MAC? DH") == plain and plain.count(" \n") > 3
    assert list(y.frames)[-1][1].count(b"0 2 ") == 1         # only the first line carries the addresses
    assert y.query("RMC?") == ""                             # "0 2 \n": empty, not a continuation line
    assert list(y.frames)[-1] == ("rx", b"0 2 \n")


def test_unaddressed_and_broadcast_commands_on_the_fake(server: FakeDaisyChainServer) -> None:
    with GcsSerialLink(server.url, BAUD, TIMEOUT_S) as link:
        assert link.query("*IDN?") == chain_identity(1)      # no address: device 1, plain answer
        link.send("255 VEL 1 4.0000")                        # broadcast: every device, no answer
        assert server.chain.wait_processed(len(b"*IDN?\n255 VEL 1 4.0000\n"), 2.0)
        assert [server.chain.device(a).model.velocity for a in (1, 2)] == [4.0, 4.0]
        with pytest.raises(StageError) as info:
            link.query("3 *IDN?")                            # nobody at address 3
        assert info.value.code == Code.PI_TIMEOUT


# ---------------------------------------------------------------------------------------------- single bytes
@pytest.mark.parametrize("ready_lf", [True, False])
def test_single_byte_commands_addressed(make_server: Callable[..., FakeDaisyChainServer],
                                        make_chain: Callable[..., GcsChain], ready_lf: bool) -> None:
    server = make_server(ready_lf=ready_lf, options=device_options(stage_reference_s=5.0))
    y = make_chain(server.url).view(2)
    assert (y.query_char(0x05), y.query_char(0x07)) == ("0", READY)
    assert list(y.frames) == [("tx", b"2 \x05"), ("rx", b"0 2 0\n"),
                              ("tx", b"2 \x07"), ("rx", b"0 2 \xb1" + (b"\n" if ready_lf else b""))]
    y.send("SVO 1 1")
    y.send("FNL 1")                                          # a 5 s reference move
    assert (y.query_char(0x07), y.query_char(0x05), y.query_char(0x07)) == (BUSY, "1", BUSY)
    y.send_char(0x18)
    assert y.query("ERR?") == "10"                           # #24 stopped device 2 only
    assert server.chain.device(2).frames[-3:] == ["#7", "#24", "ERR?"]
    assert server.chain.device(1).frames == []
    assert "2 #24" in server.chain.frames


def test_addressed_ready_answer_with_a_late_lf() -> None:
    def respond(frame: bytes) -> Any:
        if frame == b"\x07":
            return [b"0 2 \xb1", b"\n"]                      # the LF 20 ms after the byte
        if frame.endswith(b"POS? 1\n"):
            return b"0 2 1=1.5\n"
        if frame.endswith(b"RMC?\n"):
            return b"0 2\n"                                  # an empty answer without the trailing space
        return None

    with ScriptedPeer(respond) as peer, GcsChain(peer.url, BAUD, 1.0) as chain:
        y = chain.view(2)
        assert y.query_char(0x07) == READY
        assert y.query("POS? 1") == "1=1.5"                  # the late LF is not taken for the answer
        assert y.query("RMC?") == ""
        assert ("drop", b"\n") in list(chain.frames)


def test_single_byte_commands_as_text_equivalents(make_server: Callable[..., FakeDaisyChainServer],
                                                  make_chain: Callable[..., GcsChain]) -> None:
    server = make_server(char_mode="text_equivalents", options=device_options(stage_reference_s=5.0))
    y = make_chain(server.url, char_mode="text_equivalents").view(2)
    assert (y.query_char(0x05), y.query_char(0x07)) == ("0", READY)
    assert list(y.frames)[:2] == [("tx", b"2 SRG? 1 1\n"), ("rx", b"0 2 1 1=0x0\n")]
    y.send("SVO 1 1")
    y.send("FNL 1")
    assert (y.query_char(0x05), y.query_char(0x07)) == ("1", BUSY)
    assert parse_register(y.query("SRG? 1 1")) == 0x7000     # servo, in motion, referencing
    y.send_char(0x18)
    assert list(y.frames)[-1] == ("tx", b"2 STP\n")
    assert y.query("ERR?") == "10"
    assert server.chain.device(2).model.is_moving() is False
    assert "2 STP" in server.chain.frames and server.chain.device(1).frames == []


def test_addressed_single_bytes_time_out_where_only_text_equivalents_work(
        make_server: Callable[..., FakeDaisyChainServer], make_chain: Callable[..., GcsChain]) -> None:
    server = make_server(char_mode="text_equivalents")
    y = make_chain(server.url).view(2)                       # chain default: addressed single bytes
    with pytest.raises(StageError) as info:
        y.query_char(0x05)
    assert info.value.code == Code.PI_TIMEOUT
    assert y.query("POS? 1") == "1=0.000000"                 # text commands still work


# ---------------------------------------------------------------------------------------------- two controllers
def test_two_controllers_set_up_and_move_concurrently(server: FakeDaisyChainServer, chain: GcsChain) -> None:
    options = StageOptions(port_map={"COM4": server.url})
    settings = ZStageSettings(com=4, baudrate=BAUD, axis=1, speed=50.0)
    controllers = {a: ZController(PiSerialStage(link_factory=chain.link_factory(a)), options) for a in (1, 2)}
    targets = {1: (1.0, 2.0), 2: (3.0, 1.5)}
    results: dict[int, list[float]] = {}
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def run(address: int) -> None:
        try:
            ctl = controllers[address]
            barrier.wait(2.0)
            ctl.setup(settings)                              # purge, *IDN?, SAI?, ..., FNL, #7 polls, VEL + ERR?
            for target in targets[address]:
                ctl.mov(target)
                ctl.general_wait(polling_ms=5, timeout_s=2.0)
                ctl.check_error()
                results.setdefault(address, []).append(ctl.qpos())
        except (StageError, OSError, RuntimeError, AssertionError) as exc:   # reported by the main thread
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(a,)) for a in (1, 2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5.0)
    assert not errors, errors
    assert results == {1: [1.0, 2.0], 2: [3.0, 1.5]}
    assert [controllers[a].session.identity for a in (1, 2)] == [chain_identity(1), chain_identity(2)]
    assert [server.chain.device(a).model.position for a in (1, 2)] == [2.0, 1.5]
    senders = assert_not_interleaved(chain.frames)
    assert sum(1 for a, b in itertools.pairwise(senders) if a != b) >= 2   # really concurrent
    assert chain.open_count == 1 and server.connections == 1
    for address in (1, 2):
        assert server.chain.device(address).frames[:3] == ["XXX", "*idn?", "err?"]
    controllers[1].close()
    assert chain.is_open and chain.views == (2,)
    controllers[2].close()
    assert not chain.is_open


def test_chain_lock_serialises_a_background_task(chain: GcsChain) -> None:
    y = chain.view(2)
    done = threading.Event()
    worker = threading.Thread(target=lambda: (y.query("POS? 1"), done.set()))
    with chain.lock:                                         # e.g. the wafer-limit supervision's POS? X, POS? Y, STP
        worker.start()
        assert not done.wait(0.1)
    worker.join(2.0)
    assert done.is_set()


# ---------------------------------------------------------------------------------------------- port life cycle
def test_shared_port_opened_once_and_closed_with_the_last_view(server: FakeDaisyChainServer,
                                                                chain: GcsChain) -> None:
    assert not chain.is_open and chain.open_count == 0
    x = chain.view(1)
    y = chain.view(2)
    assert chain.is_open and chain.open_count == 1 and chain.views == (1, 2)
    assert wait_until(lambda: server.connections == 1)
    x.close()
    x.close()                                                # idempotent
    assert chain.is_open and y.query("POS? 1") == "1=0.000000"
    with pytest.raises(StageError) as info:
        x.query("POS? 1")
    assert (info.value.code, info.value.source) == (Code.PI_NOT_CONNECTED, "send: not connected")
    y.close()
    assert not chain.is_open and chain.views == ()
    assert wait_until(lambda: not server.connected)
    assert chain.view(2).query("*IDN?") == chain_identity(2)
    assert chain.open_count == 2 and server.connections == 2


def test_link_factory_checks_port_and_baud_rate(server: FakeDaisyChainServer, chain: GcsChain) -> None:
    factory = chain.link_factory(1)
    for port, baud in (("COM9", BAUD), (server.url, 9600)):
        with pytest.raises(StageError) as info:
            factory(port, baud, 5.0)
        assert info.value.code == Code.PI_OPEN_PORT and "device 1" in info.value.source
    assert not chain.is_open
    view = factory(server.url, BAUD, 5.0)
    assert isinstance(view, ChainLink) and view.address == 1 and view.is_open
    for address in (0, 17, 255, True, "1"):
        with pytest.raises(ValueError):
            chain.link_factory(address)                      # type: ignore[arg-type]
    with pytest.raises(ValueError):
        GcsChain(server.url, BAUD, 1.0, address_mode="sender_only")
    with pytest.raises(ValueError):
        GcsChain(server.url, BAUD, 1.0, char_mode="broadcast")


def test_unopenable_port() -> None:
    chain = GcsChain("socket://127.0.0.1:1", BAUD, TIMEOUT_S)
    with pytest.raises(StageError) as info:
        chain.view(1)
    assert info.value.code == Code.PI_OPEN_PORT and not chain.is_open and chain.views == ()


def test_view_has_the_gcs_serial_link_interface(chain: GcsChain) -> None:
    view = chain.view(1)
    public = {name for name in dir(GcsSerialLink) if not name.startswith("_")}
    assert public <= set(dir(view))
    assert (view.port, view.baudrate, view.timeout_s, view.encoding) == (chain.port, BAUD, TIMEOUT_S, "cp1252")
    log: collections.deque[tuple[str, bytes]] = collections.deque()
    view.frames = log                                        # the check tool replaces frames to record them
    view.query("*IDN?")
    assert [d for d, _ in log] == ["tx", "rx"]


def test_discard_input(server: FakeDaisyChainServer, chain: GcsChain) -> None:
    y = chain.view(2)
    y.send("POS? 1")                                         # the answer is not read
    assert wait_until(lambda: chain.link is not None and chain.link._serial.in_waiting > 0)
    assert y.discard_input() == b"0 2 1=0.000000\n"
    assert list(y.frames)[-1] == ("drop", b"0 2 1=0.000000\n")


# ---------------------------------------------------------------------------------------------- link errors
def test_a_timeout_reaches_only_its_caller(server: FakeDaisyChainServer, chain: GcsChain) -> None:
    x, y = chain.view(1), chain.view(2)
    server.chain.drop_next_answer(2)
    x_answers: list[str] = []
    x_errors: list[BaseException] = []
    y_done = threading.Event()

    def poll_x() -> None:
        try:
            while not y_done.is_set() or not x_answers:
                x_answers.append(x.query("POS? 1"))
        except (StageError, OSError, RuntimeError) as exc:
            x_errors.append(exc)

    worker = threading.Thread(target=poll_x)
    worker.start()
    try:
        with pytest.raises(StageError) as info:
            y.query("POS? 1")
    finally:
        y_done.set()
        worker.join(2.0)
    assert info.value.code == Code.PI_TIMEOUT
    assert info.value.source == f"Data Receive Timeout (RS-232, {server.url}, baud {BAUD}, device 2)"
    assert not x_errors and x_answers and set(x_answers) == {"1=0.000000"}
    assert y.query("POS? 1") == "1=0.000000"                 # not a broken port: no re-open
    assert chain.open_count == 1


def test_a_dropped_port_fails_both_and_the_next_connect_reopens_it(server: FakeDaisyChainServer,
                                                                   chain: GcsChain) -> None:
    x, y = chain.view(1), chain.view(2)
    assert y.query("POS? 1") == "1=0.000000"
    server.disconnect()                                      # the Moxa connection drops
    assert wait_until(lambda: not server.connected)
    for view in (y, x):
        with pytest.raises(StageError) as info:
            view.query("POS? 1")
        assert info.value.code in (Code.PI_RECEIVE_ERROR, Code.PI_SEND_ERROR)
        assert f"device {view.address}" in info.value.source
    x.close()
    x = chain.link_factory(1)(server.url, BAUD, 5.0)         # PiSerialStage.connect_rs232 of device 1 (Z Setup)
    assert chain.open_count == 2
    assert x.query("*IDN?") == chain_identity(1)
    assert y.query("*IDN?") == chain_identity(2)             # device 2's view uses the new port as it is
    assert wait_until(lambda: server.connections == 2)
