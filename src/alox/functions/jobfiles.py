r"""Job data file names, job-history folders and data files shared by the high-level functions (bowing, mesa, tilt).

Spec: ``docs/vision-core/map.md`` Part 4 §3.10 (folders), §3.11 (file names), ``docs/06-autofocus-refocus.md`` §8.2.
Diagrams (cited ``<vi> dN``): ``tools_alox_job_data_filename d`` (mesa and the refocus images),
``tools_alox_job_data_filename_bowing d..d4``, ``tools_alox_job_data_filename_tilt d..d3``,
``tools_alox_job_filname_filter d..d4``, ``tools_alox_shared_jobhistory_folder_{bowing,mesa,tilt}_path d..d5``,
``tools_save_job_wafer_ d..d2``.

API, for the bowing, mesa and tilt functions (and the refocus images):

- :func:`format_timestamp` (``when``, ``time_format``) and :data:`TIME_FORMAT`: the time stamp of every name;
- :func:`job_data_filename` (``job``, ``when``, ``time_format``, ``kind="mesa"|"bowing"|"tilt"``): the file name
  without extension; the caller adds ``.bow``, ``.png``…;
- :func:`job_folders` (``folders``, ``job``, ``images=``, ``files=``) → :class:`JobFolders` (``root``, ``images``,
  ``files``, ``subfolders``): the job-history tree, created; :func:`job_history_root` alone (LabVIEW's
  ``path_process_lot_wafer_job`` wire of the mesa function); bowing ``images="bowing", files=("bowing",)``, tilt
  ``images="tilt", files=("tilt",)``, mesa ``images="oxid"`` (or ``"mapping"`` when Zone ID ≠ ``"0"``, as read in
  ``…mesa_path d``) ``files=("log", "mesa")``;
- :func:`job_wafer_folder`: ``tools_save_job_wafer_``;
- :func:`write_spreadsheet` / :func:`spreadsheet_text` / :func:`format_lv_number`: ``Write To Spreadsheet File``.

**Time stamp** (every ``*_job_data_filename*`` VI): ``Format Date/Time String(<format>, time)`` (local time, 1 s
resolution), then the first ``","`` and the first ``"."`` removed (two ``Search and Replace String`` without "replace
all"). The usual format is :data:`TIME_FORMAT` ``%y%m%d_%H%M%S``; callers append their own text to it
(``"%y%m%d_%H%M%S_bowing_zwafer2"``). The mesa VI ignores its ``Timestamp`` input and formats "now"
(``docs/06`` §8.2); the bowing and tilt VIs use their input.

**Job data file names** (:func:`job_data_filename`): a SEMI job is "empty" when its five strings are empty (``≠``
against an empty cluster constant). Each of Process Job, Lot and Wafer is replaced by ``"0"`` when empty ("Empty
Data"); the Control Job ID is compared but never used.

======== ================================ =====================================================================
kind     empty job                        with a job
======== ================================ =====================================================================
mesa     ``<ts>_mesa``                    ``<PJ>_<Lot>_<Wafer>_<ts>_<zone>_mapping`` when Zone ID ≠ ``"0"``
                                          (``<zone>`` = ``Zone<id>``, ``0`` when empty), else
                                          ``<PJ>_<Lot>_<Wafer>_<ts>_oxidation``
bowing   ``<ts>_bow``                     ``<PJ>_<Lot>_<Wafer>_<ts>`` (no suffix, ``…bowing d1``)
tilt     ``<ts>_tilt``                    ``<PJ>_<Lot>_<Wafer>_<ts>_tilt``
======== ================================ =====================================================================

**Job-history tree** (:func:`job_folders`): when the Process Job ID is ``""`` or ``"Pj"`` the tree is rooted at
``[folders] data``; otherwise at ``<[folders] jobhistory>\<PJ>_<Lot>\<PJ>_<Lot>_<Wafer>\Vision`` with the three names
passed through ``tools_alox_job_filname_filter`` (``…bowing_path d``, ``d4``, ``d5``). Under the root: ``images\<x>``
and ``files`` and ``files\<y>`` (bowing: ``images\bowing``, ``files\bowing``; tilt: ``images\tilt``, ``files\tilt``;
mesa: ``images\oxid`` or ``images\mapping``, ``files\log``, ``files\mesa``), each created when missing (OpenG "Create
Dir if Non-Existant"). The front panels call the folder output wire ``path_process_lot_wafer_job`` in the mesa VI.

**Name filter** (``tools_alox_job_filname_filter d``): every string goes through two ``Search and Replace String``
("replace all"): the second replaces each space (``Space Constant``) by ``"_"``. The first one's search and replace
constants are an underscore-like glyph in both boxes, unreadable in the export: it is taken as having no effect
(:func:`filter_job_name`).

**Data files** (:func:`write_spreadsheet`): LabVIEW ``Write To Spreadsheet File`` with a format such as ``"%.4f"``:
tab-delimited, one line per row, CRLF line ends, NaN written ``NaN`` and infinities ``Inf`` / ``-Inf`` as LabVIEW
formats them; a 2-D array whose rows differ in length is padded with 0 (``Build Array`` of a 2-D array and a longer
row pads the shorter rows).

``tools_save_job_wafer_`` (:func:`job_wafer_folder`): ``<input>\<PJ>\<Wafer>``, or ``<input>\NoJob`` when the
Process Job ID is empty.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any

from alox.core.functions import JobInfo

__all__ = [
    "JOB_KINDS",
    "NO_JOB_IDS",
    "TIME_FORMAT",
    "JobFolders",
    "filter_job_name",
    "format_lv_number",
    "format_timestamp",
    "job_data_filename",
    "job_folders",
    "job_history_root",
    "job_is_empty",
    "job_wafer_folder",
    "spreadsheet_text",
    "write_spreadsheet",
]

#: The time format of the job data files (``%y%m%d_%H%M%S``, local time).
TIME_FORMAT = "%y%m%d_%H%M%S"
#: Process Job IDs that root the job-history tree at ``[folders] data`` (``…_path d``: ``""`` or ``"Pj"``).
NO_JOB_IDS = ("", "Pj")
#: The ``*_job_data_filename*`` variants: the suffix of an empty job, the suffix with a job.
JOB_KINDS: Mapping[str, tuple[str, str]] = MappingProxyType({"bowing": ("_bow", ""), "tilt": ("_tilt", "_tilt"),
                                                              "mesa": ("_mesa", "")})


def _job(job: JobInfo | None) -> JobInfo:
    return job if job is not None else JobInfo()


def job_is_empty(job: JobInfo | None) -> bool:
    """The SEMI job equals the empty cluster constant (its five strings are empty)."""
    j = _job(job)
    return not any((j.control_job_id, j.process_job_id, j.lot_id, j.wafer_id, j.zone_id))


def _or_zero(text: str) -> str:
    """"Empty Data": an empty string becomes ``"0"``."""
    return text if text != "" else "0"


def format_timestamp(when: datetime, time_format: str = TIME_FORMAT) -> str:
    """``Format Date/Time String`` of ``when`` (local time), then the first ``,`` and the first ``.`` removed."""
    return when.strftime(time_format).replace(",", "", 1).replace(".", "", 1)


def job_data_filename(job: JobInfo | None, when: datetime, time_format: str = TIME_FORMAT, *,
                      kind: str = "mesa") -> str:
    """The file name (no extension) of ``tools_alox_job_data_filename`` (``kind="mesa"``), ``…_bowing`` or
    ``…_tilt`` (module docstring). ``when`` is the time to format (the mesa VI formats "now": pass the current
    time)."""
    try:
        empty_suffix, job_suffix = JOB_KINDS[kind]
    except KeyError:
        raise ValueError(f"unknown job file kind {kind!r} (expected one of {', '.join(JOB_KINDS)})") from None
    ts = format_timestamp(when, time_format)
    if job_is_empty(job):
        return ts + empty_suffix
    j = _job(job)
    head = f"{_or_zero(j.process_job_id)}_{_or_zero(j.lot_id)}_{_or_zero(j.wafer_id)}"
    if kind == "mesa":
        if j.zone_id != "0":
            zone = "Zone" + j.zone_id if j.zone_id != "" else "0"
            return f"{head}_{ts}_{zone}_mapping"
        return f"{head}_{ts}_oxidation"
    return f"{head}_{ts}{job_suffix}"


def filter_job_name(text: str) -> str:
    """``tools_alox_job_filname_filter``: spaces replaced by ``"_"`` (module docstring)."""
    return str(text).replace(" ", "_")


def job_history_root(folders: Any, job: JobInfo | None) -> Path:
    r"""The root of the job tree: ``[folders] data`` without a job (Process Job ID ``""`` or ``"Pj"``), else
    ``<jobhistory>\<PJ>_<Lot>\<PJ>_<Lot>_<Wafer>\Vision`` (filtered names). ``folders`` is a
    :class:`alox.core.folders.Folders` (``data``, ``jobhistory``)."""
    j = _job(job)
    if j.process_job_id in NO_JOB_IDS:
        return Path(folders.data)
    pj, lot, wafer = (filter_job_name(s) for s in (j.process_job_id, j.lot_id, j.wafer_id))
    return Path(folders.jobhistory) / f"{pj}_{lot}" / f"{pj}_{lot}_{wafer}" / "Vision"


@dataclass(frozen=True)
class JobFolders:
    r"""The folders of one job: ``root``, ``images`` (``<root>\images\<x>``, None when not asked), ``files``
    (``<root>\files``) and the requested ``files`` sub-folders by name."""

    root: Path
    images: Path | None
    files: Path
    subfolders: Mapping[str, Path] = field(default_factory=dict)

    def file_folder(self, name: str) -> Path:
        return self.subfolders[name]


def job_folders(folders: Any, job: JobInfo | None, *, images: str | None = None, files: Sequence[str] = (),
                create: bool = True) -> JobFolders:
    r"""``tools_alox_shared_jobhistory_folder_<x>_path``: the job tree with ``images\<images>`` and ``files\<f>`` for
    each name of ``files`` (bowing: ``images="bowing", files=("bowing",)``). ``create`` makes the folders that are
    returned (:class:`OSError` when one cannot be created)."""
    root = job_history_root(folders, job)
    image_folder = root / "images" / images if images else None
    files_folder = root / "files"
    subfolders = {name: files_folder / name for name in files}
    if create:
        for folder in (image_folder, files_folder, *subfolders.values()):
            if folder is not None:
                folder.mkdir(parents=True, exist_ok=True)
    return JobFolders(root, image_folder, files_folder, MappingProxyType(subfolders))


def job_wafer_folder(base: Path | str, job: JobInfo | None) -> Path:
    r"""``tools_save_job_wafer_``: ``<base>\<PJ>\<Wafer>``, or ``<base>\NoJob`` when the Process Job ID is empty."""
    j = _job(job)
    if j.process_job_id == "":
        return Path(base) / "NoJob"
    return Path(base) / j.process_job_id / j.wafer_id


# ================================================================================================== data files
def format_lv_number(value: float, fmt: str = "%.4f") -> str:
    """``value`` with a C format as LabVIEW writes it: ``NaN``, ``Inf``, ``-Inf`` for the special values."""
    number = float(value)
    if math.isnan(number):
        return "NaN"
    if math.isinf(number):
        return "Inf" if number > 0 else "-Inf"
    return fmt % number


def spreadsheet_text(rows: Iterable[Sequence[float]], fmt: str = "%.4f", *, delimiter: str = "\t",
                     eol: str = "\r\n") -> str:
    """The text ``Write To Spreadsheet File`` writes for a 2-D array: rows padded with 0 to the longest one."""
    table = [list(row) for row in rows]
    width = max((len(row) for row in table), default=0)
    lines = [delimiter.join(format_lv_number(v, fmt) for v in row + [0.0] * (width - len(row))) for row in table]
    return "".join(line + eol for line in lines)


def write_spreadsheet(path: Path | str, rows: Iterable[Sequence[float]], fmt: str = "%.4f") -> Path:
    """Write ``rows`` to ``path`` as :func:`spreadsheet_text` (replaced when it exists). Returns the path."""
    target = Path(path)
    target.write_bytes(spreadsheet_text(rows, fmt).encode("ascii"))
    return target
