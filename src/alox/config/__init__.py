"""Typed reading and preserving editing of the LabVIEW "NI config" INI files of Vision Core (``alox.ini``,
``alox_setup.ini``).

- :class:`TypedIni` (:mod:`alox.config.typed_ini`): ``get("section.name", default)`` finds ``name_<SUFFIX>`` (``BOOL``,
  ``DBL``, ``I32``, ``U32``, ``STR``, ``PATH``) and converts it with the LabVIEW rules of
  :class:`aloxcam.config.niconfig.NIConfig`.
- :class:`IniDocument` (:mod:`alox.config.ini_writer`): changes single values in LabVIEW's format and keeps everything
  else (comments, order, duplicates, spacing, line endings); saves atomically.

Spec: 09 §6; docs/vision-core/phase3-skeleton.md §3.
"""

from __future__ import annotations

from alox.config.ini_writer import (
    IniDocument,
    escape_string,
    format_double,
    format_value,
    to_common_path,
)
from alox.config.typed_ini import (
    SUFFIXES,
    ConfigTypeInvalid,
    TypedIni,
    split_name,
    split_suffix,
)

__all__ = [
    "SUFFIXES",
    "ConfigTypeInvalid",
    "IniDocument",
    "TypedIni",
    "escape_string",
    "format_double",
    "format_value",
    "split_name",
    "split_suffix",
    "to_common_path",
]
