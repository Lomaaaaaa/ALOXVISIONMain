"""The application log (``alox_manager_log``; map Part 1 §4.5).

LabVIEW FGV methods (``alox_typedef_log_manager_method``): Default, Init, Exit, Set Log Window, Set Alarm Window, Clear
Log, Clear Alarm, Log Info, Log Warning, Log Error, Clear All, Save Log, Log Error In. Observed in
``alox_manager_log d0..d69``:

- A line is ``Format Date/Time String("%d/%m/%Y - %H:%M:%S%3u - ") + <level> + " -- " + <text> + End of Line``
  (d12): ``<level>`` is the localized string 000011 "Info", 000012 "Warning" or 000013 "Error" of ``alox_<lang>.lang``;
  End of Line is CRLF. ``%3u`` writes the fractional seconds **with the decimal separator of the Windows locale**
  (there is no character between ``%S`` and ``%3u``, and the LabVIEW INI does not turn the localized decimal point
  off): ``","`` on the French machines whose ``alox_default.ini`` has decimal commas, ``"."`` on an English one. It is
  a parameter here (default ``","``, **to confirm**).
- An empty text is not logged (``String ≠ ""`` case).
- The log window shows the title (string 000009 "Log") then the lines, **newest first** ("Insert new line at the
  begining (after title)"); the alarm window shows the last line only (Clear Alarm empties it).
- Lines are counted; when the counter reaches ``Max Lines`` (control default 65535; ``log.maxlines`` = 1000 in
  ``alox_default.ini``) LabVIEW **empties** the window and restarts (d13 "Reset Log, if maximum # lines reached"). Here
  the window keeps the last ``maxlines`` lines instead (sliding window): nothing visible disappears at once.
- Every line is also appended to the daily file ``<vision folder>\\logs\\<%y%m%d>_vision_events.log`` (d19 "Add line
  into daily log file"): pass ``daily_dir`` (the ``logs`` folder) to get it.
- "Save Log" opens or creates a file, goes to its end and writes the text of the log window (title included, newest
  line first) (d68): :meth:`AppLog.save`.

:meth:`AppLog.handler` gives a :class:`logging.Handler` so Python logging can feed the window (DEBUG/INFO → Info,
WARNING → Warning, ERROR/CRITICAL → Error).
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from pathlib import Path

from alox.core.translations import Translations

__all__ = ["AppLog", "AppLogHandler", "LogLevel", "LogLine", "daily_file_name", "format_line"]

log = logging.getLogger(__name__)

#: LabVIEW's End of Line constant on Windows.
EOL = "\r\n"
#: ``alox_<lang>.lang`` ID of the window title ("Log").
TITLE_ID = 9


class LogLevel(IntEnum):
    """The three log methods; the value is the ``alox_<lang>.lang`` ID of the level word."""

    INFO = 11
    WARNING = 12
    ERROR = 13

    @property
    def word(self) -> str:
        """The English word (``alox_en.lang``): ``Info``, ``Warning``, ``Error``."""
        return self.name.capitalize()

    @classmethod
    def from_logging(cls, levelno: int) -> LogLevel:
        if levelno >= logging.ERROR:
            return cls.ERROR
        if levelno >= logging.WARNING:
            return cls.WARNING
        return cls.INFO


@dataclass(frozen=True)
class LogLine:
    """One logged line: when, level, the caller's text and the formatted line (without End of Line)."""

    time: datetime
    level: LogLevel
    text: str
    line: str


def format_line(when: datetime, level_word: str, text: str, *, decimal_separator: str = ",") -> str:
    """``dd/mm/YYYY - HH:MM:SS<sep>mmm - <level> -- <text>`` (milliseconds truncated)."""
    stamp = f"{when:%d/%m/%Y - %H:%M:%S}{decimal_separator}{when.microsecond // 1000:03d} - "
    return f"{stamp}{level_word} -- {text}"


def daily_file_name(when: datetime) -> str:
    """``<%y%m%d>_vision_events.log``, e.g. ``261125_vision_events.log``."""
    return f"{when:%y%m%d}_vision_events.log"


