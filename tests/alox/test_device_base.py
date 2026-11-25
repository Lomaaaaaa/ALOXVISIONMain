"""alox.devices.base: the device actor (worker thread, priority queue, timeouts, states, link failures, reconnection,
events), driven through a fake backend."""

from __future__ import annotations

import itertools
import logging
import threading
import time
import types
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

import pytest

from alox.devices import base
from alox.devices.base import (
    Device,
    DeviceError,
    DeviceNotReady,
    DeviceState,
    DeviceTimeout,
)
from alox.events import Event, EventBus

LINK_CODE = -7
RUNNING_TIMEOUT_S = 0.2        # long enough for an idle worker to pick the operation up, so it times out *running*

_names = itertools.count()


class LinkLost(Exception):
    """The fake backend lost its link (cable pulled, port gone)."""


class FakeDevice(Device):
    """A backend with a call log, injectable open/close failures, a link-failure exception and an interrupt hook.

    It records every entry into the backend (``_open``, ``_close`` and the operations made by :meth:`op`), the threads
    that entered it and the largest number of simultaneous entries."""

    def __init__(self, name: str | None = None, **kwargs: Any) -> None:
        self.log: list[str] = []
        self.threads: set[int] = set()
        self.sleeps: list[float] = []
        self.interrupts: list[str] = []
        self.open_failures = 0                         # the next N opens raise ``open_error``
        self.open_error: Exception = LinkLost("no answer")
        self.close_error: Exception | None = None
        self.on_interrupt: Callable[[str], None] | None = None
        self.blockers: list[Blocker] = []
        self.max_active = 0
        self._active = 0
        self._active_lock = threading.Lock()
        kwargs.setdefault("default_timeout_s", 2.0)        # a regression fails the test instead of hanging it
        kwargs.setdefault("reconnect_delay_s", 0.5)
        kwargs.setdefault("sleep", self.sleeps.append)     # no real pauses between reconnection attempts
        super().__init__(name or f"fake{next(_names)}", **kwargs)

    # ---------------------------------------------------------------------------------------------- bookkeeping
    def enter(self, what: str) -> None:
        with self._active_lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        self.threads.add(threading.get_ident())
        self.log.append(what)

    def leave(self) -> None:
        with self._active_lock:
            self._active -= 1

    def op(self, what: str, result: Any = None, *, error: BaseException | None = None,
           hold_s: float = 0.0) -> Callable[[], Any]:
        """A backend operation logged as ``what``; returns ``result`` (default: ``what``) or raises ``error``."""
        def fn() -> Any:
            self.enter(what)
            try:
                if hold_s:
                    time.sleep(hold_s)
                if error is not None:
                    raise error
                return what if result is None else result
            finally:
                self.leave()
        return fn

    # ---------------------------------------------------------------------------------------------- hooks
    def _open(self) -> None:
        self.enter("open")
        try:
            if self.open_failures > 0:
                self.open_failures -= 1
                raise self.open_error
        finally:
            self.leave()

    def _close(self) -> None:
        self.enter("close")
        try:
            if self.close_error is not None:
                raise self.close_error
        finally:
            self.leave()

    def _is_link_failure(self, exc: BaseException) -> bool:
        return isinstance(exc, LinkLost)

    def _translate(self, exc: BaseException, what: str) -> DeviceError:
        if isinstance(exc, LinkLost):
            return DeviceError(self.name, f"{what}: link lost ({exc})", code=LINK_CODE)
        return super()._translate(exc, what)

    def _interrupt(self, what: str) -> None:
        self.interrupts.append(what)
        if self.on_interrupt is not None:
            self.on_interrupt(what)


class Blocker:
    """An operation that holds the worker until :meth:`release`; ``started`` is set once the worker runs it."""

    def __init__(self, device: FakeDevice, what: str = "block", result: Any = "unblocked") -> None:
        self.device = device
        self.what = what
        self.result = result
        self.started = threading.Event()
        self._release = threading.Event()
        device.blockers.append(self)

    def __call__(self) -> Any:
        self.device.enter(self.what)
        try:
            self.started.set()
            if not self._release.wait(5):
                raise RuntimeError("blocker never released")
            return self.result
        finally:
            self.device.leave()

    def hold(self) -> Future[Any]:
        """Submit the operation and wait until the worker is inside it."""
        future = self.device.submit(self.what, self)
        assert self.started.wait(2), "the worker did not start the blocking operation"
        return future

    def release(self) -> None:
        self._release.set()


