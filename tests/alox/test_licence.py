"""The signed licence file (``alox.licence``, owner L1/L2) and its generator."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

import licence_generator

from alox.core.core import Core, CoreConfig
from alox.core.folders import Folders
from alox.licence import (
    LicenceError,
    check_licence,
    machine_id,
    sign_licence,
    verify_licence,
)

MACHINE = machine_id(parts=["machine-guid", "0A1B2C3D"])
TODAY = dt.date(2026, 11, 26)


@pytest.fixture
def keys(tmp_path: Path) -> tuple[bytes, str]:
    assert licence_generator.main(["keygen", "--out", str(tmp_path / "keys")]) == 0
    return ((tmp_path / "keys" / "licence_private_key.pem").read_bytes(),
            (tmp_path / "keys" / "licence_public_key.txt").read_text(encoding="ascii").strip())


def test_machine_id_is_a_hash_of_the_identifiers():
    assert len(MACHINE) == 64 and MACHINE != machine_id(parts=["machine-guid", "FFFFFFFF"])
    assert "machine-guid" not in MACHINE


def test_a_signed_licence_is_valid_on_its_machine_only(keys):
    private, public = keys
    text = sign_licence(private, customer="Fab 1", machine=MACHINE, expires=dt.date(2027, 1, 31), issued=TODAY)
    ok = verify_licence(text, public, machine=MACHINE, today=TODAY)
    assert ok.valid and ok.customer == "Fab 1" and ok.remaining_days == 66
    assert verify_licence(text, public, machine="other", today=TODAY).code == 1514
    assert verify_licence(text, public, machine=MACHINE, today=dt.date(2027, 2, 1)).code == 1515
    forged = text.replace("Fab 1", "Fab 2")
    assert verify_licence(forged, public, machine=MACHINE, today=TODAY).reason == "the licence signature is not valid"


def test_keygen_never_overwrites_the_private_key(keys, tmp_path: Path):
    assert licence_generator.main(["keygen", "--out", str(tmp_path / "keys")]) == 1


def test_boot_check_is_off_by_default_and_blocks_when_enabled(tmp_path: Path, keys):
    private, public = keys
    root = tmp_path / "alox"
    exe = tmp_path / "exe"
    exe.mkdir()
    folders = Folders(exe=exe, settings=root / "settings", vision=root / "vision", data=root / "data",
                      profiles=root / "profiles", exposures=root / "exposures", jobhistory=root / "jobhistory")
    core = Core(folders, config=CoreConfig(supervise_hardware=False, heartbeat=False))
    core.boot()                                                   # licence_BOOL absent: no check (owner L1)
    folders.alox_ini.write_text("[general]\nlicence_BOOL = TRUE\n", encoding="cp1252")
    with pytest.raises(LicenceError) as info:
        Core(folders, config=CoreConfig(supervise_hardware=False, heartbeat=False)).boot()
    assert info.value.code == 1514                                # no public key in this build
    (exe / "licence_public_key.txt").write_text(public, encoding="ascii")
    status = check_licence(exe, machine=MACHINE, today=TODAY)
    assert status.code == 1513                                    # no licence file yet
    (exe / "licence.alox").write_text(sign_licence(private, customer="Fab 1", machine=MACHINE), encoding="utf-8")
    assert check_licence(exe, machine=MACHINE, today=TODAY).valid
