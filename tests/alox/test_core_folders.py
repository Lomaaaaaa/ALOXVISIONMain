"""alox.core.folders: alox_setup.ini [folders] → Folders, boot folders (map Part 1 §4.4, Part 4 §3.10, §3.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from alox.core.folders import (
    FILE_SUBFOLDERS,
    FOLDER_DEFAULTS,
    IMAGE_SUBFOLDERS,
    FolderError,
    Folders,
    create_folders,
    resolve_folders,
    user_documents_folder,
)


def write_setup(folder: Path, body: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "alox_setup.ini"
    path.write_bytes(body.replace("\n", "\r\n").encode("cp1252"))
    return path


def full_setup(tmp_path: Path) -> Path:
    root = tmp_path / "D"
    return write_setup(tmp_path / "exe", f"""[folders]
vision= "{root}/alox/02-Vision"
data = "{root}/alox/02-Vision/data"
profiles = "{root}/alox/02-Vision/profiles"
settings = "{root}/alox/02-Vision/settings"
exposures = "{root}/alox/02-Vision/exposures"
jobhistory = "{root}/alox/04-JobHistory"
""")


def test_values_in_effect_come_from_the_setup_file(tmp_path: Path) -> None:
    setup = full_setup(tmp_path)
    warnings: list[str] = []
    f = resolve_folders(setup, env={}, platform="linux", warn=warnings.append)
    root = tmp_path / "D" / "alox"
    assert f.exe == setup.parent
    assert f.settings == root / "02-Vision" / "settings"
    assert f.vision == root / "02-Vision"
    assert f.data == root / "02-Vision" / "data"
    assert f.profiles == root / "02-Vision" / "profiles"
    assert f.exposures == root / "02-Vision" / "exposures"
    assert f.jobhistory == root / "04-JobHistory"
    assert warnings == []


def test_derived_folders_follow_the_boot_cycle_layout(tmp_path: Path) -> None:
    f = resolve_folders(full_setup(tmp_path), env={}, platform="linux")
    # the "alox\settings" / "alox\profile" constants are unwired in alox_core_boot_cycle d38..d43
    assert f.alox_ini == f.settings / "alox.ini"
    assert f.profile == f.settings / "profile"
    assert f.profile_temp == f.settings / "profile_temp"
    assert f.defaults_ini == f.exe / "alox_default.ini"
    assert f.logs == f.vision / "logs"
    assert f.chuck_calibration == f.data / "ChuckCalibration"
    assert f.images == f.data / "images"
    assert f.files == f.data / "files"
    assert f.data_jobhistory == f.data / "JobHistory"
    assert f.image_folder("Misc") == f.data / "images" / "Misc"
    assert f.file_folder("wavelength") == f.data / "files" / "wavelength"
    with pytest.raises(ValueError):
        f.image_folder("misc")
    with pytest.raises(ValueError):
        f.file_folder("../x")


def test_common_paths_map_through_alox_drive(tmp_path: Path) -> None:
    setup = write_setup(tmp_path / "exe", '[folders]\nsettings = "/D/alox/02-Vision/settings"\n'
                                          "data = D:\\alox\\data\n")
    drive = tmp_path / "drive_d"
    f = resolve_folders(setup, env={"ALOX_DRIVE_D": str(drive)}, platform="linux")
    assert f.settings == drive / "alox" / "02-Vision" / "settings"
    assert f.data == drive / "alox" / "data"
    # keys absent from the file take the resolver defaults (02-vision in lower case for vision, as in the VI)
    assert f.vision == drive / "alox" / "02-vision"
    assert f.profiles == drive / "alox" / "02-Vision" / "profiles"
    assert f.jobhistory == drive / "alox" / "02-Vision" / "JobHistory"


def test_windows_paths_are_converted_from_the_common_form(tmp_path: Path) -> None:
    f = resolve_folders(full_setup(tmp_path).parent.parent / "exe" / "alox_setup.ini", env={}, platform="win32")
    assert str(f.jobhistory).endswith("alox\\04-JobHistory")
    setup = write_setup(tmp_path / "exe2", '[folders]\nsettings = "/D/alox/02-Vision/settings"\n')
    f = resolve_folders(setup, env={}, platform="win32")
    assert str(f.settings) == r"D:\alox\02-Vision\settings"
    assert str(f.vision) == FOLDER_DEFAULTS["vision"]


def test_missing_file_gives_defaults_with_a_warning_and_is_not_created(tmp_path: Path) -> None:
    setup = tmp_path / "exe" / "alox_setup.ini"
    drive = tmp_path / "d"
    warnings: list[str] = []
    f = resolve_folders(setup, env={"ALOX_DRIVE_D": str(drive)}, platform="linux", warn=warnings.append)
    assert f.settings == drive / "alox" / "settings"
    assert f.data == drive / "alox" / "02-Vision" / "data"
    assert any("not found" in w for w in warnings)
    assert not setup.exists()


def test_empty_value_gives_the_default(tmp_path: Path) -> None:
    setup = write_setup(tmp_path / "exe", '[folders]\ndata =\nvision = ""\n')
    warnings: list[str] = []
    f = resolve_folders(setup, env={"ALOX_DRIVE_D": str(tmp_path)}, platform="linux", warn=warnings.append)
    assert f.data == tmp_path / "alox" / "02-Vision" / "data"
    assert f.vision == tmp_path / "alox" / "02-vision"
    assert len([w for w in warnings if "is empty" in w]) == 2


def test_documents_overrides_the_settings_folder(tmp_path: Path) -> None:
    f = resolve_folders(full_setup(tmp_path), tmp_path / "docs", env={}, platform="linux")
    assert f.settings == tmp_path / "docs"
    assert f.profile == tmp_path / "docs" / "profile"


def test_unmappable_settings_falls_back_to_user_documents(tmp_path: Path) -> None:
    root = tmp_path / "D"
    setup = write_setup(tmp_path / "exe", "\n".join(
        ["[folders]"] + [f'{k} = "{root}/{k}"' for k in FOLDER_DEFAULTS if k != "settings"]) + "\n")
    warnings: list[str] = []
    f = resolve_folders(setup, env={"HOME": str(tmp_path / "home")}, platform="linux", warn=warnings.append)
    assert f.settings == tmp_path / "home" / "Documents" / "alox" / "settings"
    assert any("cannot be mapped" in w for w in warnings)


def test_unmappable_data_folder_raises(tmp_path: Path) -> None:
    setup = write_setup(tmp_path / "exe", f'[folders]\nsettings = "{tmp_path}/s"\nvision = "{tmp_path}/v"\n'
                                          "data = D:\\alox\\data\n")
    with pytest.raises(FolderError, match=r"\] data = "):
        resolve_folders(setup, env={}, platform="linux")


def test_default_setup_location_is_the_executable_folder(tmp_path: Path) -> None:
    exe = full_setup(tmp_path).parent
    f = resolve_folders(None, env={"ALOX_EXE_DIR": str(exe)}, platform="linux")
    assert f.exe == exe
    assert f.jobhistory == tmp_path / "D" / "alox" / "04-JobHistory"


def test_user_documents_folder() -> None:
    assert user_documents_folder({"USERPROFILE": r"C:\Users\op"}, "win32") == Path(r"C:\Users\op") / "Documents"
    assert user_documents_folder({"HOME": "/home/op"}, "linux") == Path("/home/op/Documents")


def make_folders(tmp_path: Path) -> Folders:
    return resolve_folders(full_setup(tmp_path), env={}, platform="linux")


def test_create_folders_creates_the_boot_folders_once(tmp_path: Path) -> None:
    f = make_folders(tmp_path)
    created = create_folders(f)
    assert created == list(f.boot_folders())
    assert created[:5] == [f.settings, f.profile, f.profile_temp, f.data, f.profiles]
    assert created[-1] == f.data_jobhistory
    for name in IMAGE_SUBFOLDERS:
        assert (f.images / name).is_dir()
    for name in FILE_SUBFOLDERS:
        assert (f.files / name).is_dir()
    assert f.logs.is_dir() and f.chuck_calibration.is_dir()
    # disabled in V1.2.6.2 (inner frame 7) / created on demand by the job-tree VIs
    assert not f.exposures.exists()
    assert not f.jobhistory.exists()
    assert create_folders(f) == []


def test_create_folders_reports_only_new_folders(tmp_path: Path) -> None:
    f = make_folders(tmp_path)
    f.images.mkdir(parents=True)
    created = create_folders(f)
    assert f.images not in created
    assert f.images / "Misc" in created


def test_create_folders_fails_on_a_file_in_the_way(tmp_path: Path) -> None:
    f = make_folders(tmp_path)
    f.settings.mkdir(parents=True)
    (f.settings / "profile").write_text("not a folder")
    with pytest.raises(OSError):
        create_folders(f)
