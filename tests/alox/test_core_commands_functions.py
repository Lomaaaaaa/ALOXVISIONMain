"""The core's command bus and function runner (``alox.core.commands``, ``alox.core.functions``)."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from alox.core.commands import Command, CommandBus, Method, UnknownCommand
from alox.core.functions import (
    FunctionBusy,
    FunctionContext,
    FunctionName,
    FunctionRunner,
    FunctionStatus,
    JobInfo,
    MeasurementFunction,
)
from alox.events import Event

# ============================================================================================== command bus


def test_method_values_and_labels():
    assert Method.MEASURE_MESA == 2 and Method.TILT_JOB_FINISHED == 21
    assert Method.SHOW_UI.label == "Show UI" and Method.LOAD_PROFILE.label == "Load Profile"


def test_commands_are_handled_in_order_without_blocking_the_sender():
    bus = CommandBus()
    seen: list[str] = []
    gate = threading.Event()

    def slow(command: Command) -> str:
        gate.wait(2)
        seen.append(command.payload)
        return command.payload.upper()

    bus.register(Method.LOAD_PROFILE, slow)
    bus.start()
    try:
        t0 = time.monotonic()
        futures = [bus.post(Method.LOAD_PROFILE, f"p{i}", source="api") for i in range(10)]   # LabVIEW blocks at 4
        assert time.monotonic() - t0 < 0.5
        gate.set()
        assert [f.result(2) for f in futures] == [f"P{i}" for i in range(10)]
        assert seen == [f"p{i}" for i in range(10)]
    finally:
        bus.stop()


def test_failures_and_unknown_commands_are_reported():
    events: list[Event] = []
    bus = CommandBus(publish=events.append)

    def boom(_command: Command) -> None:
        raise ValueError("bad profile")

    bus.register(Method.LOAD_PROFILE, boom)
    bus.start()
    try:
        with pytest.raises(ValueError):
            bus.post(Method.LOAD_PROFILE, "x").result(2)
        with pytest.raises(UnknownCommand):
            bus.post(Method.SHOW_UI, True).result(2)
        assert [e.topic for e in events] == ["command.failed", "command.unknown"]
        assert events[0].data["error"] == "bad profile"
    finally:
        bus.stop()


def test_stop_handles_queued_commands_then_refuses_new_ones():
    bus = CommandBus()
    done: list[int] = []
    bus.register(Method.STOP, lambda c: done.append(c.payload))
    bus.start()
    futures = [bus.post(Method.STOP, i) for i in range(3)]
    bus.stop()
    assert [f.result(1) for f in futures] == [None, None, None] and done == [0, 1, 2]
    with pytest.raises(RuntimeError):
        bus.post(Method.STOP).result(1)


# ============================================================================================== functions
class Steps(MeasurementFunction):
    """A function that runs ``steps`` steps of ``delay`` seconds, or fails at step ``fail_at``."""

    def __init__(self, name: FunctionName, steps: int = 3, delay: float = 0.0, fail_at: int | None = None) -> None:
        self.name = name
        self.steps, self.delay, self.fail_at = steps, delay, fail_at
        self.calls: list[Any] = []

    def run(self, params: Any, ctx: FunctionContext) -> Any:
        self.calls.append(params)
        for i in range(self.steps):
            ctx.set_state(f"step {i}")
            if self.fail_at == i:
                raise RuntimeError(f"failed at {i}")
            ctx.sleep(self.delay)
        return {"params": params, "steps": self.steps}


class HighLevel(MeasurementFunction):
    name = FunctionName.MESA_MEASURE

    def run(self, params: Any, ctx: FunctionContext) -> Any:
        ctx.set_state("Autofocus Large")
        focus = ctx.run_once(FunctionName.AUTOFOCUS_LARGE, "af")
        ctx.set_state("Refocus")
        refocus = ctx.run_once(FunctionName.REFOCUS, "rf")
        return [focus["params"], refocus["params"]]


@pytest.fixture
def runner() -> tuple[FunctionRunner, list[Event]]:
    events: list[Event] = []
    return FunctionRunner(publish=events.append), events


def test_run_once_returns_the_result_and_publishes_events(runner):
    r, events = runner
    r.register(Steps(FunctionName.GET_IMAGE))
    job = JobInfo(control_job_id="CJ1", wafer_id="W7")
    assert r.run_once(FunctionName.GET_IMAGE, "p", job=job, timeout=2) == {"params": "p", "steps": 3}
    assert r.status(FunctionName.GET_IMAGE) == FunctionStatus.FINISHED
    topics = [e.topic for e in events]
    assert topics[0] == "function.get_image.started" and topics[-1] == "function.get_image.finished"
    assert events[-1].data["job"].wafer_id == "W7"


def test_one_function_at_a_time(runner):
    r, _ = runner
    slow = Steps(FunctionName.AUTOFOCUS_LARGE, steps=50, delay=0.02)
    r.register(slow)
    r.register(Steps(FunctionName.REFOCUS))
    r.start(FunctionName.AUTOFOCUS_LARGE)
    try:
        assert not r.condition_ok()
        assert r.status(FunctionName.REFOCUS) == FunctionStatus.OFF            # not "Off Cond OK" while busy
        with pytest.raises(FunctionBusy):
            r.start(FunctionName.REFOCUS)
    finally:
        assert r.stop(timeout=2)
    assert r.condition_ok()
    assert r.status(FunctionName.REFOCUS) == FunctionStatus.OFF_COND_OK


def test_stop_and_abort_are_cooperative(runner):
    r, events = runner
    r.register(Steps(FunctionName.TRACKING, steps=100, delay=0.02))
    r.start(FunctionName.TRACKING)
    time.sleep(0.05)
    assert r.abort(timeout=2)
    assert r.status(FunctionName.TRACKING) == FunctionStatus.OFF_COND_OK
    stopped = [e for e in events if e.topic == "function.tracking.stopped"]
    assert stopped and stopped[0].data["aborted"] is True


def test_failure_sets_failed_and_keeps_the_error(runner):
    r, events = runner
    r.register(Steps(FunctionName.DETECT_CIRCLE, fail_at=1))
    with pytest.raises(RuntimeError, match="failed at 1"):
        r.run_once(FunctionName.DETECT_CIRCLE, timeout=2)
    assert r.status(FunctionName.DETECT_CIRCLE) == FunctionStatus.FAILED
    assert r.last_error(FunctionName.DETECT_CIRCLE) == "failed at 1"
    assert events[-1].topic == "function.detect_circle.failed"


def test_high_level_function_chains_low_level_ones(runner):
    r, events = runner
    r.register(HighLevel())
    r.register(Steps(FunctionName.AUTOFOCUS_LARGE))
    r.register(Steps(FunctionName.REFOCUS))
    assert r.run_once(FunctionName.MESA_MEASURE, timeout=2) == ["af", "rf"]
    states = [e.data["state"] for e in events if e.topic.endswith(".state")]
    assert states[:2] == ["Autofocus Large", "step 0"]


def test_stopping_a_high_level_function_stops_its_low_level_step(runner):
    r, _ = runner
    r.register(HighLevel())
    inner = Steps(FunctionName.AUTOFOCUS_LARGE, steps=200, delay=0.02)
    r.register(inner)
    r.register(Steps(FunctionName.REFOCUS))
    r.start(FunctionName.MESA_MEASURE)
    time.sleep(0.05)
    assert not r.joystick_ok()
    assert r.stop(timeout=2)
    assert r.joystick_ok()


def test_a_function_that_ignores_stop_is_forced(runner):
    r, events = runner

    class Stubborn(MeasurementFunction):
        name = FunctionName.SEARCH_WAVELENGTH

        def run(self, params: Any, ctx: FunctionContext) -> None:
            time.sleep(0.5)                                  # never checks the flags

    r.register(Stubborn())
    r.start(FunctionName.SEARCH_WAVELENGTH)
    assert r.stop(timeout=0.05) is False
    assert r.condition_ok()                                  # forced: another function may start
    assert any(e.data.get("forced") for e in events if e.topic.endswith(".stopped"))
    time.sleep(0.6)


def test_unregistered_function(runner):
    r, _ = runner
    with pytest.raises(KeyError):
        r.start(FunctionName.AUTO_EXPOSURE)
