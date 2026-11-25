"""Language files (``alox.core.translations``), with small synthetic ``.lang`` texts."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from alox.core.translations import (
    DOMAINS,
    Translations,
    code_id,
    decode_lang_bytes,
    parse_lang,
    read_lang_file,
)

ALOX_EN = (
    "  ;;=========================\r\n"
    ";; Test language file (English)\r\n"
    ";;=========================\r\n"
    "\r\n"
    "000009;Log\r\n"
    "000011;Info\r\n"
    "000012;Warning\r\n"
    "000006;Abort                    \r\n"
    "000039;OFF;                 function status 'OFF'\r\n"
    "  008002;set gain to '%f'\r\n"
    "001550;api received='%s'\r\n"
    "001551;%(who)s said %(what)s\r\n"
    "009000;no error\r\n"
    "   \r\n"
)

ALOX_FR = (
    ";; French\r\n"
    "000011;Info\r\n"
    "000012;Avertissement\r\n"
    "000031;Arrêt\r\n"
)


def write(folder: Path, name: str, text: str, encoding: str = "cp1252") -> Path:
    path = folder / name
    path.write_bytes(text.encode(encoding))
    return path


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    write(tmp_path, "alox_en.lang", ALOX_EN)
    write(tmp_path, "alox_fr.lang", ALOX_FR)
    write(tmp_path, "system_en.lang", "000000;System initial state\r\n001001;ERROR (High mag)\r\n")
    write(tmp_path, "mesa_en.lang", "000000;Function initial state\r\n000404;Search Object\r\n")
    write(tmp_path, "tilt_en.lang", "000000;Function initial state\r\n")
    write(tmp_path, "bowing_en.lang", "000000;Function initial state\r\n001001;Reset wavelength error\r\n")
    return tmp_path


# ============================================================================================== parsing


def test_parse_skips_comments_blank_rows_and_keeps_the_second_column():
    table = parse_lang(ALOX_EN)
    assert table[9] == "Log"
    assert table[6] == "Abort"                                   # trailing blanks stripped
    assert table[39] == "OFF"                                    # third column = comment
    assert table[8002] == "set gain to '%f'"                     # leading blanks before the ID tolerated
    assert set(table) == {9, 11, 12, 6, 39, 8002, 1550, 1551, 9000}


def test_parse_rules_for_odd_rows():
    text = "abc;not a number\n000001;\n000002\n;;000003;comment\n000004;a\\nb;c;d\n000004;last wins\r000005;x"
    table = parse_lang(text)
    assert 1 not in table                                        # empty text skipped (LabVIEW too)
    assert 2 not in table and 3 not in table
    assert table[4] == "last wins"                               # repeated ID: last one, any line end
    assert table[5] == "x"
    assert parse_lang("000007;a\\nb;comment")[7] == "a\\nb"      # no escape sequences


def test_decoding_cp1252_and_utf8():
    assert decode_lang_bytes(b"000201;'Bowing\x92 function") == "000201;'Bowing\u2019 function"
    assert decode_lang_bytes("000031;Arrêt".encode("cp1252")) == "000031;Arrêt"
    assert decode_lang_bytes("\ufeff000031;Arrêt".encode()) == "000031;Arrêt"
    assert decode_lang_bytes("000031;Arrêt".encode()) == "000031;Arrêt"


def test_read_lang_file(tmp_path: Path):
    path = write(tmp_path, "x.lang", "000001;Degrés\r\n")
    assert read_lang_file(path) == {1: "Degrés"}


def test_code_id():
    assert code_id(11) == "000011" and code_id("1651") == "001651"


# ============================================================================================== lookups


def test_english_by_default_with_domains_kept_apart(folder: Path):
    tr = Translations(folder)
    assert tr.language == "en" and tr.domains == DOMAINS
    assert tr.text(12) == "Warning"
    assert tr.text("000012") == "Warning"
    assert tr.text(0, domain="system") == "System initial state"
    assert tr.text(0, domain="mesa") == "Function initial state"
    assert tr.text(1001, domain="system") == "ERROR (High mag)"
    assert tr.text(1001, domain="bowing") == "Reset wavelength error"
    assert tr.text(0) == "000000"                               # not in alox: no fallback on another domain
    assert len(tr.loaded_files) == 5


def test_french_falls_back_to_english_then_to_the_code(folder: Path):
    tr = Translations(folder, "fr")
    assert tr.text(12) == "Avertissement"
    assert tr.text(31) == "Arrêt"
    assert tr.text(9) == "Log"                                   # missing in French
    assert tr.text(404, domain="mesa") == "Search Object"        # no mesa_fr.lang
    assert tr.text(4242) == "004242"
    assert tr.lookup(4242) is None and not tr.has(4242) and tr.has(31)


def test_language_switch(folder: Path):
    tr = Translations(folder, "fr")
    tr.set_language("EN")
    assert tr.language == "en" and tr.text(12) == "Warning" and tr.text(31) == "000031"
    tr.set_language("fr")
    assert tr.text(12) == "Avertissement"


def test_missing_language_file_uses_english(folder: Path, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING, logger="alox.core.translations"):
        tr = Translations(folder, "de")
    assert tr.language == "de"
    assert tr.text(12) == "Warning"
    assert "alox_de.lang" in caplog.text


def test_invalid_language_and_domain(folder: Path):
    tr = Translations(folder)
    for bad in ("../alox", "", "e", "en/fr", "en.lang"):
        with pytest.raises(ValueError):
            tr.set_language(bad)
    with pytest.raises(ValueError):
        tr.text(1, domain="profile")


def test_formatting(folder: Path, caplog: pytest.LogCaptureFixture):
    tr = Translations(folder)
    assert tr.text(1550, "path=D:/x") == "api received='path=D:/x'"
    assert tr.text(8002, 2.5) == "set gain to '2.500000'"        # %f: 6 decimals, as LabVIEW
    assert tr.text(1551, who="core", what="hello") == "core said hello"
    assert tr.text(1550) == "api received='%s'"                  # no arguments: unformatted
    with caplog.at_level(logging.WARNING, logger="alox.core.translations"):
        assert tr.text(1550, "a", "b") == "api received='%s'"    # mismatch: unformatted, logged
    assert "001550" in caplog.text
    assert tr.text(4242, "x") == "004242"
    with pytest.raises(TypeError):
        tr.text(1550, "a", who="b")


def test_tables_without_a_folder():
    tr = Translations()
    assert tr.folder is None and tr.text(11) == "000011"
    tr.load_text("alox", "000011;Info\n")
    tr.load_text("alox", "000011;Info FR\n", language="fr")
    assert tr.text(11) == "Info"
    tr.set_language("fr")
    assert tr.text(11) == "Info FR"
    tr.add("system", {500: "System idle"})
    assert tr.text(500, domain="system") == "System idle"
    with pytest.raises(ValueError):
        tr.add("nope", {})
