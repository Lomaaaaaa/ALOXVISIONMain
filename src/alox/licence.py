"""Licence of the application: a signed licence file bound to the PC (owner's decisions L1, L2).

LabVIEW refused to boot without a WireFlow USB dongle or a "virtual dongle" file built from the NI Vision run-time
serial and the NI License Manager computer ID (map Part 6 §2.2); those identifiers disappear with NI. The owner chose:
no check by default, and a new lock of our own design, with its own generator. This module is that lock:

- a **licence file** (``<exe>/licence.alox``, UTF-8 JSON) holds the licence fields (product, customer, the hash of the
  machine identity, issue date, optional expiry date, free text) and an **Ed25519 signature** of those fields;
- the application holds only the **public key** (:data:`PUBLIC_KEY`, set before building the installer, or a
  ``<exe>/licence_public_key.txt`` file when the constant is empty); the private key stays with Aloxtec, in the
  generator (``tools/licence_generator.py``): a licence cannot be forged or moved to another PC without it;
- the **machine identity** (:func:`machine_id`) is a SHA-256 of the Windows ``MachineGuid`` and the serial number of
  the system volume (``/etc/machine-id`` elsewhere): the raw identifiers never appear in the file.

The check runs at boot only when ``alox.ini [general] licence_BOOL`` is TRUE (default FALSE, owner L1). Results map to
LabVIEW's system codes: 1513 "no dongle" (no licence file), 1514 "invalid", 1515 "expired" (``system_en.lang``).
Needs the ``cryptography`` package (optional extra ``licence``); without it an enabled check fails as "invalid".
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import json
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["LICENCE_FILE", "PRODUCT", "PUBLIC_KEY", "LicenceError", "LicenceStatus", "check_licence", "machine_id",
           "sign_licence", "verify_licence"]

log = logging.getLogger(__name__)

PRODUCT = "ALOX Vision"
LICENCE_FILE = "licence.alox"
PUBLIC_KEY_FILE = "licence_public_key.txt"
#: The Ed25519 public key of Aloxtec's licence generator (base64 of the 32 raw bytes). Empty in the sources: set it
#: when building the installer (``tools/licence_generator.py keygen`` prints it).
PUBLIC_KEY = ""
#: System codes (``system_en.lang``): no licence file, invalid licence, expired licence.
NO_LICENCE, INVALID_LICENCE, EXPIRED_LICENCE = 1513, 1514, 1515
_FIELDS = ("product", "customer", "machine", "issued", "expires", "extra")


class LicenceError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class LicenceStatus:
    valid: bool
    code: int                       # 0 when valid, else 1513 / 1514 / 1515
    reason: str
    customer: str = ""
    expires: dt.date | None = None
    remaining_days: int | None = None


# ================================================================================================== machine identity
def _windows_identity() -> list[str]:
    parts: list[str] = []
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                            0, winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as key:
            parts.append(str(winreg.QueryValueEx(key, "MachineGuid")[0]))
    except OSError as exc:
        log.warning("licence: MachineGuid not readable: %s", exc)
    try:
        import ctypes

        serial = ctypes.c_uint32(0)
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        if ctypes.windll.kernel32.GetVolumeInformationW(drive, None, 0, ctypes.byref(serial), None, None, None, 0):
            parts.append(f"{serial.value:08X}")
    except (OSError, AttributeError) as exc:
        log.warning("licence: volume serial not readable: %s", exc)
    return parts


def machine_id(*, platform: str = sys.platform, parts: list[str] | None = None) -> str:
    """The hash (hex SHA-256) identifying this PC. ``parts`` replaces the identifiers read from the system (tests)."""
    if parts is None:
        if platform == "win32":
            parts = _windows_identity()
        else:
            parts = []
            for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                try:
                    parts.append(Path(path).read_text(encoding="ascii").strip())
                    break
                except OSError:
                    continue
    if not parts:
        raise LicenceError(INVALID_LICENCE, "this PC's identity cannot be read")
    return hashlib.sha256(("ALOX|" + "|".join(parts)).encode("utf-8")).hexdigest()


# ================================================================================================== signing
def _payload(fields: Mapping[str, Any]) -> bytes:
    return json.dumps({k: fields.get(k) for k in _FIELDS}, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sign_licence(private_key_pem: bytes, *, customer: str, machine: str, expires: dt.date | None = None,
                 extra: str = "", issued: dt.date | None = None) -> str:
    """The text of a licence file (generator side; needs the private key)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("not an Ed25519 private key")
    fields = {"product": PRODUCT, "customer": customer, "machine": machine,
              "issued": (issued or _today()).isoformat(), "expires": expires.isoformat() if expires else None,
              "extra": extra}
    signature = base64.b64encode(key.sign(_payload(fields))).decode("ascii")
    return json.dumps({"licence": fields, "signature": signature}, indent=2, ensure_ascii=False) + "\n"


def verify_licence(text: str, public_key_b64: str, *, machine: str, today: dt.date | None = None) -> LicenceStatus:
    """Check a licence file's text: signature, product, machine, expiry."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        return LicenceStatus(False, INVALID_LICENCE, "the cryptography package is not installed")
    try:
        document = json.loads(text)
        fields = document["licence"]
        signature = base64.b64decode(document["signature"], validate=True)
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        return LicenceStatus(False, INVALID_LICENCE, f"unreadable licence or public key: {exc}")
    try:
        key.verify(signature, _payload(fields))
    except InvalidSignature:
        return LicenceStatus(False, INVALID_LICENCE, "the licence signature is not valid")
    customer = str(fields.get("customer") or "")
    if fields.get("product") != PRODUCT:
        return LicenceStatus(False, INVALID_LICENCE, f"licence for another product: {fields.get('product')!r}", customer)
    if fields.get("machine") != machine:
        return LicenceStatus(False, INVALID_LICENCE, "the licence belongs to another PC", customer)
    expires = dt.date.fromisoformat(fields["expires"]) if fields.get("expires") else None
    today = today or _today()
    if expires is not None:
        remaining = (expires - today).days
        if remaining < 0:
            return LicenceStatus(False, EXPIRED_LICENCE, f"the licence expired on {expires}", customer, expires, 0)
        return LicenceStatus(True, 0, "valid", customer, expires, remaining)
    return LicenceStatus(True, 0, "valid", customer)


def check_licence(folder: Path, *, public_key: str | None = None, machine: str | None = None,
                  today: dt.date | None = None) -> LicenceStatus:
    """The boot check: ``<folder>/licence.alox`` against :data:`PUBLIC_KEY` (or ``<folder>/licence_public_key.txt``)
    and this PC's identity."""
    key = public_key if public_key is not None else (PUBLIC_KEY or _read(folder / PUBLIC_KEY_FILE))
    if not key:
        return LicenceStatus(False, INVALID_LICENCE, "no licence public key in this build")
    path = folder / LICENCE_FILE
    if not path.is_file():
        return LicenceStatus(False, NO_LICENCE, f"no licence file {path}")
    try:
        identity = machine or machine_id()
    except LicenceError as exc:
        return LicenceStatus(False, exc.code, str(exc))
    return verify_licence(path.read_text(encoding="utf-8"), key, machine=identity, today=today)


def _today() -> dt.date:
    """The PC's local date (an expiry date is a calendar day where the licence is used)."""
    return dt.datetime.now().astimezone().date()


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        return ""
