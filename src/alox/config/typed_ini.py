"""Typed reading of the Vision Core INI files (``alox.ini``, ``alox_setup.ini``) by key name, whatever the type suffix.

Spec: 09 §6.1–§6.3 (NI ``config.llb`` parsing, ``Read Key`` conversions, common paths); docs/vision-core/
phase3-skeleton.md §3.

Vision Core keys carry a type suffix (``port_I32``, ``wafersize_DBL``, ``profilefolder_PATH``). :class:`TypedIni` lets
the caller ask for ``"general.wafersize"`` and converts the value by the suffix it finds, with the LabVIEW-compatible
rules of :class:`aloxcam.config.niconfig.NIConfig` (case-insensitive names, first occurrence wins, decimal comma, NI
quotes and escapes). Plain keys without a suffix (``[folders] settings`` in ``alox_setup.ini``) are read with the type
of the caller's default. Nothing here writes a file: see :mod:`alox.config.ini_writer`.
"""

from __future__ import annotations

import os
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePath
from typing import Any

from aloxcam.config.niconfig import ConfigTypeInvalid, NIConfig
from aloxcam.lv.path import localize_path_text

__all__ = ["SUFFIXES", "ConfigTypeInvalid", "TypedIni", "resolve_key", "split_name", "split_suffix", "suffix_for"]

#: The type suffixes of Vision Core keys, upper-case (names are compared case-insensitively).
SUFFIXES = ("BOOL", "DBL", "I32", "U32", "STR", "PATH")

_NUMERIC = frozenset({"I32", "U32"})


def split_name(dotted_name: str) -> tuple[str, str]:
    """``"hardware.zstage.speed"`` → ``("hardware.zstage", "speed")``: the section is everything before the LAST dot.

    :class:`ValueError` when the section or the name is empty.
    """
    section, dot, name = dotted_name.rpartition(".")
    if not dot or not section or not name:
        raise ValueError(f"{dotted_name!r} is not a 'section.name' key name")
    return section, name


def split_suffix(key: str) -> tuple[str, str | None]:
    """``"port_I32"`` → ``("port", "I32")``; without a known suffix → ``(key, None)``. The suffix is upper-cased."""
    base, sep, tail = key.rpartition("_")
    if sep and base and tail.upper() in SUFFIXES:
        return base, tail.upper()
    return key, None


def _preferences(sample: object) -> tuple[frozenset[str], ...]:
    """Suffix tiers matching the Python type of ``sample``, best first (bool before int: bool is an int)."""
    if isinstance(sample, bool):
        return (frozenset({"BOOL"}),)
    if isinstance(sample, int):
        return (_NUMERIC, frozenset({"DBL"}))
    if isinstance(sample, float):
        return (frozenset({"DBL"}), _NUMERIC)
    if isinstance(sample, str):
        return (frozenset({"STR"}), frozenset({"PATH"}))
    if isinstance(sample, os.PathLike):
        return (frozenset({"PATH"}), frozenset({"STR"}))
    return ()


def suffix_for(value: object) -> str:
    """The suffix a new key gets for ``value``: bool → ``BOOL``, float → ``DBL``, int → ``I32``, str → ``STR``,
    path → ``PATH``. :class:`TypeError` for any other type."""
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "I32"
    if isinstance(value, float):
        return "DBL"
    if isinstance(value, str):
        return "STR"
    if isinstance(value, os.PathLike):
        return "PATH"
    raise TypeError(f"no INI type for {type(value).__name__} values")


def resolve_key(keys: Iterable[str], name: str, sample: object = None) -> tuple[str, str | None] | None:
    """The key that holds ``name`` among ``keys`` (one section, file order, each key once), with its suffix.

    ``name_<SUFFIX>`` keys come first; when several suffixes exist, the first key (in file order) whose suffix matches
    the type of ``sample`` wins, else the first one. Without a suffixed key, the plain key ``name`` is used (its suffix
    is then that of ``name`` itself, usually None). None when neither exists.
    """
    keys = list(keys)
    wanted = name.lower()
    candidates: list[tuple[str, str | None]] = []
    for key in keys:
        base, suffix = split_suffix(key)
        if suffix is not None and base.lower() == wanted:
            candidates.append((key, suffix))
    if candidates:
        for tier in _preferences(sample):
            for key, suffix in candidates:
                if suffix in tier:
                    return key, suffix
        return candidates[0]
    for key in keys:
        if key.lower() == wanted:
            return key, split_suffix(key)[1]
    return None


