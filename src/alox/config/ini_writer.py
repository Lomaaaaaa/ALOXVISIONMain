"""Editing a LabVIEW INI file in place: change single values, keep everything else byte for byte.

Spec: 09 §6.1 (what NI reads: first section, first key, quotes, comments), §6.4 (``Write Key`` formats);
docs/vision-core/phase3-skeleton.md §3.

:class:`IniDocument` keeps the file as lines with their own line endings, so comments, blank lines, order, unknown or
duplicated keys and the spacing of untouched lines survive a load/save cycle unchanged. Sections and keys are recognised
with the regular expressions of :mod:`aloxcam.config.niconfig`, so the line changed is always the one
:class:`aloxcam.config.niconfig.NIConfig` (and :class:`alox.config.typed_ini.TypedIni`) reads.

Values are written in LabVIEW's ``Write Key`` format (09 §6.4): ``TRUE``/``FALSE``, decimal integers, doubles with 6
decimals and a decimal comma by default, strings double-quoted with ``\\`` and control characters escaped as
``Unescape String`` expects, paths double-quoted in the NI common form ``/D/dir/sub``. Deviation: NI writes paths
unquoted; they are quoted here, as in the real files, so that ``;`` and trailing blanks survive.
"""

from __future__ import annotations

import math
import numbers
import os
import re
import tempfile
from pathlib import Path

from alox.config.typed_ini import (
    SUFFIXES,
    resolve_key,
    split_name,
    split_suffix,
    suffix_for,
)
from aloxcam.config.niconfig import _KEY_VALUE, _SECTION, NIConfig

__all__ = ["IniDocument", "escape_string", "format_double", "format_value", "to_common_path"]

_EOL = re.compile(r"\r\n|\r|\n")                         # the line breaks of NI String to Config
_DRIVE = re.compile(r"([A-Za-z]):(?:/|$)")
_BLANKS = " \t"
_BOM = b"\xef\xbb\xbf"
_RANGES = {"I32": (-(2**31), 2**31 - 1), "U32": (0, 2**32 - 1)}


# ---------------------------------------------------------------------------------------------- value formatting
def escape_string(text: str) -> str:
    """Inverse of NI ``Unescape String``: ``\\`` → ``\\\\``, characters 00–1F → ``\\XX`` (upper-case hex).

    Quotes need no escape: the reader takes everything between the first and the last quote.
    """
    return "".join("\\\\" if ch == "\\" else f"\\{ord(ch):02X}" if ord(ch) < 0x20 else ch for ch in text)


def format_double(value: float, decimal: str = ",") -> str:
    """LabVIEW ``%.6f`` with the given decimal separator (``24,600000``); ``NaN``, ``Inf``, ``-Inf`` as LabVIEW."""
    if decimal not in (".", ","):
        raise ValueError(f"decimal separator must be '.' or ',', not {decimal!r}")
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Inf" if value > 0 else "-Inf"
    return f"{value:.6f}".replace(".", decimal)


def to_common_path(text: str) -> str:
    """NI common path form: ``D:\\dir\\sub`` → ``/D/dir/sub``, ``\\\\srv\\share`` → ``//srv/share``, ``a\\b`` →
    ``a/b``. Text already in common (or POSIX) form keeps its components; repeated and trailing separators are
    dropped; ``""`` stays ``""`` (empty path)."""
    s = text.replace("\\", "/")
    m = _DRIVE.match(s)
    if m:
        return "/" + "/".join([m.group(1), *(part for part in s[2:].split("/") if part)])
    joined = "/".join(part for part in s.split("/") if part)
    if s.startswith("//"):
        return "//" + joined
    return "/" + joined if s.startswith("/") else joined


def format_value(suffix: str, value: object, *, decimal: str = ",") -> str:
    """``value`` as LabVIEW writes a key with type ``suffix`` (09 §6.4).

    ``BOOL``: a bool → ``TRUE``/``FALSE``. ``DBL``: a real number → :func:`format_double` (6 decimals, so the value
    read back is rounded to 6 decimals). ``I32``/``U32``: an integer within the range. ``STR``: a str (or path) →
    quoted and escaped. ``PATH``: a str or path → quoted, in common form. :class:`TypeError` for a value of the wrong
    type, :class:`ValueError` for an integer out of range, a line break in a path or an unknown suffix.
    """
    suffix = suffix.upper()
    if suffix == "BOOL":
        if not isinstance(value, bool):
            raise TypeError(f"BOOL value expected, got {type(value).__name__}")
        return "TRUE" if value else "FALSE"
    if suffix == "DBL":
        if isinstance(value, bool) or not isinstance(value, numbers.Real):
            raise TypeError(f"DBL value expected, got {type(value).__name__}")
        return format_double(float(value), decimal)
    if suffix in _RANGES:
        if isinstance(value, bool) or not isinstance(value, numbers.Integral):
            raise TypeError(f"{suffix} value expected, got {type(value).__name__}")
        lo, hi = _RANGES[suffix]
        if not lo <= int(value) <= hi:
            raise ValueError(f"{value} is out of the {suffix} range [{lo}, {hi}]")
        return str(int(value))
    if suffix == "STR":
        if isinstance(value, os.PathLike):
            value = os.fspath(value)
        if not isinstance(value, str):
            raise TypeError(f"STR value expected, got {type(value).__name__}")
        return '"' + escape_string(value) + '"'
    if suffix == "PATH":
        if not isinstance(value, (str, os.PathLike)):
            raise TypeError(f"PATH value expected, got {type(value).__name__}")
        text = to_common_path(os.fsdecode(value))
        if "\r" in text or "\n" in text:
            raise ValueError(f"line break in path {text!r}")
        return f'"{text}"'
    raise ValueError(f"unknown type suffix {suffix!r} (expected one of {', '.join(SUFFIXES)})")


