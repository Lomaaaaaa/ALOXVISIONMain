# Open questions for the owner (Vision Core migration)

Questions collected while building the integrated application. The implementation does not wait for them: each has a
default (the LabVIEW behaviour, unless noted) that a setting or a one-line change can switch. They are ordered by impact.
The detailed context is in the referenced documents.

## Hardware checks at the fab (answered by running the check tools)

| # | Question | Default meanwhile | Source |
|---|---|---|---|
| H1 | Z stage: results of `tools/z_stage_check.py` (information, then motion) | camera-server Z behaviour | docs/phase2-notes.md |
| H2 | XY daisy chain (C-663): which address form and single-character mode the C-663 chain accepts (`target_only` / `target_sender`, `#5`/`#7` addressed or `SRG?`), and the stage models (`CST?`) | `target_only`, addressed single characters | `tools/xy_stage_check.py`, xy-stage.md Q2 |
| H3 | Monochromator model (CM110 or CM112), gratings, COM port; protocol details of Appendix K of its manual | CM11x protocol as read in the diagrams | `tools/wl_check.py`, monochromator.md Q1, Q2, Q8 |
| H4 | Cameras: GenICam feature names on the GV-528x-M (`ExposureTime` µs, `Gain` as a factor so the INI gain type should be DBL, frame-rate enable, `DeviceTemperature` selector), Mono8 at 2448×2048, jumbo frames for packet size 8996, the integer values of the I/O line entries and the LED/bulb wiring, the IDS GenTL producer path on the PC, and the camera settings to save as a UserSet (NI MAX camera files are not used any more) | IDS GigE, Mono8, names mapped from IMAQdx | `tools/camera_check.py`, camera_genicam.py docstring |

## Behaviour to decide

