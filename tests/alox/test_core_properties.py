"""alox.core.properties: alox.ini as alox_manager_properties reads and writes it (map Part 1 §4.1, §8 note 7)."""

from __future__ import annotations

import math
import os
import threading
from pathlib import Path

import pytest

from alox.core.properties import (
    InvalidProperty,
    Properties,
    TypedStore,
    format_points,
    format_table,
    parse_inline_table,
    parse_number,
    parse_points,
)

#: Synthetic alox.ini: decimal comma, every suffix, duplicates, an empty [] section, comments, unreadable keys.
LINES = (
    "; synthetic settings",
    "[general]",
    'profile_STR = "RECIPE"',
    "wafersize_DBL = 100,500000",
    "core_I32 = 2",
    "maxlines_U32 = 1000 ; inline comment",
    "watchdog_BOOL = FALSE",
    'outputfolder_PATH = "/D/alox/data"',
    "core_I32 = 7",
    "; a comment line",
    "",
    "[hardware.z]",
    "slowspeed_I32 = 3",
    "slowspeed_DBL = 2,500000",
    "nbpoints9_U32 = 9",
    "nbpoints9_U32 = 10",
    "",
    "[hardware.camera.lowmag]",
    "Gain_DBL = 1,000000",
    'roi_PTS = "1,000000;2,000000;3,500000;4,000000;"',
    "noSuffix = 3",
    "bad_I32 = abc",
    "weird_XYZ = 1",
    "",
    "[]",
    "bowchuckA_DBL = 5,000000",
    "",
)
TEXT = "\r\n".join(LINES)


def write(path: Path, text: str = TEXT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("cp1252"))
    return path


@pytest.fixture
def ini(tmp_path: Path) -> Path:
    return write(tmp_path / "settings" / "alox.ini")


def text_of(path: Path) -> str:
    return path.read_bytes().decode("cp1252")


def lines_of(path: Path) -> list[str]:
    return path.read_bytes().decode("cp1252").split("\r\n")


def diff(before: list[str], after: list[str]) -> list[tuple[str, str]]:
    assert len(before) == len(after)
    return [(a, b) for a, b in zip(before, after, strict=True) if a != b]


# ---------------------------------------------------------------------------------------------- load
def test_load_copies_the_defaults_when_missing(tmp_path: Path) -> None:
    defaults = write(tmp_path / "exe" / "alox_default.ini")
    target = tmp_path / "settings" / "alox.ini"
    props = Properties.load(target, defaults)
    assert target.read_bytes() == defaults.read_bytes()
    assert props.path == target
    assert props.get("general.profile") == "RECIPE"


def test_load_keeps_an_existing_file(tmp_path: Path, ini: Path) -> None:
    defaults = write(tmp_path / "exe" / "alox_default.ini", "[general]\r\nprofile_STR = \"OTHER\"\r\n")
    props = Properties.load(ini, defaults)
    assert props.get("general.profile") == "RECIPE"
    assert "OTHER" not in text_of(ini)


