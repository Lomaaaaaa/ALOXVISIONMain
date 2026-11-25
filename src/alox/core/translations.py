"""Localized strings of Vision Core: the ``.lang`` files of the ``translations`` folder (map Part 1 §4.5, §8 note 16).

LabVIEW has one language manager per file family (``alox_manager_language`` for ``alox_<lang>.lang``; the system, mesa,
tilt and bowing status managers each read their own ``<family>_<lang>.lang``; ``alox_manager_language d5``,
``alox_manager_system_status_ID d4``). :class:`Translations` loads the five families in one object, but keeps them as
separate **domains** because their codes collide: ``000000`` is defined by system, mesa, tilt and bowing with different
texts, ``001001`` is "ERROR (High mag)" for system and "Reset wavelength error" for bowing, and ``0015xx`` are log
strings in ``alox`` but boot errors in ``system``. A flat merge would silently return the wrong text.

File format (``readme.txt``, the file headers and ``alox_manager_language d5``, read with ``Read Delimited Spreadsheet``
and the delimiter ``;``)::

    ;; comment line (any line whose first column is empty)
    NNNNNN;text;optional comment

- the ID is a decimal number written on 6 digits (``%06d`` in ``alox_manager_system_status_ID d11``); rows whose ID or
  text column is empty are skipped, as in LabVIEW; rows whose ID is not a number are skipped too;
- there is no escaping and no quoting: a ``;`` always ends the text (the rest of the row is the comment) and a
  backslash is an ordinary character;
- the files are Windows ANSI (cp1252: ``system_en.lang`` holds 0x92 quotes, ``alox_fr.lang`` accented letters) with
  CRLF line ends (the language manager comment says "LF+CR"); any line end is accepted here;
- differences with LabVIEW, all tolerant: blanks around the ID and the text are stripped (``alox_en.lang`` has
  ``"  008002;..."`` and ``"000006;Abort      "``, whose ID LabVIEW stores with its leading blanks, so it can never be
  found), and a file starting with a UTF-8 BOM, or that is valid UTF-8, is read as UTF-8. A repeated ID keeps the last
  text (LabVIEW ``Set Variant Attribute`` replaces).

Lookup precedence for ``text(code, domain=...)``: the domain's table in the current language, then the same domain's
English table (LabVIEW falls back to ``<family>_en.lang`` when the language file is missing; here English is also the
fallback for a single missing ID), then the 6-digit code itself (LabVIEW returns ``"?"``). Domains never fall back on
each other.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

__all__ = ["DEFAULT_LANGUAGE", "DOMAINS", "Translations", "code_id", "decode_lang_bytes", "parse_lang", "read_lang_file"]

log = logging.getLogger(__name__)

#: The file families of the ``translations`` folder, i.e. ``<domain>_<language>.lang``.
DOMAINS: tuple[str, ...] = ("alox", "system", "mesa", "tilt", "bowing")

#: The fallback language (LabVIEW "Get default english file if language file not found").
DEFAULT_LANGUAGE = "en"

_LANGUAGE = re.compile(r"[A-Za-z]{2,8}(?:[_-][A-Za-z0-9]{1,8})?\Z")
_BOM = b"\xef\xbb\xbf"


def code_id(code: int | str) -> str:
    """The 6-digit string ID of ``code`` (``11`` → ``"000011"``), as in the files and LabVIEW's ``%06d``."""
    return f"{int(code):06d}"


