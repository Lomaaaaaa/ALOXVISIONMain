"""The operator user interface (PySide6), replacing the LabVIEW front panels (map Part 1 §2.4, §3; migration plan
§3.6). First part: the main window ``alox_core_ui.vi``.

- :mod:`alox.ui.controller`: the window's logic without Qt (buttons, rights, status looks, starts and stops, the
  periodic refresh); testable on a core without a display.
- :mod:`alox.ui.adapters`: what the UI needs from the core (the :class:`~alox.ui.adapters.ImageProvider` protocol
  satisfied by :class:`alox.core.images.ImageStore`, the parameters of a local start, the wafer/stage conversion).
- :mod:`alox.ui.bridge`, :mod:`alox.ui.widgets`, :mod:`alox.ui.window`: the Qt side (events queued to the UI
  thread, blocking calls in workers).
- :mod:`alox.ui.theme`, :mod:`alox.ui.icons`: the look (light and dark themes as palette tokens and one style sheet,
  the icons drawn in code).
- :mod:`alox.ui.app`: :func:`alox.ui.app.run` shows the window for a started :class:`alox.app.Application`;
  ``python -m alox.ui --simulate`` runs the simulated application with it.

Qt (``PySide6``, extra ``ui``) is imported by the Qt modules only: importing :mod:`alox.ui.controller` does not need
it.
"""
