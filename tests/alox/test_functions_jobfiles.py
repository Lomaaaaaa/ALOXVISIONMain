"""Job data file names, job-history folders and spreadsheet files (``alox.functions.jobfiles``)."""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

import pytest

from alox.core.folders import Folders
from alox.core.functions import JobInfo
from alox.functions import jobfiles

WHEN = datetime(2026, 11, 26, 14, 32, 10)
JOB = JobInfo(control_job_id="CJ", process_job_id="PJ1", lot_id="LOT 7", wafer_id="W03", zone_id="0")


def folders(tmp_path: Path) -> Folders:
    return Folders(exe=tmp_path / "exe", settings=tmp_path / "settings", vision=tmp_path / "vision",
                   data=tmp_path / "data", profiles=tmp_path / "profiles", exposures=tmp_path / "exposures",
                   jobhistory=tmp_path / "jobhistory")


def test_timestamp_removes_the_first_comma_and_dot() -> None:
    assert jobfiles.format_timestamp(WHEN) == "261126_143210"
    assert jobfiles.format_timestamp(WHEN, "%y.%m.%d,%H.%M") == "2611.2614.32"
    assert jobfiles.format_timestamp(WHEN, "%y.%m.%d,%H.%M,%S") == "2611.2614.32,10"


def test_job_data_file_names_of_the_three_kinds() -> None:
    assert jobfiles.job_data_filename(None, WHEN, kind="bowing") == "261126_143210_bow"
    assert jobfiles.job_data_filename(JobInfo(), WHEN, kind="tilt") == "261126_143210_tilt"
    assert jobfiles.job_data_filename(JobInfo(), WHEN) == "261126_143210_mesa"
    assert jobfiles.job_data_filename(JOB, WHEN, kind="bowing") == "PJ1_LOT 7_W03_261126_143210"
    assert jobfiles.job_data_filename(JOB, WHEN, kind="tilt") == "PJ1_LOT 7_W03_261126_143210_tilt"
    assert jobfiles.job_data_filename(JOB, WHEN, kind="mesa") == "PJ1_LOT 7_W03_261126_143210_oxidation"
    zone = JobInfo(process_job_id="PJ1", lot_id="", wafer_id="W03", zone_id="4")
    assert jobfiles.job_data_filename(zone, WHEN) == "PJ1_0_W03_261126_143210_Zone4_mapping"
    no_zone = JobInfo(control_job_id="CJ")                     # not empty: every name "0", zone "" → "0"
    assert jobfiles.job_data_filename(no_zone, WHEN) == "0_0_0_261126_143210_0_mapping"
    fmt = f"{jobfiles.TIME_FORMAT}_bowing_zwafer2"
    assert jobfiles.job_data_filename(None, WHEN, fmt, kind="bowing") == "261126_143210_bowing_zwafer2_bow"
    with pytest.raises(ValueError, match="unknown job file kind"):
        jobfiles.job_data_filename(JOB, WHEN, kind="thermal")


def test_job_history_tree(tmp_path: Path) -> None:
    f = folders(tmp_path)
    assert jobfiles.job_history_root(f, None) == f.data
    assert jobfiles.job_history_root(f, JobInfo(process_job_id="Pj", lot_id="L")) == f.data
    root = jobfiles.job_history_root(f, JOB)
    assert root == f.jobhistory / "PJ1_LOT_7" / "PJ1_LOT_7_W03" / "Vision"
    tree = jobfiles.job_folders(f, JOB, images="bowing", files=("bowing",))
    assert tree.root == root
    assert tree.images == root / "images" / "bowing" and tree.images.is_dir()
    assert tree.files == root / "files" and tree.files.is_dir()
    assert tree.file_folder("bowing") == root / "files" / "bowing" and tree.file_folder("bowing").is_dir()
    mesa = jobfiles.job_folders(f, None, images="oxid", files=("log", "mesa"), create=False)
    assert mesa.images == f.data / "images" / "oxid" and not mesa.images.exists()
    assert set(mesa.subfolders) == {"log", "mesa"}
    assert jobfiles.job_folders(f, None, files=()).images is None


def test_name_filter_and_job_wafer_folder(tmp_path: Path) -> None:
    assert jobfiles.filter_job_name("a b  c") == "a_b__c"
    assert jobfiles.job_wafer_folder(tmp_path, JOB) == tmp_path / "PJ1" / "W03"
    assert jobfiles.job_wafer_folder(tmp_path, JobInfo(wafer_id="W")) == tmp_path / "NoJob"


def test_spreadsheet_text_pads_rows_and_formats_special_values(tmp_path: Path) -> None:
    rows = [[1.0, 2.5, math.nan], [math.inf, -math.inf, 3.14159, 4.0]]
    text = jobfiles.spreadsheet_text(rows)
    assert text == "1.0000\t2.5000\tNaN\t0.0000\r\nInf\t-Inf\t3.1416\t4.0000\r\n"
    path = jobfiles.write_spreadsheet(tmp_path / "x.bow", rows)
    assert path.read_bytes() == text.encode("ascii")
    assert jobfiles.spreadsheet_text([]) == ""
    assert jobfiles.format_lv_number(2.0, "%f") == "2.000000"
