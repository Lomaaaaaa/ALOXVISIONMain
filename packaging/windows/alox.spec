# -*- mode: python ; coding: utf-8 -*-
r"""PyInstaller spec of ALOX Vision (Python): one folder ``AloxVisionPy`` with every executable.

    pyinstaller --noconfirm --clean --distpath <dist> --workpath <work> packaging\windows\alox.spec

(``build.ps1`` does it in a clean venv.) The folder holds:

- ``alox.exe``: console, the integrated application (``alox.app:main``): the server / headless application, ``--check``,
  ``--ui``; ``alox-ui.exe``: windowed, the same with ``--ui`` (plus ``--demo``, see ``entry/alox_ui_main.py``);
- the check tools of ``tools/`` as console executables: ``z_stage_check``, ``xy_stage_check``, ``wl_check``,
  ``camera_check``, ``thermal_check``, ``af_latency_check`` (their reports go to the current folder, or
  ``Documents\AloxVisionPy\reports`` when it is the installation folder; ``entry/frozen_support.py``);
- ``_internal\``: Python, the packages ``alox`` / ``aloxcam`` (from ``src/``, compiled) and their dependencies
  (NumPy, SciPy, Pillow, OpenCV, PySide6 with its Qt plugins, pyserial, FastAPI / uvicorn / websockets, cryptography,
  Harvesters / genicam);
- ``alox_setup.ini.example`` (the template of the machine's ``alox_setup.ini``).

Nothing machine-specific is bundled: ``alox_setup.ini``, ``alox_default.ini``, ``translations\*.lang``, the licence
files (``licence.alox``, ``licence_public_key.txt``) and the IDS GenTL producer stay outside the build (see
``alox.iss`` and ``README.md``). The tests (``tests/``, the ``test`` packages of the dependencies) are excluded.

Platform: the target is Windows x64 (Python 3.13); on Linux the same spec builds the same folder without ``.exe``
(used to check the bundle; the version resource and the icon are Windows only). The packages are taken from ``src/``
of this repository (``pathex``), the dependencies from the active environment. Every dependency of the extras
``stage, api, ui, vision, licence`` plus ``harvesters`` must be importable; ``ALOX_PYI_ALLOW_MISSING=1`` turns a
missing one into a warning (test builds only: the bundle then lacks that feature).
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

SPEC_DIR = Path(SPECPATH).resolve()          # noqa: F821 - set by PyInstaller
REPO = SPEC_DIR.parents[1]
SRC = REPO / "src"
TOOLS = REPO / "tools"
ENTRY = SPEC_DIR / "entry"
WINDOWS = sys.platform == "win32"
BUNDLE = "AloxVisionPy"
GENERATED = Path(workpath) / "entry"         # noqa: F821 - set by PyInstaller
GENERATED.mkdir(parents=True, exist_ok=True)

PATHEX = [str(SRC), str(TOOLS), str(ENTRY), str(GENERATED)]
sys.path[:0] = PATHEX                        # collect_submodules() below imports alox / aloxcam from src/

# ------------------------------------------------------------------------------------------------ the dependencies
#: import name → pip name: the extras stage, api, ui, vision, licence and the camera driver.
REQUIRED = {
    "numpy": "numpy", "scipy": "scipy", "PIL": "pillow",
    "serial": "pyserial",
    "fastapi": "fastapi", "uvicorn": "uvicorn", "websockets": "websockets",
    "PySide6": "PySide6",
    "cv2": "opencv-python-headless",
    "cryptography": "cryptography",
    "harvesters": "harvesters", "genicam": "genicam",
}
missing = [pip for name, pip in REQUIRED.items() if importlib.util.find_spec(name) is None]
if missing:
    text = (f"alox.spec: missing in this Python ({sys.executable}): {', '.join(missing)}. Install them with "
            f"pip install \".[stage,api,ui,vision,licence]\" harvesters (build.ps1 does it).")
    if os.environ.get("ALOX_PYI_ALLOW_MISSING") != "1":
        raise SystemExit(text)
    print("WARNING: " + text + " Building without them (ALOX_PYI_ALLOW_MISSING=1).")


def _present(name):
    return importlib.util.find_spec(name) is not None


def _not_test(name):
    return not any(part in ("test", "tests", "testing", "conftest") for part in name.split("."))


def _submodules(package):
    return collect_submodules(package, filter=_not_test) if _present(package) else []


#: Modules imported by name at run time, which the analysis cannot see: the measurement functions and their parameter
#: classes (alox.functions.registry, alox.ui.settings_panels / adapters), the OpenCV matcher (alox.vision.matching),
#: the pyserial URL handlers (socket://, loop://: --port-map), uvicorn's protocol / loop modules, the lazy modules of
#: websockets, the camera driver (imported on first use by alox.devices.camera_genicam).
HIDDEN = (
    _submodules("alox") + _submodules("aloxcam")
    + _submodules("serial.urlhandler") + ["serial.tools.list_ports"]
    + (["cv2"] if _present("cv2") else [])
    + _submodules("uvicorn") + _submodules("websockets")
    + _submodules("harvesters") + _submodules("genicam")
)

DATAS = [
    (str(SRC / "alox" / "devices" / "scene_demo" / "*.ini"), "alox/devices/scene_demo"),   # the simulation demo
    (str(SRC / "aloxcam" / "data" / "*.toml"), "aloxcam/data"),                          # wire enum tables
    # camera_check --fake: the fake stack's "producer" is the module file itself (FAKE_PRODUCER = Path(__file__)),
    # which must exist; the module is still imported from the archive.
    (str(SRC / "alox" / "devices" / "fake_harvesters.py"), "alox/devices"),
]
for dist in ("harvesters", "genicam"):       # camera_check reports their versions (importlib.metadata)
    if _present(dist):
        DATAS += copy_metadata(dist)
if _present("genicam"):
    DATAS += collect_data_files("genicam", includes=["licenses/*"])
BINARIES = collect_dynamic_libs("genicam") if _present("genicam") else []   # GenApi libraries next to _genapi

EXCLUDES = [
    "tkinter", "_tkinter", "matplotlib", "IPython", "jupyter_client", "notebook",
    "pytest", "_pytest", "py", "hypothesis", "httpx",               # test dependencies ([dev])
    "tests", "harvesters.test", "numpy.tests", "scipy.conftest",
    # Qt modules the window does not use (PySide6 is installed with all of them)
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtWebChannel",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner",
]

# ------------------------------------------------------------------------------------------------ the entry scripts
def _commit():
    try:
        done = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=REPO, capture_output=True, text=True,
                              timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None if done.returncode == 0 else None


COMMIT = _commit()
TOOL_NAMES = ["z_stage_check", "xy_stage_check", "wl_check", "camera_check", "thermal_check", "af_latency_check"]
TOOL_ENTRY = '''"""{name}.exe: tools/{name}.py (generated by packaging/windows/alox.spec)."""

import sys

from frozen_support import run_tool

import {name} as tool

if __name__ == "__main__":
    sys.exit(run_tool(tool, {commit!r}))
'''
tool_scripts = {}
for name in TOOL_NAMES:
    path = GENERATED / f"run_{name}.py"
    path.write_text(TOOL_ENTRY.format(name=name, commit=COMMIT), encoding="utf-8")
    tool_scripts[name] = path

#: executable name → (script, console)
PROGRAMS = {"alox": (ENTRY / "alox_main.py", True), "alox-ui": (ENTRY / "alox_ui_main.py", False)}
PROGRAMS.update({name: (path, True) for name, path in tool_scripts.items()})

# ------------------------------------------------------------------------------------------------ Windows resources
ICON = SPEC_DIR / "alox.ico"                  # optional: put an icon there before building
ICON = str(ICON) if WINDOWS and ICON.is_file() else None


def _version_file():
    """The Windows version resource (Explorer > Properties > Details), from ``alox.__version__``."""
    from alox import __version__
    from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                     VarFileInfo, VarStruct, VSVersionInfo)

    numbers = tuple(int(part) for part in (__version__.split(".") + ["0"] * 4)[:4] if part.isdigit())
    numbers = (numbers + (0, 0, 0, 0))[:4]
    info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numbers, prodvers=numbers),
        kids=[StringFileInfo([StringTable("040904B0", [
                  StringStruct("CompanyName", "Aloxtec"),
                  StringStruct("FileDescription", "ALOX Vision (Python)"),
                  StringStruct("FileVersion", __version__),
                  StringStruct("ProductName", "ALOX Vision (Python)"),
                  StringStruct("ProductVersion", __version__ + (f" ({COMMIT})" if COMMIT else ""))])]),
              VarFileInfo([VarStruct("Translation", [0x0409, 1200])])])
    path = Path(workpath) / "version_info.txt"          # noqa: F821
    path.write_text(str(info), encoding="utf-8")
    return str(path)


VERSION = _version_file() if WINDOWS else None

# ------------------------------------------------------------------------------------------------ the build
# One analysis for every program: they share the same modules (af_latency_check runs the application's functions,
# the other tools its device drivers), so the folder holds each library once.
a = Analysis(                                 # noqa: F821
    [str(script) for script, _console in PROGRAMS.values()],
    pathex=PATHEX,
    binaries=BINARIES,
    datas=DATAS,
    hiddenimports=HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,                               # keep the docstrings and asserts (argparse help, checks)
)
# Qt libraries that only Qt plugins the window does not need bring in (the virtual-keyboard input context needs
# Qt Quick / QML, the PDF image format Qt Pdf): about 20 MB.
UNUSED_QT = re.compile(r"^(lib)?(qt6(quick|qml|pdf|virtualkeyboard)|qpdf|qtvirtualkeyboard)", re.IGNORECASE)
a.binaries = [entry for entry in a.binaries if not UNUSED_QT.match(Path(entry[0]).name)]
pyz = PYZ(a.pure)                             # noqa: F821
user_scripts = {Path(script).stem for script, _console in PROGRAMS.values()}

executables = []
for exe_name, (script, console) in PROGRAMS.items():
    # a.scripts = PyInstaller's bootstrap and runtime hooks, then every user script: keep the hooks and this one.
    scripts = [entry for entry in a.scripts if entry[0] not in user_scripts or entry[0] == script.stem]
    executables.append(EXE(                   # noqa: F821
        pyz,
        scripts,
        [],
        exclude_binaries=True,
        name=exe_name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,                            # UPX breaks Qt / OpenCV DLLs and triggers anti-virus false positives
        console=console,
        disable_windowed_traceback=False,
        icon=ICON,
        version=VERSION,
    ))

coll = COLLECT(                               # noqa: F821
    *executables,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=BUNDLE,
)

# Next to the executables: the template of alox_setup.ini (the installer copies it as alox_setup.ini.example too).
shutil.copy2(SPEC_DIR / "alox_setup.ini.example", Path(DISTPATH) / BUNDLE / "alox_setup.ini.example")  # noqa: F821