# ---------------------------------------------------------------------------------------------- line helpers
def _key_of(content: str) -> str | None:
    """The key of a key line (as NIConfig parses it), None for a section header, comment or blank line."""
    if _SECTION.match(content) or not _KEY_VALUE.match(content):
        return None
    return content.partition("=")[0].strip(_BLANKS)


def _stored_value(line: str) -> str | None:
    """The value NIConfig stores for the key line ``line`` (parsed by NIConfig itself, for exact parity)."""
    return NIConfig.parse(f"[_]\n{line}\n").raw("_", line.partition("=")[0])


def _replace_value(content: str, value: str) -> str:
    """``content`` (a key line) with its value replaced: the key, the spacing around ``=``, a trailing comment and
    trailing blanks are kept (the comment is dropped when the new value would swallow it)."""
    head, _, rest = content.partition("=")
    body = rest.lstrip(_BLANKS)
    gap = rest[:len(rest) - len(body)]
    if not body.strip(_BLANKS):                         # empty value: "k =" or "k = " → "k = v"
        return f"{head}={' ' if head.endswith(tuple(_BLANKS)) or gap else ''}{value}"
    if _stored_value(content) == body.rstrip(_BLANKS):
        old = body.rstrip(_BLANKS)                      # wholly quoted, or no comment: only trailing blanks follow
    else:
        old = body.partition(";")[0].rstrip(_BLANKS)
    line = f"{head}={gap}{value}{body[len(old):]}"
    if _stored_value(line) != value.strip(_BLANKS):
        line = f"{head}={gap}{value}"
    return line


def _check_section(section: str) -> None:
    m = _SECTION.match(f"[{section}]")
    if not m or m.group(1) != section or "\r" in section or "\n" in section:
        raise ValueError(f"invalid section name {section!r}")


def _check_key(key: str) -> None:
    if "\r" in key or "\n" in key or _key_of(f"{key} = x") != key:
        raise ValueError(f"invalid key name {key!r}")


def _check_value(value: str) -> None:
    if "\r" in value or "\n" in value:
        raise ValueError(f"line break in value {value!r}")


