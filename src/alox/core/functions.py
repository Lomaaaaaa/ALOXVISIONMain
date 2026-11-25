"""Measurement functions and the runner that enforces LabVIEW's rules (map Part 1 §2.6, Part 3 §2).

In LabVIEW each of the 13 functions is a wrapper functional global that opens its state-machine VI by name, starts it
without waiting and talks to it through its front-panel controls; ``alox_manager_functions_condition`` allows a start
only when no function at all is running. Here a function is a class with a ``run`` method executed in its own thread by
:class:`FunctionRunner`, which keeps the same rules:

- one function at a time (``start`` raises :class:`FunctionBusy`, which the REST API turns into HTTP 503);
- a high-level function calls low-level ones synchronously with :meth:`FunctionContext.run_once`;
- ``stop`` and ``abort`` are cooperative: they set a flag the function checks between steps
  (:meth:`FunctionContext.check`), then wait (LabVIEW: Stop 30 s for LL, 5 s for HL, Abort 30 s); a function that does
  not stop in time is marked stopped anyway ("Force Stop Status");
- statuses are LabVIEW's ``Off``, ``Off Cond OK``, ``Running``, ``Finished``, ``Failed``.

Events: ``function.<name>.started`` / ``.state`` / ``.finished`` / ``.failed`` / ``.stopped`` (``name`` in snake case,
e.g. ``mesa_measure``), with the job information when there is one.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from alox.events import Event, Publish

__all__ = ["FunctionBusy", "FunctionContext", "FunctionName", "FunctionRunner", "FunctionStatus", "JobInfo",
           "MeasurementFunction", "Stopped"]

log = logging.getLogger(__name__)


class FunctionName(IntEnum):
    """``alox_typedef_functions_condition_manager_funcname``."""

    MESA_MEASURE = 0
    TILT_MEASURE = 1
    BOWING_MEASURE = 2
    GET_IMAGE = 3
    AUTOFOCUS_LARGE = 4
    REFOCUS = 5
    REFOCUS_OXYD = 6
    DETECT_CIRCLE = 7
    TRACKING = 8
    MEASURE_OXYD = 9
    SEARCH_WAVELENGTH = 10
    SEARCH_OBJECT = 11
    AUTO_EXPOSURE = 12

    @property
    def high_level(self) -> bool:
        return self in (FunctionName.MESA_MEASURE, FunctionName.TILT_MEASURE, FunctionName.BOWING_MEASURE)

    @property
    def topic(self) -> str:
        return self.name.lower()


class FunctionStatus(IntEnum):
    """``alox_typedef_*_function_status``."""

    OFF = 0
    OFF_COND_OK = 1
    RUNNING = 2
    FINISHED = 3
    FAILED = 4


@dataclass(frozen=True)
class JobInfo:
    """SEMI job information carried by supervisor requests (``alox_typedef_*_queue_param`` "SEMI Job Infos")."""

    control_job_id: str = ""
    process_job_id: str = ""
    lot_id: str = ""
    wafer_id: str = ""
    zone_id: str = ""


class FunctionBusy(RuntimeError):
    """Another function is running (LabVIEW "Get Condition OK" FALSE)."""


class Stopped(Exception):
    """Raised by :meth:`FunctionContext.check` when a stop or an abort was requested."""

    def __init__(self, aborted: bool) -> None:
        super().__init__("aborted" if aborted else "stopped")
        self.aborted = aborted


@dataclass
class FunctionContext:
    """What a running function sees: the stop/abort flags, its job, a way to publish its state, and ``run_once`` to
    call another function synchronously (high-level functions)."""

    name: FunctionName
    job: JobInfo | None
    runner: FunctionRunner
    stop_event: threading.Event = field(default_factory=threading.Event)
    abort_event: threading.Event = field(default_factory=threading.Event)
    state: str = ""

    @property
    def stop_requested(self) -> bool:
        return self.stop_event.is_set() or self.abort_event.is_set()

    def check(self) -> None:
        """Raise :class:`Stopped` when a stop or an abort was requested (call it between steps)."""
        if self.abort_event.is_set():
            raise Stopped(aborted=True)
        if self.stop_event.is_set():
            raise Stopped(aborted=False)

    def sleep(self, seconds: float) -> None:
        """Wait, waking up at once on stop/abort (then raises :class:`Stopped`)."""
        end = time.monotonic() + seconds
        while True:
            self.check()
            left = end - time.monotonic()
            if left <= 0:
                return
            self.stop_event.wait(min(left, 0.05))

    def set_state(self, state: str, **data: Any) -> None:
        """Publish the state machine's current state (``function.<name>.state``)."""
        self.state = state
        self.runner._emit(self.name, "state", self.job, state=state, **data)

    def run_once(self, name: FunctionName, params: Any = None) -> Any:
        """Run another function to completion in this thread (``Run Once``); it shares this context's stop and abort
        flags, so stopping the high-level function stops the low-level one too."""
        function = self.runner.function(name)
        child = FunctionContext(name, self.job, self.runner, self.stop_event, self.abort_event)
        self.check()
        return function.run(params, child)