class TypedIni:
    """Read-only typed view of an INI file: ``get("section.name", default)`` whatever the key's type suffix.

    Missing sections or keys give the default; a value that cannot be converted raises :class:`ConfigTypeInvalid`
    (a :class:`ValueError`), unlike LabVIEW which silently used the default (09 §6.2).
    """

    def __init__(self, config: NIConfig) -> None:
        self._config = config

    # -- construction
    @classmethod
    def load(cls, path: str | os.PathLike[str], encoding: str = "cp1252") -> TypedIni:
        """Read and parse ``path`` (see :meth:`NIConfig.load`; :class:`FileNotFoundError` when missing)."""
        return cls(NIConfig.load(Path(path), encoding=encoding))

    @classmethod
    def parse(cls, text: str) -> TypedIni:
        """Parse INI text (see :meth:`NIConfig.parse`)."""
        return cls(NIConfig.parse(text))

    @property
    def config(self) -> NIConfig:
        """The underlying :class:`NIConfig`, for raw access (``raw``, ``read_list``…)."""
        return self._config

    # -- introspection
    def sections(self) -> list[str]:
        """Section names in file order, as written, each listed once."""
        return self._config.sections()

    def keys(self, section: str) -> list[tuple[str, str | None]]:
        """``(name, suffix)`` of every key of the (first) matching section, in file order, each key once:
        ``port_I32`` → ``("port", "I32")``, a plain key → ``("key", None)``. ``[]`` when the section is absent."""
        return [split_suffix(key) for key in self._config.keys(section)]

    def key_for(self, dotted_name: str, default: object = None) -> str | None:
        """The key :meth:`get` would read for ``dotted_name`` and ``default`` (as written in the file), or None."""
        section, name = split_name(dotted_name)
        found = resolve_key(self._config.keys(section), name, default)
        return None if found is None else found[0]

    def _blocks(self) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
        # NIConfig keeps every section block, shadowed duplicates included, but has no public accessor for them.
        return list(self._config._sections[1:])

    def duplicates(self) -> list[tuple[str, str]]:
        """``(section, key)`` for every key written more than once in a section (case-insensitive; the copies of a
        duplicated section count as one section). Only the first occurrence is ever read. Names as first written."""
        names: dict[str, str] = {}
        first: dict[tuple[str, str], str] = {}
        counts: Counter[tuple[str, str]] = Counter()
        for section, pairs in self._blocks():
            names.setdefault(section.lower(), section)
            for key, _ in pairs:
                k = (section.lower(), key.lower())
                first.setdefault(k, key)
                counts[k] += 1
        return [(names[s], first[s, k]) for (s, k), n in counts.items() if n > 1]

    def duplicate_sections(self) -> list[str]:
        """Sections written more than once (case-insensitive), as first written. Only the first copy is ever read."""
        names: dict[str, str] = {}
        counts: Counter[str] = Counter()
        for section, _ in self._blocks():
            names.setdefault(section.lower(), section)
            counts[section.lower()] += 1
        return [names[s] for s, n in counts.items() if n > 1]

    # -- typed reads
    def get(self, dotted_name: str, default: Any = None) -> Any:
        """The value of ``"section.name"`` converted by the suffix of the key found, or ``default`` when absent.

        ``BOOL`` → bool, ``DBL`` → float, ``I32`` / ``U32`` → int (saturated to the range), ``STR`` → str (unescaped),
        ``PATH`` → str (the raw path text, not localized: see :meth:`get_path`). When the name exists with several
        suffixes, the type of ``default`` chooses (see :func:`resolve_key`). Without a suffixed key, the plain key
        ``name`` is read with the type of ``default`` (str when ``default`` is None, raw text for a path default).
        :class:`ConfigTypeInvalid` when the value cannot be converted.
        """
        section, name = split_name(dotted_name)
        found = resolve_key(self._config.keys(section), name, default)
        if found is None:
            return default
        key, suffix = found
        if suffix is None:
            suffix = _suffix_of_default(default)
        return self._read(section, key, suffix)

    def _read(self, section: str, key: str, suffix: str) -> Any:
        cfg = self._config
        if suffix == "BOOL":
            return cfg.read_bool(section, key, False)
        if suffix == "DBL":
            return cfg.read_float(section, key, 0.0)
        if suffix in _NUMERIC:
            return cfg.read_int(section, key, 0, unsigned=suffix == "U32")
        if suffix == "PATH":
            return cfg.read_path(section, key, "")
        return cfg.read_str(section, key, "")

    def get_path(self, dotted_name: str, default: Path | None = None, *, platform: str = sys.platform,
                 env: Mapping[str, str] = os.environ) -> Path | None:
        """The ``PATH`` (raw text) or ``STR`` (unescaped) value of ``"section.name"`` as a local path, through
        :func:`aloxcam.lv.path.localize_path_text`; a plain key is read as path text.

        ``default`` when the key is absent; None for an empty value (LabVIEW's empty path) or a path that cannot be
        used on this machine (``D:\\…`` off Windows without ``ALOX_DRIVE_D``). :class:`ConfigTypeInvalid` when the key
        found has another suffix.
        """
        section, name = split_name(dotted_name)
        found = resolve_key(self._config.keys(section), name, PurePath())
        if found is None:
            return default
        key, suffix = found
        if suffix in (None, "PATH"):
            text = self._config.read_path(section, key, "")
        elif suffix == "STR":
            text = self._config.read_str(section, key, "")
        else:
            raise ConfigTypeInvalid(f"[{section}] {key} is a {suffix} key, not a path")
        return localize_path_text(text, platform=platform, env=env)


def _suffix_of_default(default: object) -> str:
    """How a plain key is read: by the type of the caller's default; str when there is no usable default."""
    try:
        return suffix_for(default)
    except TypeError:
        return "STR"
