r"""Application settings (``alox.ini``): the replacement of ``alox_manager_properties``.

Spec: map Part 1 §4.1 (methods, storage, types, "Check"), §2.3 (boot frame 2 "Load" + "Check"; frame 14 copies
``alox_default.ini`` when ``alox.ini`` is missing), §8 note 7 (typed-key INI with locale numbers), §8 note 1 (explicit
locks instead of FGV serialisation); docs/09-configuration-logging-errors.md §6 (NI config semantics, read through
:class:`aloxcam.config.niconfig.NIConfig`, written through :class:`alox.config.ini_writer.IniDocument`).

LabVIEW methods (``alox_typedef_properties_manager_method``) → :class:`Properties`: New (not ported: the hard-coded
defaults, see :meth:`TypedStore.missing_from`), Load → :meth:`Properties.load`, Save → :meth:`TypedStore.save`, Get
Property → :meth:`~TypedStore.get`, Set Property / Add Property / Set Default Property → :meth:`~TypedStore.set`, Del
Property → :meth:`~TypedStore.delete`, Get Properties Names → :meth:`~TypedStore.names`, Clear →
:meth:`~TypedStore.clear`, Property Exists? → :meth:`~TypedStore.exists`, Check → :meth:`~TypedStore.missing_from`.

Observed in the diagrams:

- ``alox_manager_properties d1282`` "Load": the file is ``tools_windows_user_documents_folder`` (= ``[folders]
  settings``, see :mod:`alox.core.folders`) + ``alox.ini``. Every key of every section is split at its LAST ``_`` into
  name and type; the property is ``<section>.<name>``. Types (d1283..d1293): ``BOOL``, ``DBL``, ``I32``, ``U32``,
  ``STR``, ``PATH``, ``PTS`` and ``TABLE``; any other → error 9006 "Invalid property '%s'". The values are stored as
  variant attributes, so a later key with the same name and another suffix replaces the earlier one
  (``slowspeed_I32`` / ``slowspeed_DBL``), while an exact duplicate key is read through NI ``Read Key`` (first wins).
- ``d1294..d1309`` "Save": each property is written as ``<name>_<TYPE>`` by the type of its value (Boolean → BOOL,
  Double/Single/Extended → DBL, I32, U32, String → STR, Path → PATH; also I16, U16, U64 that "Load" refuses) and an
  array of points → ``PTS`` written as ``%f;%f;`` per point (``;`` separator, 6 decimals, system decimal separator);
  section and key come from the LAST ``.`` of the name.
- ``PTS`` "Load": ``Read Key (String)``, ``Spreadsheet String To Array`` (``%f``, ``;``), then one point ``(x, y)`` per
  pair of numbers (an odd last number is dropped).
- ``TABLE`` "Load": OpenG ``Read Key (Variant)__ogtk`` with a 2-D DBL type (format: :func:`read_table`).

Deviations: :meth:`TypedStore.save` writes only the keys changed since the last load/save, in place, and leaves every
other line untouched (LabVIEW rewrote the whole file with ``Close Config Data``); a key that cannot be read is skipped
with a warning and listed in :attr:`TypedStore.invalid` (LabVIEW raised 9006 and the following ``Read Key`` calls
returned defaults because of the error chaining); a deleted property is removed from the file on save (LabVIEW kept it);
values are type-checked against the key's suffix.
"""

from __future__ import annotations

import copy
import logging
import math
import numbers
import os
import re
import shutil
import sys
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Self

from alox.config.ini_writer import (
    IniDocument,
    escape_string,
    format_double,
    format_value,
)
from alox.config.typed_ini import split_name
from aloxcam.config.niconfig import ConfigTypeInvalid, NIConfig
from aloxcam.lv.path import localize_path_text

__all__ = [
    "ALL_SUFFIXES",
    "INVALID_PROPERTY",
    "SCALAR_SUFFIXES",
    "TABLE_SIZE_TAG",
    "InvalidProperty",
    "OnChange",
    "Properties",
    "TypedStore",
    "format_points",
    "format_table",
    "parse_inline_table",
    "parse_number",
    "parse_points",
    "read_table",
    "split_typed_key",
]

log = logging.getLogger(__name__)