class AppLog:
    """The log window, the alarm line and the daily file. Thread-safe.

    - ``maxlines``: size of the window (the boot passes ``log.maxlines``); change it with :attr:`maxlines`.
    - ``translations``: localizes the level words and the title (English without it).
    - ``daily_dir``: folder of the daily files (``<vision folder>/logs``); None = no daily file.
    - ``decimal_separator``: separator of the milliseconds (module docstring).
    - ``encoding``: of the files written (LabVIEW writes Windows ANSI, cp1252; unknown characters become ``?``).
    - ``clock``: returns the local time of a new line (tests pass a fixed clock).
    """

    def __init__(self, maxlines: int = 1000, *, translations: Translations | None = None,
                 daily_dir: str | os.PathLike[str] | None = None, decimal_separator: str = ",",
                 encoding: str = "cp1252", clock: Callable[[], datetime] = datetime.now) -> None:
        if maxlines < 1:
            raise ValueError("maxlines must be at least 1")
        self._lock = threading.RLock()
        self._window: deque[LogLine] = deque(maxlen=maxlines)
        self._alarm: LogLine | None = None
        self._translations = translations
        self._daily_dir = Path(daily_dir) if daily_dir is not None else None
        self._separator = decimal_separator
        self._encoding = encoding
        self._clock = clock
        self._file_failing = False

    # ------------------------------------------------------------------------------------------------ settings

    @property
    def maxlines(self) -> int:
        return self._window.maxlen or 0

    @maxlines.setter
    def maxlines(self, value: int) -> None:
        if value < 1:
            raise ValueError("maxlines must be at least 1")
        with self._lock:
            self._window = deque(self._window, maxlen=value)

    @property
    def daily_dir(self) -> Path | None:
        return self._daily_dir

    def _word(self, ident: int, default: str) -> str:
        if self._translations is None:
            return default
        found = self._translations.lookup(ident, "alox")
        return found if found is not None else default

    @property
    def title(self) -> str:
        """The window title (string 000009, "Log" in English)."""
        return self._word(TITLE_ID, "Log")

    # ------------------------------------------------------------------------------------------------ logging

    def log(self, level: LogLevel, text: str) -> LogLine | None:
        """Add a line; None (nothing logged) when ``text`` is empty."""
        if not text:
            return None
        level = LogLevel(level)
        with self._lock:
            when = self._clock()
            entry = LogLine(when, level, text, format_line(when, self._word(level.value, level.word), text,
                                                           decimal_separator=self._separator))
            self._window.append(entry)
            self._alarm = entry
            if self._daily_dir is not None:
                self._append_daily(entry)
        return entry

    def info(self, text: str) -> LogLine | None:
        return self.log(LogLevel.INFO, text)

    def warning(self, text: str) -> LogLine | None:
        return self.log(LogLevel.WARNING, text)

    def error(self, text: str) -> LogLine | None:
        return self.log(LogLevel.ERROR, text)

    def _append_daily(self, entry: LogLine) -> None:
        assert self._daily_dir is not None
        path = self._daily_dir / daily_file_name(entry.time)
        try:
            self._daily_dir.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding=self._encoding, errors="replace", newline="") as file:
                file.write(entry.line + EOL)
        except OSError as exc:
            if not self._file_failing:                      # report once per failure streak (no log storm)
                log.warning("cannot write the daily log file %s: %s", path, exc)
            self._file_failing = True
        else:
            self._file_failing = False

    # ------------------------------------------------------------------------------------------------ reading

    def lines(self) -> list[LogLine]:
        """The window, oldest line first."""
        with self._lock:
            return list(self._window)

    def __len__(self) -> int:
        with self._lock:
            return len(self._window)

    @property
    def alarm(self) -> LogLine | None:
        """The last line, until :meth:`clear_alarm` (the alarm window)."""
        with self._lock:
            return self._alarm

    def window_text(self) -> str:
        """The log window as LabVIEW shows it: the title, then the lines newest first, each ended by CRLF."""
        with self._lock:
            lines = [self.title] + [entry.line for entry in reversed(self._window)]
        return "".join(line + EOL for line in lines)

    # ------------------------------------------------------------------------------------------------ clearing, saving

    def clear(self) -> None:
        """Empty the window ("Clear Log")."""
        with self._lock:
            self._window.clear()

    def clear_alarm(self) -> None:
        """Empty the alarm line ("Clear Alarm")."""
        with self._lock:
            self._alarm = None

    def clear_all(self) -> None:
        """"Clear All": the window and the alarm line."""
        with self._lock:
            self._window.clear()
            self._alarm = None

    def save(self, path: str | os.PathLike[str]) -> Path:
        """Append :meth:`window_text` to ``path`` (created if needed) and return the path ("Save Log")."""
        target = Path(path)
        text = self.window_text()
        with open(target, "a", encoding=self._encoding, errors="replace", newline="") as file:
            file.write(text)
        return target

    def handler(self, level: int = logging.INFO) -> AppLogHandler:
        """A :class:`logging.Handler` writing into this log."""
        return AppLogHandler(self, level)


class AppLogHandler(logging.Handler):
    """Feeds an :class:`AppLog` from Python logging. The record is formatted with the handler's formatter (default:
    the message, plus the traceback when there is one). A record emitted while the same thread is already inside this
    handler is dropped, so a warning raised by the log itself cannot loop."""

    def __init__(self, target: AppLog, level: int = logging.INFO) -> None:
        super().__init__(level)
        self.target = target
        self._busy = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(self._busy, "active", False):
            return
        self._busy.active = True
        try:
            self.target.log(LogLevel.from_logging(record.levelno), self.format(record))
        except Exception:                                  # noqa: BLE001 - logging.Handler contract: never raise
            self.handleError(record)
        finally:
            self._busy.active = False
