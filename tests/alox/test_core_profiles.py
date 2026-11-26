"""alox.core.profiles: zip recipes as alox_manager_profile handles them (map Part 1 §4.2, §2.3, §8 note 8)."""

from __future__ import annotations

import stat
import threading
import zipfile
from pathlib import Path

import pytest

from alox.core.exposures import PROFILE_DEFAULT_TABLE
from alox.core.profiles import (
    DEFAULT_PROFILE,
    ProfileError,
    ProfileManager,
    ProfileProperties,
    UnsafeArchive,
    check_profile_name,
    safe_extract,
    zip_profile_folder,
)
from alox.core.properties import Properties

RECIPE_INI = (
    "[general]\r\n"
    "; synthetic recipe\r\n"
    "wlmanual_U32 = 850\r\n"
    "exposure_DBL = 12,500000\r\n"
    'exposures_TABLE.<size(s)> = "2 7"\r\n'
    + "".join(f"exposures_TABLE {i} = {v}\r\n" for i, v in enumerate(
        ["700,000000", "25000,000000", "10000,000000", "40000,000000", "50,000000", "100,000000", "25000,000000",
         "750,000000", "45000,000000", "10000,000000", "40000,000000", "50,000000", "100,000000", "45000,000000"]))
    + "[autofocus]\r\n"
    "zstep_DBL = 0,010000\r\n"
    "showwindow_BOOL = TRUE\r\n"
)


