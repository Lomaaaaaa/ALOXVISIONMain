"""alox.config.typed_ini: Vision Core keys read by name whatever their type suffix (09 §6.1–§6.3)."""

from __future__ import annotations

from pathlib import Path, PurePath

import pytest

from alox.config import ConfigTypeInvalid, TypedIni, split_name, split_suffix
from alox.config.typed_ini import resolve_key, suffix_for

#: Synthetic lines in the format LabVIEW writes (padded numbers, decimal comma, quoted strings and paths).
SAMPLE_LINES = (
    "; written by hand for the tests",
    "[general]",
    'outputfolder_STR = "D:/alox/data"',
    'language_STR = "en"',
    'profilefolder_PATH = "/D/ALOX/profiles"',
    "extmodulesui_BOOL = FALSE    ",
    "watchdog_BOOL = TRUE    ",
    "wafersize_DBL = 100,000000    ",
    "port_I32 = 8001",
    "modehotkey_U32 = 121    ",
    "",
    "[hardware.zstage]",
    "speed_I32 = 3    ",
    "speed_DBL = 2,500000    ",
    "axis_I32 = 1",
    "axis_I32 = 10",
    "Mode_Bool = true",
    'note_STR = "a\\\\b\\0Ac;d"',
    'empty_PATH = ""',
    'script_STR = "D:/x/run.py"',
    'script_PATH = "/D/x/run.py"',
    "",
    "[folders]",
    "settings = D:\\alox\\settings",
    "count = 5",
    "ratio = 0,5",
    "flag = TRUE",
    'name = "x\\\\y"',
    "port = 1",
    "port_I32 = 2",
    "",
    "[Hardware.ZStage]",
    "speed_I32 = 99",
    "hidden_I32 = 1",
    "",
)
SAMPLE = "\r\n".join(SAMPLE_LINES)


@pytest.fixture
def ini() -> TypedIni:
    return TypedIni.parse(SAMPLE)


# ---------------------------------------------------------------------------------------------- names and suffixes
def test_split_name_on_last_dot() -> None:
    assert split_name("hardware.zstage.speed") == ("hardware.zstage", "speed")
    assert split_name("general.port") == ("general", "port")
    for bad in ("port", ".port", "general.", ""):
        with pytest.raises(ValueError):
            split_name(bad)


@pytest.mark.parametrize(("key", "expected"), [
    ("port_I32", ("port", "I32")), ("Port_i32", ("Port", "I32")), ("nbpoints9_U32", ("nbpoints9", "U32")),
    ("ScriptPythonPath_PATH", ("ScriptPythonPath", "PATH")), ("a_b_DBL", ("a_b", "DBL")),
    ("settings", ("settings", None)), ("speed_X", ("speed_X", None)), ("_I32", ("_I32", None)),
])
def test_split_suffix(key: str, expected: tuple[str, str | None]) -> None:
    assert split_suffix(key) == expected


def test_suffix_for_python_types() -> None:
    assert [suffix_for(v) for v in (True, 1.5, 3, "x", PurePath("D:/x"))] == ["BOOL", "DBL", "I32", "STR", "PATH"]
    with pytest.raises(TypeError):
        suffix_for(None)


def test_resolve_key_preferences() -> None:
    keys = ["speed_BOOL", "speed_I32", "speed_DBL", "other"]
    assert resolve_key(keys, "speed") == ("speed_BOOL", "BOOL")              # no default: first in file order
    assert resolve_key(keys, "SPEED", 1) == ("speed_I32", "I32")
    assert resolve_key(keys, "speed", 1.0) == ("speed_DBL", "DBL")
    assert resolve_key(keys, "speed", "x") == ("speed_BOOL", "BOOL")         # no STR/PATH: first in file order
    assert resolve_key(["speed_U32", "speed_I32"], "speed", 0) == ("speed_U32", "U32")
    assert resolve_key(["speed_BOOL", "speed_I32"], "speed", 2.5) == ("speed_I32", "I32")   # numeric second tier
    assert resolve_key(keys, "other") == ("other", None)
    assert resolve_key(["port_I32"], "port_I32") == ("port_I32", "I32")       # explicit suffix in the name
    assert resolve_key(keys, "missing") is None


