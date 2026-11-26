"""Start-up helpers of the frozen build (``packaging/windows/alox.spec``); not part of the package ``alox``.

The installed application lives in a read-only folder (``C:\\Program Files\\Aloxtec\\AloxVisionPy``), so everything
the executables write by default goes to the user's folders:

- the log of the windowed ``alox-ui.exe`` (no console): ``%LOCALAPPDATA%\\Aloxtec\\AloxVisionPy\\logs``;
- the simulation demo of ``alox-ui.exe --demo``: ``%LOCALAPPDATA%\\Aloxtec\\AloxVisionPy\\demo`` (written once by
  ``alox.devices.scene.make_demo``; delete the folder to start again from the shipped demo);
- the reports of the check tools: the current folder, unless it is the installation folder or is not writable, then
  ``Documents\\AloxVisionPy\\reports`` (``--out`` still chooses another one).

Off Windows ``%LOCALAPPDATA%`` is replaced by ``~/.local/state`` (Linux test builds).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

APP_FOLDER = ("Aloxtec", "AloxVisionPy")


def install_folder() -> Path:
    """The folder of the executables (``<exe>``: ``alox_setup.ini``, ``alox_default.ini``, ``translations``)."""
    return Path(sys.executable).resolve().parent


def user_folder(*parts: str) -> Path:
    """``%LOCALAPPDATA%\\Aloxtec\\AloxVisionPy\\<parts>``, created."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "state")
    path = Path(base, *APP_FOLDER, *parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def has_option(argv: Sequence[str], name: str) -> bool:
    return any(arg == name or arg.startswith(name + "=") for arg in argv)


def report_folder() -> Path:
    """Default folder of the check-tool reports (see the module docstring)."""
    cwd = Path.cwd()
    try:
        usable = cwd.resolve() != install_folder() and os.access(cwd, os.W_OK)
    except OSError:
        usable = False
    if usable:
        return cwd
    path = Path.home() / "Documents" / APP_FOLDER[1] / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_tool(module: ModuleType, commit: str | None = None) -> int:
    """Run a check tool of ``tools/`` (its ``main()``) with its reports in :func:`report_folder` instead of the
    bundle's internal folder (the tools write next to their own file by default), and ``commit`` (the source commit of
    the build, or None) as the commit quoted in their reports."""
    out = report_folder()
    for name in (module.__name__, "z_stage_check"):     # xy_stage_check also uses z_stage_check's helpers
        mod = sys.modules.get(name)
        if mod is None:
            continue
        if hasattr(mod, "TOOL_DIR"):
            mod.TOOL_DIR = out
        if hasattr(mod, "_git_commit"):
            mod._git_commit = lambda: commit
        mod.__file__ = str(out / Path(mod.__file__ or f"{name}.py").name)   # af_latency_check: --out default
    return int(module.main() or 0)
