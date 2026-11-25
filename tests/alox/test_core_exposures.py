"""alox.core.exposures: the wavelength → exposure table (alox_manager_exposure_wl_lut; map Part 1 §4.2, Part 4 §3.9)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest

from alox.core.exposures import (
    AE_DATA_NOT_AVAILABLE,
    DEFAULT_EXPOSURES_US,
    DEFAULT_WAVELENGTHS,
    INVALID_WAVELENGTH,
    PROFILE_DEFAULT_TABLE,
    AutoExposureData,
    ExposureError,
    ExposureRow,
    ExposureTable,
    default_table,
    interpolate,
    read_csv,
    upsert_auto_expo,
    write_csv,
)
from alox.core.profiles import ProfileProperties

nan = math.nan


class FakeProfile:
    def __init__(self, values: dict[str, Any] | None = None) -> None:
        self.values = dict(values or {})

    def get(self, dotted: str, default: Any = None) -> Any:
        return self.values.get(dotted, default)

    def set(self, dotted: str, value: Any) -> None:
        self.values[dotted] = value


def same(a: list[float], b: list[float]) -> bool:
    return len(a) == len(b) and all((math.isnan(x) and math.isnan(y)) or x == y for x, y in zip(a, b, strict=True))


def small_table() -> list[list[float]]:
    return [[700, 1000, 800, 1200, 50, 100, 1100],
            [750, 2000, 1600, 2400, 60, 110, 2100],
            [800, 4000, 3200, 4800, 70, 120, 4100]]


# ---------------------------------------------------------------------------------------------- defaults
def test_default_tables() -> None:
    assert DEFAULT_WAVELENGTHS == (0, *range(700, 951, 25))
    assert len(DEFAULT_EXPOSURES_US) == 12
    assert (DEFAULT_EXPOSURES_US[0], DEFAULT_EXPOSURES_US[1], DEFAULT_EXPOSURES_US[-1]) == (27000, 18000, 628000)
    table = default_table()
    assert len(table) == 12
    assert same(table[0], [0, 27000, 21600, 32400, nan, nan, nan])
    assert same(table[7], [850, 132000, 105600, 158400, nan, nan, nan])
    assert table[6][2:4] == [69680.0, 104520.0]                            # 87100 ∓ 20 %
    assert same(default_table((700,), (1000,), 150)[0], [700, 1000, 0, 2000, nan, nan, nan])   # % coerced to 100
    assert len(PROFILE_DEFAULT_TABLE) == 12 and all(len(r) == 7 for r in PROFILE_DEFAULT_TABLE)
    assert PROFILE_DEFAULT_TABLE[1] == (700.0, 25000.0, 10000.0, 40000.0, 50.0, 100.0, 25000.0)
    assert PROFILE_DEFAULT_TABLE[-1][:2] == (950.0, 750000.0)


# ---------------------------------------------------------------------------------------------- interpolation
WLS = [700.0, 750.0, 800.0]
DATA = [1000.0, 2000.0, 4000.0]


def test_interpolation_between_points() -> None:
    r = interpolate(725, WLS, DATA)
    assert (r.value, r.interpolated, r.index) == (1500.0, True, 1)
    r = interpolate(790, WLS, DATA)
    assert (r.value, r.interpolated, r.index) == (3600.0, True, 2)


def test_interpolation_exact_points() -> None:
    assert interpolate(750, WLS, DATA) == interpolate(750.0, WLS, DATA)
    r = interpolate(750, WLS, DATA)
    assert (r.value, r.interpolated, r.index) == (2000.0, False, 1)


def test_interpolation_is_clamped_below_and_above() -> None:
    for wl in (0, 699.9, 700):
        r = interpolate(wl, WLS, DATA)
        assert (r.value, r.interpolated, r.index) == (1000.0, False, 0)
    for wl in (800, 801, 5000):
        r = interpolate(wl, WLS, DATA)
        assert (r.value, r.interpolated, r.index) == (4000.0, False, 2)


def test_interpolation_invalid_arrays() -> None:
    for wls, data in (([], []), (WLS, DATA[:2])):
        r = interpolate(750, wls, data)
        assert math.isnan(r.value) and not r.interpolated and r.index == -1
    r = interpolate(750, [700.0], [5.0])
    assert (r.value, r.index) == (5.0, 0)


def test_interpolation_of_an_unsorted_table_is_not_sorted() -> None:
    # as LabVIEW: first/last tests, then the first i with WL <= WL[i] (no sort): 750 → between 700 and 900
    r = interpolate(750, [700.0, 900.0, 800.0], [1.0, 3.0, 5.0])
    assert (r.value, r.interpolated, r.index) == (1.5, True, 1)


# ---------------------------------------------------------------------------------------------- table methods
def test_create_default_and_exposure_for() -> None:
    profile = FakeProfile()
    lut = ExposureTable(profile)
    assert not lut.has_fixed_expo()
    with pytest.raises(ExposureError):
        lut.exposure_for(800)
    assert lut.create_default() == list(DEFAULT_WAVELENGTHS)
    assert "general.exposures" in profile.values
    assert lut.exposure_for(700) == 18000
    assert lut.exposure_for(712.5) == 19000
    assert lut.exposure_for(350) == pytest.approx(27000 + 350 * (18000 - 27000) / 700)   # the 0 nm row
    assert lut.exposure_for(2000) == 628000
    assert lut.has_fixed_expo()
    assert not lut.has_auto_expo()
    assert isinstance(lut.rows()[1], ExposureRow) and lut.rows()[1].expo_th == 18000


def test_fixed_expo_get_set() -> None:
    lut = ExposureTable(FakeProfile({"general.exposures": small_table()}))
    assert lut.fixed_expo(750) == 2000
    lut.set_fixed_expo(750, 2500)
    assert lut.table()[1] == [750, 2500, 1600, 2400, 60, 110, 2100]      # only ExpoTh changes
    with pytest.raises(ExposureError) as err:
        lut.fixed_expo(760)
    assert err.value.code == INVALID_WAVELENGTH
    with pytest.raises(ExposureError):
        lut.set_fixed_expo(760, 1)


def test_auto_expo_get_set_reset() -> None:
    profile = FakeProfile({"general.exposures": small_table()})
    lut = ExposureTable(profile)
    assert lut.has_auto_expo()
    expo, ae = lut.auto_expo(725)
    assert expo == 1500
    assert ae == AutoExposureData(auto_expo=1600, expo_min=1200, expo_max=1800, brightness_min=55,
                                  brightness_max=105)
    lut.reset_auto_expo()
    assert same(lut.table()[0], [700, 1000, 800, 1200, nan, nan, nan])
    assert not lut.has_auto_expo()
    expo, ae = lut.auto_expo(725)
    assert expo == 1500 and math.isnan(ae.auto_expo) and ae.expo_min == 1200
    with pytest.raises(ExposureError) as err:
        ExposureTable(FakeProfile()).auto_expo(700)
    assert err.value.code == AE_DATA_NOT_AVAILABLE


def test_set_auto_expo_existing_wavelength() -> None:
    lut = ExposureTable(FakeProfile({"general.exposures": small_table()}))
    lut.set_auto_expo(750, AutoExposureData(2222, 1111, 3333, 40, 90))
    assert lut.table()[1] == [750, 2000, 1111, 3333, 40, 90, 2222]


def test_set_auto_expo_new_wavelength_is_inserted_sorted() -> None:
    table = upsert_auto_expo(small_table(), 725, AutoExposureData(1500, 1200, 1800, 50, 100))
    assert [row[0] for row in table] == [700, 725, 750, 800]
    assert table[1] == [725, 1500, 1200, 1800, 50, 100, 1500]
    table = upsert_auto_expo(small_table(), 900, AutoExposureData(9, 8, 10, 1, 2))
    assert table[-1] == [900, 9, 8, 10, 1, 2, 9]
    table = upsert_auto_expo(small_table(), 650, AutoExposureData(9, 8, 10, 1, 2))
    assert table[0][0] == 650
    with pytest.raises(ExposureError) as err:
        upsert_auto_expo([], 700, AutoExposureData(1, 1, 1, 1, 1))
    assert err.value.code == INVALID_WAVELENGTH
    with pytest.raises(ExposureError):
        upsert_auto_expo([[700, 1000]], 700, AutoExposureData(1, 1, 1, 1, 1))


def test_has_auto_expo_rules() -> None:
    table = small_table()
    table[2][6] = math.inf
    assert not ExposureTable(FakeProfile({"general.exposures": table})).has_auto_expo()
    assert not ExposureTable(FakeProfile({"general.exposures": [[700, 1000]]})).has_auto_expo()
    assert ExposureTable(FakeProfile({"general.exposures": [[700, 1000]]})).has_fixed_expo()


def test_save_calib_values() -> None:
    lut = ExposureTable(FakeProfile({"general.exposures": small_table()}))
    lut.save_calib_values()
    assert [row[1] for row in lut.table()] == [1100, 2100, 4100]


def test_wavelengths_get_set() -> None:
    lut = ExposureTable(FakeProfile({"general.exposures": small_table()}))
    assert lut.wavelengths() == [700, 750, 800]
    lut.set_wavelengths([701, 751, 801])                                   # same size: other columns kept
    assert lut.table()[0] == [701, 1000, 800, 1200, 50, 100, 1100]
    lut.set_wavelengths([600, 650])                                        # other size: NaN table
    assert same(lut.table()[1], [650, nan, nan, nan, nan, nan, nan])
    lut.store([[700.5, 1], [701.5, 1], [nan, 1], [-3, 1]])
    assert lut.wavelengths() == [700, 702, 0, 0]                           # round half to even, saturated


# ---------------------------------------------------------------------------------------------- CSV
def write_text(path: Path, text: str) -> Path:
    path.write_bytes(text.encode("cp1252"))
    return path


def test_csv_two_columns(tmp_path: Path) -> None:
    table = read_csv(write_text(tmp_path / "e.csv", "700\t1000\r\n750\t2000\r\n"))
    assert same(table[0], [700, 1000, 800, 1200, nan, nan, nan])
    assert same(table[1], [750, 2000, 1600, 2400, nan, nan, nan])


def test_csv_four_and_seven_columns(tmp_path: Path) -> None:
    table = read_csv(write_text(tmp_path / "e.csv", "700\t1000\t900\t1100\n"))
    assert same(table[0], [700, 1000, 900, 1100, nan, nan, nan])
    table = read_csv(write_text(tmp_path / "e.csv", "700\t1000\t900\t1100\t50\t100\t1050\n"))
    assert table[0] == [700, 1000, 900, 1100, 50, 100, 1050]
    table = read_csv(write_text(tmp_path / "e.csv", "700\t1000\t900\t1100\t50\n"))      # 5 columns: B* ignored
    assert same(table[0], [700, 1000, 900, 1100, nan, nan, nan])


def test_csv_column_rules_and_formats(tmp_path: Path) -> None:
    with pytest.raises(ExposureError):
        read_csv(write_text(tmp_path / "e.csv", "700\n750\n"))
    text = "WL;ExpoTh\n\n700,5;1000,25\n750;2000;1500;2500\n"           # header, blank line, comma, short row
    table = read_csv(write_text(tmp_path / "e.csv", text))
    assert table[0][:4] == [700.5, 1000.25, 0.0, 0.0]                      # padded cells read 0 (LabVIEW)
    assert table[1][:4] == [750, 2000, 1500, 2500]
    table = read_csv(write_text(tmp_path / "e.csv", "700,1000\n"))
    assert table[0][:2] == [700, 1000]
    table = read_csv(write_text(tmp_path / "e.csv", "700\t1000\t\t\n"))           # trailing delimiters
    assert same(table[0], [700, 1000, 800, 1200, nan, nan, nan])
    with pytest.raises(ValueError, match="row 2"):
        read_csv(write_text(tmp_path / "e.csv", "700\t1000\n750\tabc\n"))


def test_csv_export_and_round_trip(tmp_path: Path) -> None:
    lut = ExposureTable(FakeProfile())
    lut.create_default()
    path = lut.export_csv(tmp_path / "out.csv")
    lines = path.read_bytes().decode("cp1252").split("\r\n")
    assert lines[0] == "0\t27000\t21600\t32400\tNaN\tNaN\tNaN"
    assert lines[-1] == ""
    write_csv([[700.4, 1000.6]], tmp_path / "r.csv")
    assert (tmp_path / "r.csv").read_bytes() == b"700\t1001\r\n"
    other = ExposureTable(FakeProfile())
    assert other.import_csv(path) == list(DEFAULT_WAVELENGTHS)
    assert all(same(a, b) for a, b in zip(other.table(), lut.table(), strict=True))


# ---------------------------------------------------------------------------------------------- profile storage
def test_table_is_stored_in_the_profile_property(tmp_path: Path) -> None:
    store = ProfileProperties(tmp_path / "recipe.ini")
    lut = ExposureTable(store)
    lut.create_default()
    lut.set_auto_expo(725, AutoExposureData(21000, 18000, 24000, 50, 200))
    store.save()
    text = (tmp_path / "recipe.ini").read_bytes().decode("cp1252")
    assert text.startswith('[general]\r\nexposures_TABLE.<size(s)> = "12 7"\r\nexposures_TABLE 0 = 0,000000\r\n')
    reloaded = ExposureTable(ProfileProperties.read(tmp_path / "recipe.ini"))
    assert all(same(a, b) for a, b in zip(reloaded.table(), lut.table(), strict=True))
    assert reloaded.auto_expo(725)[1].auto_expo == 21000
    assert reloaded.exposure_for(725) == 20000