def decode_lang_bytes(data: bytes) -> str:
    """Decode a ``.lang`` file: UTF-8 when it has a BOM or is valid UTF-8, else Windows ANSI (cp1252)."""
    if data.startswith(_BOM):
        return data[len(_BOM):].decode("utf-8", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def parse_lang(text: str) -> dict[int, str]:
    """``{code: text}`` of the content of a ``.lang`` file (format in the module docstring)."""
    table: dict[int, str] = {}
    for row in text.splitlines():
        columns = row.split(";")
        if len(columns) < 2:
            continue
        ident, value = columns[0].strip(), columns[1].strip()
        if not ident or not value or not (ident.isascii() and ident.isdigit()):
            continue
        table[int(ident)] = value
    return table


def read_lang_file(path: str | os.PathLike[str]) -> dict[int, str]:
    """:func:`parse_lang` of a file."""
    return parse_lang(decode_lang_bytes(Path(path).read_bytes()))


def _check_language(language: str) -> str:
    if not isinstance(language, str) or not _LANGUAGE.match(language):
        raise ValueError(f"invalid language code {language!r} (ISO-639, e.g. 'en' or 'fr')")
    return language.lower()


class Translations:
    """The localized strings of the application, by domain (``alox``, ``system``, ``mesa``, ``tilt``, ``bowing``).

    ``folder`` is the ``translations`` folder (``<application>\\translations`` in LabVIEW, **inferred** path); without a
    folder the tables only come from :meth:`load_text` (tests, embedded defaults). The boot cycle calls
    :meth:`set_language` with the property ``general.language`` (map Part 1 §2.3, boot frames 4 and 5). Thread-safe:
    a language switch builds the new tables and swaps them at once.
    """

    def __init__(self, folder: str | os.PathLike[str] | None = None, language: str = DEFAULT_LANGUAGE, *,
                 domains: Iterable[str] = DOMAINS) -> None:
        self._folder = Path(folder) if folder is not None else None
        self._domains = tuple(domains)
        self._lock = threading.Lock()
        self._language = _check_language(language)
        # (domain, language) -> table; only the current language and English are kept
        self._tables: dict[tuple[str, str], dict[int, str]] = {}
        self._loaded: list[Path] = []
        if self._folder is not None:
            self.set_language(language)

    # ------------------------------------------------------------------------------------------------ loading

    @property
    def folder(self) -> Path | None:
        return self._folder

    @property
    def language(self) -> str:
        """The current language code (lower case)."""
        return self._language

    @property
    def domains(self) -> tuple[str, ...]:
        return self._domains

    @property
    def loaded_files(self) -> list[Path]:
        """The files read by the last :meth:`set_language` (for diagnostics)."""
        with self._lock:
            return list(self._loaded)

    def set_language(self, language: str) -> None:
        """Switch to ``language``: read ``<domain>_en.lang`` and ``<domain>_<language>.lang`` of every domain from the
        folder. A missing file is logged and leaves that table empty (English is then used, as LabVIEW does). Tables
        added with :meth:`load_text` for other languages are dropped."""
        language = _check_language(language)
        tables: dict[tuple[str, str], dict[int, str]] = {}
        loaded: list[Path] = []
        if self._folder is not None:
            for domain in self._domains:
                for lang in dict.fromkeys((DEFAULT_LANGUAGE, language)):
                    path = self._folder / f"{domain}_{lang}.lang"
                    try:
                        tables[(domain, lang)] = read_lang_file(path)
                    except FileNotFoundError:
                        log.warning("language file %s not found", path)
                        continue
                    except OSError as exc:
                        log.warning("cannot read language file %s: %s", path, exc)
                        continue
                    loaded.append(path)
        else:
            with self._lock:
                tables = {key: table for key, table in self._tables.items() if key[1] in (DEFAULT_LANGUAGE, language)}
        with self._lock:
            self._tables = tables
            self._loaded = loaded
            self._language = language

    def load_text(self, domain: str, text: str, language: str = DEFAULT_LANGUAGE) -> None:
        """Add (or replace) the table of ``domain`` in ``language`` from the content of a ``.lang`` file."""
        self.add(domain, parse_lang(text), language)

    def add(self, domain: str, table: Mapping[int, str], language: str = DEFAULT_LANGUAGE) -> None:
        """Add (or replace) the table of ``domain`` in ``language``."""
        if domain not in self._domains:
            raise ValueError(f"unknown translation domain {domain!r} (known: {', '.join(self._domains)})")
        language = _check_language(language)
        with self._lock:
            self._tables[(domain, language)] = {int(k): str(v) for k, v in table.items()}

    # ------------------------------------------------------------------------------------------------ lookup

    def lookup(self, code: int | str, domain: str = "alox") -> str | None:
        """The text of ``code`` with the precedence of the module docstring, or None when no table has it."""
        if domain not in self._domains:
            raise ValueError(f"unknown translation domain {domain!r} (known: {', '.join(self._domains)})")
        key = int(code)
        with self._lock:
            for lang in dict.fromkeys((self._language, DEFAULT_LANGUAGE)):
                table = self._tables.get((domain, lang))
                if table is not None and key in table:
                    return table[key]
        return None

    def has(self, code: int | str, domain: str = "alox") -> bool:
        return self.lookup(code, domain) is not None

    def text(self, code: int | str, *args: Any, domain: str = "alox", **fmt: Any) -> str:
        """The localized text of ``code``; the 6-digit code itself when no table has it.

        The texts use LabVIEW ``Format Into String`` codes (``%s``, ``%f``, ``%d``; e.g. 001550 "api
        'system/configuration' (put) received='%s'"): positional ``args`` fill them with Python's ``%`` operator, which
        follows the same printf rules. Keyword ``fmt`` fills ``%(name)s`` codes instead. Without arguments the text is
        returned as is (a lone ``%`` is not an error). A text that does not match its arguments is returned unformatted
        (and logged): a translation must never break the caller.
        """
        if args and fmt:
            raise TypeError("text() takes positional or keyword format arguments, not both")
        found = self.lookup(code, domain)
        if found is None:
            return code_id(code)
        if not args and not fmt:
            return found
        try:
            return found % (fmt if fmt else args)
        except (TypeError, ValueError, KeyError) as exc:
            log.warning("text %s/%s does not match its arguments: %s", domain, code_id(code), exc)
            return found
