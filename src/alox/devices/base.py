"""The device framework: one worker thread per device, a command queue, timeouts, reconnection and events.

Plan: ``docs/vision-core/migration-plan.md`` §3.3; design: ``docs/vision-core/phase3-skeleton.md``.

A :class:`Device` owns one piece of hardware (a serial port, a camera handle). Every operation is a callable executed
by the device's worker thread, in order, so the backend never sees two callers at once. The caller waits for the result
with a timeout: a stuck device makes the *caller* fail with :class:`DeviceTimeout`, it never freezes the application.

States: ``closed`` → ``opening`` → ``ready``; a link failure gives ``fault``; with ``auto_reconnect`` the next call
first tries to re-open the device (``_close`` then ``_open``, up to ``max_reconnect_attempts`` times); ``shutdown`` is
final. Every state change and incident is published as an :class:`alox.events.Event` with the topic
``device.<name>.<kind>``.

Subclasses implement :meth:`Device._open` and :meth:`Device._close`, and may override :meth:`Device._is_link_failure`
(which exceptions mean "the link is gone"), :meth:`Device._translate` (backend exception → :class:`DeviceError`) and
:meth:`Device._interrupt` (called in the caller's thread when a call times out).
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

from alox.events import Event, Publish

__all__ = ["Device", "DeviceError", "DeviceNotReady", "DeviceState", "DeviceTimeout"]

log = logging.getLogger(__name__)

T = TypeVar("T")

_URGENT, _NORMAL = 0, 1


class DeviceState(StrEnum):
    CLOSED = "closed"
    OPENING = "opening"
    READY = "ready"
    FAULT = "fault"
    SHUTDOWN = "shutdown"


class DeviceError(Exception):
    """A device operation failed. ``code`` is the backend's numeric code when it has one (for the Z stage: the codes of
    ``docs/08-z-stage.md``), else None."""

    def __init__(self, device: str, message: str, *, code: int | None = None) -> None:
        super().__init__(f"{device}: {message}" + (f" (code {code})" if code is not None else ""))
        self.device = device
        self.message = message
        self.code = code


class DeviceTimeout(DeviceError):
    """The caller stopped waiting; the operation may still be running in the worker."""


class DeviceNotReady(DeviceError):
    """The device is not open (closed, opening, in fault without a successful reconnection, or shut down)."""


@dataclass(order=True)
class _Item:
    priority: int
    seq: int
    what: str = field(compare=False)
    fn: Callable[[], Any] = field(compare=False)
    future: Future[Any] = field(compare=False)
    needs_ready: bool = field(compare=False, default=True)


class Device:
    """Base class of every device. Thread-safe: any thread may call :meth:`call`, :meth:`open`, :meth:`close`."""

    def __init__(self, name: str, *, publish: Publish | None = None, default_timeout_s: float = 30.0,
                 auto_reconnect: bool = True, max_reconnect_attempts: int = 2, reconnect_delay_s: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.name = name
        self.default_timeout_s = default_timeout_s
        self.auto_reconnect = auto_reconnect
        self.max_reconnect_attempts = max(1, max_reconnect_attempts)
        self.reconnect_delay_s = reconnect_delay_s
        self._publish = publish
        self._sleep = sleep
        self._queue: queue.PriorityQueue[_Item | None] = queue.PriorityQueue()
        self._seq = itertools.count()
        self._state_lock = threading.Lock()
        self._state = DeviceState.CLOSED
        self._last_error: DeviceError | None = None
        self._thread: threading.Thread | None = None
        self._current: str | None = None

    # ============================================================================================== state
    @property
    def state(self) -> DeviceState:
        with self._state_lock:
            return self._state

    @property
    def last_error(self) -> DeviceError | None:
        """The error that put the device in ``fault`` (None otherwise)."""
        with self._state_lock:
            return self._last_error

    @property
    def busy_with(self) -> str | None:
        """The operation the worker is executing now (None when idle)."""
        return self._current

    def _set_state(self, state: DeviceState, error: DeviceError | None = None) -> None:
        with self._state_lock:
            if self._state == DeviceState.SHUTDOWN:
                return
            changed = state != self._state
            self._state = state
            self._last_error = error if state == DeviceState.FAULT else None
        if changed:
            data: dict[str, Any] = {"state": str(state)}
            if error is not None:
                data["error"] = str(error)
                data["code"] = error.code
            self.emit("state", **data)

    def emit(self, kind: str, **data: Any) -> None:
        """Publish ``device.<name>.<kind>`` with ``data`` (no-op without a publisher)."""
        if self._publish is not None:
            self._publish(Event(f"device.{self.name}.{kind}", self.name, dict(data)))

    # ============================================================================================== life cycle
    def start(self) -> None:
        """Start the worker thread (idempotent). The device stays ``closed`` until :meth:`open`."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._worker, name=f"device-{self.name}", daemon=True)
        self._thread.start()

    def open(self, timeout: float | None = None) -> None:
        """Open the device (run :meth:`_open` in the worker): ``ready`` on success, ``fault`` and :class:`DeviceError`
        on failure. Starts the worker if needed."""
        self.start()
        self._wait(self._enqueue("open", self._do_open, _NORMAL, needs_ready=False), "open", timeout)

    def close(self, timeout: float | None = None) -> None:
        """Close the device (run :meth:`_close` in the worker after the queued operations): ``closed``."""
        if self._thread is None or self.state == DeviceState.SHUTDOWN:
            return
        self._wait(self._enqueue("close", self._do_close, _NORMAL, needs_ready=False), "close", timeout)

    def shutdown(self, timeout: float = 10.0) -> None:
        """Close the device after the queued operations, stop the worker and enter ``shutdown`` (idempotent; final)."""
        if self.state == DeviceState.SHUTDOWN:
            return
        if self._thread is not None:
            try:
                self.close(timeout=timeout)
            except DeviceError as exc:
                log.warning("%s: close at shutdown failed: %s", self.name, exc)
            self._queue.put(None)
            self._thread.join(timeout)
        self._set_state(DeviceState.SHUTDOWN)          # emits the state event once; SHUTDOWN is final

    # ============================================================================================== calls
    def submit(self, what: str, fn: Callable[[], T], *, urgent: bool = False) -> Future[T]:
        """Queue ``fn`` (run by the worker when the device is ready); ``urgent`` puts it before the normal operations
        that are still waiting (not before the one running)."""
        if self.state == DeviceState.SHUTDOWN:
            raise DeviceNotReady(self.name, f"{what}: device shut down")
        if self._thread is None:
            raise DeviceNotReady(self.name, f"{what}: device not started")
        return self._enqueue(what, fn, _URGENT if urgent else _NORMAL)

    def call(self, what: str, fn: Callable[[], T], *, timeout: float | None = None, urgent: bool = False) -> T:
        """Run ``fn`` in the worker and return its result; :class:`DeviceError` on failure, :class:`DeviceTimeout`
        when it does not finish within ``timeout`` seconds (default ``default_timeout_s``)."""
        return self._wait(self.submit(what, fn, urgent=urgent), what, timeout)

    def run_direct(self, what: str, fn: Callable[[], T]) -> T:
        """Run ``fn`` in the caller's thread, bypassing the queue, for operations that must not wait behind a long one
        (an emergency stop). Only for backends that are thread-safe for that operation."""
        if self.state != DeviceState.READY:
            raise DeviceNotReady(self.name, f"{what}: device {self.state}")
        try:
            return fn()
        except Exception as exc:
            raise self._handle_failure(exc, what) from exc

    def _enqueue(self, what: str, fn: Callable[[], Any], priority: int, *, needs_ready: bool = True) -> Future[Any]:
        future: Future[Any] = Future()
        self._queue.put(_Item(priority, next(self._seq), what, fn, future, needs_ready))
        return future

    def _wait(self, future: Future[T], what: str, timeout: float | None) -> T:
        limit = self.default_timeout_s if timeout is None else timeout
        try:
            return future.result(timeout=limit)
        except FutureTimeout:
            if future.cancel():                               # still queued: it will not run
                message = f"{what}: not started within {limit:g} s (device busy with {self._current!r})"
            else:
                message = f"{what}: no result within {limit:g} s"
                try:
                    self._interrupt(what)
                except Exception:
                    log.exception("%s: interrupt after timeout failed", self.name)
            self.emit("timeout", operation=what, timeout_s=limit)
            raise DeviceTimeout(self.name, message) from None

    # ============================================================================================== worker
    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            if not item.future.set_running_or_notify_cancel():
                continue
            self._current = item.what
            try:
                if item.needs_ready:
                    self._ensure_ready(item.what)
                result = item.fn()
            except DeviceError as exc:
                item.future.set_exception(exc)
            except Exception as exc:                          # noqa: BLE001 - any backend failure goes to the caller
                item.future.set_exception(self._handle_failure(exc, item.what))
            else:
                item.future.set_result(result)
            finally:
                self._current = None

    def _ensure_ready(self, what: str) -> None:
        state = self.state
        if state == DeviceState.READY:
            return
        if state == DeviceState.FAULT and self.auto_reconnect:
            self._reconnect(what)
            return
        error = self.last_error
        reason = f"device {state}" + (f" ({error.message})" if error is not None else "")
        raise DeviceNotReady(self.name, f"{what}: {reason}", code=error.code if error else None)

    def _do_open(self) -> None:
        self._set_state(DeviceState.OPENING)
        try:
            self._open()
        except Exception as exc:
            error = self._translate(exc, "open")
            self._set_state(DeviceState.FAULT, error)
            raise error from exc
        self._set_state(DeviceState.READY)

    def _do_close(self) -> None:
        try:
            self._close()
        except Exception as exc:                              # noqa: BLE001 - closing must always end in closed
            log.warning("%s: close failed: %s", self.name, exc)
        self._set_state(DeviceState.CLOSED)

    def _reconnect(self, what: str) -> None:
        last: DeviceError | None = None
        for attempt in range(1, self.max_reconnect_attempts + 1):
            self.emit("reconnecting", attempt=attempt, operation=what)
            try:
                self._close()
            except Exception as exc:                          # noqa: BLE001 - best effort before re-opening
                log.debug("%s: close before reconnect failed: %s", self.name, exc)
            try:
                self._do_open()
            except DeviceError as exc:
                last = exc
                log.warning("%s: reconnect attempt %d failed: %s", self.name, attempt, exc)
                if attempt < self.max_reconnect_attempts:
                    self._sleep(self.reconnect_delay_s)
                continue
            self.emit("reconnected", attempt=attempt)
            return
        raise DeviceNotReady(self.name, f"{what}: reconnection failed ({last.message if last else '?'})",
                             code=last.code if last else None)

    def _handle_failure(self, exc: BaseException, what: str) -> DeviceError:
        error = self._translate(exc, what)
        if self._is_link_failure(exc):
            log.error("%s: link failure during %s: %s", self.name, what, error)
            self._set_state(DeviceState.FAULT, error)
        return error

    # ============================================================================================== hooks
    def _open(self) -> None:
        """Open the hardware (worker thread). Raise on failure."""
        raise NotImplementedError

    def _close(self) -> None:
        """Release the hardware (worker thread). Should not raise for an already closed link."""
        raise NotImplementedError

    def _is_link_failure(self, exc: BaseException) -> bool:
        """TRUE when ``exc`` means the link is gone (the device enters ``fault``). Default: never."""
        return False

    def _translate(self, exc: BaseException, what: str) -> DeviceError:
        """Backend exception → :class:`DeviceError` (default: the exception text, no code)."""
        if isinstance(exc, DeviceError):
            return exc
        return DeviceError(self.name, f"{what}: {exc}")

    def _interrupt(self, what: str) -> None:
        """Called in the caller's thread when ``what`` timed out while running. Default: nothing."""
