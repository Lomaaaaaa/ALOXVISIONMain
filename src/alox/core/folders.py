r"""Vision Core folders: ``alox_setup.ini [folders]`` → :class:`Folders`, and the folders the boot cycle creates.

Spec: map Part 1 §4.4 (folders created at boot), §2.3 (boot frame 14), Part 4 §3.10 (folder resolvers), Part 4 §4
(``alox_setup.ini``), "Owner's answers" §3.1 (the folders in effect come from ``alox_setup.ini [folders]``, read at every
call; the code defaults are fallbacks only); docs/09-configuration-logging-errors.md §3 (resolver semantics, shared with
the camera server: :mod:`aloxcam.config.folders`).

Observed in the diagrams (they correct the comments quoted in map §4.4):

- ``tools_windows_user_documents_folder d, d1, d2``: despite its name, the VI returns ``alox_setup.ini [folders]
  settings`` (default ``D:\alox\settings``); the registry read of the Windows "Personal" folder sits in a Disabled
  frame. LabVIEW labels its output "My Documents"; :attr:`Folders.settings` is that folder.
- ``alox_core_boot_cycle d38..d123`` (frame 14, 28 inner frames): the constants ``alox\settings``, ``alox\profile``,
  ``alox\profile_temp`` and ``alox\settings\alox.ini`` are left unwired; the wired names give ``<settings>\alox.ini``,
  ``<settings>\profile``, ``<settings>\profile_temp``. The "<USER>/Documents/alox/..." frame titles are obsolete. The
  ``<data>\data\coreX`` per-core branch is Disabled, as is the creation of ``<exposures>`` (inner frame 7, d49..d50).
- ``tools_alox_shared_{vision,data,profiles,exposures,jobhistory}_folder d``: ``Read Key (Path)`` of ``[folders]
  <key>`` with the defaults of :data:`FOLDER_DEFAULTS`; ``tools_alox_shared_images_folder d`` = ``[folders] data`` +
  ``\images``.

Order of the boot cycle, inner frames of frame 14 (``alox_core_boot_cycle d38..d123``): 0 ``<settings>``; 1 copy
``<exe>\alox_default.ini`` to ``<settings>\alox.ini`` when missing (:meth:`alox.core.properties.Properties.load`); 2
``<settings>\profile``; 3 ``<settings>\profile_temp``; 4 ``<data>``; 5 ``<profiles>``; 6 at least one profile
(:meth:`alox.core.profiles.ProfileManager.ensure_default_profile`); 7 ``<exposures>`` (Disabled); 8 ``<data>``; 9
``<vision>\logs``; 10 ``<data>\ChuckCalibration``; 11 ``<data>\images``; 12 nothing (Disabled ``images\NoJob``); 13..19
``<data>\images\{Misc, refocus, autoexpo, mapping, oxid, tilt, bowing}``; 20 ``<data>\files``; 21..26
``<data>\files\{autofocus, refocus, autoexpo, wavelength, mesa, bowing}``; 27 ``<data>\JobHistory``. The job-history
root of ``[folders] jobhistory`` is not created at boot: the job-tree VIs create their own sub-folders (Part 4 §3.10).

Deviations: the file is read once (LabVIEW re-reads it at every call); a missing ``alox_setup.ini`` is not created
(LabVIEW's ``Open Config Data`` creates it empty); an empty value gives the default with a warning (LabVIEW: an empty
path); paths are localized with :func:`aloxcam.lv.path.localize_path_text` (``D:\…`` off Windows needs
``$ALOX_DRIVE_D``).
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from aloxcam.config.folders import SETUP_INI_NAME, executable_folder
from aloxcam.config.niconfig import NIConfig
from aloxcam.lv.path import localize_path_text

__all__ = [
    "DEFAULTS_INI_NAME",
    "FILE_SUBFOLDERS",
    "FOLDER_DEFAULTS",
    "IMAGE_SUBFOLDERS",
    "SETTINGS_INI_NAME",
    "SETUP_INI_NAME",
    "FolderError",
    "Folders",
    "create_folders",
    "resolve_folders",
    "user_documents_folder",
]

log = logging.getLogger(__name__)

SECTION = "folders"
SETTINGS_INI_NAME = "alox.ini"
DEFAULTS_INI_NAME = "alox_default.ini"

#: ``[folders]`` keys and the hard-coded defaults of their resolver VIs (``tools_windows_user_documents_folder d2``,
#: ``tools_alox_shared_*_folder d``). The values really in effect are those of the shipped ``alox_setup.ini``
#: (``/D/alox/02-Vision/...``, ``jobhistory = /D/alox/04-JobHistory``; owner's answers §3.1).
FOLDER_DEFAULTS: Mapping[str, str] = MappingProxyType({
    "settings": r"D:\alox\settings",
    "vision": r"D:\alox\02-vision",
    "data": r"D:\alox\02-Vision\data",
    "profiles": r"D:\alox\02-Vision\profiles",
    "exposures": r"D:\alox\02-Vision\exposures",
    "jobhistory": r"D:\alox\02-Vision\JobHistory",
})

#: ``<data>\images\...`` created at boot (inner frames 13..19, ``alox_core_boot_cycle d69..d97``).
IMAGE_SUBFOLDERS = ("Misc", "refocus", "autoexpo", "mapping", "oxid", "tilt", "bowing")
#: ``<data>\files\...`` created at boot (inner frames 21..26, ``alox_core_boot_cycle d105..d120``).
FILE_SUBFOLDERS = ("autofocus", "refocus", "autoexpo", "wavelength", "mesa", "bowing")


class FolderError(ValueError):
    """A ``[folders]`` value cannot be used on this machine (``D:\\…`` off Windows without ``$ALOX_DRIVE_D``)."""


@dataclass(frozen=True)
class Folders:
    """The folders of the integrated application.

    ``exe`` is the application folder (``alox_setup.ini``, ``alox_default.ini``); the six others are the ``[folders]``
    values. Everything else is derived the way the boot cycle builds it (module docstring).
    """

    exe: Path
    settings: Path
    vision: Path
    data: Path
    profiles: Path
    exposures: Path
    jobhistory: Path

    # -- settings ("My Documents" in LabVIEW)
    @property
    def alox_ini(self) -> Path:
        """``<settings>\\alox.ini``, the application settings (``alox_manager_properties d1282``)."""
        return self.settings / SETTINGS_INI_NAME

    @property
    def defaults_ini(self) -> Path:
        """``<exe>\\alox_default.ini``, copied to :attr:`alox_ini` when missing (``alox_core_boot_cycle d39``)."""
        return self.exe / DEFAULTS_INI_NAME

    @property
    def profile(self) -> Path:
        """``<settings>\\profile``: the unzipped active profile (``alox_core_boot_cycle d42``)."""
        return self.settings / "profile"

    @property
    def profile_temp(self) -> Path:
        """``<settings>\\profile_temp``: the profile being edited (``alox_core_boot_cycle d43``)."""
        return self.settings / "profile_temp"

    # -- data
    @property
    def logs(self) -> Path:
        """``<vision>\\logs`` (``alox_core_boot_cycle d55``)."""
        return self.vision / "logs"

    @property
    def chuck_calibration(self) -> Path:
        """``<data>\\ChuckCalibration`` (``alox_core_boot_cycle d58``)."""
        return self.data / "ChuckCalibration"

    @property
    def images(self) -> Path:
        """``<data>\\images`` (``alox_core_boot_cycle d61``, ``tools_alox_shared_images_folder d``)."""
        return self.data / "images"

    @property
    def files(self) -> Path:
        """``<data>\\files`` (``alox_core_boot_cycle d102``)."""
        return self.data / "files"

    @property
    def data_jobhistory(self) -> Path:
        """``<data>\\JobHistory``, created at boot (``alox_core_boot_cycle d123``); not :attr:`jobhistory`."""
        return self.data / "JobHistory"

    def image_folder(self, name: str) -> Path:
        """``<data>\\images\\<name>`` for one of :data:`IMAGE_SUBFOLDERS` (exact spelling, ``Misc`` included)."""
        if name not in IMAGE_SUBFOLDERS:
            raise ValueError(f"unknown image folder {name!r} (expected one of {', '.join(IMAGE_SUBFOLDERS)})")
        return self.images / name

    def file_folder(self, name: str) -> Path:
        """``<data>\\files\\<name>`` for one of :data:`FILE_SUBFOLDERS`."""
        if name not in FILE_SUBFOLDERS:
            raise ValueError(f"unknown file folder {name!r} (expected one of {', '.join(FILE_SUBFOLDERS)})")
        return self.files / name

    def boot_folders(self) -> tuple[Path, ...]:
        """The folders the boot cycle creates, in its order (module docstring), each once."""
        ordered = [
            self.settings, self.profile, self.profile_temp, self.data, self.profiles, self.logs,
            self.chuck_calibration, self.images, *(self.images / n for n in IMAGE_SUBFOLDERS),
            self.files, *(self.files / n for n in FILE_SUBFOLDERS), self.data_jobhistory,
        ]
        seen: set[Path] = set()
        unique: list[Path] = []
        for folder in ordered:
            if folder not in seen:
                seen.add(folder)
                unique.append(folder)
        return tuple(unique)


def user_documents_folder(env: Mapping[str, str] = os.environ, platform: str = sys.platform) -> Path:
    """The user's Documents folder from the environment: ``%USERPROFILE%\\Documents`` on Windows, ``$HOME/Documents``
    elsewhere (the home folder when the variable is missing). LabVIEW's registry read of it is Disabled
    (``tools_windows_user_documents_folder d1``); it only serves as the fallback of :func:`resolve_folders`."""
    var = "USERPROFILE" if platform == "win32" else "HOME"
    base = env.get(var)
    return (Path(base) if base else Path.home()) / "Documents"


def _warn_default(message: str) -> None:
    log.warning("%s", message)


def resolve_folders(setup_ini: Path | None = None, documents: Path | None = None, *,
                    env: Mapping[str, str] = os.environ, platform: str = sys.platform,
                    warn: Callable[[str], None] | None = None) -> Folders:
    """Read ``alox_setup.ini [folders]`` once and return the folders in effect (owner's answers §3.1).

    ``setup_ini`` defaults to ``<exe>/alox_setup.ini`` (``exe`` = :func:`aloxcam.config.folders.executable_folder`,
    ``$ALOX_EXE_DIR`` first); otherwise ``exe`` is the folder of ``setup_ini``. ``documents`` replaces the folder
    LabVIEW calls "My Documents", i.e. ``[folders] settings`` (it holds ``alox.ini``, ``profile`` and
    ``profile_temp``), like the camera server's ``--settings-dir``.

    A missing or unreadable file gives every default with a warning (the file is not created); an empty value gives
    its default with a warning. A settings folder that cannot be mapped on this machine (``D:\\…`` off Windows without
    ``$ALOX_DRIVE_D``) falls back to ``<user Documents>/alox/settings`` (:func:`user_documents_folder`, the location
    named by the obsolete boot comments) with a warning; any other unmappable folder raises :class:`FolderError`.
    """
    report = warn or _warn_default
    exe = Path(setup_ini).parent if setup_ini is not None else executable_folder(env)
    setup = Path(setup_ini) if setup_ini is not None else exe / SETUP_INI_NAME
    try:
        cfg = NIConfig.load(setup)
    except FileNotFoundError:
        report(f"{setup}: not found; built-in folder defaults used (the file is not created)")
        cfg = NIConfig.parse("")
    except OSError as exc:
        report(f"{setup}: cannot be read ({exc}); built-in folder defaults used")
        cfg = NIConfig.parse("")

    found: dict[str, Path] = {}
    for key, default in FOLDER_DEFAULTS.items():
        if key == "settings" and documents is not None:
            found[key] = Path(documents)
            continue
        text = cfg.read_path(SECTION, key, default)
        if text.strip() == "":
            report(f"{setup}: [{SECTION}] {key} is empty; default {default} used")
            text = default
        local = localize_path_text(text, platform=platform, env=env)
        if local is None:
            if key != "settings":
                raise FolderError(f"[{SECTION}] {key} = {text!r} cannot be mapped to a local folder "
                                  "(set ALOX_DRIVE_<LETTER> or change alox_setup.ini)")
            local = user_documents_folder(env, platform) / "alox" / "settings"
            report(f"[{SECTION}] settings = {text!r} cannot be mapped to a local folder; {local} used")
        found[key] = local
    log.debug("folders resolved from %s: %s", setup, found)
    return Folders(exe=exe, **found)


def create_folders(folders: Folders) -> list[Path]:
    """Create :meth:`Folders.boot_folders` (``alox_core_boot_cycle`` frame 14) and return those that did not exist,
    in boot order. Idempotent; :class:`OSError` when a folder cannot be created (e.g. a file has its name)."""
    created: list[Path] = []
    for folder in folders.boot_folders():
        if folder.is_dir():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        created.append(folder)
    if created:
        log.info("created %d folder(s): %s", len(created), ", ".join(map(str, created)))
    return created
