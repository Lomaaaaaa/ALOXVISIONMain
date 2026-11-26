# AloxCamServer

Re-implementation of the ALOX Vision Core **Alox Camera Server** (`alox_camera_server.vi`, LabVIEW 2018).

The LabVIEW server is a TCP server that drives:

- two cameras (High Mag / Low Mag) through NI-IMAQdx;
- a PI Mercury Z stage through the PI GCS driver over RS-232;
- LED and bulb lighting through the cameras' digital outputs;
- autofocus and refocus routines.

The Python package `aloxcam` replaces it. **Phase 1** of the migration plan (the protocol skeleton) is done: the LabVIEW
wire protocol, the configuration files and event logs, the server runtime, the 46 commands with **simulated** cameras
and Z stage, a protocol client library and a replay tool. **Phase 2** adds the real PI C-663 Z stage over RS-232
(see [Phase 2: real Z stage](#phase-2-real-z-stage)); the cameras stay simulated until phase 3. The autofocus and
refocus jobs come in phase 4; until then `Cam Autofocus` and `Cam Refocus` reply error 9507 ("not implemented in this
phase").

> **Direction change (25/11/2026).** The whole ALOX Vision Core application is now being migrated as **one integrated
> application**, without separate hardware servers ([`docs/vision-core/migration-plan.md`](docs/vision-core/migration-plan.md)).
> The camera server is therefore not completed as a server: its Z-stage driver, simulators, configuration reader, event log
> and focus metrics are reused as modules of the new application, and the TCP server stays as reference and test tooling.

## The integrated application (`alox`)

The package `alox` is the new Vision Core application: one process with the devices (Z stage, XY stages on the C-663
daisy chain, monochromator, two GenICam cameras), the core (settings, recipes, statuses, command queue, measurement
functions, hardware supervision) and the supervisor API (REST on port 8002, websocket events on port 6123, the same
routes and JSON as LabVIEW). The plan and its progress are in
[`docs/vision-core/migration-plan.md`](docs/vision-core/migration-plan.md); the questions waiting for the owner are in
[`docs/vision-core/questions.md`](docs/vision-core/questions.md).

```sh
pip install -e ".[stage,api]"
alox --simulate --setup-ini D:\alox\alox_setup.ini
```

- The folders come from `alox_setup.ini [folders]` (default `<exe>/alox_setup.ini`); `--settings-dir DIR` replaces
  `[folders] settings`. At the first start `alox.ini` is created from `<exe>/alox_default.ini` and an empty profiles
  folder gets the `DEFAULT` recipe.
- `--simulate` replaces every device by its simulator; `--devices z,xy,wl,highmag,lowmag` chooses the devices;
  `--port-map COM2=socket://127.0.0.1:50000` connects a COM port of `alox.ini` to an emulator or another port.
- `--rest-port`, `--ws-port`, `--host` and `--no-api` set the API; the application runs until Ctrl+C or the
  supervisor's `PUT /alox/v1/system/shutdown`.
- `alox --simulate --check` only opens the devices, prints their status as JSON and exits (0 when all are ready).

## Documentation

The LabVIEW code has been analysed into a specification for the re-implementation. It is in [`docs/`](docs/README.md).

- [`docs/README.md`](docs/README.md): index and reading order.
- [`docs/10-migration-plan.md`](docs/10-migration-plan.md): recommended language, target design, phased plan, risks.
- [`docs/open-questions.md`](docs/open-questions.md): questions for the system owner, as a fill-in questionnaire.
- [`docs/phase1-design.md`](docs/phase1-design.md): the phase 1 implementation contract (package layout, APIs, tests).
- [`docs/phase1-notes.md`](docs/phase1-notes.md): decisions taken while implementing phase 1.
- [`docs/phase2-design.md`](docs/phase2-design.md): the phase 2 contract (the real Z stage over RS-232).
- [`docs/phase2-notes.md`](docs/phase2-notes.md): decisions taken while implementing phase 2.

## Development set-up

Python 3.11 or later. The only runtime dependency is numpy; the real Z stage needs pyserial (`pip install -e
".[stage]"`); the tests need pytest and pyserial (both in `[dev]`).

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

The commands below assume this virtual environment is active (`. .venv/bin/activate`). Without the editable install,
prefix the `python -m aloxcam` commands with `PYTHONPATH=src`.

## Running the tests

```sh
python -m pytest                       # the whole suite (about 20 s)
python -m pytest -m "not slow"         # without the tests that take more than 2 s
python -m pytest tests/integration     # end to end: in-process server, replay scripts, CLI smoke tests
```

The tests need no hardware and no network besides loopback; servers listen on ephemeral ports.

## Running the simulated server

The server finds its folders as the LabVIEW one does: `alox_setup.ini` (`[folders]`) names the settings, vision and
data folders; the settings folder holds `alox.ini` (read-only, shared with the other ALOX programs) and the optional
Python-only `alox_camera_server.ini`. A minimal installation on Linux, using the production `alox.ini`:

```sh
mkdir -p /tmp/alox/settings /tmp/alox/vision /tmp/alox/data
printf '[folders]\nsettings = /tmp/alox/settings\nvision = /tmp/alox/vision\ndata = /tmp/alox/data\n' \
    > /tmp/alox/alox_setup.ini
cp docs/answers/ini/alox.ini /tmp/alox/settings/
python -m aloxcam --setup-ini /tmp/alox/alox_setup.ini --simulate --port 5030
```

- `--simulate` uses the simulated cameras and Z stage (`[simulation] enabled = TRUE` does the same);
  `--simulate-cameras` keeps the cameras simulated and drives the real Z stage (next section). There is no camera
  backend before phase 3: without one of the two options (or `[simulation] enabled` / `cameras = TRUE`) the server
  exits with code 2.
- `--port` overrides the `alox.ini` port (503, which needs administrator rights on Linux); `--port 0` picks a free
  port, and `--ready-file PATH` writes `<host> <port>` once the server listens.
- Stop the server with Ctrl+C or with the `Exit` command. Exit codes: 0 normal stop, 1 start-up failure, 2 usage error,
  3 shutdown timeout.
- The LabVIEW-format event logs are written to `<vision>/logs/<yymmdd>_camera_events.log` and `…_z_events.log`, the
  diagnostic log to `<vision>/logs/<yymmdd>_camera_server_diag.log` and to stderr.
- `python -m aloxcam --write-settings-template /tmp/alox/settings/alox_camera_server.ini` writes the template of the
  Python-only settings: the `[compat]` switches of [docs/10](docs/10-migration-plan.md) §6, `[simulation]`, `[wire]`,
  `[logging]`, `[stage]`, …
- `python -m aloxcam --help` lists every option. After `pip install -e .` the `aloxcam` command is the same program.

Without `--setup-ini`, `alox_setup.ini` is read from the current folder (or `$ALOX_EXE_DIR`); when it is missing, the
LabVIEW default folders `D:\alox\…` apply, which off Windows need `ALOX_DRIVE_D=<folder>` or `--settings-dir`.

### On Windows (the target platform)

In a Command Prompt, from the repository folder. `py` is the Python launcher installed with Python 3.11+.

```bat
py -3 -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest
```

To run the simulated server against the real ALOX folders, use the existing `alox_setup.ini` (or a copy). It only reads
`alox.ini` and never writes to it. Choose another port than 503 while the LabVIEW server is running:

```bat
.venv\Scripts\python -m aloxcam --setup-ini D:\alox\alox_setup.ini --check-config
.venv\Scripts\python -m aloxcam --setup-ini D:\alox\alox_setup.ini --simulate --port 5030
.venv\Scripts\python tools\replay_client.py tools\scripts\smoke.toml --port 5030
```

The simulated server writes its event logs to `<vision>\logs`, next to the LabVIEW ones. Their file names are the same
(`<yymmdd>_camera_events.log`), so point `alox_setup.ini` at a separate test folder if you don't want both servers writing
to the same daily files.

## Phase 2: real Z stage

The server drives the real PI C-663 (RS-232, 115200 Bd, through the Moxa virtual COM port) with the GCS frames of the
LabVIEW PI driver, while the cameras stay simulated. It uses `pyserial`, not PI's `pipython`
([docs/phase2-design.md](docs/phase2-design.md) §2).

```bat
.venv\Scripts\pip install -e ".[stage]"
.venv\Scripts\python -m aloxcam --setup-ini D:\alox\alox_setup.ini --simulate-cameras --check-config
.venv\Scripts\python -m aloxcam --setup-ini D:\alox\alox_setup.ini --simulate-cameras --port 5030
```

- **Stop the LabVIEW server first.** The COM port is exclusive: while LabVIEW holds it, `Z Setup` fails with error −6
  (and the other way round).
- The port and baud rate come from `alox.ini` `[hardware.z]` (`comport_I32` → `COM<n>`, `baudrate_I32 = 3` →
  115200). As in LabVIEW, the port is opened by the client's `Z Setup`, not at start-up, and `Z Setup` references the
  axis (the stage drives to its negative limit switch, `[stage] always_reference = TRUE`); `Z Get Status`, `Z Stop` and
  `Z Cleanup` restart the `DH` macro as before. `--check-config` shows the stage mode, the port and whether pyserial is
  importable.
- `[stage] port_map` in `alox_camera_server.ini` sends a port name elsewhere: `COM4=/dev/ttyUSB0` on Linux, or
  `COM4=socket://127.0.0.1:5555` for the fake controller below. `[simulation] cameras = TRUE` is the INI form of
  `--simulate-cameras`.
- Every frame exchanged with the controller is logged at DEBUG on the `aloxcam.zstage.serial` logger
  (`--log-level DEBUG`), in the diagnostic log.

### Check the Z stage

`tools/z_stage_check.py` checks the controller through the server's own serial code and writes a report to send back
([docs/phase2-design.md](docs/phase2-design.md) §4.1). It needs Python 3.11+ and pyserial only, no install of
`aloxcam`. Run it on the PC that controls the stage, in a Command Prompt in the repository folder, **with the LabVIEW
camera server stopped** (it keeps the COM port open). `D:\alox\settings` is the settings folder of `alox_setup.ini`;
`--port COM4` can replace `--alox-ini`.

1. Once: `py -3 -m pip install pyserial` (or use `.venv\Scripts\python` instead of `py -3` below if you made the
   virtual environment above).
2. **Information first.** Read-only: nothing moves, no setting changes, no macro is stopped.

   ```bat
   py -3 tools\z_stage_check.py --alox-ini D:\alox\settings\alox.ini
   ```

   Opening the port sends the same purge as LabVIEW (`XXX`, `*idn?`, `err?`); then it reads `SAI?`, `*IDN?`, `ERR?`
   (this clears the controller error; the value read is reported), `CST? 1`, the servo, reference and switch states,
   the travel range, the position and speed, `JON? 1`, `RMC?`, the `DH` and `HOME` macro texts, `#5` and `#7`.
   `--help` lists every frame.
3. **Motion tests, only when the stage area is safe**: nothing on or near the stage that a move can hit, and the
   controller power switch within reach.

   ```bat
   py -3 tools\z_stage_check.py --alox-ini D:\alox\settings\alox.ini --motion
   ```

   The tool prints the start position, the allowed window (the controller range, the `alox.ini` `zmin_DBL` /
   `zmax_DBL`, and ±1 mm around the start: `--window`) and the test speed (1 mm/s: `--speed`, at most 5). It explains
   each test of questionnaire Q35 (`POS?` and `STP 1` during a move, `MVR` while a move runs, `MOV` with the joystick
   on, `VEL 0`) and runs it only after you type `yes`. Ctrl+C, an error or a timeout sends `#24` (stop all axes) at
   once. At the end the tool offers to move the stage back to its start position, restores the speed and restarts
   `DH` if it was running. Do not press the HOME button during the tests: while `DH` runs, it moves the stage to
   0 mm. `--setup-twice` adds the server's `Z Setup` twice; it references the axis, so the stage drives to its
   negative limit switch.
4. **Send back** the `z_stage_report_<date>-<time>.json` file named on the last lines of the output (written in
   `tools\`, or in `--out <folder>`). The `.txt` next to it is the same report, easier to read.

`py -3 tools\z_stage_check.py --fake --motion --yes` rehearses everything against an emulated controller, without
hardware.

### Check the XY stages

`tools/xy_stage_check.py` checks the two XY controllers (PI C-663, X = device 1, Y = device 2 on one RS-232 daisy
chain) through the integrated application's own code (`alox.devices.gcs_chain`, `alox.devices.xy`). First it finds out
how the controllers want to be addressed on the shared port (target address alone or with the sender address; whether
the single-byte commands `#5` / `#7` can be addressed or must be replaced by `SRG? 1 1`), then it reads both
controllers. Same requirements as the Z check; run it **with the LabVIEW XY server (AloxXYServer.exe) stopped** (it
keeps the COM port open). The port, baud rate and device numbers come from `alox.ini` `[hardware.xy]` (`comport_I32`,
`baudrate_I32`, `xaxis_I32`, `yaxis_I32`); `--port COM3` can replace `--alox-ini`.

1. **Information first.** Read-only: nothing moves, no setting changes.

   ```bat
   py -3 tools\xy_stage_check.py --alox-ini D:\alox\settings\alox.ini
   ```

   It prints the daisy-chain variant found (`DAISY-CHAIN VARIANT: GcsChain(..., address_mode=..., char_mode=...)`)
   and, per controller, `*IDN?`, `CST? 1` (stage model), the travel range, the reference and limit switches, the
   reference state, position, speed, servo, joystick (`JON? 1`, and `JBS? 1 1` on X) and `ERR?`. `--help` lists every
   frame.
2. **Motion tests, only when the stage area is safe**: nothing on or near the stages that a move can hit, and the
   controllers' power switch within reach.

   ```bat
   py -3 tools\xy_stage_check.py --alox-ini D:\alox\settings\alox.ini --motion
   ```

   Each step needs a typed `yes`: the set-up through the application's XY device (servo on; an axis that is not
   referenced is referenced only after a second `yes`, and that drives it to a switch), ±1 mm relative moves on X and
   on Y at 1 mm/s with position checks, a move that must be refused because it leaves a tiny test wafer, and `STP`
   during a move. Moves stay within ±1 mm of the start position (`--window`) and the `alox.ini` `xmin`/`xmax`,
   `ymin`/`ymax`; Ctrl+C, an error or a timeout sends `#24` (stop all axes) to both controllers. At the end the tool
   offers to move the stages back and restores their speeds.
3. **Send back** the `xy_stage_report_<date>-<time>.json` file named on the last lines of the output (written in
   `tools\`, or in `--out <folder>`); the `.txt` next to it is the same report, easier to read. Its first lines give the
   daisy-chain settings the application must use.

`py -3 tools\xy_stage_check.py --fake --motion --yes` rehearses everything against two emulated controllers;
`--fake-address-mode target_sender --fake-char-mode text_equivalents` makes them speak the other variant.

### Check the monochromator

`tools/wl_check.py` checks the Spectral Products CM110/CM112 monochromator (RS-232, 9600 Bd 8N1, binary protocol)
through the integrated application's own code (`alox.devices.cm11x`), so it sends exactly the bytes the new
application will send ([docs/vision-core/monochromator.md](docs/vision-core/monochromator.md)). Same requirements as the
Z check (Python 3.11+ and pyserial, no install); run it **with Alox Vision stopped** (`AloxWLServer.exe` keeps the COM
port open). The port, baud rate and answer timeout come from `alox.ini` `[hardware.monochromator]` (`comport_I32`,
`baudrate_I32`, `timeout_I32`); `--port COM1` can replace `--alox-ini`.

1. **Information first.** Read-only: nothing moves, no setting changes.

   ```bat
   py -3 tools\wl_check.py --alox-ini D:\alox\settings\alox.ini
   ```

   It sends `ECHO` (the instrument must return 27), then only `QUERY` requests: serial number, type, number of
   gratings, current grating, grooves/mm, blaze, units, position, scan speed, step size, the grooves and blaze of
   both gratings, the zero offsets, the calibrations and NOVRAM #14. Every answer is kept byte for byte in the
   report. `--help` lists the requests.
2. **Moves, when a wavelength change does not disturb anything** (no measurement running on the tool). The grating
   turns; nothing outside the monochromator moves.

   ```bat
   py -3 tools\wl_check.py --alox-ini D:\alox\settings\alox.ini --motion
   ```

   Each test needs a typed `yes`: a 10 nm move and back (`--step`), then the working range 700 nm → 950 nm
   (`--range LO HI`), each `GOTO` timed and its position read back; `UNITS 1` (nanometres) first only if the
   instrument is in other units (after its own `yes`). `--reset` adds a `RESET` (the grating re-homes) and measures
   how long the instrument takes to answer again. At the end the tool offers to go back to the start wavelength. A
   `GOTO` cannot be interrupted once sent: Ctrl+C stops the tool after the current move. The tool never sends
   CALIBRATE, SELECT (grating change), SPEED or SCAN.
3. **Send back** the `wl_report_<date>-<time>.json` file named on the last lines of the output (written in `tools\`,
   or in `--out <folder>`); the `.txt` next to it is the same report, easier to read. It answers the open questions
   of the monochromator spec: model and gratings, the bytes of each answer, the duration of a move, what RESET does.

`py -3 tools\wl_check.py --fake --motion --reset --yes` rehearses everything against an emulated CM110.
`python -m alox.devices.cm11x_sim --port 5556` serves the same emulator on a TCP port (`--port
socket://127.0.0.1:5556` for the tool or the application).

### Check the cameras

`tools/camera_check.py` checks the two IDS GigE Vision cameras through the integrated application's own GenICam
driver (`alox.devices.camera_genicam`: Harvesters on the IDS peak GenTL producer) and writes a report to send back. It
needs Python 3.11+ and the `harvesters` package (it brings `genicam` and numpy), no install of this repository. Run it
on the PC the cameras are connected to, **with the LabVIEW camera server and IDS peak Cockpit closed** (a camera is
opened exclusively).

1. Once: `py -3 -m pip install harvesters`, and IDS peak with the GigE Vision transport layer (the "Kernel (GEVK)"
   producer). The tool finds the producer through `GENICAM_GENTL64_PATH`, which the IDS peak setup sets; otherwise
   give it with `--producer`: `dir /s /b "C:\Program Files\IDS\ids_peak\*.cti"` shows where it is.
2. **List the cameras** the producer sees, with their serial numbers:

   ```bat
   py -3 tools\camera_check.py --list
   ```

3. **Information.** Nothing the cameras keep is written (no pixel format, packet size or trigger mode); only the
   selectors (`LineSelector`, `UserOutputSelector`, …) are stepped through their entries, then put back.

   ```bat
   py -3 tools\camera_check.py --alox-ini D:\alox\settings\alox.ini --name-map cam2=<serial of the High Mag camera>
   ```

   The cameras are the `alox.ini` `name_STR` ones. An NI MAX alias such as `cam2` is not known to the cameras: give
   its serial number with `--name-map` (from the list of step 2), or set the camera's user-defined name to `cam2` in
   IDS peak Cockpit. For each camera the tool reads the identity and IP address, every feature the application uses
   (type, value, range, enumeration entries with their integer values), the I/O lines, the application's exposure /
   gain / frame-rate / temperature attributes as the driver resolves them, and grabs 5 frames with timing
   (`--frames`; lost frames and incomplete buffers point to jumbo frames missing on the network card). The last frame
   of each camera is saved as PNG.
4. **Guided actions**, when changing the exposure and switching the LED and the bulb disturbs nothing:

   ```bat
   py -3 tools\camera_check.py --alox-ini D:\alox\settings\alox.ini --name-map cam2=<serial> --actions
   ```

   Each needs a typed `yes`: a test exposure (10 ms, `--test-exposure`) written, read back and restored; the packet
   size 8996 the application writes, with a timed grab, restored; the LED / bulb output switched on, then off, on the
   lights camera (`alox.ini` `setLEDcam_I32`: High Mag; `--lights`) with the application's own `Cam Set Led` code.
   Watch the LED and the bulb: the tool asks what you saw.
5. **Send back** the `camera_report_<date>-<time>.json` file named on the last lines of the output **and the `.png`
   files next to it** (written in `tools\`, or in `--out <folder>`); the `.txt` is the same report, easier to read.
   It answers the camera checklist: feature names, gain units, pixel format and image size, packet size and lost
   frames, the integer values of the I/O enumerations, which line is an output.

`py -3 tools\camera_check.py --fake --actions --yes` rehearses everything on a fake Harvesters stack (two cameras),
without hardware.

### Measure the Fast autofocus latency

The Fast autofocus dates each image by the time it was grabbed, while the image was exposed earlier, which shifts the
best Z. `tools/af_latency_check.py` measures that latency by comparing a Slow and a Fast autofocus on the same sample
(the method and the procedure are in the tool's help). With Alox Vision stopped and a sharp sample under the camera,
roughly in focus:

```bat
py -3 tools\af_latency_check.py --alox-ini D:\alox\settings\alox.ini --camera highmag --z-min 4.8 --z-max 5.2
```

Nothing moves before a typed `yes`. Copy the printed `fastaflatency_DBL = …` line into `alox.ini` under
`[hardware.z]` and send back the `af_latency_report_<date>-<time>.json` file. `--simulate --yes` rehearses it without
hardware.

### Rehearsal with the fake controller

`aloxcam.zstage.fake_c663` emulates a C-663 on a TCP port (the simulated stage behind the real serial protocol). It
lets the whole real-stage path be rehearsed without the machine:

```sh
python -m aloxcam.zstage.fake_c663 --port 5555          # prints the URL; Ctrl+C stops it
```

then in `<settings>/alox_camera_server.ini` (with the production `alox.ini`, `comport_I32 = 4`):

```ini
[stage]
port_map = COM4=socket://127.0.0.1:5555
```

and start the server with `--simulate-cameras` as above. The fake's travel is 0–100 mm by default (`--max-mm`), its
reference move takes 2 s (`--reference-s`), `--initially-referenced` and `--no-dh` change its power-up state, and
`--log-level DEBUG` prints every frame it receives. Remove the `port_map` line before running against the real stage.

### The Z replay script

`tools/scripts/phase2_z_stage.toml` sends the 19 Z commands to a server running with the real stage (or the fake):
set-up, status, a slow speed, absolute and relative moves with `Z Wait End Move` and position checks, a move outside
the software range (9405), `Z Stop` during a move (error 10 left pending), the joystick commands, the limits, `Z
Cleanup` and `Exit`. **The stage moves**: read the script header first. The positions have no default; choose a safe
window and pass the `alox.ini` limits:

```bat
.venv\Scripts\python tools\replay_client.py tools\scripts\phase2_z_stage.toml --port 5030 ^
    --var z_abs=60.0 --var z_rel=0.5 --var z_rel_end=60.5 --var z_min=0.0 --var z_max=90.0 --record z_stage.jsonl
```

Steps marked "lab finding" record what only the real controller can show (08 §13.6: `STP 1`, `MVR`, the serial
number); a FAIL there is a finding to report. Keep the `--record` file. The script ends with `Exit`.

## Checking the configuration

```sh
python -m aloxcam --setup-ini /tmp/alox/alox_setup.ini --check-config
```

prints the folders, the effective listen address, the device modes (simulated cameras, simulated or real Z stage
with its port), the configuration warnings, the wire enum tables, the routing table and its self-check, and the
`[compat]` values. Exit code 0 means usable; 1 means a fatal problem (listed at the end).

## Replay client

`tools/replay_client.py` runs a script of commands against a server (Python or LabVIEW), checks every reply and can
record the session and compare it with an earlier recording. With the server above running:

```sh
python tools/replay_client.py tools/scripts/smoke.toml --port 5030
```

```text
[  1] PASS heartbeat frame (ignored by the server)  none
[  2] PASS Get Heartbeat  U32 8602644  err={F,0,""}  1.5 ms
[  3] PASS Exit: no reply, the server closes the connection  none
3 step(s) run: 3 passed, 0 failed -- OK
```

The smoke script ends with `Exit`, so the server stops. Exit codes: 0 every step passed (and no compare difference), 1 a
step failed or a difference was found, 2 usage or connection error. The other scripts in `tools/scripts/` are used by the
integration tests: `phase1_all_commands.toml` sends every command (it expects the reference `alox.ini` of
[docs/09](docs/09-configuration-logging-errors.md) §5.6 and takes `--var sim_dir=… --var ffc=… --var dfc=…`, see its
header) and `phase1_error_replies.toml` needs `[compat] send_error_replies = TRUE` and `reply_unknown_commands = TRUE`;
`phase2_z_stage.toml` is described above.

A script is a TOML file (format: [docs/phase1-design.md](docs/phase1-design.md) §3.7.2):

```toml
[session]
port = 5030
timeout_s = 5.0

[vars]                                   # ${name} in any string; --var name=value overrides
cam = "cam0"

[[step]]
command = "Cam Setup"
data = { camera_type = "High Mag.", camera_name = "${cam}", number_of_buffers = 5, continuous_mode = true }
expect = { reply = "ACK", error = { status = false, code = 0 } }

[[step]]
command = "Cam Get Exposure"
data = { camera_type = "High Mag." }
expect = { reply = "DBL", value = 10000.0, abs_tol = 1e-9 }

[[step]]
command = "Z Move Absolute"
data = { pos_mm = 95.0 }
expect = { reply = "ACK", error = { status = true, code = 9405 } }   # outside [zmin_DBL, zmax_DBL]

[[step]]
command = "Exit"
expect = { reply = "none", connection = "closed" }
```

To compare the Python server with the LabVIEW one, record the same script against both and diff the replies, ignoring
the tick fields (`TCP Sending timestamp`, the `Get Heartbeat` value) and, if needed, the image bytes:

```sh
python tools/replay_client.py session.toml --host <LabVIEW PC> --port 503 --record labview.jsonl
python tools/replay_client.py session.toml --port 5030 --compare labview.jsonl --mask tick
```

Other options: `--timeout S`, `--settings-ini PATH` (wire enum overrides and layouts from an `alox_camera_server.ini`),
`--enums PATH`, `--templates PATH` (captured variant headers), `--stop-on-fail`, `--quiet`.

The protocol client used by the tool is a library (`aloxcam.client.AloxClient`):

```python
from aloxcam.client import AloxClient
from aloxcam.dispatch import ReplyKind
from aloxcam.wire.enums import WireEnums
from aloxcam.wire.messages import PayloadCodec

enums = WireEnums.load()
with AloxClient("127.0.0.1", 5030, enums=enums, payloads=PayloadCodec(enums)) as client:
    reply = client.call("Get Heartbeat")
    value, error = reply.value(ReplyKind.U32)
```
