"""Application events: a thread-safe publish/subscribe bus with lossless per-subscriber queues.

LabVIEW Vision Core passes events through a few named notifiers, so a later event can overwrite one that was not read yet
(map Part 5). Here every subscriber has its own queue: nothing is lost unless the subscriber asks for a bounded queue,
and then the oldest events are dropped and counted.

Topics are dotted names (``"device.z.state"``); a subscription filters on topic prefixes.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Event", "EventBus", "Publish", "Subscription"]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    """One event. ``topic`` is a dotted name, ``source`` the component that published it, ``time`` the wall-clock time
    (seconds since the epoch) and ``data`` a read-only mapping of plain values."""

    topic: str
    source: str
    data: Mapping[str, Any] = field(default_factory=dict)
    time: float = field(default_factory=time.time)

    def matches(self, prefixes: tuple[str, ...]) -> bool:
        """TRUE when ``prefixes`` is empty or the topic equals one of them or starts with ``<prefix>.``."""
        if not prefixes:
            return True
        return any(self.topic == p or self.topic.startswith(p + ".") for p in prefixes)


#: What a component needs to publish: ``publish(event)``. ``EventBus.publish`` fits; tests can pass ``list.append``.
Publish = Callable[[Event], None]


class Subscription:
    """A subscriber's queue. Read it with :meth:`get` or iterate with :meth:`drain`; close it with :meth:`close`."""

    def __init__(self, bus: EventBus, prefixes: tuple[str, ...], maxsize: int) -> None:
        self._bus = bus
        self.prefixes = prefixes
        self.maxsize = maxsize
        self._queue: queue.Queue[Event] = queue.Queue()
        self._lock = threading.Lock()
        self.dropped = 0
        self.closed = False

    def _offer(self, event: Event) -> None:
        if self.closed or not event.matches(self.prefixes):
            return
        with self._lock:
            if self.maxsize > 0:
                while self._queue.qsize() >= self.maxsize:
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        break
                    self.dropped += 1
            self._queue.put(event)

    def get(self, timeout: float | None = None) -> Event | None:
        """The next event, waiting up to ``timeout`` seconds (None = forever); None on timeout."""
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> list[Event]:
        """Every event already queued, oldest first, without waiting."""
        out: list[Event] = []
        while True:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                return out

    def close(self) -> None:
        """Stop receiving events (idempotent)."""
        self.closed = True
        self._bus._remove(self)

    def __iter__(self) -> Iterator[Event]:
        return iter(self.drain())


class EventBus:
    """Publish/subscribe hub. ``publish`` never blocks and may be called from any thread; callbacks registered with
    :meth:`on` run in the publisher's thread and must be quick (their exceptions are logged and swallowed)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscriptions: list[Subscription] = []
        self._callbacks: list[tuple[tuple[str, ...], Callable[[Event], None]]] = []

    def subscribe(self, prefixes: Iterable[str] = (), *, maxsize: int = 0) -> Subscription:
        """A new queue receiving the events whose topic starts with one of ``prefixes`` (all events when empty).
        ``maxsize`` > 0 bounds the queue: the oldest events are then dropped and counted in ``dropped``."""
        sub = Subscription(self, tuple(prefixes), maxsize)
        with self._lock:
            self._subscriptions.append(sub)
        return sub

    def on(self, prefixes: Iterable[str], callback: Callable[[Event], None]) -> Callable[[], None]:
        """Call ``callback(event)`` for matching events; returns a function that removes the callback."""
        entry = (tuple(prefixes), callback)
        with self._lock:
            self._callbacks.append(entry)

        def remove() -> None:
            with self._lock:
                if entry in self._callbacks:
                    self._callbacks.remove(entry)
        return remove

    def publish(self, event: Event) -> None:
        """Deliver ``event`` to every matching subscription and callback."""
        with self._lock:
            subscriptions = list(self._subscriptions)
            callbacks = list(self._callbacks)
        for sub in subscriptions:
            sub._offer(event)
        for prefixes, callback in callbacks:
            if event.matches(prefixes):
                try:
                    callback(event)
                except Exception:                                   # a faulty listener must not break the publisher
                    log.exception("event callback failed for %s", event.topic)

    def _remove(self, sub: Subscription) -> None:
        with self._lock:
            if sub in self._subscriptions:
                self._subscriptions.remove(sub)