def make_zip(path: Path, members: dict[str, bytes | str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


@pytest.fixture
def manager(tmp_path: Path) -> ProfileManager:
    profiles = tmp_path / "profiles"
    make_zip(profiles / "recipe_a.zip", {"recipe_a.ini": RECIPE_INI, "model.png": b"\x89PNG model"})
    make_zip(profiles / "Recipe_B.ZIP", {"recipe_b.ini": RECIPE_INI.replace("850", "900")})
    (profiles / "notes.txt").write_text("not a profile")
    settings = tmp_path / "settings"
    return ProfileManager(profiles, settings / "profile", settings / "profile_temp")


def names_in(archive: Path) -> list[str]:
    with zipfile.ZipFile(archive) as zf:
        return sorted(zf.namelist())


def read_member(archive: Path, name: str) -> str:
    with zipfile.ZipFile(archive) as zf:
        return zf.read(name).decode("cp1252")


# ---------------------------------------------------------------------------------------------- list / load
def test_list_is_upper_case_and_sorted(manager: ProfileManager) -> None:
    assert manager.list() == ["RECIPE_A", "RECIPE_B"]
    assert manager.exists("recipe_b") and manager.exists("RECIPE_A")
    assert not manager.exists("notes")


def test_list_of_a_missing_folder(tmp_path: Path) -> None:
    assert ProfileManager(tmp_path / "none", tmp_path / "w", tmp_path / "t").list() == []


def test_load_clears_and_unzips_the_working_folder(manager: ProfileManager) -> None:
    manager.work.mkdir(parents=True)
    (manager.work / "stale.ini").write_text("[x]\n")
    (manager.work / "old").mkdir()
    store = manager.load("RECIPE_A")
    assert sorted(p.name for p in manager.work.iterdir()) == ["model.png", "recipe_a.ini"]
    assert manager.current == "RECIPE_A"
    assert store is manager.properties
    assert manager.get("general.wlmanual") == 850
    assert manager.get("general.exposure") == 12.5
    assert manager.get("autofocus.showwindow") is True
    table = manager.get("general.exposures")
    assert table[1] == [750.0, 45000.0, 10000.0, 40000.0, 50.0, 100.0, 45000.0]
    assert "autofocus.zstep" in manager.names()


def test_load_finds_names_in_any_case(manager: ProfileManager) -> None:
    manager.load("recipe_b")
    assert manager.current == "recipe_b"
    assert manager.get("general.wlmanual") == 900


def test_load_missing_profile(manager: ProfileManager) -> None:
    with pytest.raises(ProfileError):
        manager.load("NOPE")
    with pytest.raises(ProfileError):
        manager.get("general.wlmanual")                                    # no profile loaded


def test_archive_without_its_ini_leaves_the_active_profile(manager: ProfileManager) -> None:
    manager.load("RECIPE_A")
    make_zip(manager.profiles / "broken.zip", {"other.ini": "[x]\r\n"})
    with pytest.raises(ProfileError, match=r"broken\.ini"):
        manager.load("BROKEN")
    assert manager.current == "RECIPE_A"
    assert (manager.work / "recipe_a.ini").is_file()
    (manager.profiles / "corrupt.zip").write_bytes(b"PK\x03\x04 not really a zip")
    with pytest.raises(ProfileError):
        manager.load("CORRUPT")
    assert sorted(p.name for p in manager.work.iterdir()) == ["model.png", "recipe_a.ini"]
    assert not [p for p in manager.work.parent.iterdir() if p.name.startswith(".")]   # staging removed


# ---------------------------------------------------------------------------------------------- zip safety
@pytest.mark.parametrize("member", ["../evil.txt", "a/../../evil.txt", "/abs.txt", "C:/evil.txt", "..\\evil.txt",
                                    "sub\\..\\..\\evil.txt"])
def test_traversal_is_rejected(manager: ProfileManager, tmp_path: Path, member: str) -> None:
    manager.load("RECIPE_A")
    make_zip(manager.profiles / "evil.zip", {"evil.ini": "[x]\r\n", member: "owned"})
    with pytest.raises(UnsafeArchive):
        manager.load("EVIL")
    assert not list(tmp_path.rglob("evil.txt"))
    assert manager.current == "RECIPE_A"
    assert (manager.work / "model.png").is_file()


def test_symlink_member_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "link.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(UnsafeArchive):
        safe_extract(archive, tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_safe_extract_keeps_sub_folders(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "ok.zip", {"a.ini": "x", "models/m1.png": b"1", "./b.txt": "b", "dir/": ""})
    written = safe_extract(archive, tmp_path / "out")
    assert sorted(p.relative_to(tmp_path / "out").as_posix() for p in written) == ["a.ini", "b.txt", "models/m1.png"]
    assert (tmp_path / "out" / "dir").is_dir()


def test_profile_names_are_checked(manager: ProfileManager) -> None:
    for bad in ("", "  ", "..", "a/b", "a\\b", "c:x", "x?", "dot."):
        with pytest.raises(ValueError):
            check_profile_name(bad)
    with pytest.raises(ValueError):
        manager.load("../recipe_a")
    assert check_profile_name(" Recipe 1 ") == "Recipe 1"


# ---------------------------------------------------------------------------------------------- save
def test_save_round_trip_keeps_other_files_and_untouched_lines(manager: ProfileManager) -> None:
    manager.load("RECIPE_A")
    (manager.work / "other.ini").write_text("[x]\n")                     # not this profile's: left out of the zip
    manager.set("general.wlmanual", 875)
    archive = manager.save()
    assert archive == manager.profiles / "recipe_a.zip"
    assert names_in(archive) == ["model.png", "recipe_a.ini"]
    ini = read_member(archive, "recipe_a.ini")
    assert ini == RECIPE_INI.replace("wlmanual_U32 = 850", "wlmanual_U32 = 875")
    assert not [p for p in manager.profiles.iterdir() if p.name.startswith(".")]  # no temporary file left
    manager.load("RECIPE_B")
    manager.load("RECIPE_A")
    assert manager.get("general.wlmanual") == 875
    assert (manager.work / "model.png").read_bytes() == b"\x89PNG model"


def test_save_under_a_new_name(manager: ProfileManager) -> None:
    manager.load("RECIPE_A")
    manager.set("autofocus.zstep", 0.02)
    archive = manager.save("Recipe C")
    assert archive == manager.profiles / "recipe c.zip"
    assert manager.current == "Recipe C"
    assert names_in(archive) == ["model.png", "recipe c.ini"]
    assert "zstep_DBL = 0,020000" in read_member(archive, "recipe c.ini")
    assert "zstep_DBL = 0,010000" in read_member(manager.profiles / "recipe_a.zip", "recipe_a.ini")
    assert manager.list() == ["RECIPE C", "RECIPE_A", "RECIPE_B"]


def test_zip_profile_folder_filters_ini_files(tmp_path: Path) -> None:
    folder = tmp_path / "f"
    (folder / "sub").mkdir(parents=True)
    (folder / "sub" / "nested.txt").write_text("x")
    for name in ("mine.ini", "MINE.INI.bak", "theirs.ini", "model.png"):
        (folder / name).write_text(name)
    archive = zip_profile_folder("Mine", folder, tmp_path / "out" / "mine.zip")
    assert names_in(archive) == ["MINE.INI.bak", "mine.ini", "model.png"]


# ---------------------------------------------------------------------------------------------- new / default profile
def test_new_from_defaults_file(manager: ProfileManager, tmp_path: Path) -> None:
    defaults = tmp_path / "default.ini"
    defaults.write_bytes(b"[general]\r\nwlmanual_U32 = 800\r\n[tilt]\r\nmethod_I32 = 1\r\n")
    manager.load("RECIPE_A")
    store = manager.new("Fresh", defaults)
    assert not any(manager.work.iterdir())                                 # "recreate current profile folder"
    assert manager.current == "Fresh"
    assert store.get("general.wlmanual") == 800
    assert store.get("general.exposures") == [list(row) for row in PROFILE_DEFAULT_TABLE]
    archive = manager.save()
    assert names_in(archive) == ["fresh.ini"]
    reloaded = manager.load("FRESH")
    assert reloaded.get("tilt.method") == 1
    assert reloaded.suffix_of("general.exposures") == "TABLE"
    assert len(reloaded.get("general.exposures")) == 11


def test_new_with_a_missing_template_keeps_the_active_profile(manager: ProfileManager, tmp_path: Path) -> None:
    manager.load("RECIPE_A")
    with pytest.raises(FileNotFoundError):
        manager.new("X", tmp_path / "missing.ini")
    assert manager.current == "RECIPE_A"
    assert (manager.work / "recipe_a.ini").is_file()


def test_new_from_mapping_and_check(manager: ProfileManager) -> None:
    manager.new("M", {"general.wlmanual": 700, "measure.tilt.enabled": True})
    assert manager.get("measure.tilt.enabled") is True
    assert manager.check({"general.wlmanual": 1, "autofocus.duration": 2.0}) == ["autofocus.duration"]
    assert manager.get("general.wlmanual") == 700


def test_ensure_default_profile(tmp_path: Path) -> None:
    settings = tmp_path / "settings"
    pm = ProfileManager(tmp_path / "profiles", settings / "profile", settings / "profile_temp")
    props = Properties.parse('[general]\r\nprofile_STR = "OLD"\r\n')
    assert pm.ensure_default_profile(properties=props)
    assert pm.list() == [DEFAULT_PROFILE]
    assert (tmp_path / "profiles" / "default.zip").is_file()
    assert names_in(tmp_path / "profiles" / "default.zip") == ["default.ini"]
    assert props.get("general.profile") == DEFAULT_PROFILE
    assert pm.current == DEFAULT_PROFILE
    assert not pm.ensure_default_profile(properties=props)


# ---------------------------------------------------------------------------------------------- edited copy
def test_edit_copy_does_not_touch_the_active_profile(manager: ProfileManager) -> None:
    manager.load("RECIPE_A")
    work_before = {p.name: p.read_bytes() for p in manager.work.iterdir()}
    edit = manager.load_copy("RECIPE_A")
    assert edit is not manager.properties
    assert edit.path is not None and edit.path.parent == manager.temp
    edit.set("general.wlmanual", 999)
    archive = manager.save_copy(edit, "RECIPE_A")
    assert archive == manager.profiles / "recipe_a.zip"
    assert manager.get("general.wlmanual") == 850                          # active profile unchanged
    assert manager.current == "RECIPE_A"
    assert {p.name: p.read_bytes() for p in manager.work.iterdir()} == work_before
    assert "wlmanual_U32 = 999" in read_member(archive, "recipe_a.ini")
    manager.load("RECIPE_A")
    assert manager.get("general.wlmanual") == 999


def test_edit_copy_of_another_profile_and_new_copy(manager: ProfileManager) -> None:
    manager.load("RECIPE_A")
    edit = manager.load_copy("RECIPE_B")
    edit.set("general.exposure", 1.0)
    manager.save_copy(edit, "RECIPE_D")
    assert names_in(manager.profiles / "recipe_d.zip") == ["recipe_d.ini"]  # recipe_b.ini left out
    fresh = manager.new_copy("RECIPE_E", {"general.wlmanual": 1})
    assert isinstance(fresh, ProfileProperties)
    manager.save_copy(fresh, "RECIPE_E")
    assert manager.current == "RECIPE_A"
    assert manager.list() == ["RECIPE_A", "RECIPE_B", "RECIPE_D", "RECIPE_E"]
    e = manager.load_copy("RECIPE_E")
    assert e.get("general.wlmanual") == 1 and e.get("general.exposures")


# ---------------------------------------------------------------------------------------------- delete / copy
def test_delete(manager: ProfileManager) -> None:
    assert manager.delete("recipe_b")
    assert not manager.delete("recipe_b")
    assert manager.list() == ["RECIPE_A"]


def test_copy_renames_the_ini(manager: ProfileManager) -> None:
    target = manager.copy("RECIPE_A", "Copy1")
    assert target == manager.profiles / "copy1.zip"
    assert names_in(target) == ["copy1.ini", "model.png"]
    assert read_member(target, "copy1.ini") == RECIPE_INI
    with pytest.raises(FileExistsError):
        manager.copy("RECIPE_B", "copy1")
    manager.copy("RECIPE_B", "copy1", overwrite=True)
    assert "wlmanual_U32 = 900" in read_member(target, "copy1.ini")
    with pytest.raises(ProfileError):
        manager.copy("missing", "x")


def test_concurrent_access(manager: ProfileManager) -> None:
    manager.load("RECIPE_A")
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(20):
                manager.set(f"thread.v{n}", i)
                if i % 10 == 0:
                    manager.save()
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    manager.save()
    manager.load("RECIPE_A")
    assert [manager.get(f"thread.v{n}") for n in range(4)] == [19] * 4