SCALAR_SUFFIXES = ("BOOL", "DBL", "I32", "U32", "STR", "PATH")
ALL_SUFFIXES = (*SCALAR_SUFFIXES, "PTS", "TABLE")
INVALID_PROPERTY = 9006

#: Legacy array layout (read only, see :func:`read_table`): ``<key>.<size(s)> = "rows cols"`` + one key per element.
TABLE_SIZE_TAG = ".<size(s)>"
#: OpenG ``Write Key (Variant)`` array value as found in real recipes: ``"<size(s)=20 7> 700,000000 6710,609340 ..."``.
_INLINE_TABLE = re.compile(r"^\s*<size\(s\)=(?P<dims>[^>]*)>(?P<values>.*)$", re.IGNORECASE | re.DOTALL)
_ELEMENT = re.compile(r"^(?P<base>.+_TABLE) (?P<index>\d+(?:[ ,]\d+)?)$", re.IGNORECASE)
_COMMA = re.compile(r"\d,\d")
_POINT = re.compile(r"\d\.\d")
_SPECIAL = {"nan": math.nan, "inf": math.inf, "+inf": math.inf, "-inf": -math.inf}
_RANGES = {"I32": (-(2**31), 2**31 - 1), "U32": (0, 2**32 - 1)}

#: ``on_change(name, old, new)``: called after a value changed (``old`` None for a new property, ``new`` None for a
#: deleted one), outside the store's lock.
OnChange = Callable[[str, Any, Any], None]


class InvalidProperty(ValueError):
    """LabVIEW error 9006 "Invalid property '%s'": unknown type suffix or unreadable value."""

    code = INVALID_PROPERTY


# ---------------------------------------------------------------------------------------------- value codecs
def split_typed_key(key: str) -> tuple[str, str | None]:
    """``"speed_I32"`` → ``("speed", "I32")``, ``"exposures_TABLE"`` → ``("exposures", "TABLE")``; a key without one
    of :data:`ALL_SUFFIXES` → ``(key, None)``. Split at the LAST ``_`` (``alox_manager_properties d1282``)."""
    base, sep, tail = key.rpartition("_")
    if sep and base and tail.upper() in ALL_SUFFIXES:
        return base, tail.upper()
    return key, None


def parse_number(text: str) -> float:
    """A number written by LabVIEW ``%f`` with either decimal separator: ``"2,5"``, ``"2.5"``, ``"NaN"``, ``"-Inf"``.
    :class:`ValueError` otherwise. The caller splits fields first, so a comma is never a field separator here."""
    s = text.strip()
    special = _SPECIAL.get(s.lower())
    if special is not None:
        return special
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"not a number: {text!r}") from None


def parse_points(text: str) -> list[tuple[float, float]]:
    """``PTS`` value (``"1,000000;2,000000;3,000000;4,000000;"``) → ``[(1.0, 2.0), (3.0, 4.0)]``; an odd last number
    is dropped as in ``alox_manager_properties`` "Load" (case "PTS")."""
    values = [parse_number(item) for item in text.split(";") if item.strip()]
    return [(values[i - 1], values[i]) for i in range(1, len(values), 2)]


def format_points(points: Iterable[tuple[float, float]], decimal: str = ",") -> str:
    """The quoted INI value of a ``PTS`` key: ``%f;%f;`` per point (6 decimals, ``decimal`` separator), then
    ``Write Key (String)``: escaped and double-quoted, so the ``;`` are not read as a comment (09 §6.4)."""
    body = "".join(f"{format_double(x, decimal)};{format_double(y, decimal)};" for x, y in points)
    return '"' + escape_string(body) + '"'


def parse_inline_table(text: str) -> list[list[float]]:
    """A ``TABLE`` value (2-D DBL array) as OpenG ``Write Key (Variant)__ogtk`` writes it, confirmed on a real recipe
    (``[general] exposures_TABLE``): ``<size(s)=<rows> <cols>>`` then the elements in row-major order, separated by
    spaces, each ``%f`` with the system decimal separator (``700,000000``) or ``NaN`` / ``Inf``. One dimension → one
    column. :class:`InvalidProperty` when the size is unreadable or the number of elements does not match it."""
    m = _INLINE_TABLE.match(text)
    if m is None:
        raise InvalidProperty(f"not an array value: {text[:40]!r}")
    try:
        dims = [int(token) for token in re.split(r"[\s,;]+", m.group("dims").strip()) if token]
        values = [parse_number(token) for token in m.group("values").split()]
    except ValueError as exc:
        raise InvalidProperty(f"unreadable array value: {exc}") from None
    if len(dims) == 1:
        dims.append(1)
    if len(dims) != 2 or min(dims) < 0:
        raise InvalidProperty(f"<size(s)={m.group('dims')}> is not a 2-D array size")
    rows, cols = dims
    if len(values) != rows * cols:
        raise InvalidProperty(f"<size(s)={rows} {cols}> holds {len(values)} elements, {rows * cols} expected")
    return [values[r * cols:(r + 1) * cols] for r in range(rows)] if cols else []


