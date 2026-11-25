"""alox.config.ini_writer: preserving edits in LabVIEW's Write Key format, read back through TypedIni (09 §6)."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path, PurePath, PureWindowsPath

import pytest

from alox.config import (
    IniDocument,
    TypedIni,
    escape_string,
    format_double,
    format_value,
    to_common_path,
)
from aloxcam.config.niconfig import NIConfig

#: Synthetic lines: a comment before the first section, padded values, a comment line, an inline comment, duplicates.
LINES = (
    "; synthetic test file",
    "[general]",
    'outputfolder_STR = "D:/alox/data"',
    "wafersize_DBL = 100,000000    ",
    "port_I32 = 8001",
    "extmodulesui_BOOL = FALSE    ",
    "; a comment line",
    "speed_I32=3 ; inline comment",
    "",
    "[hardware.zstage]",
    "speed_I32 = 3",
    "speed_DBL = 2,500000",
    "axis_I32 = 1",
    "AXIS_I32 = 10",
    'profile_PATH = "/D/ALOX/profiles"',
    "",
    "",
    "[folders]",
    "settings = D:\\alox\\settings",
    "",
)
CRLF_TEXT = "\r\n".join(LINES)
LF_TEXT = "\n".join(LINES)


def read(doc: IniDocument) -> TypedIni:
    return TypedIni.parse(doc.text())


def changed_lines(before: str, after: str) -> list[tuple[str, str]]:
    a, b = before.splitlines(), after.splitlines()
    assert len(a) == len(b)
    return [(x, y) for x, y in zip(a, b, strict=True) if x != y]


# ---------------------------------------------------------------------------------------------- preservation
@pytest.mark.parametrize("text", [
    CRLF_TEXT, LF_TEXT, "\r".join(LINES), "[a]\r\nk = 1\nx = 2\r", "[a]\nk = 1", "", "\n\n", "\ufeff[a]\r\nk = 1\r\n",
    "no section = 1\n[a]\n  ; indented comment\n\tk\t=\t1\t\n#x=2\n[a]\nk = 3\n",
])
def test_untouched_document_is_identical(text: str) -> None:
    assert IniDocument.parse(text).text() == text


def test_load_save_unchanged_is_byte_identical(tmp_path: Path) -> None:
    p = tmp_path / "alox.ini"
    data = (CRLF_TEXT + 'name_STR = "Caméra €"\r\n').encode("cp1252") + b"odd = \x81\x8d\r\n"
    p.write_bytes(data)
    doc = IniDocument.load(p)
    assert doc.save() == p
    assert p.read_bytes() == data                       # undecodable cp1252 bytes written back as they were


def test_set_changes_only_the_value(tmp_path: Path) -> None:
    doc = IniDocument.parse(CRLF_TEXT)
    doc.set("GENERAL", "WaferSize_dbl", "76,200000")
    assert changed_lines(CRLF_TEXT, doc.text()) == [("wafersize_DBL = 100,000000    ", "wafersize_DBL = 76,200000    ")]
    assert doc.text().count("\r\n") == CRLF_TEXT.count("\r\n") and "\n" not in doc.text().replace("\r\n", "")
    assert read(doc).get("general.wafersize", 0.0) == pytest.approx(76.2)


@pytest.mark.parametrize(("line", "value", "expected"), [
    ("k=1", "2", "k=2"),
    ("\tk   =   1    ", "2", "\tk   =   2    "),
    ("k = 1 ; one", "2", "k = 2 ; one"),
    ('k = "a" ; note', '"b"', 'k = "b" ; note'),
    ('k = "x;y"   ', '"z"', 'k = "z"   '),
    ("k = 1 ; one", '"a;b"', 'k = "a;b"'),               # the comment would cut the new value: dropped
    ("k =", "2", "k = 2"),
    ("k=", "2", "k=2"),
    ("k =    ", "2", "k = 2"),
    ("k = ; empty", "2", "k = 2; empty"),
])
def test_set_keeps_spacing_and_comment(line: str, value: str, expected: str) -> None:
    doc = IniDocument.parse(f"[s]\n{line}\n")
    doc.set("s", "K", value)
    assert doc.text() == f"[s]\n{expected}\n"
    assert NIConfig.parse(doc.text()).raw("s", "k") == value


def test_set_first_key_of_first_section_only() -> None:
    text = "[a]\nk = 1\nK = 2\n[b]\nk = 5\n[A]\nk = 3\nother = 4\n"
    doc = IniDocument.parse(text)
    doc.set("a", "k", "9")
    assert doc.text() == text.replace("k = 1", "k = 9", 1)
    doc.set("a", "other", "7")                          # only in the shadowed copy: added to the first one
    assert doc.text().startswith("[a]\nk = 9\nK = 2\nother = 7\n[b]")
    assert NIConfig.parse(doc.text()).read_int("a", "other", 0) == 7


def test_set_appends_at_end_of_section_before_blank_lines() -> None:
    doc = IniDocument.parse(LF_TEXT)
    doc.set("hardware.zstage", "new_I32", "5")
    lines = doc.text().split("\n")
    i = lines.index("new_I32 = 5")
    assert lines[i - 1] == 'profile_PATH = "/D/ALOX/profiles"' and lines[i + 1:i + 4] == ["", "", "[folders]"]
    doc.set("general", "after_I32", "1")                # after the last non-blank line (a key with a comment)
    assert "speed_I32=3 ; inline comment\nafter_I32 = 1\n\n[hardware.zstage]" in doc.text()


def test_set_in_empty_section_and_last_section() -> None:
    doc = IniDocument.parse("[a]\n\n[b]\nx = 1")
    doc.set("a", "k", "1")
    doc.set("b", "y", "2")                              # the last line had no line ending: one is added
    assert doc.text() == "[a]\nk = 1\n\n[b]\nx = 1\ny = 2\n"


def test_set_creates_section_at_end() -> None:
    doc = IniDocument.parse(CRLF_TEXT.rstrip("\r\n"))
    doc.set("hardware.new", "Flag_BOOL", "TRUE")
    assert doc.text().endswith("settings = D:\\alox\\settings\r\n\r\n[hardware.new]\r\nFlag_BOOL = TRUE\r\n")
    assert read(doc).get("hardware.new.flag", False) is True
    doc = IniDocument.parse("[a]\nk = 1\n\n")
    doc.set("b", "k", "2")
    assert doc.text() == "[a]\nk = 1\n\n[b]\nk = 2\n"


def test_new_document_uses_crlf() -> None:
    doc = IniDocument.parse("")
    doc.set("general", "port_I32", "8001")
    assert doc.text() == "[general]\r\nport_I32 = 8001\r\n"
    doc = IniDocument.parse("[a]\nk = 1\r\n")           # the first line ending of the file is used for new lines
    doc.set("a", "x", "2")
    assert doc.text() == "[a]\nk = 1\r\nx = 2\n"


@pytest.mark.parametrize(("section", "key", "value"), [
    ("s", "k", "a\nb"), ("s", "k", "a\rb"), ("", "k", "1"), ("a]b", "k", "1"), ("a\nb", "k", "1"),
    ("s", "", "1"), ("s", "k=x", "1"), ("s", ";k", "1"), ("s", " k", "1"), ("s", "[x]", "1"), ("s", "k\n", "1"),
])
def test_set_rejects_unreadable_input(section: str, key: str, value: str) -> None:
    doc = IniDocument.parse("[s]\nk = 1\n")
    with pytest.raises(ValueError):
        doc.set(section, key, value)
    assert doc.text() == "[s]\nk = 1\n"


def test_remove_first_occurrence() -> None:
    doc = IniDocument.parse(LF_TEXT)
    assert doc.remove("hardware.zstage", "axis_i32") is True
    assert read(doc).get("hardware.zstage.axis", 0) == 10      # the duplicate is now the first occurrence
    assert doc.text() == LF_TEXT.replace("axis_I32 = 1\n", "", 1)
    assert doc.remove("hardware.zstage", "nope") is False
    assert doc.remove("nope", "axis_I32") is False


def test_get_raw() -> None:
    doc = IniDocument.parse(LF_TEXT)
    assert doc.get_raw("General", "Speed_I32") == "3"
    assert doc.get_raw("general", "outputfolder_STR") == '"D:/alox/data"'
    assert doc.get_raw("general", "nope") is None and doc.get_raw("nope", "k") is None


# ---------------------------------------------------------------------------------------------- formatting
def test_escape_string_matches_ni_unescape() -> None:
    assert escape_string('a\\b"c;d') == 'a\\\\b"c;d'
    assert escape_string("x\ny\r\t\x00\x1f\x7f") == "x\\0Ay\\0D\\09\\00\\1F\x7f"
    assert escape_string("\\0A") == "\\\\0A"


@pytest.mark.parametrize(("value", "decimal", "text"), [
    (76.2, ",", "76,200000"), (-42.7531, ",", "-42,753100"), (24.6, ".", "24.600000"), (0, ",", "0,000000"),
    (1e-7, ",", "0,000000"), (3630398355.263576, ",", "3630398355,263576"), (math.nan, ",", "NaN"),
    (math.inf, ",", "Inf"), (-math.inf, ".", "-Inf"),
])
def test_format_double(value: float, decimal: str, text: str) -> None:
    assert format_double(value, decimal) == text


def test_format_double_rejects_other_separators() -> None:
    with pytest.raises(ValueError):
        format_double(1.0, ";")


@pytest.mark.parametrize(("text", "common"), [
    ("D:\\dir\\sub", "/D/dir/sub"), ("D:/dir/sub/", "/D/dir/sub"), ("d:\\", "/d"), ("D:", "/D"),
    ("/D/ALOX/profiles", "/D/ALOX/profiles"), ("\\\\server\\share\\x", "//server/share/x"),
    ("dir\\sub", "dir/sub"), ("..\\x", "../x"), ("D:\\\\a\\\\b", "/D/a/b"), ("", ""),
])
def test_to_common_path(text: str, common: str) -> None:
    assert to_common_path(text) == common


def test_format_value() -> None:
    assert format_value("BOOL", True) == "TRUE" and format_value("bool", False) == "FALSE"
    assert format_value("DBL", 5) == "5,000000"
    assert format_value("I32", -(2**31)) == "-2147483648"
    assert format_value("U32", 2**32 - 1) == "4294967295"
    assert format_value("STR", "a\\b") == '"a\\\\b"'
    assert format_value("STR", PurePath("x")) == '"x"'
    assert format_value("PATH", PureWindowsPath("D:/alox/data")) == '"/D/alox/data"'


@pytest.mark.parametrize(("suffix", "value", "error"), [
    ("BOOL", 1, TypeError), ("DBL", True, TypeError), ("DBL", "1.5", TypeError), ("I32", 1.0, TypeError),
    ("I32", False, TypeError), ("I32", 2**31, ValueError), ("U32", -1, ValueError), ("U32", 2**32, ValueError),
    ("STR", 5, TypeError), ("PATH", 5, TypeError), ("PATH", "a\nb", ValueError), ("XYZ", 1, ValueError),
])
def test_format_value_errors(suffix: str, value: object, error: type[Exception]) -> None:
    with pytest.raises(error):
        format_value(suffix, value)


# ---------------------------------------------------------------------------------------------- set_typed
def test_set_typed_formats_by_existing_suffix() -> None:
    doc = IniDocument.parse(CRLF_TEXT)
    assert doc.set_typed("general.wafersize", 76.2) == "wafersize_DBL"
    assert doc.set_typed("general.port", 8002) == "port_I32"
    assert doc.set_typed("general.extmodulesui", True) == "extmodulesui_BOOL"
    assert doc.set_typed("general.outputfolder", "E:/new") == "outputfolder_STR"
    assert doc.set_typed("hardware.zstage.profile", "E:\\alox\\profiles") == "profile_PATH"
    assert changed_lines(CRLF_TEXT, doc.text()) == [
        ('outputfolder_STR = "D:/alox/data"', 'outputfolder_STR = "E:/new"'),
        ("wafersize_DBL = 100,000000    ", "wafersize_DBL = 76,200000    "),
        ("port_I32 = 8001", "port_I32 = 8002"),
        ("extmodulesui_BOOL = FALSE    ", "extmodulesui_BOOL = TRUE    "),
        ('profile_PATH = "/D/ALOX/profiles"', 'profile_PATH = "/E/alox/profiles"'),
    ]


def test_set_typed_decimal_point_and_int_into_double() -> None:
    doc = IniDocument.parse("[s]\nx_DBL = 1,000000\n")
    doc.set_typed("s.x", 2.5, decimal=".")
    assert doc.text() == "[s]\nx_DBL = 2.500000\n"
    doc.set_typed("s.x", 3)
    assert doc.text() == "[s]\nx_DBL = 3,000000\n"
    assert read(doc).get("s.x", 0.0) == 3.0


def test_set_typed_chooses_the_suffix_of_the_value_type() -> None:
    doc = IniDocument.parse(LF_TEXT)
    assert doc.set_typed("hardware.zstage.speed", 7.25) == "speed_DBL"
    assert doc.set_typed("hardware.zstage.speed", 4) == "speed_I32"
    ini = read(doc)
    assert ini.get("hardware.zstage.speed", 0.0) == 7.25 and ini.get("hardware.zstage.speed", 0) == 4
    assert doc.set_typed("hardware.zstage.axis", 2) == "axis_I32"   # first of the duplicates
    assert "axis_I32 = 2\nAXIS_I32 = 10\n" in doc.text()


def test_set_typed_creates_keys_by_python_type() -> None:
    doc = IniDocument.parse(LF_TEXT)
    created = [doc.set_typed(f"hardware.new.{name}", value) for name, value in [
        ("flag", True), ("ratio", 0.5), ("count", 3), ("label", "x"), ("folder", PureWindowsPath("D:/a/b"))]]
    assert created == ["flag_BOOL", "ratio_DBL", "count_I32", "label_STR", "folder_PATH"]
    assert doc.text().endswith('[hardware.new]\nflag_BOOL = TRUE\nratio_DBL = 0,500000\ncount_I32 = 3\n'
                               'label_STR = "x"\nfolder_PATH = "/D/a/b"\n')
    assert doc.set_typed("hardware.new.nbpoints9_U32", 7) == "nbpoints9_U32"   # explicit suffix kept
    assert read(doc).get("hardware.new.nbpoints9", 0) == 7
    with pytest.raises(TypeError):
        doc.set_typed("hardware.new.thing", None)


def test_set_typed_plain_key_formatted_by_value_type() -> None:
    doc = IniDocument.parse(LF_TEXT)
    assert doc.set_typed("folders.settings", Path("E:\\alox\\settings")) == "settings"
    assert 'settings = "/E/alox/settings"\n' in doc.text()
    p = read(doc).get_path("folders.settings", platform="win32", env={})
    assert p is not None and str(p) == "E:\\alox\\settings"


def test_set_typed_type_mismatch_with_existing_key() -> None:
    doc = IniDocument.parse(LF_TEXT)
    with pytest.raises(TypeError):
        doc.set_typed("general.port", 1.5)              # only an I32 key: no silent truncation
    with pytest.raises(TypeError):
        doc.set_typed("general.extmodulesui", 1)
    with pytest.raises(ValueError):
        doc.set_typed("general.port", 2**31)
    assert doc.text() == LF_TEXT


ROUND_TRIP = [
    True, False, 0, 1, -1, 2**31 - 1, -(2**31), 0.0, 76.2, -42.7531, 1e15, 123.4567894, 3630398355.263576, -0.5,
    "", "en", "D:\\alox\\data", 'say "hi"', '"quoted"', "a;b = c", "  spaced  ", "tab\there", "line\nbreak\r\n",
    "\x00\x01\x1f", "\\0A literal", "trailing\\", "Caméra €", "[not a section]", "; not a comment", "'single'",
]


@pytest.mark.parametrize("value", ROUND_TRIP, ids=repr)
@pytest.mark.parametrize("existing", [False, True], ids=["new", "existing"])
def test_set_typed_round_trip(value: object, existing: bool, tmp_path: Path) -> None:
    text = LF_TEXT
    if existing:                                        # replace a padded value followed by a comment
        suffix = {bool: "BOOL", int: "I32", float: "DBL", str: "STR"}[type(value)]
        text += f"[t]\nv_{suffix} = 0    ; comment\n"
    doc = IniDocument.parse(text)
    doc.set_typed("t.v", value)
    expected = round(value, 6) if isinstance(value, float) else value
    got = read(doc).get("t.v", value)
    assert got == expected and type(got) is type(value)
    p = tmp_path / "rt.ini"
    doc.save(p)
    assert TypedIni.load(p).get("t.v", value) == expected
    assert NIConfig.load(p).sections() == ["general", "hardware.zstage", "folders", "t"]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_set_typed_round_trip_special_doubles(value: float) -> None:
    doc = IniDocument.parse("")
    doc.set_typed("s.x", value)
    got = read(doc).get("s.x", 0.0)
    assert (math.isnan(got) and math.isnan(value)) or got == value


@pytest.mark.parametrize(("value", "common", "windows"), [
    ("D:\\alox\\data", "/D/alox/data", "D:\\alox\\data"),
    ("D:/alox/data", "/D/alox/data", "D:\\alox\\data"),
    (PureWindowsPath("D:/ALOX/profiles"), "/D/ALOX/profiles", "D:\\ALOX\\profiles"),
    ("/D/ALOX/profiles", "/D/ALOX/profiles", "D:\\ALOX\\profiles"),
    ("\\\\server\\share\\x", "//server/share/x", "\\\\server\\share\\x"),
    ("relative\\dir", "relative/dir", "relative\\dir"),
    ("D:\\with ; semicolon\\x ", "/D/with ; semicolon/x ", "D:\\with ; semicolon\\x "),
])
def test_set_typed_path_round_trip(value: str | PurePath, common: str, windows: str) -> None:
    doc = IniDocument.parse('[s]\np_PATH = ""\n')
    assert doc.set_typed("s.p", value) == "p_PATH"
    ini = read(doc)
    assert ini.get("s.p", PurePath()) == common          # raw common text, as LabVIEW reads it
    p = ini.get_path("s.p", platform="win32", env={})
    assert p is not None and str(p) == windows


def test_set_typed_new_path_key_round_trip(tmp_path: Path) -> None:
    doc = IniDocument.parse("")
    assert doc.set_typed("s.p", PureWindowsPath("D:/ALOX/profiles")) == "p_PATH"
    ini = read(doc)
    assert ini.get("s.p", "") == "/D/ALOX/profiles"
    assert ini.get_path("s.p", platform="linux", env={"ALOX_DRIVE_D": str(tmp_path)}) == tmp_path / "ALOX" / "profiles"


def test_set_typed_empty_path() -> None:
    doc = IniDocument.parse("[s]\np_PATH = \"/D/x\"\n")
    doc.set_typed("s.p", "")
    assert doc.text() == '[s]\np_PATH = ""\n'
    assert read(doc).get_path("s.p", Path("default"), platform="win32", env={}) is None


# ---------------------------------------------------------------------------------------------- saving
def test_save_keeps_crlf_and_bom(tmp_path: Path) -> None:
    p = tmp_path / "alox.ini"
    p.write_bytes(b"\xef\xbb\xbf" + CRLF_TEXT.encode("cp1252"))
    doc = IniDocument.load(p)
    doc.set_typed("general.port", 8003)
    doc.set_typed("hardware.extra.on", True)
    doc.save()
    data = p.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf; synthetic test file\r\n[general]\r\n")
    assert data.endswith(b"[hardware.extra]\r\non_BOOL = TRUE\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert TypedIni.load(p).get("general.port", 0) == 8003


def test_save_is_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "alox.ini"
    p.write_bytes(CRLF_TEXT.encode("cp1252"))
    doc = IniDocument.load(p)
    doc.set_typed("general.port", 1)
    calls: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def spy(src: str, dst: Path) -> None:
        calls.append((Path(src), Path(dst)))
        assert Path(src).read_bytes() == doc.text().encode("cp1252") and p.read_bytes() == CRLF_TEXT.encode()
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    doc.save()
    assert len(calls) == 1 and calls[0][0].parent == tmp_path and calls[0][1] == p
    assert p.read_bytes() == doc.text().encode("cp1252")
    assert sorted(tmp_path.iterdir()) == [p]


def test_failed_save_leaves_file_and_no_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = tmp_path / "alox.ini"
    p.write_bytes(LF_TEXT.encode())
    doc = IniDocument.load(p)
    doc.set_typed("general.port", 1)

    def boom(src: str, dst: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        doc.save()
    monkeypatch.undo()
    assert p.read_bytes() == LF_TEXT.encode() and sorted(tmp_path.iterdir()) == [p]
    doc.set_typed("general.name", "日本")               # not encodable in cp1252: nothing written
    with pytest.raises(UnicodeEncodeError):
        doc.save()
    assert p.read_bytes() == LF_TEXT.encode() and sorted(tmp_path.iterdir()) == [p]


def test_save_to_new_file_and_without_path(tmp_path: Path) -> None:
    doc = IniDocument.parse(LF_TEXT)
    with pytest.raises(ValueError):
        doc.save()
    target = tmp_path / "copy.ini"
    assert doc.save(target, encoding="utf-8") == target
    assert target.read_text(encoding="utf-8") == LF_TEXT
    with pytest.raises(FileNotFoundError):
        IniDocument.load(tmp_path / "missing.ini")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_save_keeps_permissions(tmp_path: Path) -> None:
    p = tmp_path / "alox.ini"
    p.write_bytes(LF_TEXT.encode())
    p.chmod(0o640)
    doc = IniDocument.load(p)
    doc.set_typed("general.port", 2)
    doc.save()
    assert p.stat().st_mode & 0o777 == 0o640