# ---------------------------------------------------------------------------------------------- get
def test_get_converts_by_suffix(ini: TypedIni) -> None:
    assert ini.get("general.extmodulesui", True) is False
    assert ini.get("general.watchdog", False) is True
    assert ini.get("general.wafersize", 0.0) == 100.0                       # decimal comma, trailing blanks
    assert ini.get("general.port", 0) == 8001
    assert ini.get("general.modehotkey", 0) == 121
    assert ini.get("general.outputfolder", "") == "D:/alox/data"
    assert ini.get("general.language") == "en"
    assert ini.get("general.profilefolder", "") == "/D/ALOX/profiles"       # raw common path, not localized
    assert ini.get("hardware.zstage.note", "") == "a\\b\nc;d"               # unescaped, ';' kept inside quotes
    assert ini.get("hardware.zstage.empty", "x") == ""


def test_get_result_types_follow_the_suffix(ini: TypedIni) -> None:
    assert type(ini.get("general.wafersize")) is float
    assert type(ini.get("general.port")) is int
    assert type(ini.get("general.watchdog")) is bool
    assert type(ini.get("general.port", 0.0)) is int                         # only an I32 key: converted as I32


def test_get_is_case_insensitive(ini: TypedIni) -> None:
    assert ini.get("GENERAL.Port", 0) == 8001
    assert ini.get("hardware.zstage.mode", False) is True                    # key written Mode_Bool
    assert ini.get("HARDWARE.ZSTAGE.SPEED", 0) == 3


def test_get_missing_gives_default(ini: TypedIni) -> None:
    assert ini.get("nope.port", 7) == 7
    assert ini.get("general.nope", 2.5) == 2.5
    assert ini.get("general.nope") is None
    assert ini.get("hardware.zstage.hidden", -1) == -1                      # only in the shadowed duplicate section


def test_two_suffixes_choose_by_default_type(ini: TypedIni) -> None:
    assert ini.get("hardware.zstage.speed", 0) == 3
    assert ini.get("hardware.zstage.speed", 0.0) == 2.5
    assert ini.get("hardware.zstage.speed") == 3                             # no default: first in file order
    assert ini.get("hardware.zstage.speed", False) == 3                      # no BOOL key: first in file order
    assert ini.get("hardware.zstage.script", "") == "D:/x/run.py"            # str → STR before PATH
    assert ini.get("hardware.zstage.script", PurePath()) == "/D/x/run.py"    # path → PATH before STR
    assert ini.key_for("hardware.zstage.speed", 1.0) == "speed_DBL"
    assert ini.key_for("hardware.zstage.nope") is None


def test_duplicate_key_first_wins_and_is_reported(ini: TypedIni) -> None:
    assert ini.get("hardware.zstage.axis", 0) == 1
    assert ini.duplicates() == [("hardware.zstage", "speed_I32"), ("hardware.zstage", "axis_I32")]
    assert ini.duplicate_sections() == ["hardware.zstage"]
    assert TypedIni.parse("[a]\nk = 1\n").duplicates() == []


def test_duplicates_are_case_insensitive() -> None:
    ini = TypedIni.parse("[s]\nPort_I32 = 1\nport_i32 = 2\n[t]\nport_I32 = 3\n")
    assert ini.duplicates() == [("s", "Port_I32")]
    assert ini.get("s.port", 0) == 1


def test_plain_key_fallback_uses_default_type(ini: TypedIni) -> None:
    assert ini.get("folders.count", 0) == 5
    assert ini.get("folders.ratio", 0.0) == 0.5
    assert ini.get("folders.flag", False) is True
    assert ini.get("folders.name", "") == "x\\y"                             # str: unescaped
    assert ini.get("folders.name") == "x\\y"                                 # no default: str
    assert ini.get("folders.settings", Path()) == "D:\\alox\\settings"       # path: raw text, not unescaped
    assert ini.get("folders.port", 0) == 2                                   # the suffixed key wins over the plain one


def test_explicit_suffix_in_name() -> None:
    ini = TypedIni.parse("[hardware.bandit]\nnbpoints9_U32 = -5\nport_I32 = 7\n")
    assert ini.get("hardware.bandit.nbpoints9_U32", 9) == 0                  # U32: saturated at 0
    assert ini.get("hardware.bandit.port_I32") == 7


