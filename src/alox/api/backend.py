"""What the API needs from the application (``docs/vision-core/map.md`` Part 5, Appendix A).

The REST routes and the websocket server only talk to an :class:`ApiBackend`; the core implements it (and tests use a
fake). Keeping this boundary small makes the API exact and testable without hardware.

Reply codes are those of LabVIEW's language file (``alox_en.lang``): the PUT routes answer the localized text of the
code, as plain text.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Protocol

from alox.core.commands import Method
from alox.core.functions import JobInfo

__all__ = ["ApiBackend", "Measure", "ReplyCode", "VersionInfo"]


class ReplyCode(IntEnum):
    """``alox_en.lang`` codes used by the API replies."""

    NO_ERROR = 9000
    UNKNOWN_ERROR = 9001
    INVALID_ARGUMENTS = 9002
    NOT_READY = 9005                      # "function not ready to start, or already running" (HTTP 503)
    INVALID_PROPERTY = 9006
    LOAD_PROFILE_ERROR = 9007
    GET_IMAGE_NOT_READY = 9013            # "get image error (not ready condition)" (HTTP 503)


class Measure(IntEnum):
    """The three supervisor measurements."""

    MESA = 2
    TILT = 3
    BOWING = 4


@dataclass(frozen=True)
class VersionInfo:
    major: int
    minor: int
    build: int
    api: int
    release: str                          # "<date> - <time>"


class ApiBackend(Protocol):
    """The application as the API sees it. Every method may raise; the route then answers 500."""

    # ---------------------------------------------------------------- system
    def system_status(self) -> tuple[int, str]:
        """``(code, localized description)`` of the system status."""

    def measure_status(self, measure: Measure) -> tuple[int, str]:
        """Status ID of the mesa, tilt or bowing measurement and its description."""

    def heartbeat(self) -> int:
        """The core heartbeat (seconds, as LabVIEW's global "Core Heartbeat")."""

    def get_property(self, dotted: str, default: Any = None) -> Any:
        """An application setting (``alox.ini``), e.g. ``general.mode``."""

    def set_property(self, dotted: str, value: Any) -> None:
        """Change and save an application setting."""

    def version(self) -> VersionInfo:
        ...

    def text(self, code: int) -> str:
        """The localized text of a language-file code (``009000`` → ``"no error"``)."""

    def hostname(self) -> str:
        ...

    # ---------------------------------------------------------------- profiles and folders
    def profiles(self) -> list[str]:
        """Names of the ``*.zip`` recipes of the profiles folder, without extension."""

    def current_profile(self) -> tuple[str, str]:
        """``(path, filename)`` of the active recipe."""

    def profiles_folder(self) -> str:
        ...

    def set_profiles_folder(self, path: str) -> None:
        """Write ``alox_setup.ini [folders] profiles`` (the folder must exist)."""

    def images_folder(self) -> str:
        ...

    # ---------------------------------------------------------------- commands and measurements
    def post(self, method: Method, payload: Any = None) -> None:
        """Queue a core command (returns at once)."""

    def can_start(self, measure: Measure | None) -> bool:
        """LabVIEW "Get Condition OK" (``None``: the Get Image condition)."""

    def not_ready(self, measure: Measure) -> None:
        """A measurement request was refused because a function runs (LabVIEW sets the status ID to 10)."""

    def measure_request(self, measure: Measure, job: JobInfo, overrides: Mapping[str, Any]) -> Any:
        """Build the queue payload of a measurement: the active recipe's parameters with the request's
        ``overrides`` (keys as in Appendix A: ``x``, ``y``, ``target``, ``tilt``, ``singleshot``, ...)."""

    # ---------------------------------------------------------------- tilt, cameras, overlays, images
    def tilt(self) -> float:
        ...

    def reset_tilt(self) -> None:
        ...

    def camera_status(self, camera: str) -> Mapping[str, str]:
        """``camera`` is ``"highmag"`` or ``"lowmag"``: Type, Status, ImageTotal, Timestamp, PixelSize, PixelUnits,
        ExposureTime (strings)."""

    def overlays(self) -> list[str]:
        ...

    def overlay(self, name: str) -> bool:
        ...

    def set_overlay(self, name: str, on: bool) -> None:
        ...

    def jpeg(self, quality: int, width: int, height: int) -> bytes:
        """The current image with its overlays, resampled to ``width`` × ``height``, as JPEG."""

    def stream_start(self, quality: int, width: int, height: int) -> None:
        ...

    def stream_stop(self) -> None:
        ...
