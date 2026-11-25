"""The application log (``alox.core.log``): line format, window, alarm, daily file, save and the logging adapter."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from alox.core.log import AppLog, LogLevel, daily_file_name, format_line
from alox.core.translations import Translations

T0 = datetime(2026, 11, 25, 8, 5, 3, 45678)


class Clock:
    def __init__(self, start: datetime = T0, step: timedelta = timedelta(milliseconds=1)) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> datetime:
        value = self.now
        self.now += self.step
        return value


# ============================================================================================== format


def test_line_format():
    assert format_line(T0, "Info", "hello") == "25/11/2026 - 08:05:03,045 - Info -- hello"
    assert format_line(T0, "Error", "x", decimal_separator=".") == "25/11/2026 - 08:05:03.045 - Error -- x"
    assert format_line(datetime(2026, 1, 2, 23, 59, 59, 999999), "Warning", "") == (
        "02/01/2026 - 23:59:59,999 - Warning -- ")                  # truncated, never ",1000"
    assert daily_file_name(T0) == "261125_vision_events.log"


def test_levels():
    assert [(lv.value, lv.word) for lv in LogLevel] == [(11, "Info"), (12, "Warning"), (13, "Error")]
    assert LogLevel.from_logging(logging.DEBUG) is LogLevel.INFO
    assert LogLevel.from_logging(logging.WARNING) is LogLevel.WARNING
    assert LogLevel.from_logging(logging.CRITICAL) is LogLevel.ERROR


# ============================================================================================== window and alarm


def test_window_alarm_and_clearing():
    log = AppLog(clock=Clock())
    first = log.info("boot")
    assert first is not None and first.line == "25/11/2026 - 08:05:03,045 - Info -- boot"
    assert log.info("") is None                                     # empty text: not logged
    log.warning("careful")
    log.error("broken")
    assert [e.level for e in log.lines()] == [LogLevel.INFO, LogLevel.WARNING, LogLevel.ERROR]
    assert log.alarm is not None and log.alarm.text == "broken"
    assert log.window_text() == (
        "Log\r\n"
        "25/11/2026 - 08:05:03,047 - Error -- broken\r\n"
        "25/11/2026 - 08:05:03,046 - Warning -- careful\r\n"
        "25/11/2026 - 08:05:03,045 - Info -- boot\r\n")
    log.clear_alarm()
    assert log.alarm is None and len(log) == 3
    log.clear()
    assert len(log) == 0 and log.window_text() == "Log\r\n"
    log.info("again")
    log.clear_all()
    assert len(log) == 0 and log.alarm is None


def test_window_keeps_the_last_maxlines():
    log = AppLog(3, clock=Clock())
    for i in range(5):
        log.info(f"line {i}")
    assert [e.text for e in log.lines()] == ["line 2", "line 3", "line 4"]
    log.maxlines = 2
    assert log.maxlines == 2 and [e.text for e in log.lines()] == ["line 3", "line 4"]
    with pytest.raises(ValueError):
        AppLog(0)
    with pytest.raises(ValueError):
        log.maxlines = 0


def test_level_words_and_title_are_localized():
    tr = Translations()
    tr.load_text("alox", "000009;Log\n000011;Info\n000012;Warning\n000013;Error\n")
    tr.load_text("alox", "000009;Journal\n000012;Avertissement\n000013;Erreur\n", language="fr")
    tr.set_language("fr")
    log = AppLog(clock=Clock(), translations=tr)
    entry = log.warning("chaud")
    assert entry is not None and entry.line.endswith(" - Avertissement -- chaud")
    assert log.info("x").line.endswith(" - Info -- x")               # type: ignore[union-attr]
    assert log.window_text().startswith("Journal\r\n")


# ============================================================================================== files


def test_daily_file(tmp_path: Path):
    logs = tmp_path / "vision" / "logs"
    clock = Clock()
    log = AppLog(clock=clock, daily_dir=logs)
    log.info("première")
    log.error("second")
    clock.now = datetime(2026, 11, 26, 0, 0, 1)
    log.info("next day")
    first = (logs / "261125_vision_events.log").read_bytes()
    assert first == ("25/11/2026 - 08:05:03,045 - Info -- première\r\n"
                     "25/11/2026 - 08:05:03,046 - Error -- second\r\n").encode("cp1252")
    assert (logs / "261126_vision_events.log").read_bytes().endswith(b"Info -- next day\r\n")


def test_daily_file_failure_is_reported_once(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    log = AppLog(clock=Clock(), daily_dir=blocker)
    with caplog.at_level(logging.WARNING, logger="alox.core.log"):
        log.info("one")
        log.info("two")
    assert len(log) == 2
    assert len([r for r in caplog.records if "daily log file" in r.getMessage()]) == 1


def test_save_appends_the_window(tmp_path: Path):
    log = AppLog(clock=Clock())
    log.info("a")
    target = tmp_path / "saved.log"
    assert log.save(target) == target
    log.info("b")
    log.save(target)
    assert target.read_bytes() == (
        b"Log\r\n25/11/2026 - 08:05:03,045 - Info -- a\r\n"
        b"Log\r\n25/11/2026 - 08:05:03,046 - Info -- b\r\n25/11/2026 - 08:05:03,045 - Info -- a\r\n")


# ============================================================================================== logging adapter


def test_logging_handler():
    log = AppLog(clock=Clock())
    logger = logging.getLogger("test.core.log.handler")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    handler = log.handler()
    logger.addHandler(handler)
    try:
        logger.debug("hidden")                                       # below the handler level (INFO)
        logger.info("value %d", 3)
        logger.warning("warm")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logger.exception("failed")
    finally:
        logger.removeHandler(handler)
    entries = log.lines()
    assert [(e.level, e.text.splitlines()[0]) for e in entries] == [
        (LogLevel.INFO, "value 3"), (LogLevel.WARNING, "warm"), (LogLevel.ERROR, "failed")]
    assert "RuntimeError: boom" in entries[-1].text


def test_handler_does_not_loop_when_the_log_logs():
    logger = logging.getLogger("test.core.log.loop")
    logger.propagate = False

    class Noisy(AppLog):
        def log(self, level: LogLevel, text: str):                   # type: ignore[override]
            logger.warning("inner")                                  # re-enters the handler: dropped
            return super().log(level, text)

    log = Noisy(clock=Clock())
    handler = log.handler()
    logger.addHandler(handler)
    try:
        logger.warning("outer")
    finally:
        logger.removeHandler(handler)
    assert [e.text for e in log.lines()] == ["outer"]
