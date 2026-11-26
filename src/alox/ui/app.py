"""Show the operator window for a running application, and ``python -m alox.ui``.

:func:`run` takes an :class:`alox.app.Application` whose core has been started (``Application.start()``), shows the
main window and runs the Qt event loop until the window closes or the application asks to stop
(``Application.exit_requested``: EXIT, the supervisor's ``PUT /v1/system/shutdown``, Ctrl+C). The caller stops the
application afterwards (``with Application(...)``).

To show the UI from ``alox.app.main`` (not changed here), replace its wait loop, after ``states = app.start()``, by::

    if args.ui:                                   # a new --ui option
        from alox.ui.app import run
        return run(app)

``python -m alox.ui --simulate`` does exactly that with every device simulated; ``--theme light|dark`` chooses the
look (:mod:`alox.ui.theme`; default: the operator's last choice, remembered in the user settings, else light).
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from alox.ui import theme as themes
from alox.ui.controller import Controller
from alox.ui.window import MainWindow

__all__ = ["create_window", "main", "run", "screenshot"]

log = logging.getLogger(__name__)


def _qapp(argv: Sequence[str] | None = None) -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(list(argv) if argv is not None else sys.argv[:1])
    app.setApplicationName("ALOX Vision Core")
    return app                                                   # type: ignore[return-value]


def create_window(application: Any, **kwargs: Any) -> MainWindow:
    """The main window of ``application`` (its core must exist; ``application.live`` is the continuous Get Image)."""
    if application.core is None:
        raise ValueError("the application has no core (AppConfig(core=True))")
    controller = Controller(application.core, live=getattr(application, "live", None))
    return MainWindow(controller, **kwargs)


def run(application: Any, *, argv: Sequence[str] | None = None, theme: str | None = None) -> int:
    """Show the window and run the event loop (module docstring); returns the Qt exit code. ``theme``: ``light`` or
    ``dark`` (None: the user's last choice)."""
    app = _qapp(argv)
    themes.apply(app, theme)
    window = create_window(application)
    window.resize(1600, 900)
    window.show()

    def check_exit() -> None:
        if application.exit_requested.is_set():
            window.allow_close()
            window.close()
            app.quit()
    watch = QTimer()
    watch.setInterval(200)                  # also lets Python run its signal handlers during the Qt loop
    watch.timeout.connect(check_exit)
    watch.start()
    previous = signal.signal(signal.SIGINT, lambda *_a: application.exit_requested.set())
    try:
        return app.exec()
    finally:
        signal.signal(signal.SIGINT, previous)
        watch.stop()
        window.shutdown()


def screenshot(application: Any, path: str | Path, *, size: tuple[int, int] = (1600, 900),
               wait_image_s: float = 10.0, theme: str | None = None) -> Path:
    """Render the window of ``application`` to a PNG (offscreen use: documentation, reviews); waits up to
    ``wait_image_s`` for the first image of the display. ``theme`` as :func:`run`."""
    themes.apply(_qapp(), theme)
    window = create_window(application)
    try:
        window.resize(*size)
        window.show()
        end = time.monotonic() + wait_image_s
        while time.monotonic() < end:
            window.poll()
            window.refresh_image()
            window.flush(2.0)
            if window.view.has_image:
                break
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 100)
            time.sleep(0.1)
        window.view.zoom_fit()
        window.flush(2.0)
        target = Path(path)
        if not window.grab().save(str(target)):
            raise OSError(f"cannot write {target}")
        return target
    finally:
        window.allow_close()
        window.close()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m alox.ui", description="ALOX Vision Core with the operator window")
    p.add_argument("--simulate", action="store_true", help="simulated devices (no hardware)")
    p.add_argument("--setup-ini", type=Path, help="alox_setup.ini with the [folders] (default: <exe>/alox_setup.ini)")
    p.add_argument("--settings-dir", type=Path, help="replaces [folders] settings (alox.ini, profile, profile_temp)")
    p.add_argument("--devices", default="z,xy,wl,highmag,lowmag", help="comma-separated devices to use")
    p.add_argument("--no-api", action="store_true", help="run without the REST and websocket servers")
    p.add_argument("--rest-port", type=int, default=8002, help="REST API port (default 8002)")
    p.add_argument("--ws-port", type=int, default=6123, help="websocket events port (default 6123)")
    p.add_argument("--host", default="0.0.0.0", help="address the API listens on (default 0.0.0.0)")
    p.add_argument("--theme", choices=tuple(themes.THEMES), help="light or dark (default: the last one chosen)")
    p.add_argument("--screenshot", type=Path, help="render the window to this PNG and exit")
    p.add_argument("--log-level", default="INFO", help="DEBUG, INFO, WARNING, ERROR (default INFO)")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m alox.ui [--simulate] ...``: the whole application (core, devices, API) with the window."""
    from alox.app import AppConfig, Application

    args = _parser().parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    devices = tuple(d.strip().lower() for d in args.devices.split(",") if d.strip())
    config = AppConfig(simulate=args.simulate, core=True, devices=devices, setup_ini=args.setup_ini,
                       settings_dir=args.settings_dir, api=not args.no_api, host=args.host,
                       rest_port=args.rest_port, ws_port=args.ws_port)
    with Application(config) as application:
        application.start()
        if args.screenshot is not None:
            screenshot(application, args.screenshot, theme=args.theme)
            print(args.screenshot)
            return 0
        return run(application, theme=args.theme)
