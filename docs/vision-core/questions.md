# Open questions for the owner (Vision Core migration)

Questions collected while building the integrated application. The implementation does not wait for them: each has a
default (the LabVIEW behaviour, unless noted) that a setting or a one-line change can switch. They are ordered by impact.
The detailed context is in the referenced documents.

## Hardware checks at the fab (answered by running the check tools)

| # | Question | Default meanwhile | Source |
|---|---|---|---|
| H1 | Z stage: results of `tools/z_stage_check.py` (information, then motion) | camera-server Z behaviour | docs/phase2-notes.md |
| H2 | XY daisy chain: which address form and single-character mode the C-663 chain accepts (`target_only` / `target_sender`, `#5`/`#7` addressed or `SRG?`), and the stage models (`CST?`) | `target_only`, addressed single characters | `tools/xy_stage_check.py`, xy-stage.md Q2 |
| H3 | Monochromator model (CM110 or CM112), gratings, COM port; protocol details of Appendix K of its manual | CM11x protocol as read in the diagrams | `tools/wl_check.py`, monochromator.md Q1, Q2, Q8 |
| H4 | Cameras: model and GenICam feature names, pixel format, packet size, I/O lines; GenTL producer path on the PC | IDS GigE, Mono8, names mapped from IMAQdx | camera_genicam.py docstring |

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

## Data

| # | Question | Default meanwhile | Source |
|---|---|---|---|
| D1 | The production wavelength/exposure table per tool (the default one is marked "not suited to the OPTO cameras") | LabVIEW's default table | monochromator.md Q7 |
| D2 | Are the stage directions (coordinate system #4) and the chuck origin still valid on every delivered tool? Which `xaxis_I32` (1 or 10) is meant? | INI values | xy-stage.md Q9, Q10 |