@pytest.fixture
def make() -> Iterator[Callable[..., FakeDevice]]:
    """``make(opened=True, **device_kwargs)``: a :class:`FakeDevice`, opened by default; shut down after the test."""
    made: list[FakeDevice] = []

    def factory(*, opened: bool = True, **kwargs: Any) -> FakeDevice:
        device = FakeDevice(**kwargs)
        made.append(device)
        if opened:
            device.open()
        return device

    yield factory
    for device in made:
        for blocker in device.blockers:
            blocker.release()
        device.on_interrupt = None
        device.shutdown(timeout=2)


def trace(events: list[Event]) -> list[str]:
    """Compact form of device events: ``state:<state>``, ``reconnecting:<attempt>``, ``timeout:<operation>``..."""
    out: list[str] = []
    for event in events:
        kind = event.topic.split(".", 2)[2]
        if kind == "state":
            out.append(f"state:{event.data['state']}")
        elif kind in ("reconnecting", "reconnected"):
            out.append(f"{kind}:{event.data['attempt']}")
        else:
            out.append(f"{kind}:{event.data.get('operation')}")
    return out


def worker_alive(device: Device) -> bool:
    return any(t.name == f"device-{device.name}" and t.is_alive() for t in threading.enumerate())


def wait_queued(device: Device, count: int) -> None:
    """Wait until ``count`` items wait in the device queue (white box: the queue has no public view)."""
    deadline = time.monotonic() + 2
    while device._queue.qsize() < count:
        assert time.monotonic() < deadline, "the items were not queued"
        time.sleep(0.001)


