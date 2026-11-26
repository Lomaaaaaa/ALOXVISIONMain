"""Error type and codes shared by the thermal probes (Optris pyrometer, kSA BandiT over Modbus TCP).

Codes (``ProbeError.code``, then :class:`alox.devices.base.DeviceError` ``code``): the transport codes reuse the
values of the stages and of the monochromator (``alox.devices.cm11x``): −6 open/connect failed, −2 send failed, −3
receive failed, −4 not open, −8 answer out of step; −7 no (complete) answer within the timeout (the PI receive-timeout
value); 1 an argument refused before anything is sent; ``100 + n`` Modbus exception ``n`` returned by the device.
All but 1 and the Modbus exceptions mean "the link is unusable" (:data:`LINK_FAILURE_CODES`): the device enters
``fault`` and re-opens the link at the next call.
"""

from __future__ import annotations

__all__ = [
    "BAD_ANSWER",
    "INVALID_INPUT",
    "LINK_FAILURE_CODES",
    "MODBUS_EXCEPTION_BASE",
    "NOT_OPEN",
    "OPEN_FAILED",
    "RECEIVE_FAILED",
    "SEND_FAILED",
    "TIMEOUT",
    "ProbeError",
]

OPEN_FAILED = -6
SEND_FAILED = -2
RECEIVE_FAILED = -3
NOT_OPEN = -4
TIMEOUT = -7
BAD_ANSWER = -8
INVALID_INPUT = 1
MODBUS_EXCEPTION_BASE = 100
LINK_FAILURE_CODES = frozenset({OPEN_FAILED, SEND_FAILED, RECEIVE_FAILED, NOT_OPEN, TIMEOUT, BAD_ANSWER})


class ProbeError(Exception):
    """A probe operation failed; ``code`` as in the module docstring, ``received`` the bytes read when relevant."""

    def __init__(self, message: str, code: int, *, received: bytes = b"") -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.received = received

    @property
    def link_failure(self) -> bool:
        return self.code in LINK_FAILURE_CODES
