r"""Recipes ("profiles"): the replacement of ``alox_manager_profile``.

Spec: map Part 1 §4.2 (methods, sections, files, variant methods, active profile), §2.3 (boot frame 14 inner frame
6: "Check if at least one profile exists, if not create a default one, and set it as default",
``alox_core_boot_cycle d46..d48``; init frame 25 "Init Profile": ``general.profile`` → "Load", "Check"), §8 note 8
(profile working copy), Part 4 §3.12 (``tools_zip_profile_folder``, ``tools_clear_folder``); folders:
:mod:`alox.core.folders`.

LabVIEW methods (``alox_typedef_profile_manager_method``) → :class:`ProfileManager`: Get Profiles List →
:meth:`~ProfileManager.list`, Get Current Profile → :attr:`~ProfileManager.current`, New → :meth:`~ProfileManager.new`,
Load → :meth:`~ProfileManager.load`, Save → :meth:`~ProfileManager.save`, Get Properties List / Get Property / Set
Property / Add Property / Del Property / Get All Properties → the :class:`ProfileProperties` of the active profile
(:meth:`~ProfileManager.get`, :meth:`~ProfileManager.set`, :attr:`~ProfileManager.properties`), Check →
:meth:`~ProfileManager.check`, New To Variant Out / Load to Variant Out / Save From Variant In →
:meth:`~ProfileManager.new_copy` / :meth:`~ProfileManager.load_copy` / :meth:`~ProfileManager.save_copy` (the profile
editor works on a copy in ``profile_temp`` without touching the active profile). Delete and copy are done by the
profiles UI in LabVIEW (map Part 1 §3, ``alox_profiles_ui``): :meth:`~ProfileManager.delete`,
:meth:`~ProfileManager.copy`.

Observed in the diagrams:

- "Get Profiles List" (``alox_manager_profile d3``): ``List Folder <profiles> "*.zip"``, file names without extension,
  then ``To Upper Case`` (glyph "aA"): a file ``default.zip`` is listed as ``DEFAULT``.
- "Load" (d1149): the name goes through ``To Lower Case`` (glyph "Aa") before ``.zip`` and ``.ini`` are appended;
  ``tools_clear_folder`` empties ``<settings>\profile``, ``Unzip`` extracts ``<profiles>\<name>.zip`` into it, then
  ``<profile>\<name>.ini`` is read like ``alox.ini`` (split at the last ``_``; types as in
  :mod:`alox.core.properties`, the 2-D arrays being ``TABLE`` keys read with OpenG ``Read Key (Variant)``).
- "Save" (d1167): ``Open Config Data(<profile>\<name>.ini, create)``, one ``Write Key`` per property (Boolean →
  BOOL, arrays → ``TABLE`` through OpenG ``Write Key (Variant)``, …), then ``tools_zip_profile_folder``. That VI (``c``,
  ``d``, ``d1..d4``) lists the top-level files of the folder and zips them flat into ``<profiles>\<name>.zip``, except
  the ``.ini`` files whose name is not ``<name>`` ("Ignore other profiles .ini").
- "Load to Variant Out" / "Save From Variant In" (d1158, d1178): the same with ``<settings>\profile_temp``.
- "New" / "New To Variant Out" (d5): "recreate current profile folder" (``profile``, resp. ``profile_temp``: delete +
  create), then the hard-coded defaults of 17 sections. "Check" (d0, d1): adds every missing default. "Set Property"
  (d1204) refuses an unknown name (9006); "Set Property Default" only sets a missing one.
- d0: the constant default exposure table (see :data:`alox.core.exposures.PROFILE_DEFAULT_TABLE`).

Not ported: the hundreds of hard-coded defaults of "New"/"Check" (1222 diagram images). :meth:`ProfileManager.new`
takes them from a template (an INI file such as a ``default.ini`` taken from a LabVIEW system, a store or a mapping);
the only built-in default is ``general.exposures``.

Deviations: a profile archive is checked before the working folder is cleared, and extracted through a staging
folder, so a bad archive leaves the active profile intact; archive members that would land outside the folder
(absolute paths, ``..``, drive letters, symbolic links) are refused (:class:`UnsafeArchive`); archives are written
atomically; profile names are checked (no path separators or characters Windows refuses); file names are matched
case-insensitively (LabVIEW relied on Windows for that).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import tempfile
import threading
import zipfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from alox.core.exposures import PROFILE_DEFAULT_TABLE
from alox.core.properties import OnChange, Properties, TypedStore

if TYPE_CHECKING:
    from alox.core.folders import Folders

__all__ = [
    "BUILTIN_DEFAULTS",
    "DEFAULT_PROFILE",
    "MAX_UNPACKED_BYTES",
    "ProfileError",
    "ProfileManager",
    "ProfileProperties",
    "UnsafeArchive",
    "check_profile_name",
    "safe_extract",
    "zip_profile_folder",
]

log = logging.getLogger(__name__)

#: The profile created at boot when none exists (``alox_core_boot_cycle d48``), also ``general.profile`` in
#: ``alox_default.ini``.
DEFAULT_PROFILE = "DEFAULT"
#: Built-in defaults of a new profile (the rest of "New" is not ported, see the module docstring).
BUILTIN_DEFAULTS: Mapping[str, Any] = {"general.exposures": [list(row) for row in PROFILE_DEFAULT_TABLE]}
#: Refuse archives that would unpack to more than this (zip bombs).
MAX_UNPACKED_BYTES = 1 << 30
LOAD_PROFILE_ERROR = 9007

_BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_DRIVE = re.compile(r"^[A-Za-z]:")

Defaults = TypedStore | str | os.PathLike[str] | Mapping[str, Any] | None


class ProfileError(RuntimeError):
    """A profile cannot be loaded, saved or found (``alox_en.lang`` 9007 "load profile error")."""

    code = LOAD_PROFILE_ERROR


class UnsafeArchive(ProfileError):
    """A profile archive holds a member that would be written outside the profile folder, or is too large."""


class ProfileProperties(TypedStore):
    """The properties of one profile (``<name>.ini``); a new property set from a sequence is a ``TABLE``."""

    array_suffix = "TABLE"


# ---------------------------------------------------------------------------------------------- helpers
def check_profile_name(name: str) -> str:
    """``name`` stripped, or :class:`ValueError` when it cannot be a file name (empty, ``.``/``..``, path separators,
    characters Windows refuses, trailing dot or blank)."""
    if not isinstance(name, str):
        raise TypeError(f"profile name must be a str, not {type(name).__name__}")
    stripped = name.strip()
    if not stripped or stripped in (".", "..") or _BAD_NAME.search(stripped) or stripped.endswith("."):
        raise ValueError(f"invalid profile name {name!r}")
    return stripped


def _zip_name(name: str) -> str:
    return f"{name.lower()}.zip"                     # To Lower Case (d1149, d1167)


def _ini_name(name: str) -> str:
    return f"{name.lower()}.ini"


def _find_file(folder: Path, file_name: str) -> Path | None:
    """``folder/file_name`` if it exists, else a file of the folder with that name in another case, else None."""
    exact = folder / file_name
    if exact.is_file():
        return exact
    if not folder.is_dir():
        return None
    wanted = file_name.lower()
    for child in sorted(folder.iterdir()):
        if child.name.lower() == wanted and child.is_file():
            return child
    return None


def _member_parts(info: zipfile.ZipInfo) -> tuple[str, ...] | None:
    """The relative path parts of a member, None to skip it ("./"), :class:`UnsafeArchive` if it escapes."""
    name = info.filename.replace("\\", "/")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise UnsafeArchive(f"symbolic link in profile archive: {info.filename!r}")
    if name.startswith("/") or _DRIVE.match(name):
        raise UnsafeArchive(f"absolute path in profile archive: {info.filename!r}")
    parts = tuple(p for p in PurePosixPath(name).parts if p not in ("", "."))
    if any(p == ".." for p in parts):
        raise UnsafeArchive(f"path escaping the profile folder: {info.filename!r}")
    if any(_DRIVE.match(p) or "\x00" in p for p in parts):
        raise UnsafeArchive(f"invalid member name in profile archive: {info.filename!r}")
    return parts or None


def safe_extract(archive: str | os.PathLike[str], dest: str | os.PathLike[str]) -> list[Path]:
    """Extract ``archive`` into ``dest`` (created) and return the files written. Every member is checked first: an
    absolute path, a drive letter, a ``..`` component, a symbolic link or more than :data:`MAX_UNPACKED_BYTES` raise
    :class:`UnsafeArchive` before anything is written; a corrupt archive raises :class:`ProfileError`."""
    root = Path(dest)
    try:
        with zipfile.ZipFile(archive) as zf:
            plan: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
            total = 0
            for info in zf.infolist():
                parts = _member_parts(info)
                if parts is None:
                    continue
                total += info.file_size
                if total > MAX_UNPACKED_BYTES:
                    raise UnsafeArchive(f"{archive}: more than {MAX_UNPACKED_BYTES} bytes once unpacked")
                plan.append((info, parts))
            root.mkdir(parents=True, exist_ok=True)
            base = root.resolve()
            written: list[Path] = []
            for info, parts in plan:
                target = root.joinpath(*parts)
                if not target.resolve().is_relative_to(base):
                    raise UnsafeArchive(f"path escaping the profile folder: {info.filename!r}")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                written.append(target)
            return written
    except zipfile.BadZipFile as exc:
        raise ProfileError(f"{archive}: not a valid zip archive ({exc})") from exc


def _write_zip_atomically(target: Path, members: list[tuple[str, bytes | Path]]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as raw:
            with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for arcname, content in members:
                    if isinstance(content, Path):
                        zf.write(content, arcname)
                    else:
                        zf.writestr(arcname, content)
            raw.flush()
            os.fsync(raw.fileno())
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def zip_profile_folder(name: str, folder: str | os.PathLike[str], target: str | os.PathLike[str]) -> Path:
    """``tools_zip_profile_folder``: zip the top-level files of ``folder`` flat into ``target`` (atomically), leaving
    out every ``.ini`` file other than ``<name>.ini`` ("Ignore other profiles .ini"); sub-folders are not included,
    as in LabVIEW (``List Folder``)."""
    wanted = name.lower()
    members: list[tuple[str, bytes | Path]] = []
    for child in sorted(Path(folder).iterdir()):
        if not child.is_file():
            continue
        if child.suffix.lower() == ".ini" and child.stem.lower() != wanted:
            continue
        members.append((child.name, child))
    return _write_zip_atomically(Path(target), members)


def _clear_folder(folder: Path) -> None:
    """``tools_clear_folder`` (and "recreate current profile folder"): empty ``folder``, creating it if needed."""
    if folder.is_symlink() or folder.is_file():
        raise ProfileError(f"{folder} is not a folder")
    folder.mkdir(parents=True, exist_ok=True)
    for child in folder.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


# ---------------------------------------------------------------------------------------------- manager
class ProfileManager:
    """The recipes of ``profiles`` (``<name>.zip``), the active one unzipped in ``work``, the edited copy in ``temp``.

    Thread-safe (one lock for the file operations; the property stores have their own). ``defaults`` is the template
    of :meth:`new` (see the module docstring); ``on_change(name, old, new)`` is given to the active profile's store.
    """

    def __init__(self, profiles: str | os.PathLike[str], work: str | os.PathLike[str],
                 temp: str | os.PathLike[str], *, defaults: Defaults = None,
                 on_change: OnChange | None = None) -> None:
        self.profiles = Path(profiles)
        self.work = Path(work)
        self.temp = Path(temp)
        self.defaults = defaults
        self.on_change = on_change
        self._lock = threading.RLock()
        self._active: ProfileProperties | None = None
        self._current: str | None = None

    @classmethod
    def from_folders(cls, folders: Folders, **kwargs: Any) -> ProfileManager:
        """A manager on ``folders.profiles``, ``folders.profile`` and ``folders.profile_temp``."""
        return cls(folders.profiles, folders.profile, folders.profile_temp, **kwargs)

    # -- profiles folder
    def list(self) -> list[str]:
        """"Get Profiles List": the ``*.zip`` of the profiles folder, without extension, upper-cased, sorted."""
        if not self.profiles.is_dir():
            return []
        names = {p.stem.upper() for p in self.profiles.iterdir() if p.is_file() and p.suffix.lower() == ".zip"}
        return sorted(names)

    def archive(self, name: str) -> Path | None:
        """The archive of ``name`` (``<profiles>/<name lower>.zip``, any case), None when it does not exist."""
        return _find_file(self.profiles, _zip_name(check_profile_name(name)))

    def exists(self, name: str) -> bool:
        return self.archive(name) is not None

    def delete(self, name: str) -> bool:
        """Delete the archive of ``name``; False when there is none. The active profile in memory and its working
        folder are left as they are."""
        with self._lock:
            path = self.archive(name)
            if path is None:
                return False
            path.unlink()
            log.info("profile %s deleted (%s)", name, path)
            return True

    def copy(self, src: str, dst: str, *, overwrite: bool = False) -> Path:
        """Copy the archive of ``src`` to ``<profiles>/<dst>.zip``, renaming ``<src>.ini`` to ``<dst>.ini`` (and leaving
        out other ``.ini`` of the top level, as the zip tool does). Neither the active profile nor the working folders
        are used. :class:`FileExistsError` when ``dst`` exists and ``overwrite`` is false."""
        src_name, dst_name = check_profile_name(src), check_profile_name(dst)
        with self._lock:
            source = self.archive(src_name)
            if source is None:
                raise ProfileError(f"profile {src_name!r} not found in {self.profiles}")
            existing = self.archive(dst_name)
            if existing is not None and not overwrite:
                raise FileExistsError(f"profile {dst_name!r} already exists ({existing})")
            members: list[tuple[str, bytes | Path]] = []
            found_ini = False
            try:
                with zipfile.ZipFile(source) as zf:
                    if sum(info.file_size for info in zf.infolist()) > MAX_UNPACKED_BYTES:
                        raise UnsafeArchive(f"{source}: more than {MAX_UNPACKED_BYTES} bytes once unpacked")
                    for info in zf.infolist():
                        parts = _member_parts(info)
                        if parts is None or info.is_dir():
                            continue
                        arcname = "/".join(parts)
                        if len(parts) == 1 and arcname.lower().endswith(".ini"):
                            if arcname.lower() != _ini_name(src_name):
                                continue
                            arcname, found_ini = _ini_name(dst_name), True
                        members.append((arcname, zf.read(info)))
            except zipfile.BadZipFile as exc:
                raise ProfileError(f"{source}: not a valid zip archive ({exc})") from exc
            if not found_ini:
                raise ProfileError(f"{source} holds no {_ini_name(src_name)}")
            target = existing if existing is not None else self.profiles / _zip_name(dst_name)
            return _write_zip_atomically(target, members)

    # -- active profile
    @property
    def current(self) -> str | None:
        """"Get Current Profile": the name of the profile last loaded, created or saved (None before)."""
        with self._lock:
            return self._current

    @property
    def properties(self) -> ProfileProperties:
        """The store of the active profile; :class:`ProfileError` when no profile is loaded."""
        with self._lock:
            if self._active is None:
                raise ProfileError("no profile loaded")
            return self._active

    def get(self, dotted: str, default: Any = None) -> Any:
        """"Get Property" of the active profile."""
        return self.properties.get(dotted, default)

    def set(self, dotted: str, value: Any) -> None:
        """"Set Property" / "Add Property" of the active profile (same typing rules as :class:`alox.core.properties.
        TypedStore.set`); saved by :meth:`save`."""
        self.properties.set(dotted, value)

    def names(self) -> list[str]:
        """"Get Properties List" of the active profile."""
        return self.properties.names()

    def check(self, defaults: Defaults = None) -> list[str]:
        """"Check": add the missing properties of ``defaults`` (or the manager's template, plus
        :data:`BUILTIN_DEFAULTS`) to the active profile; returns the names added."""
        return self._fill(self.properties, defaults)

    def load(self, name: str) -> ProfileProperties:
        """"Load": unzip ``<profiles>/<name>.zip`` into the working folder (emptied first) and read ``<name>.ini``.

        The archive is checked and extracted into a staging folder first, so a missing, corrupt or unsafe archive
        (:class:`ProfileError`, :class:`UnsafeArchive`) leaves the active profile and its folder untouched.
        """
        clean = check_profile_name(name)
        with self._lock:
            store = self._unpack(clean, self.work, on_change=self.on_change)
            self._active, self._current = store, clean
            log.info("profile %s loaded", clean)
            return store

    def new(self, name: str, from_defaults: Defaults = None) -> ProfileProperties:
        """"New": empty the working folder ("recreate current profile folder") and make ``name`` the active profile
        with the properties of ``from_defaults`` (else the manager's template) plus :data:`BUILTIN_DEFAULTS`. Nothing is
        written before :meth:`save`."""
        clean = check_profile_name(name)
        with self._lock:
            store = ProfileProperties(self.work / _ini_name(clean), on_change=self.on_change)
            self._fill(store, from_defaults, notify=False)         # template read before the folder is emptied
            _clear_folder(self.work)
            self._active, self._current = store, clean
            return store

    def save(self, name: str | None = None) -> Path:
        """"Save": write ``<work>/<name>.ini`` (only the changed keys when it is the file loaded; a complete copy under
        a new name) and zip the working folder to ``<profiles>/<name>.zip`` atomically. ``name`` defaults to
        :attr:`current`; saving under another name makes it the current one. Returns the archive."""
        with self._lock:
            store = self.properties
            clean = check_profile_name(name if name is not None else self._current or "")
            self._store_and_zip(store, clean, self.work)
            self._current = clean
            return self._archive_path(clean)

    # -- edited copy ("...Variant" methods)
    def load_copy(self, name: str) -> ProfileProperties:
        """"Load to Variant Out": unzip ``name`` into ``profile_temp`` and return its properties; the active profile is
        not touched."""
        clean = check_profile_name(name)
        with self._lock:
            return self._unpack(clean, self.temp, on_change=None)

    def new_copy(self, name: str, from_defaults: Defaults = None) -> ProfileProperties:
        """"New To Variant Out": empty ``profile_temp`` and return a new profile ``name`` with the defaults (not
        written before :meth:`save_copy`); the active profile is not touched."""
        clean = check_profile_name(name)
        with self._lock:
            store = ProfileProperties(self.temp / _ini_name(clean))
            self._fill(store, from_defaults, notify=False)
            _clear_folder(self.temp)
            return store

    def save_copy(self, store: ProfileProperties, name: str) -> Path:
        """"Save From Variant In": write ``store`` as ``profile_temp/<name>.ini`` and zip ``profile_temp`` to
        ``<profiles>/<name>.zip``. The active profile is not touched, even when ``name`` is the active profile's: load
        it again to use the new values."""
        clean = check_profile_name(name)
        with self._lock:
            self.temp.mkdir(parents=True, exist_ok=True)
            self._store_and_zip(store, clean, self.temp)
            return self._archive_path(clean)

    def ensure_default_profile(self, from_defaults: Defaults = None, properties: Properties | None = None) -> bool:
        """Boot frame 14, inner frame 6 (``alox_core_boot_cycle d46..d48``): when the profiles folder holds no
        profile, "New" + "Save" :data:`DEFAULT_PROFILE` and set the application property ``general.profile`` to it
        (when ``properties`` is given; it is saved at exit). Returns True when the profile was created."""
        with self._lock:
            if self.list():
                return False
            self.new(DEFAULT_PROFILE, from_defaults)
            self.save()
        if properties is not None:
            properties.set("general.profile", DEFAULT_PROFILE)
        log.info("no profile found: %s created", DEFAULT_PROFILE)
        return True

    # -- internals
    def _archive_path(self, name: str) -> Path:
        return self.archive(name) or self.profiles / _zip_name(name)

    def _store_and_zip(self, store: ProfileProperties, name: str, folder: Path) -> None:
        ini = _find_file(folder, _ini_name(name)) or folder / _ini_name(name)
        store.save(ini)
        zip_profile_folder(name, folder, self._archive_path(name))
        log.info("profile %s saved (%s)", name, self._archive_path(name))

    def _unpack(self, name: str, folder: Path, *, on_change: OnChange | None) -> ProfileProperties:
        archive = self.archive(name)
        if archive is None:
            raise ProfileError(f"profile {name!r} not found in {self.profiles}")
        folder.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{folder.name}.", dir=folder.parent))
        try:
            safe_extract(archive, staging)
            ini = _find_file(staging, _ini_name(name))
            if ini is None:
                raise ProfileError(f"{archive} holds no {_ini_name(name)}")
            ProfileProperties.read(ini)                       # readable before the working folder is cleared
            _clear_folder(folder)
            for child in sorted(staging.iterdir()):
                shutil.move(str(child), str(folder / child.name))
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        ini = _find_file(folder, _ini_name(name))
        assert ini is not None
        return ProfileProperties.read(ini, on_change=on_change)

    def _fill(self, store: ProfileProperties, defaults: Defaults, *, notify: bool = True) -> list[str]:
        """Add the missing properties of ``defaults`` (else the manager's template), then :data:`BUILTIN_DEFAULTS`."""
        template = defaults if defaults is not None else self.defaults
        callback = store.on_change
        if not notify:
            store.on_change = None
        try:
            added: list[str] = []
            if isinstance(template, Mapping):
                for dotted, value in template.items():
                    if not store.exists(dotted):
                        store.set(dotted, value)
                        added.append(dotted)
            elif template is not None:
                added += store.missing_from(template if isinstance(template, TypedStore)
                                            else ProfileProperties.read(template))
            for dotted, value in BUILTIN_DEFAULTS.items():
                if not store.exists(dotted):
                    store.set(dotted, value)
                    added.append(dotted)
            return added
        finally:
            store.on_change = callback
