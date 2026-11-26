# Questions for the owner (Vision Core migration)

The owner answered the questionnaire on 2026-11-26 (shared doc "ALOX Vision Core: questions for the owner"). This file
records the answers and what the code does with them; the questions still open are at the end. Each open question has
a default (the LabVIEW behaviour, unless noted) that a setting or a one-line change can switch.

## Decided

| # | Question (short) | Owner's answer | What the code does |
|---|---|---|---|
| H2 | XY and Z stages | PI **L-511** (XY, two C-663 on a daisy chain) and **L-509** (Z) | Stage models noted; the daisy-chain variant still comes from `tools/xy_stage_check.py` |
| H3 | Monochromator model | **CM110** | CM11x protocol; CM110 range |
| H4 | Cameras | Both **USB and GigE** cameras must work; the LED and bulb control mapping depends on the interface | GenICam backend for both interfaces (USB3 Vision and GigE Vision producers); LED/bulb mapping per interface |
| B1 | XY move check radius | **80 mm** by default, can be raised to **105 mm** | Setting `[hardware.xy] moveradius_DBL` (default 80, at most 105) |
| B2 | XY supervision at the wafer limit | **Do not stop the stage, but do not let it go any further** | "Soft wall": when the joystick reaches the limit the stage is held on it; moves back inside stay free |
| B3 | Wait after a wavelength change | **Wait for a new image according to the exposure** | The first image used is one exposed entirely after the change (wait old + new exposure, then the newest frame) |
| B4 | Monochromator Reset/Scan/Calibrate/Select Grating | **Maintenance tool only** | Not used by the functions |
| B5 | Log milliseconds separator, log window | Owner lets us decide | `,` (Windows French locale, as LabVIEW); sliding window of the last lines |
| B6 | User after boot | **Operator** (the default user) | Operator; passwords behind a replaceable store |
| B7 | Bowing chuck step names | ZC1/ZC4/ZC5 | As decided |
| B8 | Fast autofocus latency | **Do better if possible, or give a procedure to measure it** | Latency measured automatically from two scans in opposite directions (tool and procedure) |
| B9 | Autofocus end position | **Move to the best Z** | Autofocus ends at the best Z (changed from LabVIEW) |
| B10 | Profile name case | As read (`DEFAULT` ↔ `default.zip`) | Kept |
| B11 | Monochromator range | 1500 nm × 1200 / grooves is the real limit | Kept; move timeout 30 s |
| B12 | Camera identification | Cameras named **HighMag** and **LowMag** by the owner; prefer names chosen by the owner over serial numbers | Cameras are found by their user-defined name (GenICam `DeviceUserID`), serial number as fallback |
| M1–M4, M6–M8 | Formulas | As in the diagrams | Kept |
| M5 | Error 9003, zero slope | Not used, no preference | Kept as implemented |
| M9 | Minimum-zone fit | **Use a better algorithm** | Exact roundness fits (ISO 12181: minimum zone, minimum circumscribed, maximum inscribed) replace LabVIEW's fit |
| D1 | Production exposure tables | Not known | LabVIEW's default table (from 700 nm, see D4) |
| D4 | 0 nm row of the default exposure table | **Not used; the lowest wavelength by default is 700 nm** | Default table starts at 700 nm; default scans start at 700 nm |
| L1 | Licence check at boot | **No check**; a new software lock of our own design is welcome | No check by default; a new signed licence file bound to the PC, off until enabled |
| L2 | Dongle back-office tools | A **licence generator** is wanted; it can differ from the old one | A generator for the new licence files |
| I1 | ZOOM | Centre crop | Kept |
| I2 | Result overlays after a function | Owner lets us decide | Result overlays stay on the live display until the next function starts |
| I3 | Overlay switches from the API | Not saved in the recipe | Kept |
| I4 | Stream rate | As LabVIEW | No cap |
| I5 | LED camera | As LabVIEW | Kept |
| I6 | Exposure units | Microseconds | Kept |
| I7 | Default overlays | Get Image ones off, others on | Kept |
| I8 | Brightness/contrast/gamma | A close equivalent; LabVIEW's gave bad results | Own implementation, not NI's table |

## Still open

| # | Question | Default meanwhile | Source |
|---|---|---|---|
| H1 | Z stage: results of `tools/z_stage_check.py` (information, then motion) | camera-server Z behaviour | docs/phase2-notes.md |
| H2 | XY daisy chain: address form and single-character mode (`target_only` / `target_sender`; `#5`/`#7` or `SRG?`): results of `tools/xy_stage_check.py` | `target_only`, addressed single characters | xy-stage.md Q2 |
| H3 | Monochromator: gratings and COM port, protocol details of Appendix K: results of `tools/wl_check.py` | CM11x protocol as read in the diagrams | monochromator.md Q1, Q2, Q8 |
| H4 | Cameras: results of `tools/camera_check.py` on each camera (feature names, jumbo frames, I/O line values, producer path) | IDS GigE, Mono8 | camera_genicam.py |
| D2 | Stage directions (coordinate system #4), chuck origin, `xaxis_I32` (1 or 10) on each tool: a complete `alox.ini` of a machine (without passwords) answers it | INI values | xy-stage.md Q9, Q10 |
| D3 | A real recipe `.zip` and the `alox.ini` / `alox_setup.ini` of a machine (owner will forward them) | format and layout as read in the diagrams | core/profiles.py, core/folders.py |