class IniDocument:
    """An INI file being edited. Untouched lines are kept exactly, with their own line endings.

    Lookups follow NIConfig: case-insensitive names, the FIRST matching section (later copies are never read), the
    FIRST matching key in it. Lines before the first section are never searched.
    """

    def __init__(self) -> None:
        self._lines: list[tuple[str, str]] = []         # (content, line ending: "\r\n", "\n", "\r" or "" at the end)
        self._newline = "\r\n"                          # for new lines: the first line ending of the file, else CRLF
        self._bom = False
        self.path: Path | None = None
        self.encoding = "cp1252"

    # -- construction and output
    @classmethod
    def parse(cls, text: str) -> IniDocument:
        """A document holding ``text`` (a leading BOM is kept apart and written back)."""
        doc = cls()
        if text.startswith("\ufeff"):
            doc._bom, text = True, text[1:]
        pos = 0
        for m in _EOL.finditer(text):
            doc._lines.append((text[pos:m.start()], m.group()))
            pos = m.end()
        if pos < len(text):
            doc._lines.append((text[pos:], ""))
        if doc._lines and doc._lines[0][1]:
            doc._newline = doc._lines[0][1]
        return doc

    @classmethod
    def load(cls, path: str | os.PathLike[str], encoding: str = "cp1252") -> IniDocument:
        """Read ``path``. Bytes that ``encoding`` cannot decode are kept (``surrogateescape``) and written back as they
        were. :class:`FileNotFoundError` when the file is missing."""
        data = Path(path).read_bytes()
        bom = data.startswith(_BOM)
        doc = cls.parse(data[len(_BOM) if bom else 0:].decode(encoding, errors="surrogateescape"))
        doc._bom = doc._bom or bom
        doc.path, doc.encoding = Path(path), encoding
        return doc

    def text(self) -> str:
        """The whole document as text (unchanged lines exactly as read)."""
        return ("\ufeff" if self._bom else "") + "".join(content + eol for content, eol in self._lines)

    def save(self, path: str | os.PathLike[str] | None = None, encoding: str | None = None) -> Path:
        """Write the document to ``path`` (default: the file it was loaded from) atomically: a temporary file in the
        same folder, flushed to disk, then renamed over the target, which keeps its permissions. The text is encoded
        first, so an unencodable character (:class:`UnicodeEncodeError`) leaves the file untouched. Returns the path.
        """
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("no path to save to")
        enc = encoding or self.encoding
        body = "".join(content + eol for content, eol in self._lines).encode(enc, errors="surrogateescape")
        if self._bom and not body.startswith(_BOM):
            body = _BOM + body
        fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, target.stat().st_mode & 0o7777 if target.exists() else 0o644)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return target

    # -- lookup
    def _block(self, section: str) -> tuple[int, int] | None:
        """``(header index, end index)`` of the first section named ``section``, or None."""
        wanted = section.lower()
        start: int | None = None
        for i, (content, _) in enumerate(self._lines):
            m = _SECTION.match(content)
            if not m:
                continue
            if start is not None:
                return start, i
            if m.group(1).lower() == wanted:
                start = i
        return None if start is None else (start, len(self._lines))

    def _find(self, start: int, end: int, key: str) -> int | None:
        wanted = key.strip(_BLANKS).lower()
        for i in range(start + 1, end):
            k = _key_of(self._lines[i][0])
            if k is not None and k.lower() == wanted:
                return i
        return None

    def _keys(self, start: int, end: int) -> list[str]:
        """Keys of a section block in file order, each once (case-insensitive), as written."""
        seen: set[str] = set()
        keys: list[str] = []
        for i in range(start + 1, end):
            k = _key_of(self._lines[i][0])
            if k is not None and k.lower() not in seen:
                seen.add(k.lower())
                keys.append(k)
        return keys

    def get_raw(self, section: str, key: str) -> str | None:
        """The stored text of the first matching key (quotes and comment handled as NIConfig does), or None."""
        block = self._block(section)
        i = None if block is None else self._find(*block, key)
        return None if i is None else _stored_value(self._lines[i][0])

    def _insert(self, index: int, contents: list[str]) -> None:
        if index > 0 and not self._lines[index - 1][1]:
            self._lines[index - 1] = (self._lines[index - 1][0], self._newline)
        self._lines[index:index] = [(content, self._newline) for content in contents]

    # -- editing
    def set(self, section: str, key: str, value: str) -> None:
        """Set the already formatted text ``value`` of ``key`` in ``section``.

        The first matching key of the first matching section is changed in place (its spelling, the spacing around
        ``=``, a trailing comment and trailing blanks are kept). A missing key is added as ``key = value`` after the
        last non-blank line of the section; a missing section is added at the end of the file. :class:`ValueError` for
        a line break in ``value`` or a name NIConfig could not read back.
        """
        _check_section(section)
        _check_key(key)
        _check_value(value)
        block = self._block(section)
        if block is None:
            separator = [""] if self._lines and self._lines[-1][0].strip(_BLANKS) else []
            self._insert(len(self._lines), [*separator, f"[{section}]", f"{key} = {value}"])
            return
        start, end = block
        i = self._find(start, end, key)
        if i is not None:
            content, eol = self._lines[i]
            self._lines[i] = (_replace_value(content, value), eol)
            return
        last = start
        for j in range(start + 1, end):
            if self._lines[j][0].strip(_BLANKS):
                last = j
        self._insert(last + 1, [f"{key} = {value}"])

    def set_typed(self, dotted_name: str, value: object, *, decimal: str = ",") -> str:
        """Set ``"section.name"`` to ``value`` in LabVIEW format (:func:`format_value`); returns the key written.

        The key is chosen as :class:`alox.config.typed_ini.TypedIni` reads it: an existing ``name_<SUFFIX>`` key (the
        one matching the type of ``value`` when several exist) is formatted by its suffix; an existing plain ``name``
        key by the type of ``value``. Otherwise ``name_<SUFFIX>`` is created with the suffix of the Python type
        (bool → ``BOOL``, float → ``DBL``, int → ``I32``, str → ``STR``, path → ``PATH``), or ``name`` itself when it
        already ends with a suffix (``"hardware.bandit.nbpoints9_U32"``).
        """
        section, name = split_name(dotted_name)
        block = self._block(section)
        found = resolve_key(self._keys(*block) if block else [], name, value)
        if found is None:
            key, suffix = (name, split_suffix(name)[1])
        else:
            key, suffix = found
        if suffix is None:
            suffix = suffix_for(value)
            if found is None:
                key = f"{name}_{suffix}"
        self.set(section, key, format_value(suffix, value, decimal=decimal))
        return key

    def remove(self, section: str, key: str) -> bool:
        """Remove the line of the first matching key of the first matching section; False when there is none."""
        block = self._block(section)
        i = None if block is None else self._find(*block, key)
        if i is None:
            return False
        del self._lines[i]
        return True