def test_load_without_file_nor_defaults(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Properties.load(tmp_path / "alox.ini", tmp_path / "missing_default.ini")
    with pytest.raises(FileNotFoundError):
        Properties.load(tmp_path / "alox.ini")


def test_types_decimal_comma_and_lookup(ini: Path) -> None:
    props = Properties.load(ini)
    assert props.get("general.wafersize") == 100.5
    assert props.get("general.maxlines") == 1000
    assert props.get("general.watchdog") is False
    assert props.get("general.outputfolder") == "/D/alox/data"
    assert props.get("GENERAL.Profile") == "RECIPE"                        # case-insensitive (NI)
    assert props.get("general.profile_STR") == "RECIPE"                    # suffix in the name accepted
    assert props.get("general.profile_I32", "x") == "x"                    # but it must be the right one
    assert props.get("hardware.camera.lowmag.gain") == 1.0                 # section = everything before the last dot
    assert props.get("general.missing", 42) == 42
    assert props.suffix_of("general.maxlines") == "U32"
    assert props.decimal == ","


def test_duplicates_follow_labview(ini: Path) -> None:
    props = Properties.load(ini)
    assert props.get("general.core") == 2                                  # exact duplicate: NI Read Key, first wins
    assert props.get("hardware.z.nbpoints9") == 9
    assert props.get("hardware.z.slowspeed") == 2.5                        # other suffix: variant attribute replaced
    assert props.suffix_of("hardware.z.slowspeed") == "DBL"
    # keys after an empty [] header belong to the previous section (NI never sees "[]" as a section)
    assert props.get("hardware.camera.lowmag.bowchuckA") == 5.0


def test_points_array(ini: Path) -> None:
    props = Properties.load(ini)
    assert props.get("hardware.camera.lowmag.roi") == [(1.0, 2.0), (3.5, 4.0)]
    assert parse_points("1;2;3;") == [(1.0, 2.0)]                          # odd last number dropped
    assert parse_points("") == []
    assert format_points([(1.0, 2.5)], ",") == '"1,000000;2,500000;"'
    assert format_points([(1.0, 2.5)], ".") == '"1.000000;2.500000;"'


def test_unreadable_keys_are_skipped_and_listed(ini: Path) -> None:
    props = Properties.load(ini)
    assert set(props.invalid) == {"hardware.camera.lowmag.noSuffix", "hardware.camera.lowmag.bad_I32",
                                  "hardware.camera.lowmag.weird_XYZ"}
    assert not props.exists("hardware.camera.lowmag.bad")
    assert not props.exists("hardware.camera.lowmag.noSuffix")


def test_names_exists_items(ini: Path) -> None:
    props = Properties.load(ini)
    names = props.names()
    assert names[:6] == ["general.profile", "general.wafersize", "general.core", "general.maxlines",
                         "general.watchdog", "general.outputfolder"]
    assert props.exists("hardware.z.slowspeed")
    assert not props.exists("hardware.z.fastspeed")
    assert props.items()["general.core"] == 2


def test_parse_number() -> None:
    assert parse_number("2,5") == 2.5
    assert parse_number(" 2.5 ") == 2.5
    assert math.isnan(parse_number("NaN"))
    assert parse_number("-Inf") == -math.inf
    with pytest.raises(ValueError):
        parse_number("abc")


# ---------------------------------------------------------------------------------------------- set
def test_set_is_type_checked_against_the_suffix(ini: Path) -> None:
    props = Properties.load(ini)
    props.set("general.wafersize", 150)                                    # int accepted for DBL
    assert props.get("general.wafersize") == 150.0
    with pytest.raises(TypeError):
        props.set("general.core", 2.5)
    with pytest.raises(TypeError):
        props.set("general.core", True)
    with pytest.raises(ValueError):
        props.set("general.maxlines", -1)
    with pytest.raises(TypeError):
        props.set("general.watchdog", 1)
    with pytest.raises(TypeError):
        props.set("general.profile", 3)
    with pytest.raises(TypeError):
        props.set("hardware.camera.lowmag.roi", [(1, 2, 3)])
    with pytest.raises(TypeError):
        props.set("general.core_DBL", 2.0)                                 # the key is an I32
    props.set("general.outputfolder", Path("data"))
    assert props.get("general.outputfolder") == "data"


def test_new_keys_take_the_python_type(ini: Path) -> None:
    props = Properties.load(ini)
    props.set("new.flag", True)
    props.set("new.count", 3)
    props.set("new.ratio", 0.25)
    props.set("new.text", "a;b")
    props.set("new.points", [(1, 2)])
    props.set("new.limit_U32", 7)
    assert [props.suffix_of(f"new.{n}") for n in ("flag", "count", "ratio", "text", "points", "limit")] == \
        ["BOOL", "I32", "DBL", "STR", "PTS", "U32"]
    with pytest.raises(TypeError):
        props.set("new.bytes", b"x")
    with pytest.raises(ValueError):
        props.set("nodot", 1)


# ---------------------------------------------------------------------------------------------- save
def test_save_rewrites_only_the_changed_lines(ini: Path) -> None:
    before = lines_of(ini)
    props = Properties.load(ini)
    props.set("general.wafersize", 200.25)
    props.set("general.maxlines", 500)
    props.set("general.profile", 'TEST "1"')
    props.save()
    after = lines_of(ini)
    assert diff(before, after) == [
        ('profile_STR = "RECIPE"', 'profile_STR = "TEST "1""'),
        ("wafersize_DBL = 100,500000", "wafersize_DBL = 200,250000"),
        ("maxlines_U32 = 1000 ; inline comment", "maxlines_U32 = 500 ; inline comment"),
    ]
    again = Properties.load(ini)
    assert again.get("general.profile") == 'TEST "1"'
    assert again.get("general.maxlines") == 500


def test_save_writes_the_key_that_was_read(ini: Path) -> None:
    props = Properties.load(ini)
    props.set("hardware.z.slowspeed", 4.0)                                 # the DBL key (the last one read)
    props.set("hardware.z.nbpoints9", 11)                                  # the first of the duplicates
    props.save()
    text = text_of(ini)
    assert "slowspeed_I32 = 3\r\nslowspeed_DBL = 4,000000" in text
    assert "nbpoints9_U32 = 11\r\nnbpoints9_U32 = 10" in text


def test_save_without_change_does_not_touch_the_file(ini: Path) -> None:
    os.utime(ini, ns=(1_000_000_000, 1_000_000_000))
    props = Properties.load(ini)
    props.set("general.core", 2)                                           # same value: not a change
    assert props.dirty == []
    props.save()
    assert ini.stat().st_mtime_ns == 1_000_000_000


def test_new_keys_points_and_decimal_point(tmp_path: Path) -> None:
    path = write(tmp_path / "alox.ini", "[general]\nratio_DBL = 1.500000\n")
    props = Properties.load(path)
    assert props.decimal == "."
    props.set("general.ratio", 2.0)
    props.set("overlays.marks", [(1.5, 2), (3, 4)])
    props.set("overlays.show", True)
    props.save()
    text = text_of(path)
    assert text == ("[general]\nratio_DBL = 2.000000\n\n[overlays]\nmarks_PTS = \"1.500000;2.000000;3.000000;"
                    "4.000000;\"\nshow_BOOL = TRUE\n")
    again = Properties.load(path)
    assert again.get("overlays.marks") == [(1.5, 2.0), (3.0, 4.0)]


def test_invalid_keys_survive_a_save(ini: Path) -> None:
    props = Properties.load(ini)
    props.set("hardware.camera.lowmag.gain", 2.0)
    props.save()
    text = text_of(ini)
    assert "bad_I32 = abc" in text and "noSuffix = 3" in text and "weird_XYZ = 1" in text


def test_delete_removes_every_copy_on_save(ini: Path) -> None:
    changes: list[tuple] = []
    props = Properties.load(ini, on_change=lambda *a: changes.append(a))
    assert props.delete("hardware.z.nbpoints9")
    assert not props.delete("hardware.z.nbpoints9")
    assert changes == [("hardware.z.nbpoints9", 9, None)]
    props.save()
    assert "nbpoints9" not in text_of(ini)


def test_clear_does_not_change_the_file(ini: Path) -> None:
    before = ini.read_bytes()
    props = Properties.load(ini)
    props.clear()
    assert props.names() == []
    props.save()
    assert ini.read_bytes() == before


def test_save_elsewhere_is_a_complete_copy(ini: Path, tmp_path: Path) -> None:
    props = Properties.load(ini)
    props.set("general.core", 5)
    copy_path = props.save(tmp_path / "copy" / "alox.ini")
    assert props.path == copy_path
    assert Properties.load(copy_path).get("general.core") == 5
    assert Properties.load(copy_path).get("hardware.camera.lowmag.roi") == [(1.0, 2.0), (3.5, 4.0)]
    assert Properties.load(ini).get("general.core") == 2                   # the original is untouched


def test_store_from_text_needs_a_path(tmp_path: Path) -> None:
    store = TypedStore.parse("[a]\nx_I32 = 1\n")
    store.set("a.y", 2)
    with pytest.raises(ValueError):
        store.save()
    target = store.save(tmp_path / "new.ini")
    assert TypedStore.read(target).items() == {"a.x": 1, "a.y": 2}


# ---------------------------------------------------------------------------------------------- Check
def test_missing_from_adds_the_defaults(ini: Path, tmp_path: Path) -> None:
    defaults = write(tmp_path / "alox_default.ini", "[general]\r\ncore_I32 = 1\r\nlanguage_STR = \"en\"\r\n"
                                                    "[websocket]\r\nport_I32 = 6123\r\n")
    props = Properties.load(ini)
    added = props.missing_from(defaults)
    assert added == ["general.language", "websocket.port"]
    assert props.get("general.core") == 2                                  # existing values are kept
    props.save()
    again = Properties.load(ini)
    assert again.get("general.language") == "en"
    assert again.get("websocket.port") == 6123
    assert props.missing_from(defaults) == []


# ---------------------------------------------------------------------------------------------- tables
TABLE_TEXT = (
    "[general]\r\n"
    'exposures_TABLE.<size(s)> = "2 3"\r\n'
    "exposures_TABLE 0 = 700,000000\r\n"
    "exposures_TABLE 1 = 25000,000000\r\n"
    "exposures_TABLE 2 = NaN\r\n"
    "exposures_TABLE 3 = 725,000000\r\n"
    "exposures_TABLE 4 = 30000,000000\r\n"
    "exposures_TABLE 5 = 1,500000\r\n"
    "name_STR = \"x\"\r\n"
)


def test_table_read_write_round_trip(tmp_path: Path) -> None:
    path = write(tmp_path / "p.ini", TABLE_TEXT)
    store = TypedStore.read(path)
    table = store.get("general.exposures")
    assert table[0][:2] == [700.0, 25000.0] and math.isnan(table[0][2])
    assert table[1] == [725.0, 30000.0, 1.5]
    assert store.suffix_of("general.exposures") == "TABLE"
    store.set("general.exposures", [[1, 2], [3, 4], [5, 6]])
    store.save()
    text = text_of(path)                    # rewritten in the one-line layout of real recipes, legacy keys removed
    assert 'exposures_TABLE = "<size(s)=3 2> 1,000000 2,000000 3,000000 4,000000 5,000000 6,000000"' in text
    assert "<size(s)>" not in text and "exposures_TABLE 0" not in text and 'name_STR = "x"' in text
    assert TypedStore.read(path).get("general.exposures") == [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]
    store.set("general.exposures", [[9, 8]])
    store.save()
    assert 'exposures_TABLE = "<size(s)=1 2> 9,000000 8,000000"' in text_of(path)
    with pytest.raises(ValueError):
        store.set("general.exposures", [[1, 2], [3]])
    with pytest.raises(TypeError):
        store.set("general.exposures", [["a"]])


# The layout of real recipes (OpenG Write Key (Variant)): one quoted line, size then the elements row by row.
INLINE_TABLE_TEXT = (
    "[general]\r\n"
    'exposures_TABLE = "<size(s)=2 3> 700,000000 25000,000000 NaN 725,000000 30000,000000 1,500000"\r\n'
    "version_I32 = 1    \r\n"
)


def test_inline_table_is_read_and_rewritten_byte_identical(tmp_path: Path) -> None:
    path = write(tmp_path / "p.ini", INLINE_TABLE_TEXT)
    store = TypedStore.read(path)
    assert store.invalid == []
    table = store.get("general.exposures")
    assert table[0][:2] == [700.0, 25000.0] and math.isnan(table[0][2]) and table[1] == [725.0, 30000.0, 1.5]
    store.set("general.exposures", [[1.0, 2.0, 3.0]])
    store.save()
    store.set("general.exposures", table)
    store.save()
    assert text_of(path) == INLINE_TABLE_TEXT


def test_inline_table_codec() -> None:
    assert parse_inline_table("<size(s)=2 2> 1 2,5 3 4") == [[1.0, 2.5], [3.0, 4.0]]
    assert parse_inline_table("<size(s)=3> 1 2 3") == [[1.0], [2.0], [3.0]]
    assert parse_inline_table("<size(s)=0 0>") == []
    assert format_table([[700, 1.5]], ".") == '"<size(s)=1 2> 700.000000 1.500000"'
    assert format_table([]) == '"<size(s)=0 0>"'
    for bad in ("1 2 3", "<size(s)=2 2> 1 2 3", "<size(s)=x> 1", "<size(s)=1 1> abc"):
        with pytest.raises(InvalidProperty):
            parse_inline_table(bad)
    store = TypedStore.parse('[g]\nt_TABLE = "<size(s)=1 2> 1"\n')
    assert store.invalid == ["g.t_TABLE"]


def test_table_with_missing_element_is_invalid(tmp_path: Path) -> None:
    store = TypedStore.parse('[g]\nt_TABLE.<size(s)> = "1 2"\nt_TABLE 0 = 1\n')
    assert store.invalid == ["g.t_TABLE.<size(s)>"]
    store = TypedStore.parse('[g]\nt_TABLE.<size(s)> = "1 2"\nt_TABLE 0 0 = 1\nt_TABLE 0 1 = 2\n')
    assert store.get("g.t") == [[1.0, 2.0]]
    assert InvalidProperty.code == 9006


def test_table_delete_removes_all_its_keys(tmp_path: Path) -> None:
    path = write(tmp_path / "p.ini", TABLE_TEXT)
    store = TypedStore.read(path)
    store.delete("general.exposures")
    store.save()
    assert text_of(path) == '[general]\r\nname_STR = "x"\r\n'


# ---------------------------------------------------------------------------------------------- notification, threads
def test_on_change_notifications(ini: Path) -> None:
    events: list[tuple] = []
    props = Properties.load(ini, on_change=lambda *a: events.append(a))
    props.set("general.core", 3)
    props.set("general.core", 3)
    props.set("new.value", 1.5)
    assert events == [("general.core", 2, 3), ("new.value", None, 1.5)]


def test_failing_listener_does_not_break_the_store(ini: Path) -> None:
    def boom(*_args: object) -> None:
        raise RuntimeError("listener")

    props = Properties.load(ini, on_change=boom)
    props.set("general.core", 9)
    assert props.get("general.core") == 9


def test_concurrent_sets(ini: Path) -> None:
    props = Properties.load(ini)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(50):
                props.set(f"thread{n}.value{i}", i)
                props.get("general.core")
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    props.save()
    again = Properties.load(ini)
    assert all(again.get(f"thread{n}.value49") == 49 for n in range(8))