| # | Question | Default meanwhile | Source |
|---|---|---|---|
| B1 | XY move check: which radius does the deployed AloxXYServer.exe use (80 mm, 105 mm, or diameter/2 + 10)? Test: a move 90 mm from the wafer origin. Which rule do you want? | 80 mm (setting `move_radius_mm`) | xy-stage.md Q4 |
| B2 | XY supervision: should it use the same radius as the move check? Should it switch the joystick off when it stops the stage (LabVIEW's code for that is disabled)? | diameter/2; joystick left on (setting) | xy-stage.md Q5, Q6 |
| B3 | Wait after a wavelength change: `2 × max(old, new exposure)` (changelog) or `100 ms + 0.11 × max` (Get Image)? | to be applied by the core; not decided | monochromator.md Q5 |
| B4 | Are the monochromator's Reset, Scan, Calibrate and Select Gratings needed in production, or only as maintenance tools? | maintenance methods only | monochromator.md Q4 |
| B5 | Log: milliseconds separator in the log lines (`,` from the Windows locale?), and should the log window keep a sliding window of the last lines (new) or be emptied when full (LabVIEW)? | `,`; sliding window | core/log.py |
| B6 | Which user is logged in after boot (LabVIEW: Operator), and should passwords keep the Base64 storage? | Operator; Base64 behind a replaceable store | map Part 1 Q4, decision 5 |
| B7 | Bowing: `bowing_en.lang` names the chuck steps SW/SE/NW "ZC3" (codes 207/209/211): are they ZC1/ZC4/ZC5? | ZC1/ZC4/ZC5 | core/status.py |
| B8 | Fast autofocus: the Z estimate included the TCP delay; the latency compensation must be measured on the machine | 0 (LabVIEW's formula) | functions/focus.py, docs/06 D16 |
| B9 | Autofocus: the stage is not moved to the best Z at the end (as the camera server); confirm | not moved | docs/06 Q22 |
| B10 | Profile names: listed upper-case, files lower-case (`DEFAULT` ↔ `default.zip`)? | as read in the diagrams | core/profiles.py |
| B11 | Monochromator range: the upper limit 1500 nm at 1200 gr/mm (scaled by the grating) comes from a disabled LabVIEW check. Is it the instrument's real limit? How long does a GOTO across the range take (move timeout 30 s, new optional key `movetimeout`)? | 1500 nm × 1200 / grooves; 30 s | devices/monochromator.py, monochromator.md Q3 |
| B12 | Cameras: the real `alox_GiGeIDS.ini` names the High Mag camera `cam2` (an NI MAX alias). Which serial number is the High Mag and which the Low Mag on each tool? | `--name-map` in the check tool | tools/camera_check.py |

## Measurement formulas

Read in the diagrams by the formula work (`alox.vision`); where the diagram and the map disagree, the diagram is implemented and the limits are parameters.

| # | Question | Default meanwhile | Source |
|---|---|---|---|
| M1 | NaN filter: the second pass keeps `[median − 1σ, median + 2σ]` (5 samples or fewer: `[mean − 1σ, mean + 2σ]`), the third pass (mean ± 2σ) runs only when `round(σ) ≠ 0`. Keep these asymmetric bands, or make them symmetric? | as in the diagram | vision/stats.py |
| M2 | Standard deviation of a single sample: 0 (the measurement is kept) or NaN (single-image averages become NaN)? | 0 | vision/stats.py |
| M3 | Circularity: circumscribed / inscribed (matches the recorded `measure_mesa.txt`, 1.255618), or (circumscribed − inscribed) / inscribed (a subtraction also appears in `d40`)? | ratio | vision/circles.py |
| M4 | The perimeter VI returns P without `/π` and no report field uses it: was a "perimeter diameter" P/π ever intended? | P, unused | vision/geometry.py |
| M5 | Oxide-rate fit: LabVIEW never raises 9003 (disabled frame). Raise it when the fit window has fewer than 10 points? Remaining time when the slope is exactly 0? | 9003 below 10 points | vision/stats.py |
| M6 | Tracking: the sign convention of `IMAQ Get Angles` (Y up or down) and the stage coordinate-system number of each machine (a required parameter now) | from `d5…d21` | vision/wafer.py |
| M7 | Bowing: validity is signed (`bow ≤ max`, so a large negative bow passes): intended? The nine bowing positions are not implemented yet; the chuck point labels NE/SW/SE/NW are inferred | signed; 5 points | vision/wafer.py |
| M8 | "# Samples Oxyd" counts the series of the selected measure type? | selected type | vision/stats.py |
| M9 | In measure-mesa (`d40`) the circumscribed and inscribed diameters come from the minimum-zone fit (outer and inner circles), Elzinga–Hearn and Delaunay only when that fit fails; the minimum-zone cost is `Σ(d−R_out)² + Σ(d−R_in)²`, not the zone width. Confirm this is the intended fit | as in the diagram | vision/circles.py |

## Data

| # | Question | Default meanwhile | Source |
|---|---|---|---|
| D1 | The production wavelength/exposure table per tool (the default one is marked "not suited to the OPTO cameras") | LabVIEW's default table | monochromator.md Q7 |
| D2 | Are the stage directions (coordinate system #4) and the chuck origin still valid on every delivered tool? Which `xaxis_I32` (1 or 10) is meant? | INI values | xy-stage.md Q9, Q10 |
| D3 | A real recipe `.zip` and the `alox.ini` / `alox_setup.ini` of a machine (without secrets): to confirm how the exposure table is stored in a recipe and the folder layout (the diagrams put `alox.ini`, `profile` and `profile_temp` directly in `[folders] settings`, not in `Documents\alox\…`) | format and layout as read in the diagrams | core/profiles.py, core/folders.py |
| D4 | The default exposure tables start with a 0 nm row (zero order?): intended? | kept | core/exposures.py, monochromator.md Q6 |

## Licence (phase 12; nothing is built until decided)

| # | Question | Default meanwhile | Source |
|---|---|---|---|
| L1 | Keep a licence check at boot? LabVIEW refuses to boot without a WireFlow USB dongle (gen2 keys) or a "virtual dongle" file bound to the PC. The virtual dongle uses the NI Vision run-time serial and the NI License Manager computer ID, which disappear once NI is no longer installed. Options: (a) no check; (b) keep the WireFlow dongle through its vendor library; (c) a new licence file bound to the PC (drive serial / machine ID) and signed with a private key held by Aloxtec | no check (boot is not locked) | map Part 6 §2.2 |
| L2 | Are the dongle back-office tools (dongle generator, demo dongle editor) still used? They are separate builds and are not ported unless needed | not ported | map Part 6 §0 |