# ================================================================================================== calls
class TestCalls:
    def test_call_returns_the_result(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        assert device.call("answer", lambda: 42) == 42
        assert device.call("none", lambda: None) is None

    def test_operations_run_in_submission_order_in_one_worker_thread(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        futures = [device.submit(f"op{i}", device.op(f"op{i}", i)) for i in range(50)]
        assert [f.result(2) for f in futures] == list(range(50))
        assert device.log == ["open"] + [f"op{i}" for i in range(50)]
        assert len(device.threads) == 1
        assert threading.get_ident() not in device.threads

    def test_urgent_items_overtake_queued_normal_ones(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        blocker = Blocker(device)
        held = blocker.hold()
        assert device.busy_with == "block"
        a = device.submit("a", device.op("a"))
        b = device.submit("b", device.op("b"))
        u1 = device.submit("u1", device.op("u1"), urgent=True)
        u2 = device.submit("u2", device.op("u2"), urgent=True)
        c = device.submit("c", device.op("c"))
        blocker.release()
        assert [f.result(2) for f in (held, a, b, u1, u2, c)] == ["unblocked", "a", "b", "u1", "u2", "c"]
        assert device.log == ["open", "block", "u1", "u2", "a", "b", "c"]     # not before the running one
        assert device.busy_with is None

    def test_urgent_call_from_another_thread(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        blocker = Blocker(device)
        blocker.hold()
        normal = device.submit("normal", device.op("normal"))
        results: list[Any] = []
        caller = threading.Thread(target=lambda: results.append(device.call("stop", device.op("stop"), urgent=True)))
        caller.start()
        wait_queued(device, 2)                                      # "normal" and "stop" wait behind the blocker
        blocker.release()
        caller.join(2)
        assert results == ["stop"]
        assert normal.result(2) == "normal"
        assert device.log == ["open", "block", "stop", "normal"]

    def test_device_error_passes_through_unchanged(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        error = DeviceError(device.name, "limit switch reached", code=5)
        with pytest.raises(DeviceError) as info:
            device.call("move", device.op("move", error=error))
        assert info.value is error
        assert device.state == DeviceState.READY
        assert device.call("after", lambda: "ok") == "ok"

    def test_other_exceptions_are_translated(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        with pytest.raises(DeviceError) as info:
            device.call("read", device.op("read", error=ValueError("bad reply")))
        assert type(info.value) is DeviceError
        assert info.value.device == device.name
        assert info.value.message == "read: bad reply"
        assert info.value.code is None
        assert str(info.value) == f"{device.name}: read: bad reply"
        assert device.state == DeviceState.READY                  # not a link failure
        assert device.last_error is None
        assert device.call("after", lambda: "ok") == "ok"

    def test_submit_returns_a_future_with_the_result_or_the_error(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        good = device.submit("good", lambda: "fine")
        bad = device.submit("bad", device.op("bad", error=KeyError("k")))
        assert good.result(2) == "fine"
        error = bad.exception(2)
        assert isinstance(error, DeviceError) and error.message == "bad: 'k'"

    def test_submit_before_start_raises(self) -> None:
        device = FakeDevice()
        with pytest.raises(DeviceNotReady, match="not started"):
            device.submit("x", lambda: None)
        with pytest.raises(DeviceNotReady, match="not started"):
            device.call("x", lambda: None)
        device.shutdown()

    def test_started_but_not_opened_is_not_ready(self, make: Callable[..., FakeDevice]) -> None:
        device = make(opened=False)
        device.start()
        device.start()                                              # idempotent
        assert device.state == DeviceState.CLOSED
        ran = threading.Event()
        with pytest.raises(DeviceNotReady, match="closed"):
            device.call("x", ran.set)
        assert not ran.is_set()
        assert device.log == []


# ================================================================================================== timeouts
class TestTimeouts:
    def test_timeout_while_queued_cancels_the_item(self, make: Callable[..., FakeDevice]) -> None:
        events: list[Event] = []
        device = make(publish=events.append)
        blocker = Blocker(device)
        blocker.hold()
        ran = threading.Event()
        with pytest.raises(DeviceTimeout) as info:
            device.call("queued", ran.set, timeout=0.05)
        assert info.value.message == "queued: not started within 0.05 s (device busy with 'block')"
        assert device.interrupts == []                              # nothing running of ours to interrupt
        blocker.release()
        assert device.call("after", lambda: "ok") == "ok"          # the worker went past the cancelled item
        assert not ran.is_set()                                     # ... without running it
        timeouts = [e for e in events if e.topic == f"device.{device.name}.timeout"]
        assert [dict(e.data) for e in timeouts] == [{"operation": "queued", "timeout_s": 0.05}]
        assert device.state == DeviceState.READY

    def test_timeout_while_running_interrupts_and_discards_the_late_result(
            self, make: Callable[..., FakeDevice]) -> None:
        events: list[Event] = []
        device = make(publish=events.append)
        slow = Blocker(device, "slow", result="late")
        with pytest.raises(DeviceTimeout) as info:
            device.call("slow", slow, timeout=RUNNING_TIMEOUT_S)
        assert info.value.message == f"slow: no result within {RUNNING_TIMEOUT_S:g} s"
        assert isinstance(info.value, DeviceError)
        assert device.interrupts == ["slow"]
        assert device.busy_with == "slow"                           # still running in the worker
        following = device.submit("next", lambda: "fresh")
        slow.release()
        assert following.result(2) == "fresh"                      # the late "late" went nowhere
        assert device.state == DeviceState.READY
        assert device.call("again", lambda: 1) == 1
        assert trace(events)[-1] == "timeout:slow"

    def test_interrupt_can_abort_the_running_operation(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        slow = Blocker(device, "move")
        device.on_interrupt = lambda what: slow.release()          # e.g. a stop command sent to the controller
        with pytest.raises(DeviceTimeout):
            device.call("move", slow, timeout=RUNNING_TIMEOUT_S)
        assert device.call("position", lambda: 1.5, timeout=1) == 1.5
        assert device.interrupts == ["move"]

    def test_failing_interrupt_is_logged_and_the_timeout_still_raised(
            self, make: Callable[..., FakeDevice], caplog: pytest.LogCaptureFixture) -> None:
        device = make()
        slow = Blocker(device, "slow")

        def broken(_what: str) -> None:
            raise RuntimeError("interrupt failed")

        device.on_interrupt = broken
        with caplog.at_level(logging.ERROR, logger="alox.devices.base"), pytest.raises(DeviceTimeout):
            device.call("slow", slow, timeout=RUNNING_TIMEOUT_S)
        assert "interrupt after timeout failed" in caplog.text
        slow.release()
        assert device.call("after", lambda: "ok") == "ok"

    def test_default_timeout_applies_when_none_is_given(self, make: Callable[..., FakeDevice]) -> None:
        device = make(default_timeout_s=0.05)
        blocker = Blocker(device)
        blocker.hold()
        start = time.monotonic()
        with pytest.raises(DeviceTimeout, match="within 0.05 s"):
            device.call("waiting", lambda: None)
        assert time.monotonic() - start < 1.0

    def test_result_that_arrives_as_the_wait_expires_is_returned_without_interrupt(
            self, make: Callable[..., FakeDevice]) -> None:
        """The operation finishes between the end of the caller's wait and its attempt to cancel: the result is there,
        nothing is running any more, so :meth:`_interrupt` must not be called (it would hit the next operation)."""
        device = make()

        class FinishesAtTheDeadline(Future[Any]):
            waits = 0

            def result(self, timeout: float | None = None) -> Any:
                self.waits += 1
                if self.waits == 1:                                 # the worker completes it just as the wait expires
                    self.set_running_or_notify_cancel()
                    self.set_result("done")
                    raise FutureTimeout
                return super().result(timeout)

        assert device._wait(FinishesAtTheDeadline(), "quick", 0.01) == "done"
        assert device.interrupts == []


# ================================================================================================== states and events
class TestStates:
    def test_life_cycle_states_and_events_on_the_bus(self, make: Callable[..., FakeDevice]) -> None:
        bus = EventBus()
        sub = bus.subscribe(["device"])
        device = make(opened=False, publish=bus.publish)
        assert device.state == DeviceState.CLOSED
        assert device.last_error is None
        device.open()
        assert device.state == DeviceState.READY
        device.close()
        assert device.state == DeviceState.CLOSED
        device.open()
        device.shutdown()
        assert device.state == DeviceState.SHUTDOWN
        events = sub.drain()
        assert trace(events) == ["state:opening", "state:ready", "state:closed",
                                 "state:opening", "state:ready", "state:closed", "state:shutdown"]
        assert {e.topic for e in events} == {f"device.{device.name}.state"}
        assert {e.source for e in events} == {device.name}
        assert device.log == ["open", "close", "open", "close"]

    def test_open_failure_gives_fault(self, make: Callable[..., FakeDevice]) -> None:
        events: list[Event] = []
        device = make(opened=False, publish=events.append)
        device.open_failures = 1
        with pytest.raises(DeviceError) as info:
            device.open()
        assert info.value.code == LINK_CODE
        assert info.value.message == "open: link lost (no answer)"
        assert device.state == DeviceState.FAULT
        assert device.last_error is info.value
        assert trace(events) == ["state:opening", "state:fault"]
        assert dict(events[-1].data) == {"state": "fault", "error": str(info.value), "code": LINK_CODE}
        device.open()                                               # an explicit open recovers
        assert device.state == DeviceState.READY
        assert device.last_error is None
        assert trace(events)[-2:] == ["state:opening", "state:ready"]

    def test_open_failure_with_an_ordinary_exception_is_translated(self, make: Callable[..., FakeDevice]) -> None:
        device = make(opened=False)
        device.open_failures = 1
        device.open_error = OSError("port COM3 busy")
        with pytest.raises(DeviceError) as info:
            device.open()
        assert type(info.value) is DeviceError
        assert info.value.message == "open: port COM3 busy"
        assert device.state == DeviceState.FAULT
        assert device.last_error is info.value

    def test_open_timeout(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        blocker = Blocker(device)
        blocker.hold()
        with pytest.raises(DeviceTimeout, match="open: not started"):
            device.open(timeout=0.05)
        blocker.release()
        assert device.call("after", lambda: "ok") == "ok"
        assert device.log.count("open") == 1                         # the cancelled open never ran

    def test_without_publisher_nothing_is_emitted_and_nothing_fails(self) -> None:
        device = FakeDevice(publish=None)
        device.open()
        device.emit("custom", x=1)
        device.close()
        device.shutdown()
        assert device.state == DeviceState.SHUTDOWN

    def test_emit_publishes_custom_events(self, make: Callable[..., FakeDevice]) -> None:
        events: list[Event] = []
        device = make(opened=False, publish=events.append)
        device.emit("position", z_um=12.5)
        assert len(events) == 1
        assert events[0].topic == f"device.{device.name}.position"
        assert events[0].source == device.name
        assert dict(events[0].data) == {"z_um": 12.5}


# ================================================================================================== link failures
class TestLinkFailure:
    def test_link_failure_gives_fault_and_the_next_call_reconnects(self, make: Callable[..., FakeDevice]) -> None:
        events: list[Event] = []
        device = make(publish=events.append)
        events.clear()
        with pytest.raises(DeviceError) as info:
            device.call("read", device.op("read", error=LinkLost("cable")))
        assert not isinstance(info.value, DeviceNotReady)          # the caller sees the link error itself
        assert info.value.code == LINK_CODE
        assert device.state == DeviceState.FAULT
        assert device.last_error is info.value
        assert trace(events) == ["state:fault"]
        assert events[0].data["code"] == LINK_CODE

        assert device.call("read2", device.op("read2", 7)) == 7
        assert device.log == ["open", "read", "close", "open", "read2"]
        assert trace(events) == ["state:fault", "reconnecting:1", "state:opening", "state:ready", "reconnected:1"]
        assert events[1].data["operation"] == "read2"
        assert device.state == DeviceState.READY
        assert device.last_error is None
        assert device.sleeps == []                                  # no pause before the first attempt

    def test_reconnect_succeeds_at_a_later_attempt(self, make: Callable[..., FakeDevice]) -> None:
        events: list[Event] = []
        device = make(publish=events.append, max_reconnect_attempts=3, reconnect_delay_s=0.25)
        with pytest.raises(DeviceError):
            device.call("read", device.op("read", error=LinkLost("cable")))
        events.clear()
        device.open_failures = 1
        assert device.call("read2", device.op("read2")) == "read2"
        assert trace(events) == ["reconnecting:1", "state:opening", "state:fault",
                                 "reconnecting:2", "state:opening", "state:ready", "reconnected:2"]
        assert device.sleeps == [0.25]
        assert device.log == ["open", "read", "close", "open", "close", "open", "read2"]

    def test_reconnect_gives_up_after_the_attempts(self, make: Callable[..., FakeDevice]) -> None:
        events: list[Event] = []
        device = make(publish=events.append, max_reconnect_attempts=3)
        with pytest.raises(DeviceError):
            device.call("read", device.op("read", error=LinkLost("cable")))
        events.clear()
        device.log.clear()
        device.open_failures = 100
        with pytest.raises(DeviceNotReady) as info:
            device.call("read2", device.op("read2"))
        assert "read2: reconnection failed" in info.value.message
        assert "link lost (no answer)" in info.value.message
        assert info.value.code == LINK_CODE
        assert device.state == DeviceState.FAULT
        assert device.sleeps == [0.5, 0.5]                          # between the attempts, not after the last
        assert [e for e in trace(events) if e.startswith("reconnect")] == [
            "reconnecting:1", "reconnecting:2", "reconnecting:3"]
        assert "reconnected" not in "".join(trace(events))
        assert device.log == ["close", "open"] * 3                   # the operation itself never ran

        device.open_failures = 0                                    # the link is back: the next call recovers
        assert device.call("read3", device.op("read3")) == "read3"
        assert device.state == DeviceState.READY

    def test_close_failure_before_reconnect_is_tolerated(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        with pytest.raises(DeviceError):
            device.call("read", device.op("read", error=LinkLost("cable")))
        device.close_error = OSError("already closed")
        assert device.call("read2", lambda: 2) == 2
        assert device.state == DeviceState.READY

    def test_failed_open_then_call_reconnects(self, make: Callable[..., FakeDevice]) -> None:
        device = make(opened=False)
        device.open_failures = 1
        with pytest.raises(DeviceError):
            device.open()
        assert device.call("read", lambda: "r") == "r"
        assert device.log == ["open", "close", "open"]

    def test_without_auto_reconnect_the_device_stays_in_fault(self, make: Callable[..., FakeDevice]) -> None:
        events: list[Event] = []
        device = make(publish=events.append, auto_reconnect=False)
        with pytest.raises(DeviceError) as info:
            device.call("read", device.op("read", error=LinkLost("cable")))
        error = info.value
        with pytest.raises(DeviceNotReady) as not_ready:
            device.call("read2", device.op("read2"))
        assert not_ready.value.message == f"read2: device fault ({error.message})"
        assert not_ready.value.code == LINK_CODE
        assert device.state == DeviceState.FAULT
        assert device.last_error is error
        assert device.log == ["open", "read"]                       # no reconnection, operation not run
        assert not any(e.startswith("reconnect") for e in trace(events))
        device.open()                                               # an explicit open recovers
        assert device.call("read3", device.op("read3")) == "read3"

    def test_non_link_errors_do_not_cause_fault(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        for error in (ValueError("v"), DeviceError("x", "y", code=3), TimeoutError("t")):
            with pytest.raises(DeviceError):
                device.call("op", device.op("op", error=error))
            assert device.state == DeviceState.READY


# ================================================================================================== run_direct
class TestRunDirect:
    def test_runs_in_the_callers_thread_while_the_worker_is_busy(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        blocker = Blocker(device)
        held = blocker.hold()
        assert device.run_direct("stop", threading.get_ident) == threading.get_ident()
        assert device.busy_with == "block"
        blocker.release()
        assert held.result(2) == "unblocked"

    def test_errors(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        error = DeviceError(device.name, "refused", code=9)
        with pytest.raises(DeviceError) as info:
            device.run_direct("stop", device.op("stop", error=error))
        assert info.value is error
        with pytest.raises(DeviceError) as info:
            device.run_direct("stop", device.op("stop", error=ValueError("bad")))
        assert info.value.message == "stop: bad"
        assert isinstance(info.value.__cause__, ValueError)
        assert device.state == DeviceState.READY

    def test_link_failure_gives_fault(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        with pytest.raises(DeviceError) as info:
            device.run_direct("stop", device.op("stop", error=LinkLost("gone")))
        assert info.value.code == LINK_CODE
        assert device.state == DeviceState.FAULT
        with pytest.raises(DeviceNotReady, match="device fault"):
            device.run_direct("stop", lambda: None)                 # run_direct never reconnects
        assert device.call("read", lambda: "r") == "r"              # the worker does

    def test_not_ready(self, make: Callable[..., FakeDevice]) -> None:
        device = make(opened=False)
        ran = threading.Event()
        with pytest.raises(DeviceNotReady, match="stop: device closed"):
            device.run_direct("stop", ran.set)
        assert not ran.is_set()


# ================================================================================================== close and shutdown
class TestCloseAndShutdown:
    def test_close_then_call_is_not_ready(self, make: Callable[..., FakeDevice]) -> None:
        events: list[Event] = []
        device = make(publish=events.append)
        device.close()
        assert device.state == DeviceState.CLOSED
        assert device.log == ["open", "close"]
        assert trace(events)[-1] == "state:closed"
        ran = threading.Event()
        with pytest.raises(DeviceNotReady, match="x: device closed"):
            device.call("x", ran.set)
        with pytest.raises(DeviceNotReady):
            device.run_direct("x", ran.set)
        assert not ran.is_set()
        assert device.log == ["open", "close"]                      # closed is not fault: no reconnection
        device.open()                                               # re-opening works
        assert device.call("y", lambda: "y") == "y"

    def test_close_runs_after_the_queued_operations(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        blocker = Blocker(device)
        blocker.hold()
        queued = device.submit("queued", device.op("queued"))
        closer = threading.Thread(target=device.close)
        closer.start()
        wait_queued(device, 2)                                      # "queued" and "close" wait behind the blocker
        blocker.release()
        closer.join(2)
        assert queued.result(2) == "queued"
        assert device.log == ["open", "block", "queued", "close"]
        assert device.state == DeviceState.CLOSED

    def test_close_failure_is_logged_and_the_device_closed(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        device.close_error = OSError("port vanished")
        device.close()
        assert device.state == DeviceState.CLOSED

    def test_close_before_start_does_nothing(self) -> None:
        events: list[Event] = []
        device = FakeDevice(publish=events.append)
        device.close()
        assert device.log == []
        assert events == []
        device.shutdown()

    def test_shutdown_is_final_and_idempotent(self, make: Callable[..., FakeDevice]) -> None:
        events: list[Event] = []
        device = make(publish=events.append)
        assert worker_alive(device)
        device.shutdown()
        assert device.state == DeviceState.SHUTDOWN
        assert not worker_alive(device)
        assert device.log == ["open", "close"]
        assert trace(events) == ["state:opening", "state:ready", "state:closed", "state:shutdown"]

        device.shutdown()                                           # idempotent: nothing more happens
        assert len(events) == 4
        with pytest.raises(DeviceNotReady, match="shut down"):
            device.submit("x", lambda: None)
        with pytest.raises(DeviceNotReady, match="shut down"):
            device.call("x", lambda: None)
        with pytest.raises(DeviceNotReady):
            device.run_direct("x", lambda: None)
        device.close()                                              # no-op
        start = time.monotonic()
        with pytest.raises(DeviceNotReady):
            device.open()                                           # final: no re-open
        assert time.monotonic() - start < 1.0
        assert device.state == DeviceState.SHUTDOWN
        assert device.log == ["open", "close"]
        assert len(events) == 4

    def test_shutdown_before_start(self) -> None:
        events: list[Event] = []
        device = FakeDevice(publish=events.append)
        device.shutdown()
        assert device.state == DeviceState.SHUTDOWN
        assert trace(events) == ["state:shutdown"]
        with pytest.raises(DeviceNotReady):
            device.open()                                           # must not open the hardware after shutdown
        assert device.log == []
        assert not worker_alive(device)
        assert device.state == DeviceState.SHUTDOWN

    def test_shutdown_from_fault_clears_the_error(self, make: Callable[..., FakeDevice]) -> None:
        device = make(auto_reconnect=False)
        with pytest.raises(DeviceError):
            device.call("read", device.op("read", error=LinkLost("cable")))
        device.shutdown()
        assert device.state == DeviceState.SHUTDOWN
        assert device.last_error is None
        assert device.log[-1] == "close"

    def test_shutdown_while_an_operation_is_stuck(self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        blocker = Blocker(device)
        blocker.hold()
        pending = device.submit("pending", device.op("pending"))
        device.shutdown(timeout=0.05)                               # close cannot run: logged, not raised
        assert device.state == DeviceState.SHUTDOWN
        blocker.release()
        with pytest.raises(DeviceNotReady):
            pending.result(2)                                       # resolved, not left hanging
        deadline = time.monotonic() + 2
        while worker_alive(device) and time.monotonic() < deadline:
            time.sleep(0.005)
        assert not worker_alive(device)
        assert "pending" not in device.log


# ================================================================================================== concurrency
class TestConcurrency:
    def test_many_callers_get_their_own_results_and_the_backend_is_never_entered_twice(
            self, make: Callable[..., FakeDevice]) -> None:
        device = make()
        callers, calls = 8, 25
        barrier = threading.Barrier(callers)

        def caller(k: int) -> list[Any]:
            barrier.wait(2)                                         # all callers start together
            results = []
            for i in range(calls):
                op = device.op(f"t{k}.{i}", (k, i), hold_s=0.0002)
                results.append(device.call(f"t{k}", op, urgent=i % 5 == 0))
            return results

        with ThreadPoolExecutor(callers) as pool:
            futures = [pool.submit(caller, k) for k in range(callers)]
            results = [future.result(5) for future in futures]
        for k in range(callers):
            assert results[k] == [(k, i) for i in range(calls)]
            assert [e for e in device.log if e.startswith(f"t{k}.")] == [f"t{k}.{i}" for i in range(calls)]
        assert device.max_active == 1
        assert len(device.threads) == 1
        assert len(device.log) == 1 + callers * calls

    def test_concurrent_open_starts_a_single_worker(self, make: Callable[..., FakeDevice],
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
        device = make(opened=False)
        created: list[threading.Thread] = []
        both_inside = threading.Barrier(2, timeout=0.2)

        class GatedThread(threading.Thread):
            """Holds each ``start()`` inside the thread creation until a second opener got there too (or 0.2 s)."""

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                try:
                    both_inside.wait()
                except threading.BrokenBarrierError:
                    pass
                super().__init__(*args, **kwargs)
                created.append(self)

        monkeypatch.setattr(base, "threading", types.SimpleNamespace(Thread=GatedThread, Lock=threading.Lock))
        with ThreadPoolExecutor(2) as pool:
            for future in [pool.submit(device.open) for _ in range(2)]:
                future.result(3)
        assert len(created) == 1                                    # one worker, so the backend is never shared
        assert device.state == DeviceState.READY
        assert device.max_active == 1