class MeasurementFunction(ABC):
    """A measurement function. ``run`` executes the whole state machine in the runner's thread, calls
    ``ctx.check()`` between steps and returns its result (the "last measure")."""

    name: FunctionName

    @abstractmethod
    def run(self, params: Any, ctx: FunctionContext) -> Any:
        """Run to the end; raise :class:`Stopped` (from ``ctx.check``) when stopped, any other exception on failure."""


@dataclass
class _Slot:
    function: MeasurementFunction
    status: FunctionStatus = FunctionStatus.OFF
    last_result: Any = None
    last_error: str | None = None


class FunctionRunner:
    """Owns the functions, starts one at a time, stops and aborts them, reports their status."""

    #: LabVIEW's waits for a function to stop (map Part 1 §2.6, Part 3 §2.1).
    STOP_TIMEOUT_LL_S = 30.0
    STOP_TIMEOUT_HL_S = 5.0
    ABORT_TIMEOUT_S = 30.0

    def __init__(self, *, publish: Publish | None = None) -> None:
        self._publish = publish
        self._lock = threading.RLock()
        self._slots: dict[FunctionName, _Slot] = {}
        self._running: tuple[FunctionName, FunctionContext, threading.Thread] | None = None
        self._finished_callbacks: list[Callable[[FunctionName, JobInfo | None, FunctionStatus, Any], None]] = []

    # ============================================================================================== registry
    def register(self, function: MeasurementFunction) -> None:
        with self._lock:
            self._slots[function.name] = _Slot(function)

    def function(self, name: FunctionName) -> MeasurementFunction:
        with self._lock:
            slot = self._slots.get(name)
        if slot is None:
            raise KeyError(f"function {name.name} is not registered (LabVIEW error 9002 'Invalid function')")
        return slot.function

    def on_finished(self, callback: Callable[[FunctionName, JobInfo | None, FunctionStatus, Any], None]) -> None:
        """``callback(name, job, status, result)`` after every run (the core sends the websocket ``finished`` event
        and the "<X> Job Finished" command from there)."""
        self._finished_callbacks.append(callback)

    # ============================================================================================== conditions
    @property
    def running(self) -> FunctionName | None:
        with self._lock:
            return self._running[0] if self._running else None

    def condition_ok(self) -> bool:
        """LabVIEW "Get Condition OK": no function running."""
        return self.running is None

    def joystick_ok(self) -> bool:
        """LabVIEW "Get Joystick OK": no high-level function running."""
        running = self.running
        return running is None or not running.high_level

    def status(self, name: FunctionName) -> FunctionStatus:
        with self._lock:
            slot = self._slots.get(name)
            if slot is None:
                return FunctionStatus.OFF
            if slot.status == FunctionStatus.OFF and self._running is None:
                return FunctionStatus.OFF_COND_OK
            return slot.status

    def last_result(self, name: FunctionName) -> Any:
        with self._lock:
            slot = self._slots.get(name)
            return None if slot is None else slot.last_result

    def last_error(self, name: FunctionName) -> str | None:
        with self._lock:
            slot = self._slots.get(name)
            return None if slot is None else slot.last_error

    # ============================================================================================== start / stop
    def start(self, name: FunctionName, params: Any = None, *, job: JobInfo | None = None) -> None:
        """Start ``name`` in its own thread; :class:`FunctionBusy` when any function runs."""
        with self._lock:
            slot = self._slots.get(name)
            if slot is None:
                raise KeyError(f"function {name.name} is not registered")
            if self._running is not None:
                raise FunctionBusy(f"{name.name}: {self._running[0].name} is running")
            ctx = FunctionContext(name, job, self)
            thread = threading.Thread(target=self._run, args=(slot, params, ctx), name=f"function-{name.topic}",
                                      daemon=True)
            self._running = (name, ctx, thread)
            slot.status = FunctionStatus.RUNNING
            slot.last_error = None
        self._emit(name, "started", job)
        thread.start()

    def run_once(self, name: FunctionName, params: Any = None, *, job: JobInfo | None = None,
                 timeout: float | None = None) -> Any:
        """Start ``name`` and wait for its result (raises its error; :class:`FunctionBusy` when busy;
        :class:`TimeoutError` when it does not finish within ``timeout``)."""
        done = threading.Event()
        outcome: dict[str, Any] = {}

        def finished(fname: FunctionName, _job: JobInfo | None, status: FunctionStatus, result: Any) -> None:
            if fname == name and not done.is_set():
                outcome.update(status=status, result=result)
                done.set()

        self._finished_callbacks.append(finished)
        try:
            self.start(name, params, job=job)
            if not done.wait(timeout):
                raise TimeoutError(f"{name.name} did not finish within {timeout} s")
        finally:
            self._finished_callbacks.remove(finished)
        if outcome["status"] == FunctionStatus.FAILED:
            raise RuntimeError(self.last_error(name) or f"{name.name} failed")
        return outcome["result"]

    def stop(self, *, timeout: float | None = None) -> bool:
        """Ask the running function to stop and wait; returns whether it stopped in time (no function: TRUE)."""
        return self._halt(abort=False, timeout=timeout)

    def abort(self, *, timeout: float | None = None) -> bool:
        """Emergency stop of the running function (LabVIEW Abort)."""
        return self._halt(abort=True, timeout=timeout)

    def _halt(self, *, abort: bool, timeout: float | None) -> bool:
        with self._lock:
            running = self._running
        if running is None:
            return True
        name, ctx, thread = running
        (ctx.abort_event if abort else ctx.stop_event).set()
        if timeout is None:
            timeout = self.ABORT_TIMEOUT_S if abort else (
                self.STOP_TIMEOUT_HL_S if name.high_level else self.STOP_TIMEOUT_LL_S)
        thread.join(timeout)
        if thread.is_alive():
            log.warning("%s did not stop within %g s: status forced to stopped", name.name, timeout)
            with self._lock:
                if self._running is running:
                    self._running = None
                    self._slots[name].status = FunctionStatus.OFF
            self._emit(name, "stopped", ctx.job, forced=True, aborted=abort)
            return False
        return True

    # ============================================================================================== worker
    def _run(self, slot: _Slot, params: Any, ctx: FunctionContext) -> None:
        name = ctx.name
        status, result, error = FunctionStatus.FINISHED, None, None
        try:
            result = slot.function.run(params, ctx)
        except Stopped as exc:
            status = FunctionStatus.OFF
            self._emit(name, "stopped", ctx.job, aborted=exc.aborted)
        except Exception as exc:
            status, error = FunctionStatus.FAILED, str(exc) or type(exc).__name__
            log.exception("%s failed", name.name)
        with self._lock:
            forced = self._running is None or self._running[1] is not ctx
            slot.status = status
            if status == FunctionStatus.FINISHED:
                slot.last_result = result
            slot.last_error = error
            if not forced:
                self._running = None
        if status == FunctionStatus.FINISHED:
            self._emit(name, "finished", ctx.job)
        elif status == FunctionStatus.FAILED:
            self._emit(name, "failed", ctx.job, error=error)
        for callback in list(self._finished_callbacks):
            try:
                callback(name, ctx.job, status, result)
            except Exception:
                log.exception("function finished callback failed")

    def _emit(self, name: FunctionName, kind: str, job: JobInfo | None, **data: Any) -> None:
        if self._publish is None:
            return
        payload: dict[str, Any] = dict(data)
        if job is not None:
            payload["job"] = job
        self._publish(Event(f"function.{name.topic}.{kind}", "core", payload))
