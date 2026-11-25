"""The core's command bus: the replacement of LabVIEW's process queue (map Part 1 §2.5).

LabVIEW feeds the core's consumer loop through the named queue ``alox.process.queue`` of **3** items: a fourth sender
blocks until the consumer takes one (map Part 1 §8). Here :meth:`CommandBus.post` never blocks: commands wait in an
unbounded queue and are handled one at a time, in order, by one consumer thread, as in LabVIEW. Each command has a
:class:`concurrent.futures.Future`, so a caller that needs the outcome (a test, the UI) can wait for it, while the REST
API can return at once as LabVIEW does.

The methods and their values are those of ``alox_typedef_process_queue_item`` (typedefs.md).
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from alox.events import Event, Publish

__all__ = ["Command", "CommandBus", "Method", "UnknownCommand"]

log = logging.getLogger(__name__)


class Method(IntEnum):
    """``alox_typedef_process_queue_item`` Method (U16)."""

    INIT = 0
    EXIT = 1
    MEASURE_MESA = 2
    MEASURE_TILT = 3
    MEASURE_BOWING = 4
    STOP = 5
    LOAD_PROFILE = 6
    PROFILES_FOLDER_CHANGED = 7
    GET_IMAGE = 8
    MODULE_HEARTBEAT = 9
    ABORT = 10
    REFRESH_PROFILES = 11
    REFRESH_CORE_INFOS = 12
    LOG_WINDOW_HIDDEN = 13
    SYSTEM_RESET = 14
    AXIS_REFERENCING = 15
    SYSTEM_SHUTDOWN = 16
    SHOW_UI = 17
    MODULE_RESETTED = 18
    MESA_JOB_FINISHED = 19
    BOWING_JOB_FINISHED = 20
    TILT_JOB_FINISHED = 21

    @property
    def label(self) -> str:
        """The LabVIEW item text (``"Measure Mesa"``)."""
        return self.name.replace("_", " ").title().replace("Ui", "UI")


class UnknownCommand(LookupError):
    """No handler is registered for a command's method."""


@dataclass(frozen=True)
class Command:
    """One command: ``method``, its ``payload`` (the LabVIEW variant), who sent it (``"ui"``, ``"api"``,
    ``"internal"``) and when (wall clock)."""

    method: Method
    payload: Any = None
    source: str = "internal"
    time: float = field(default_factory=time.time)


Handler = Callable[[Command], Any]


@dataclass(order=True)
class _Item:
    seq: int
    command: Command = field(compare=False)
    future: Future[Any] = field(compare=False)


class CommandBus:
    """Ordered, non-blocking command delivery to one consumer thread. Events: ``command.failed`` (a handler raised)
    and ``command.unknown`` (no handler)."""

    def __init__(self, *, publish: Publish | None = None, name: str = "core-commands") -> None:
        self._publish = publish
        self._name = name
        self._handlers: dict[Method, Handler] = {}
        self._queue: queue.Queue[_Item | None] = queue.Queue()
        self._seq = itertools.count()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stopped = False
        self.current: Command | None = None

    def register(self, method: Method, handler: Handler) -> None:
        """Handle ``method`` with ``handler(command)`` (replaces a previous handler)."""
        with self._lock:
            self._handlers[method] = handler

    def start(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(target=self._consume, name=self._name, daemon=True)
            self._thread.start()

    def post(self, method: Method | Command, payload: Any = None, *, source: str = "internal") -> Future[Any]:
        """Queue a command and return at once; the future gives the handler's result or exception."""
        command = method if isinstance(method, Command) else Command(Method(method), payload, source)
        future: Future[Any] = Future()
        with self._lock:
            if self._stopped:
                future.set_exception(RuntimeError("command bus stopped"))
                return future
            self._queue.put(_Item(next(self._seq), command, future))
        return future

    def pending(self) -> int:
        """Commands waiting (not counting the one being handled)."""
        return self._queue.qsize()

    def stop(self, timeout: float = 10.0) -> None:
        """Handle the commands already queued, then stop the consumer (idempotent)."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            thread = self._thread
        self._queue.put(None)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def _consume(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            if not item.future.set_running_or_notify_cancel():
                continue
            command = item.command
            with self._lock:
                handler = self._handlers.get(command.method)
            self.current = command
            try:
                if handler is None:
                    self._emit("command.unknown", command)
                    raise UnknownCommand(f"no handler for {command.method.label!r}")
                result = handler(command)
            except Exception as exc:
                if not isinstance(exc, UnknownCommand):
                    log.exception("command %s failed", command.method.label)
                    self._emit("command.failed", command, error=str(exc))
                item.future.set_exception(exc)
            else:
                item.future.set_result(result)
            finally:
                self.current = None

    def _emit(self, topic: str, command: Command, **data: Any) -> None:
        if self._publish is not None:
            self._publish(Event(topic, "core", {"method": command.method.label, "source": command.source, **data}))