def format_table(table: list[list[float]], decimal: str = ",") -> str:
    """The quoted INI value of a ``TABLE`` key, the layout of :func:`parse_inline_table`:
    ``"<size(s)=2 3> 700,000000 25000,000000 NaN ..."``; an empty table → ``"<size(s)=0 0>"``."""
    rows = len(table)
    cols = len(table[0]) if rows else 0
    body = "".join(f" {format_double(v, decimal)}" for row in table for v in row)
    return f'"<size(s)={rows} {cols}>{body}"'


def read_table(cfg: NIConfig, section: str, key: str) -> list[list[float]]:
    """A ``TABLE`` array in the legacy layout of earlier versions of this application (still read; :meth:`save`
    rewrites it as :func:`format_table`): ``<key>.<size(s)> = "<rows> <cols>"`` then one key per element,
    ``<key> <i>`` with the row-major flat index ``i`` (also accepted: ``<key> <row> <col>`` and ``<key> <row>,<col>``),
    each a double. One dimension → one column. :class:`InvalidProperty` when the size or an element is missing.
    """
    size_text = cfg.read_str(section, key + TABLE_SIZE_TAG, "")
    try:
        dims = [int(token) for token in re.split(r"[\s,;]+", size_text.strip()) if token]
    except ValueError:
        raise InvalidProperty(f"[{section}] {key}{TABLE_SIZE_TAG} = {size_text!r} is not an array size") from None
    if len(dims) == 1:
        dims.append(1)
    if len(dims) != 2 or min(dims) < 0:
        raise InvalidProperty(f"[{section}] {key}{TABLE_SIZE_TAG} = {size_text!r} is not a 2-D array size")
    rows, cols = dims
    if rows == 0 or cols == 0:
        return []
    table: list[list[float]] = []
    for r in range(rows):
        row: list[float] = []
        for c in range(cols):
            for candidate in (f"{key} {r * cols + c}", f"{key} {r} {c}", f"{key} {r},{c}"):
                if cfg.raw(section, candidate) is not None:
                    try:
                        row.append(cfg.read_float(section, candidate, math.nan))
                    except ConfigTypeInvalid as exc:
                        raise InvalidProperty(str(exc)) from None
                    break
            else:
                raise InvalidProperty(f"[{section}] {key}: element ({r}, {c}) is missing")
        table.append(row)
    return table