def test_integers_saturate() -> None:
    ini = TypedIni.parse("[s]\na_I32 = 99999999999\nb_U32 = 99999999999\nc_I32 = 12abc\n")
    assert ini.get("s.a", 0) == 2**31 - 1
    assert ini.get("s.b", 0) == 2**32 - 1
    assert ini.get("s.c", 0) == 12


@pytest.mark.parametrize(("line", "default"), [
    ("x_I32 = abc", 0), ("x_U32 = ", 0), ("x_DBL = fast", 0.0), ("x_BOOL = yes", False), ("x = maybe", True),
])
def test_invalid_value_raises(line: str, default: object) -> None:
    ini = TypedIni.parse(f"[s]\n{line}\n")
    with pytest.raises(ConfigTypeInvalid, match=r"\[s\] x") as info:
        ini.get("s.x", default)
    assert isinstance(info.value, ValueError)


# ---------------------------------------------------------------------------------------------- paths
def test_get_path_windows(ini: TypedIni) -> None:
    p = ini.get_path("general.profilefolder", platform="win32", env={})
    assert p is not None and str(p) == "D:\\ALOX\\profiles"
    p = ini.get_path("general.outputfolder", platform="win32", env={})       # STR value used as a path
    assert p is not None and str(p) == "D:\\alox\\data"
    p = ini.get_path("folders.settings", platform="win32", env={})           # plain key: raw path text
    assert p is not None and str(p) == "D:\\alox\\settings"


def test_get_path_posix_with_and_without_drive_mapping(ini: TypedIni, tmp_path: Path) -> None:
    env = {"ALOX_DRIVE_D": str(tmp_path)}
    assert ini.get_path("general.profilefolder", platform="linux", env=env) == tmp_path / "ALOX" / "profiles"
    assert ini.get_path("general.outputfolder", platform="linux", env=env) == tmp_path / "alox" / "data"
    assert ini.get_path("folders.settings", platform="linux", env=env) == tmp_path / "alox" / "settings"
    assert ini.get_path("general.profilefolder", platform="linux", env={}) == Path("/D/ALOX/profiles")
    assert ini.get_path("general.outputfolder", platform="linux", env={}) is None    # D:/… without a mapping


def test_get_path_empty_missing_and_wrong_type(ini: TypedIni) -> None:
    default = Path("fallback")
    assert ini.get_path("hardware.zstage.empty", default, platform="win32", env={}) is None   # empty path
    assert ini.get_path("general.nope", default, platform="win32", env={}) == default
    assert ini.get_path("general.nope", platform="linux", env={}) is None
    p = ini.get_path("hardware.zstage.script", platform="win32", env={})     # PATH preferred over STR
    assert p is not None and str(p) == "D:\\x\\run.py"
    with pytest.raises(ConfigTypeInvalid):
        ini.get_path("general.port", platform="win32", env={})


# ---------------------------------------------------------------------------------------------- introspection, files
def test_sections_and_keys(ini: TypedIni) -> None:
    assert ini.sections() == ["general", "hardware.zstage", "folders"]
    assert ini.keys("general")[:3] == [("outputfolder", "STR"), ("language", "STR"), ("profilefolder", "PATH")]
    assert ini.keys("HARDWARE.ZSTAGE")[:4] == [("speed", "I32"), ("speed", "DBL"), ("axis", "I32"), ("Mode", "BOOL")]
    assert ini.keys("folders")[0] == ("settings", None)
    assert ini.keys("nope") == []
    assert ini.config.raw("general", "port_I32") == "8001"


def test_load_cp1252(tmp_path: Path) -> None:
    p = tmp_path / "alox.ini"
    p.write_bytes('[general]\r\nname_STR = "Caméra €"\r\nwafersize_DBL = 76,200000    \r\n'.encode("cp1252"))
    ini = TypedIni.load(p)
    assert ini.get("general.name", "") == "Caméra €"
    assert ini.get("general.wafersize", 0.0) == pytest.approx(76.2)
    assert TypedIni.load(str(p), encoding="latin-1").get("general.name", "").startswith("Caméra")
    with pytest.raises(FileNotFoundError):
        TypedIni.load(tmp_path / "missing.ini")
