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

## Round 2 (asked 2026-11-26, from the measurement functions)

| # | Question | Default meanwhile | Source |
|---|---|---|---|
| R1 | Bowing "Fast" method always ends in Error in V1.2.6.2: what should it produce? | ends in Error | functions/bowing.py |
| R2 | End of bowing: wavelength to `wavelength.wlmanual` (LabVIEW) or the initial one? | `wlmanual` | functions/bowing.py |
| R3 | `bowing_en.lang` autofocus error texts 1012–1019 in another point order than the codes: correct the texts? | texts to correct | functions/bowing.py |
| R4 | Search Object "Found Object?" compares a 0–1000 score with a % (always true): compare properly? | as LabVIEW | functions/search_object.py |
| R5 | Search Object refocus retries: 5 (wiring) or 3 (comment)? | 5 | functions/search_object.py |
| R6 | Search Object Offset X/Y in stage coordinates? | stage coordinates | functions/search_object.py |
| R7 | Search Object refuses to start when the monochromator is down? | kept | functions/search_object.py |
| R8 | Detection methods used in production (Shape / Blobs / Object learning; Mesa Shape Line or Rectangle; rotation)? | Shape and Blobs | functions/detect_circle.py |
| R9 | Did Refocus Oxyd ever work (empty ROI in the camera server)? New: the ring's bounding square | new behaviour | functions/refocus_oxyd.py |
| R10 | Recipe values of `measure.vcsel.maxMesaDiam` (default 0) and `tracking.diameter` (0.2) | recipe values | functions/measure_oxyd.py, tracking.py |
| R11 | Auto Exposure `bypassCircleIn` TRUE measures inside the circle in the diagram: intended? | as the diagram | functions/auto_exposure.py |
| R12 | Auto Exposure brightness line through (0, 0) with measure 2 only: use both measures? | as LabVIEW | functions/auto_exposure.py |
| R13 | Search Wavelength with no table wavelength in [Start, End]? | nearest table wavelength | functions/search_wavelength.py |
| R14 | Are the `.ae` / `.wal` files read by other tools or people? | kept | functions/auto_exposure.py |
| R15 | Get Image digital Gain ever ≠ 1? | 1 | functions/get_image.py |
| R16 | Recorded images with LabVIEW's results (Search Object, measure_mesa) and recipe model/mask PNGs, to validate the image algorithms | synthetic tests only | vision/edges.py, matching.py |
| R17 | NI Vision run-time licence kept on the machines, and `nivision.h`, to finish the exact NI pattern matcher? | OpenCV matcher | vision/matching.py |
| R18 | Continuous mesa job: after an error LabVIEW sends `failed` but restarts; keep, or end the job? | restart | functions/mesa.py |
| R19 | Stop of a continuous mesa job sends `failed` (LabVIEW), not `finished`: keep? | `failed` | functions/mesa.py |
| R20 | Send `measure_mesa` also for cycles where the mesa or oxide was not found? | only found ones | functions/mesa.py |
| R21 | Recipe EXPOSURE MODE value 1 meaning; Oxyd Target key `measure.oxyd.oxydtarget` or `measure.mesa.oxydtarget`? | auto exposure; both read | functions/mesa.py |
| R22 | Tilt Dual Objects: remove object 1 before searching object 2 (our reading) or keep only its box (literal)? Same model for both? | object 1 removed | functions/tilt.py |
| R23 | Tilt object Min Score (%) ×10 against NI's 0–1000 score (LabVIEW used it unscaled)? | ×10 | functions/tilt.py |
| R24 | Tilt and edge methods used in recipes; a recorded tilt image with LabVIEW's angle (sign convention) | all ported | vision/tilt.py |
| R25 | Save tilt images only for a valid tilt? | every attempt | functions/tilt.py |
| R26 | Retry tilt autofocus/refocus errors as bowing does? | ends at once | functions/tilt.py |
| T1 | Thermal map still used? Which probe on which machine (Optris CT / CTlaser / CSlaser; kSA BandiT)? | both ported | thermal/ |
| T2 | BandiT: device at the Modbus address, 0–5 V ↔ 300–500 °C scale, non-zero `minVoltage`? | LabVIEW's values | thermal/bandit.py |
| T3 | A CSV exported by LabVIEW after a full thermal measurement | as read in the diagrams | thermal/table.py |
| T4 | Keep the external 3D script (`readdata_arg.py`) or a picture made by the application; after each measurement or on EXPORT? | script on EXPORT | thermal/plot.py |
| T5 | Thermal points by angle (LabVIEW, circles mixed) or circle by circle? | by angle | thermal/table.py |
| T6 | Refuse the other functions and the supervisor while the thermal tool is open? | not decided; nothing refused | thermal/controller.py |

### Round 2 answers (2026-11-26)

| # | Owner's answer | Status |
|---|---|---|
| R1 | Build the Fast bowing: measure 3 points, compute the bowing from them | Done: ZW2, ZW3, ZW1 → Bowing A, then Finished (confirm "zc1 2 3" meant these wafer points) |
| R7 | Remove the monochromator check from Search Object and all Low Mag functions | Done |
| R8 | Blobs not used any more; Shape and Image (object learning, in Python); mesa shapes Line, Rectangle and Circular all used | Done: Blobs removed (old recipes run Shape), Line and Rectangle measured, object learning with rotation |
| R9 | Refocus Oxyd never worked well; keep the mechanism | Kept |
| R10 | `measure.vcsel.maxMesaDiam` default 40 | Done |
| R11 | As the diagram | Kept |
| R12 | (0, 0) plus a second measure is enough; to be rebuilt later | Kept |
| R13 | A recipe always has a wavelength table | Kept |
| R14 | Keep the `.ae` / `.wal` files | Kept |
| R15 | Gain often 2, 3, 4 or more; better correlated to the camera gain | Done: camera gain = alox.ini base gain × recipe gain, digital fallback |
| R16 | Recorded data: owner will send it | Reminder set for 2026-11-27 |
| R18 | As LabVIEW: a continuous job never fails, it always retries | Kept |
| R19 | Not known | Kept |
| R22–R24 | Tilt: build a better method | To do |
| R25 | Save tilt images at every attempt | Kept |
| T3, T5 | The thermal tool will be built differently later | No more work on it for now |
| T4 | Export the 3D map and also display it in the software | To do with the thermal panel |

### Round 3 (to ask, from the Line/Rectangle work)

| # | Question | Default meanwhile | Source |
|---|---|---|---|
| S1 | Rectangle mesas: LabVIEW ran an external `mesure_edges.py` (not in the export). Can you send it, or confirm the definitions used (area, equivalent and mean radial diameters, minimum-area rectangle)? | native equivalent | functions/measure_oxyd.py |
| S2 | Rectangle: LabVIEW's Mean is 0, so the default measure type gives nothing. Which Oxyd Measure Type do you use for Rectangle (Fit = equivalent diameter, Surface = height)? | as LabVIEW | functions/measure_oxyd.py |
| S3 | Line: is Median = oxidised depth per side what you use, and which measure type? | as LabVIEW | functions/measure_oxyd.py |
| S4 | Object learning searches 0–360° (≈1.4 s on a full image): acceptable, or recommend a downsampling? | 0–360° | functions/detect_circle.py |