def _is_real(value: object) -> bool:
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def _coerce(suffix: str, value: Any) -> Any:
    """``value`` checked against ``suffix`` and normalised (:class:`TypeError` / :class:`ValueError`)."""
    if suffix == "BOOL":
        if not isinstance(value, bool):
            raise TypeError(f"BOOL value expected, got {type(value).__name__}")
        return value
    if suffix == "DBL":
        if not _is_real(value):
            raise TypeError(f"DBL value expected, got {type(value).__name__}")
        return float(value)
    if suffix in _RANGES:
        if isinstance(value, bool) or not isinstance(value, numbers.Integral):
            raise TypeError(f"{suffix} value expected, got {type(value).__name__}")
        lo, hi = _RANGES[suffix]
        if not lo <= int(value) <= hi:
            raise ValueError(f"{value} is out of the {suffix} range [{lo}, {hi}]")
        return int(value)
    if suffix == "STR":
        if not isinstance(value, str):
            raise TypeError(f"STR value expected, got {type(value).__name__}")
        return value
    if suffix == "PATH":
        if not isinstance(value, (str, os.PathLike)):
            raise TypeError(f"PATH value expected, got {type(value).__name__}")
        text = os.fsdecode(value)
        if "\r" in text or "\n" in text:
            raise ValueError(f"line break in path {text!r}")
        return text
    if suffix == "PTS":
        if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
            raise TypeError(f"PTS value (sequence of (x, y) points) expected, got {type(value).__name__}")
        points: list[tuple[float, float]] = []
        for point in value:
            pair = list(point) if isinstance(point, Iterable) and not isinstance(point, (str, bytes)) else None
            if pair is None or len(pair) != 2 or not all(_is_real(v) for v in pair):
                raise TypeError(f"PTS point (x, y) expected, got {point!r}")
            points.append((float(pair[0]), float(pair[1])))
        return points
    if suffix == "TABLE":
        if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
            raise TypeError(f"TABLE value (2-D numeric array) expected, got {type(value).__name__}")
        table: list[list[float]] = []
        for row in value:
            if isinstance(row, (str, bytes)) or not isinstance(row, Iterable):
                raise TypeError(f"TABLE row expected, got {row!r}")
            cells = list(row)
            if not all(_is_real(v) for v in cells):
                raise TypeError(f"TABLE row of numbers expected, got {cells!r}")
            table.append([float(v) for v in cells])
        if len({len(row) for row in table}) > 1:
            raise ValueError("TABLE rows must all have the same length")
        return table
    raise InvalidProperty(f"unknown type suffix {suffix!r}")


def _suffix_for(value: Any, array_suffix: str) -> str:
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, numbers.Integral):
        return "I32"
    if isinstance(value, numbers.Real):
        return "DBL"
    if isinstance(value, str):
        return "STR"
    if isinstance(value, os.PathLike):
        return "PATH"
    if isinstance(value, Iterable) and not isinstance(value, bytes):
        return array_suffix
    raise TypeError(f"no property type for {type(value).__name__} values")


def _same(a: Any, b: Any) -> bool:
    try:
        return bool(a == b)
    except Exception:                                    # noqa: BLE001 - exotic values: treat as changed
        return False


# ---------------------------------------------------------------------------------------------- store
@dataclass
class _Entry:
    name: str           # "section.name" as first written
    section: str
    key: str            # the key written in the file, suffix included ("speed_I32", "exposures_TABLE")
    suffix: str
    value: Any


