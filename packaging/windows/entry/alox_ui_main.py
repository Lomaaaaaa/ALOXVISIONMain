"""``alox-ui.exe`` (windowed): ``alox --ui`` without a console window.

- The log goes to daily files in ``%LOCALAPPDATA%\\Aloxtec\\AloxVisionPy\\logs`` unless ``--log-dir`` is given; what
  would go to the console (warnings, tracebacks of threads) goes to ``alox-ui_console.log`` in the same folder.
- ``--demo`` (packaging only, the "Alox Vision (simulation demo)" shortcut): the simulated machine of
  ``python -m alox.devices.scene --make-demo``, written once to ``%LOCALAPPDATA%\\Aloxtec\\AloxVisionPy\\demo``, without
  the supervisor API (its ports may be used by LabVIEW on the machine). It is the same as
  ``alox --ui --simulate --no-api --setup-ini <demo>\\alox_setup.ini``.
- ``--screenshot PNG`` (tests of the build) renders the window as ``python -m alox.ui --screenshot`` and exits.
"""

import multiprocessing
import sys

from frozen_support import has_option, install_folder, user_folder


def _copy_translations(target):
    """The demo reads ``<demo>\\translations`` (the folder of its ``alox_setup.ini``): give it the installed
    ``<exe>\\translations\\*.lang`` it does not have yet."""
    import shutil

    source = install_folder() / "translations"
    files = sorted(source.glob("*.lang")) if source.is_dir() else []
    for path in files:
        if not (target / path.name).exists():
            target.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target / path.name)


def _arguments(argv: list[str]) -> tuple[list[str], bool]:
    args = list(argv)
    if "--demo" in args:
        args.remove("--demo")
        demo = user_folder("demo")
        setup = demo / "alox_setup.ini"
        if not setup.is_file():
            from alox.devices.scene import make_demo

            setup = make_demo(demo)
        _copy_translations(demo / "translations")
        args = ["--simulate", "--no-api", "--setup-ini", str(setup), *args]
    screenshot = has_option(args, "--screenshot")
    if not has_option(args, "--log-dir") and not screenshot:
        args += ["--log-dir", str(user_folder("logs"))]
    return args, screenshot


def main() -> int:
    args, screenshot = _arguments(sys.argv[1:])
    if sys.stderr is None or sys.stdout is None:                 # windowed: no console
        console = open(user_folder("logs") / "alox-ui_console.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or console
        sys.stderr = sys.stderr or console
    if screenshot:
        from alox.ui.app import main as ui_main

        return ui_main(args)
    from alox.app import main as app_main

    return app_main(["--ui", *args])


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
