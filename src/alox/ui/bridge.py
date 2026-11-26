"""Thread hand-off between the application and Qt: widgets are only touched in the UI thread.

- :class:`EventBridge`: a callback on the :class:`alox.events.EventBus` (called in the publisher's thread) emits a Qt
  signal; the receivers live in the UI thread, so Qt queues the delivery (``AutoConnection`` across threads).
- :class:`Worker`: runs blocking controller calls (devices, a function's stop) in one background thread and gives
  the outcome back to a callback in the UI thread, through a signal as well.
- :func:`to_qimage`: a Pillow image (``ImageStore.merged()``) as a :class:`QImage` (safe outside the UI thread).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import wait as wait_futures
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from alox.events import Event, EventBus

__all__ = ["EventBridge", "Worker", "to_qimage"]

log = logging.getLogger(__name__)


class EventBridge(QObject):
    """Re-emits the bus events whose topic starts with one of ``topics`` as :attr:`event` (queued to the UI)."""

    event = Signal(object)

    def __init__(self, bus: EventBus, topics: Iterable[str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._remove: Callable[[], None] | None = bus.on(tuple(topics), self._forward)

    def _forward(self, event: Event) -> None:
        try:
            self.event.emit(event)
        except RuntimeError:                                     # the bridge was deleted while the bus published
            pass

    def close(self) -> None:
        if self._remove is not None:
            self._remove()
            self._remove = None


class Worker(QObject):
    """One background thread; :meth:`submit` returns at once and ``on_done(future)`` runs later in the UI thread."""

    _done = Signal(object, object)

    def __init__(self, name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(1, thread_name_prefix=name)
        self._pending: set[Future[Any]] = set()
        self._done.connect(self._finish)

    def submit(self, fn: Callable[..., Any], *args: Any,
               on_done: Callable[[Future[Any]], None] | None = None) -> Future[Any]:
        future = self._pool.submit(fn, *args)
        self._pending.add(future)

        def emit(done: Future[Any]) -> None:
            try:
                self._done.emit(on_done, done)
            except RuntimeError:                                 # the worker was deleted meanwhile
                pass
        future.add_done_callback(emit)
        return future

    def _finish(self, on_done: Callable[[Future[Any]], None] | None, future: Future[Any]) -> None:
        self._pending.discard(future)
        if future.exception() is not None:
            log.error("UI action failed: %s", future.exception(), exc_info=future.exception())
        if on_done is not None:
            on_done(future)

    @property
    def busy(self) -> bool:
        return bool(self._pending)

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the calls submitted so far (their UI callbacks still need the event loop)."""
        pending = list(self._pending)
        _done, not_done = wait_futures(pending, timeout)
        return not not_done

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def to_qimage(image: Any) -> QImage:
    """A Pillow image (grey ``L`` or anything convertible to ``RGB``) as an owned :class:`QImage`."""
    if image.mode == "L":
        fmt, depth = QImage.Format.Format_Grayscale8, 1
    else:
        if image.mode != "RGB":
            image = image.convert("RGB")
        fmt, depth = QImage.Format.Format_RGB888, 3
    width, height = image.size
    data = image.tobytes()
    return QImage(data, width, height, width * depth, fmt).copy()