class TypedStore:
    """Thread-safe typed properties backed by a LabVIEW INI file (``alox.ini`` or a profile ``<name>.ini``).

    Names are ``"section.name"`` (section = everything before the last dot), compared case-insensitively as NI does.
    Values: ``BOOL`` bool, ``DBL`` float, ``I32``/``U32`` int, ``STR`` str, ``PATH`` str (raw path text, see
    :meth:`get_path`), ``PTS`` list of ``(x, y)`` tuples, ``TABLE`` list of rows of floats.
    """

    #: Suffix of a new property set from a sequence: ``PTS`` in ``alox.ini`` (the only array "Save" writes there).
    array_suffix = "PTS"

    def __init__(self, path: str | os.PathLike[str] | None = None, *, on_change: OnChange | None = None,
                 decimal: str = ",", encoding: str = "cp1252") -> None:
        if decimal not in (".", ","):
            raise ValueError(f"decimal separator must be '.' or ',', not {decimal!r}")
        self._lock = threading.RLock()
        self._entries: dict[str, _Entry] = {}
        self._dirty: set[str] = set()
        self._removed: dict[str, _Entry] = {}
        self.path: Path | None = Path(path) if path is not None else None
        self.on_change = on_change
        self.decimal = decimal
        self.encoding = encoding
        self.invalid: list[str] = []

    # -- construction
    @classmethod
    def read(cls, path: str | os.PathLike[str], *, on_change: OnChange | None = None,
             encoding: str = "cp1252") -> Self:
        """Load ``path`` (:class:`FileNotFoundError` when missing)."""
        store = cls(path, on_change=on_change, encoding=encoding)
        store.reload()
        return store

    @classmethod
    def parse(cls, text: str, **kwargs: Any) -> Self:
        """A store holding the properties of ``text`` and no file (:meth:`save` then needs a path)."""
        store = cls(**kwargs)
        with store._lock:
            store._load_text(text)
        return store

    def reload(self) -> None:
        """Read the file again, dropping unsaved changes."""
        with self._lock:
            if self.path is None:
                raise ValueError("no file to read")
            data = self.path.read_bytes()
            if data.startswith(b"\xef\xbb\xbf"):
                data = data[3:]
            self._load_text(data.decode(self.encoding, errors="replace"))

    def _load_text(self, text: str) -> None:
        cfg = NIConfig.parse(text)
        entries: dict[str, _Entry] = {}
        invalid: list[str] = []
        decimal: str | None = None
        for section in cfg.sections():
            for key in cfg.keys(section):
                if _ELEMENT.match(key):
                    continue                                    # read with its <size(s)> key
                try:
                    if key.lower().endswith(TABLE_SIZE_TAG.lower()):
                        written = key[:-len(TABLE_SIZE_TAG)]
                        name, suffix = split_typed_key(written)
                        if suffix != "TABLE":
                            raise InvalidProperty(f"[{section}] {key}: array of an unknown type")
                        value: Any = read_table(cfg, section, written)
                    else:
                        written = key
                        name, suffix = split_typed_key(key)
                        if suffix is None:
                            raise InvalidProperty(f"Invalid property '{section}.{key}'")
                        if suffix == "TABLE":
                            value = parse_inline_table(cfg.read_str(section, key, ""))
                            if any(k.lower() == (key + TABLE_SIZE_TAG).lower() for k in cfg.keys(section)):
                                log.warning("%s: [%s] %s written in both layouts; the one-line value is read",
                                            self.path or "<text>", section, key)
                        else:
                            value = self._read_value(cfg, section, key, suffix)
                except (InvalidProperty, ConfigTypeInvalid, ValueError) as exc:
                    invalid.append(f"{section}.{key}")
                    log.warning("%s: %s (skipped)", self.path or "<text>", exc)
                    continue
                if decimal is None and suffix in ("DBL", "PTS"):
                    raw = cfg.raw(section, key) or ""
                    decimal = "," if _COMMA.search(raw) else "." if _POINT.search(raw) else None
                dotted = f"{section}.{name}"
                entries[dotted.lower()] = _Entry(dotted, section, written, suffix, value)
        self._entries = entries
        self._dirty.clear()
        self._removed.clear()
        self.invalid = invalid
        if decimal is not None:
            self.decimal = decimal

    @staticmethod
    def _read_value(cfg: NIConfig, section: str, key: str, suffix: str) -> Any:
        if suffix == "BOOL":
            return cfg.read_bool(section, key, False)
        if suffix == "DBL":
            return cfg.read_float(section, key, 0.0)
        if suffix in _RANGES:
            return cfg.read_int(section, key, 0, unsigned=suffix == "U32")
        if suffix == "PATH":
            return cfg.read_path(section, key, "")
        if suffix == "PTS":
            return parse_points(cfg.read_str(section, key, ""))
        return cfg.read_str(section, key, "")

    # -- reading
    def _find(self, dotted: str) -> _Entry | None:
        entry = self._entries.get(dotted.lower())
        if entry is None:
            section, name = split_name(dotted)
            base, suffix = split_typed_key(name)
            if suffix is not None:
                entry = self._entries.get(f"{section}.{base}".lower())
                if entry is not None and entry.suffix != suffix:
                    entry = None
        return entry

    def names(self) -> list[str]:
        """``"section.name"`` of every property, in file order ("Get Properties Names")."""
        with self._lock:
            return [entry.name for entry in self._entries.values()]

    def exists(self, dotted: str) -> bool:
        """"Property Exists?"."""
        with self._lock:
            return self._find(dotted) is not None

    def suffix_of(self, dotted: str) -> str | None:
        """The type suffix of a property (``"DBL"``…), None when absent."""
        with self._lock:
            entry = self._find(dotted)
            return None if entry is None else entry.suffix

    def get(self, dotted: str, default: Any = None) -> Any:
        """The value of ``dotted`` (a copy for arrays), or ``default`` when absent ("Get Property"). The name may carry
        its suffix (``"general.port_I32"``)."""
        with self._lock:
            entry = self._find(dotted)
            return default if entry is None else copy.deepcopy(entry.value)

    def get_path(self, dotted: str, default: Path | None = None, *, platform: str = sys.platform,
                 env: Mapping[str, str] = os.environ) -> Path | None:
        """A ``PATH`` or ``STR`` property as a local path (:func:`aloxcam.lv.path.localize_path_text`); ``default``
        when absent, None for an empty or unmappable value."""
        with self._lock:
            entry = self._find(dotted)
            if entry is None:
                return default
            if entry.suffix not in ("PATH", "STR"):
                raise TypeError(f"{entry.name} is a {entry.suffix} property, not a path")
            text = entry.value
        return localize_path_text(text, platform=platform, env=env)

    def items(self) -> dict[str, Any]:
        """A snapshot ``{name: value}`` ("Get All Properties")."""
        with self._lock:
            return {entry.name: copy.deepcopy(entry.value) for entry in self._entries.values()}

    @property
    def dirty(self) -> list[str]:
        """Names changed (or deleted) since the last load or save."""
        with self._lock:
            return [self._entries[k].name for k in self._dirty if k in self._entries] + \
                [entry.name for entry in self._removed.values()]

    # -- writing
    def _notify(self, events: list[tuple[str, Any, Any]]) -> None:
        callback = self.on_change
        if callback is None:
            return
        for name, old, new in events:
            try:
                callback(name, old, new)
            except Exception:
                log.exception("on_change listener failed for %s", name)

    def set(self, dotted: str, value: Any) -> None:
        """Set ``dotted`` ("Set Property" / "Add Property").

        An existing property keeps its type: ``value`` must match its suffix (:class:`TypeError`; :class:`ValueError`
        out of the I32/U32 range). A new property gets the suffix written in the name (``"x.count_U32"``) or the one
        of the Python type (bool → BOOL, int → I32, float → DBL, str → STR, path → PATH, sequence →
        :attr:`array_suffix`). LabVIEW's "Set Property" refused unknown names; "Add Property" added them.
        """
        section, name = split_name(dotted)
        base, given = split_typed_key(name)
        with self._lock:
            lower = f"{section}.{base}".lower()
            entry = self._entries.get(lower)
            if entry is not None:
                if given is not None and given != entry.suffix:
                    raise TypeError(f"{entry.name} is a {entry.suffix} property, not {given}")
                new = _coerce(entry.suffix, value)
                old = entry.value
                if _same(old, new):
                    return
                entry.value = new
                event = (entry.name, copy.deepcopy(old), copy.deepcopy(new))
            else:
                suffix = given or _suffix_for(value, self.array_suffix)
                new = _coerce(suffix, value)
                removed = self._removed.pop(lower, None)
                key = removed.key if removed is not None and removed.suffix == suffix else f"{base}_{suffix}"
                if removed is not None and removed.key != key:
                    self._removed[lower] = removed                # still remove the old key on save
                entry = _Entry(f"{section}.{base}", section, key, suffix, new)
                self._entries[lower] = entry
                event = (entry.name, None, copy.deepcopy(new))
            self._dirty.add(lower)
        self._notify([event])

    def delete(self, dotted: str) -> bool:
        """Remove a property ("Del Property"); it is removed from the file on :meth:`save`. False when absent."""
        with self._lock:
            entry = self._find(dotted)
            if entry is None:
                return False
            lower = entry.name.lower()
            del self._entries[lower]
            self._dirty.discard(lower)
            self._removed.setdefault(lower, entry)
            event = (entry.name, copy.deepcopy(entry.value), None)
        self._notify([event])
        return True

    def clear(self) -> None:
        """"Clear": forget every property in memory. The file is not changed by a later :meth:`save` (as LabVIEW,
        whose "Save" only writes the properties it holds)."""
        with self._lock:
            self._entries.clear()
            self._dirty.clear()
            self._removed.clear()

    def missing_from(self, defaults: TypedStore | str | os.PathLike[str]) -> list[str]:
        """"Check": add every property of ``defaults`` (a store or an INI file) that is missing here, with its default
        value and key; returns the names added (written by the next :meth:`save`)."""
        source = defaults if isinstance(defaults, TypedStore) else type(self).read(defaults)
        with source._lock:
            wanted = [replace(entry, value=copy.deepcopy(entry.value)) for entry in source._entries.values()]
        events: list[tuple[str, Any, Any]] = []
        with self._lock:
            for entry in wanted:
                lower = entry.name.lower()
                if lower in self._entries:
                    continue
                self._removed.pop(lower, None)
                self._entries[lower] = entry
                self._dirty.add(lower)
                events.append((entry.name, None, copy.deepcopy(entry.value)))
        self._notify(events)
        return [name for name, _, _ in events]

    # -- saving
    def save(self, path: str | os.PathLike[str] | None = None) -> Path:
        """Write the changes ("Save") and return the file written.

        To the file read (``path`` None or the same file): only the changed, added and deleted keys are written, in
        place (:class:`alox.config.ini_writer.IniDocument`), every other line stays byte for byte; nothing is written
        when nothing changed. To another file, or when the file does not exist yet: the current file text (if any)
        plus every change, i.e. a complete copy. The store then refers to the file written. Atomic (temporary file +
        rename). Numbers use :attr:`decimal` (the separator found in the file, default comma).
        """
        with self._lock:
            target = Path(path) if path is not None else self.path
            if target is None:
                raise ValueError("no path to save to")
            same = self.path is not None and _same_file(self.path, target)
            source = self.path if self.path is not None and self.path.exists() else None
            if source is not None:
                if same and not self._dirty and not self._removed:
                    return target
                doc = IniDocument.load(source, encoding=self.encoding)
                todo = [self._entries[k] for k in self._entries if k in self._dirty]
            else:
                doc = IniDocument.parse("")
                doc.encoding = self.encoding
                todo = list(self._entries.values())
            for entry in self._removed.values():
                self._remove_entry(doc, entry)
            for entry in todo:
                self._write_entry(doc, entry)
            target.parent.mkdir(parents=True, exist_ok=True)
            doc.save(target, encoding=self.encoding)
            self.path = target
            self._dirty.clear()
            self._removed.clear()
            return target

    def _write_entry(self, doc: IniDocument, entry: _Entry) -> None:
        if entry.suffix == "PTS":
            doc.set(entry.section, entry.key, format_points(entry.value, self.decimal))
        elif entry.suffix == "TABLE":
            doc.set(entry.section, entry.key, format_table(entry.value, self.decimal))
            while doc.remove(entry.section, entry.key + TABLE_SIZE_TAG):   # the legacy layout, if it was read
                pass
            self._remove_elements(doc, entry.section, entry.key, keep=set())
        else:
            doc.set(entry.section, entry.key, format_value(entry.suffix, entry.value, decimal=self.decimal))

    @staticmethod
    def _remove_elements(doc: IniDocument, section: str, base: str, keep: set[str]) -> None:
        for key in NIConfig.parse(doc.text()).keys(section):
            m = _ELEMENT.match(key)
            if m and m.group("base").lower() == base.lower() and key.lower() not in keep:
                while doc.remove(section, key):
                    pass

    def _remove_entry(self, doc: IniDocument, entry: _Entry) -> None:
        if entry.suffix == "TABLE":
            while doc.remove(entry.section, entry.key + TABLE_SIZE_TAG):
                pass
            self._remove_elements(doc, entry.section, entry.key, keep=set())
        while doc.remove(entry.section, entry.key):
            pass


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


class Properties(TypedStore):
    """The application settings ``<settings>\\alox.ini`` (map Part 1 §4.1)."""

    array_suffix = "PTS"

    @classmethod
    def load(cls, path: str | os.PathLike[str], defaults_path: str | os.PathLike[str] | None = None, *,
             on_change: OnChange | None = None, encoding: str = "cp1252") -> Properties:
        """Load ``path``; when it is missing, first copy ``defaults_path`` (``<exe>\\alox_default.ini``) there, as the
        boot cycle does (``alox_core_boot_cycle d39..d41``: "if not => try to create one by copying a default file
        from the software folder"). :class:`FileNotFoundError` when neither exists.

        The boot cycle then runs "Check" (:meth:`missing_from`) with the hard-coded defaults of "New"; these are not
        ported (1312 diagram images): pass ``alox_default.ini`` to :meth:`missing_from` instead.
        """
        target = Path(path)
        if not target.exists():
            if defaults_path is None or not Path(defaults_path).is_file():
                raise FileNotFoundError(f"{target} is missing and no defaults file ({defaults_path}) to copy")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(defaults_path, target)
            log.info("%s created from %s", target, defaults_path)
        return cls.read(target, on_change=on_change, encoding=encoding)
