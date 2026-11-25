# Vision Core map (V1.2.6.2)

This document maps the whole ALOX Vision Core LabVIEW code base for its migration: which programs exist, how the main
application is built, how it talks to the hardware and to the supervisor, what each measurement function does, which
libraries and formulas it relies on, and which code is dead.

**Sources.** The HTML export of the 747 VIs of `Versionning Sources\V1.2.6.2` (block diagrams with every hidden frame),
the 10 VIs of `Pyro\framework.llb`, the decoded typedefs ([typedefs.md](typedefs.md)), the direct dependencies of every VI
([call-graph.json](call-graph.json)), the REST service definition ([rest-api-routes.md](rest-api-routes.md)), and the text
sources (`alox.lvproj`, INI files, changelog, translations, Python/C code). Six parts were analysed independently (about
400 block-diagram images read) and assembled here. The camera server is not repeated: it is specified in
[`../01-architecture.md`](../01-architecture.md) to [`../10-migration-plan.md`](../10-migration-plan.md).

**Conventions.** `<vi> dN` cites block-diagram image N of a VI's HTML export (`d0` or `d` = the visible diagram, `d1`… =
hidden frames; `p` = front panel). **(inferred)** marks a deduction; "(from name/calls)" marks a VI whose diagram was not
opened. Untagged statements were observed. The export of `alox_camera_server_refocus.vi` contains an experiment of the owner
(an `ACC.vi` call) that is not part of V1.2.6.2 and is ignored.

## Contents

1. [At a glance](#1-at-a-glance)
2. [Key findings for the migration](#2-key-findings-for-the-migration)
3. [Decisions for the owner](#3-decisions-for-the-owner)
4. Part 1: [Core application skeleton](#part-1-core-application-skeleton-aloxvisionexe)
5. Part 2: [Hardware layer](#part-2-hardware-layer-managers-tcp-clients-hardware-servers-heartbeats-watchdog)
6. Part 3: [Measurement functions](#part-3-measurement-functions-sourcesfunctions)
7. Part 4: [Shared libraries: vision, geometry, tools](#part-4-shared-libraries-vision-geometry-tools-datetime)
8. Part 5: [API: REST, websocket, JSON reports](#part-5-api-rest-web-service-websocket-events-json-reports)
9. Part 6: [Thermal measurement, licensing, simulator, tools, tests, R&D](#part-6-other-parts-thermal-pyrobandit-measurement-licensing-simulator-tools-tests-rd)

## 1. At a glance

### 1.1 Processes and links

```
   Supervisor (external host, SECS/GEM side)                      Operator
        |  HTTP  :8002  /alox/v1/...   (REST, no authentication)      |  LabVIEW front panels
        |  WS    :6123  (JSON events, image stream; one client)       |
        v                                                             v
 +------------------------------------------------------------------------------------------+
 | AloxVision.exe  (alox_core_ui.vi)                                       Part 1           |
 |   UI event loop --> queue "alox.process.queue" (3 items) --> consumer loop (22 methods)   |
 |   managers = functional globals: properties, profile, users, log, display, image, ...    |
 |   13 measurement functions: wrapper + asynchronous state machine, one at a time  Part 3  |
 |   embedded REST service (alox_api_v1_*) + websocket server                       Part 5  |
 |   services: heartbeat, modules heartbeat, function status                        Part 2  |
 |   thermal measurement tool (pyrometer / BandiT)                                  Part 6  |
 +-----------|------------------------------|------------------------------|----------------+
   TCP localhost:503              TCP localhost:502 (INI 505)     TCP localhost:504
   (started with cmd START, killed with taskkill, restarted by the modules heartbeat)
             v                              v                              v
     AloxCamServer.exe              AloxWLServer.exe               AloxXYServer.exe
     2 GigE cameras (IMAQdx)        Spectral Products              2 PI Mercury axes
     + PI C-663 Z stage             monochromator (VISA serial)    (PI GCS, RS-232 daisy chain)
     docs/01-10                     Part 2                         Part 2

   AloxWatchdog.exe --HTTP :8002 /v1/system/heartbeat--> AloxVision.exe; kills and restarts all processes (Part 2)
```

### 1.2 Programs built from the project

Sizes count the ALOX VIs reached through static calls from each program's top-level VI and the VIs its build always
includes (`alox.lvproj` build specifications; `call-graph.json`).

| Program (executable) | Top-level VI | ALOX VIs | Role |
|---|---|---|---|
| **Alox** (`AloxVision.exe`) | `core/alox_core_ui.vi` + 53 VIs always included (managers, functions, services, dialogs) | 436, plus the 82 REST route VIs | The product: user interface, measurement functions, recipes, users, embedded REST service (port 8002) and websocket server (port 6123) |
| **AloxCamServer** | `extcamera/alox_camera_server.vi` | 30 | Cameras and Z stage, TCP 503. Specified in `docs/01`–`10`; Python port in this repository |
| **AloxWLServer** | `extwl/alox_wl_server.vi` | 19 | Monochromator (wavelength) server, TCP 502 in the code, 505 in the INI |
| **AloxXYServer** | `extxy/alox_xy_server.vi` | 17 | XY stages server, TCP 504 |
| **AloxWatchdog** | `core/alox_watchdog_ui.vi` | 6 | Polls the REST heartbeat; restarts everything |
| **AloxViewer** | `core/alox_browser_ui.vi` | 1 | Image viewer |
| **AloxSim** | `core/alox_core_simulator_ui.vi` | 255 (245 shared with Alox) | Reduced 2018 copy of the main loop with simulated functions; same REST and websocket API |
| Alox Demo Dongle, ALOXVISIONdonglegenerator | `dongle/alox_dongle_generation(_v2).vi` | 5 | Licence file generators |
| acquireCameraTemperature (`_usb`) | `tools/LED_alox_control_tool(_vUB).vi` | 1 | Camera temperature / LED bench tool |
| Python_fct_dectectcircle | `PythonLV/test_py_corr_batch.vi` | 1 | R&D test |
| OF44108_exe | `LYNRED/main.vi` | 2 | Customer image recorder (LYNRED) |

The REST service is not a separate process: the `alox` web service is built into AloxVision.exe (and AloxSim) and its route
VIs call the core's managers and queue in-process (Part 5).

### 1.3 Where the 759 VIs are

| Part | VIs | Content |
|---|---|---|
| 1. Core | 75 | Main application skeleton, non-hardware managers, dialogs, synchronous calls |
| 2. Hardware | 166 | Hardware managers, TCP clients, WL/XY servers, heartbeats, watchdog, coordinate systems |
| 3. Functions | 88 | The 13 measurement functions |
| 4. Vision and tools | 141 | Focus metrics, image processing, geometry and diameter fits, files and folders |
| 5. API | 107 | REST routes, websocket, JSON reports |
| 6. Other | 182 | Thermal measurement, licensing, simulator, tools, tests, R&D, customer builds, documentation tool |

About 270 VIs are reached by no build. Most are tests, old versions and R&D; the exceptions (started dynamically, such as
the thermal measurement and the function state machines) are identified in the parts. Each part ends with its unused code.

## 2. Key findings for the migration

**Architecture (Part 1).**
- `alox_core_ui.vi` is an 11-frame sequence: kill leftover servers, abort stray VIs, boot cycle (29 steps), init cycle
  (36 steps), idle, main loop, exit cycle, shutdown cycle, quit. Licensing is checked in the boot cycle (Part 6).
- The main loop is a producer/consumer pair: 73 UI event cases feed the named queue `alox.process.queue`, which holds only 3
  items (a 4th sender blocks), and a consumer that accepts 22 methods.
- Every manager is a functional global: a process-wide singleton whose calls are serialised by LabVIEW. A rewrite needs
  explicit objects with locks.
- One measurement function runs at a time (global condition). The operator starts it directly after a rights check; the REST
  API queues a "Measure X" command with the SEMI job IDs; the end is signalled by an "X Job Finished" message and a websocket
  `finished` event. The core only stores and echoes the SEMI job IDs; SECS/GEM itself is the supervisor's job.

**Interfaces (Parts 2 and 5).**
- REST: base `http://<host>:8002/alox/v1/`, no authentication. PUT arguments are query/form parameters, not JSON bodies; a PUT
  only queues a command and returns localized plain text; results arrive as websocket events. Status codes are only 200, 500
  (also for invalid arguments) and 503 (function not ready).
- Websocket (port 6123): one client, server push only, JSON text frames with an `"event"` field. Delivery is lossy: 17 event
  types share 8 notifiers, so a later event can overwrite an unread one.
- The WL and XY servers are clones of the camera server's TCP layer (same framing, `extshared` VIs and typedefs); everything in
  [`../02-tcp-protocol.md`](../02-tcp-protocol.md) applies to them. The main application starts the server programs with
  `cmd /c START /B`, kills them with `taskkill /F`, and restarts a server after `maxfails` missed heartbeats.

**Measurement functions (Part 3).**
- All 13 functions share one pattern: a wrapper (Init, Exit, Start, Stop, Run Once, Get Status, Get Last Measure, Abort for HL)
  opens its `_state_machine` VI by name, starts it asynchronously and exchanges data through its controls by name.
- The high-level functions chain the low-level ones with Run Once. Mesa is a 21-state flow (search object, autofocus, refocus,
  detect circle, tracking, auto exposure, wavelength, then a measure/refocus/save loop computing oxidation rate and remaining
  time); bowing measures Z at 9 wafer and chuck points; tilt uses a straight edge or two pattern-matched objects.
- Detect circle uses NI Vision only. The Python/OpenCV Hough code and its DLL are R&D, reached by no build.

**Algorithms and data (Part 4).**
- The bowing/parallelism, tilt, recentring, wavelength/exposure interpolation and mesa/oxide diameter formulas
  (surface-equivalent, inscribed via Delaunay, circumscribed via Elzinga–Hearn, minimum-zone) are written out, with the
  averaging and NaN-filter chain.
- The "RANSAC" line fit is defective: it cannot return a horizontal or vertical line, it chooses the pair with the smallest
  total distance instead of the most inliers, and its random index can go one past the end of the array.
- Settings live in `Documents\alox\settings\alox.ini` (type suffix on every key, decimal comma, hard-coded defaults); recipes
  are zip files unpacked into `Documents\alox\profile`; three different job-history roots and two data roots are used by
  different VIs; file names have one-second resolution, so two saves in the same second overwrite each other; `.foc`/`.focf`
  files are LabVIEW binary and result PNGs carry NI overlay/calibration data.

**Other parts (Part 6).**
- The thermal measurement is product code: an operator button starts it; XY stages move a pyrometer (Optris, serial) or a kSA
  BandiT probe (Modbus TCP) over concentric circles of the heated chuck; results go to CSV. It has no REST access.
  `[hardware.chuck]` (dz1–dz5, maxparallelism, bowchuckA/B) belongs to the bowing measurement, not to this tool.
- Licensing only gates boot (WireFlow USB dongle or a "virtual dongle" file tied to the D: volume serial, the NI Vision serial
  and the NI computer ID); no feature is locked. Keys and generator passwords are hard-coded in diagrams; they are not copied
  in this document.
- Dead: the other Pyro VIs, `framework.llb` (inherited from older furnace software), `PythonLV`, `rd/`, most of `tests/`, the
  `?` orphans and the documentation/export VIs.

**Defects and risks to decide on** (details in each part).
- At exit the core deletes the content of the user's `%LOCALAPPDATA%\Temp` folder.
- `GET /v1/system/version` appears to queue an `Abort` command (would stop a running measurement).
- The bowing JSON payload has duplicate keys (`bowinga`, `parallelismb`).
- The watchdog restarts `Alox.exe`, but the build produces `AloxVision.exe`.
- An XY move can silently re-home both axes when a reference is lost; the XY wafer-limit loop uses the PI connection without a
  lock; error replies such as 9404 "outside wafer" are probably never sent, so the client sees a timeout.
- Detect circle depends on a subVI in a developer's OneDrive folder; the measure-mesa worker calls `System Exec.vi` with an
  unknown command; `refocus_oxyd` is used by no high-level function.
- Port settings disagree: web service 8001 in the INI vs 8002 in the build; WL server 502 in the code vs 505 in the INI.
- Passwords are stored Base64-encoded (reversible).

## 3. Decisions for the owner

The full lists are at the end of each part ("Open questions"). The decisions that shape the rewrite most:

1. **Keep the REST/websocket API exactly** (query parameters, plain-text PUT replies, status codes, key names, port 8002/6123,
   lossy events) or design a new version with a compatibility layer?
2. **Keep the four processes** (main + three hardware servers on localhost TCP) or merge the hardware drivers into one service?
   **Decided (25/11/2026): one integrated application without hardware servers** ([migration plan](migration-plan.md)).
3. **Defects:** which ones to reproduce for compatibility and which to fix (RANSAC fit, Temp-folder deletion, `version` →
   `Abort`, duplicate JSON keys, watchdog executable name, silent re-homing)?
4. **Licensing:** keep the WireFlow dongle and the virtual-dongle file, or replace them?
5. **Users and passwords:** keep the Base64 storage and the rights flags in the settings file, or move to hashed passwords?
6. **Scope:** confirm that the simulator, the thermal tool, the customer builds (LYNRED) and the bench tools are needed in the
   new version, and that the dead code listed in each part can be dropped.
7. **File formats:** keep `.foc`/`.focf` binary files, PNG custom data, zip recipes and the INI format for compatibility with
   existing data, or convert them?

### 3.1 Owner's answers on the defects (25/11/2026)

| Point | Owner's answer | Decision for the rewrite |
|---|---|---|
| The exit cycle empties `%LOCALAPPDATA%\Temp` (Part 1, frame 10) | Intentional, to prevent a LabVIEW error | A LabVIEW workaround: not reproduced. The new application only removes its own temporary files. |
| `GET /v1/system/version` enqueues `Abort` (Part 5, note 8) | Unknown | **Confirmed in the diagram** (`alox_api_v1_get_system_version d`): an `Alox Queue Item` with Method `Abort` is enqueued into the core process queue on every call, with no condition; the core handles `Abort` as an emergency stop of all running functions. Most likely a copy-paste from `put_functions_abort`. Not reproduced: `version` becomes read-only. |
| The "RANSAC" line fit cannot return a horizontal or vertical line (Part 4) | Confirmed; it would crash the process | Fixed: a line fit that handles every orientation (normal-form or total-least-squares model inside a real RANSAC that maximises inliers). |
| The watchdog restarts `Alox.exe` instead of `AloxVision.exe` (Part 2, 2.6) | The watchdog is not used | AloxWatchdog is out of the migration scope. |
| An XY move silently re-homes both axes when a reference is lost (Part 2) | Unknown, but the behaviour is welcome | Kept, but made visible: the re-homing is logged and reported as an event/status. |
| Duplicate keys in the bowing JSON (`bowinga` twice, `parallelismb` twice; Part 5, note 7) | Should be corrected | Corrected to `bowinga`/`bowingb` and `parallelisma`/`parallelismb` (cluster order). Check the supervisor first: a standard JSON parser keeps the last duplicate, so today it reads the second value under `bowinga` and `parallelismb`. |
| Ports and folders that disagree between code and INI files | Use the value that is really in effect | The rewrite keeps the value the running program actually uses: REST port **8002** (build setting; the INI key `[webservice] port = 8001` is not read by the service), websocket **6123**, WL server **505** (both the server and the manager read `[hardware.monochromator] port`, the shipped INI says 505; 502 is only the code default), folders from `alox_setup.ini [folders]` (read at every call; the code defaults are fallbacks only). Defaults live in one place in the new code. |

---

## Part 1: Core application skeleton (`AloxVision.exe`)

Group `1_core`, 75 VIs. Sources: HTML export (diagrams and hidden frames), `typedefs.md`, `alox.lvproj`, `alox_default.ini`,
`alox_setup.ini`, `changelog.txt`, `translations/*.lang`, `errors/alox-errors.txt`.
Notation: `<vi> dN` = diagram image N of that VI's HTML page (`d0` = main diagram). **(inferred)** = deduced from names, calls or
comments, not seen in a diagram. Enum names are quoted exactly as they appear in `typedefs.md`.

### 1. Purpose

This part is the application frame of the main program `AloxVision.exe`, built from the "Alox" build specification
(top-level `alox_core_ui.vi` plus 53 VIs that are always included because they are started dynamically). It brings the machine up in
two cycles, "boot" (software managers, licence dongle, data folders) and "init" (hardware server executables, cameras, stages,
monochromator, profile, measurement functions). It then runs a producer/consumer main loop that takes commands from the operator UI
and from the REST API, starts the measurement "functions", and reports their end to the supervisor. It also holds the
singleton managers that every other part uses: settings (properties), recipes (profiles), users and rights, log, language, status
codes, SEMI job data, display and image, the panels shown in the UI, and the run conditions of the functions. Finally it contains the
operator dialogs (login, profiles, properties, log, calibration, vignetting, about), the image viewer `AloxViewer.exe`
(`alox_browser_ui.vi`) and the "synchronous calls" request/response helpers that the hardware clients use.

### 2. Architecture

#### 2.1 Building blocks and patterns

- **Managers are functional global variables (FGV).** Every `alox_manager_*` VI in this part is a non-reentrant VI with a
  single-iteration `While` loop, uninitialized shift registers that hold the state, an `error in` "No Error/Error" case and a case
  structure on a `Method` enum (observed: `alox_manager_process_queue d0`, `alox_manager_functions_ui_events d0`,
  `alox_manager_semi d2..d6`, `alox_manager_users d0`, `alox_manager_properties d0`, `alox_manager_profile d0`,
  `alox_manager_functions_status d0`, `alox_manager_log d0`). Because LabVIEW serializes calls to a non-reentrant VI, each
  manager is a process-wide singleton with an implicit lock. Most have `Init` / `Exit` methods that the boot cycle and the shutdown
  cycle call.
- **Stacked sequences.** `alox_core_ui`, the four cycle VIs and most dialogs are stacked sequences with one commented frame per
  step.
- **Dynamic VIs.** Several VIs are opened by path through VI Server and started with `Run VI` (Wait Until Done = F), inserted into
  sub-panels, or stopped with `Ctrl Val.Set "Stop"` / `Abort VI`. This explains the 53 "Include" entries of the build.

#### 2.2 Runtime structure

```
                       +-------------------------------- AloxVision.exe (one LabVIEW application instance) ----------------------------+
 operator  ------UI--> | alox_core_ui.vi  frame 8 "MAIN EVENTS LOOP"                                                                 |
                       |   PRODUCER loop: Event structure (73 cases)  --Enqueue--+                                                    |
                       |      timeout = general.displaytimer: periodic refresh   |    named queue "alox.process.queue" (max 3 items)   |
                       |      <Function UI Event> user event (registered)        v                                                    |
                       |   CONSUMER loop: Dequeue "Alox Queue Item" {Method, Variant} -> case per Method (22 cases + Default)         |
                       |                                                         ^                                                    |
 supervisor --REST---> | alox.lvws web service (port 8002, same process): alox_api_v1_* VIs --Enqueue / direct FGV calls--+          |
 (HTTP)                |                                                                                                    |          |
 supervisor <-WebSocket| alox_api_websocket_service  <--Notifiers (alox_manager_api_notifiers, 17 types)-- state machines, core     |
                       |                                                                                                                |
                       | function wrappers alox_function_<x>.vi (FGV) --VI Server Run--> alox_function_<x>_state_machine.vi (13)        |
                       |      state machines --Generate User Event--> "Function UI Event" --> ui_config / ui_run panels, properties UI  |
                       |      state machines --Enqueue "<X> Job Finished"--> consumer                                                    |
                       | services (dynamic): functions status update, heartbeat, modules heartbeat, websocket, image stream            |
                       | dialogs (dynamic): splash, floating log, profiles, about; properties UI and function panels in sub-panels     |
                       | hardware clients: alox_camera_client / alox_xy_client / alox_wl_client --TCP--> AloxCamServer.exe,            |
                       |    AloxXYServer.exe, AloxWLServer.exe (started by alox_manager_server_module); command VIs wait on            |
                       |    "Synchronous Response" notifiers                                                                           |
                       +----------------------------------------------------------------------------------------------------------------+
```

#### 2.3 Lifecycle: the 11 frames of `alox_core_ui.vi` (observed `alox_core_ui d1..d18, d537..d549`)

| Frame | Title in diagram | What it does |
|---|---|---|
| 0 | "ALOX CORE GEN1.4 (01/06/2022), TT, ALOXTEC" | `alox_manager_server_module` "Any Server alive ?" → if true "Kill 'em All" (kills leftover server EXEs). A disabled block used to start `alox_splash_ui.vi` (d1). |
| 1 | "ABORT RUNNING VI's" | Lists `App.AllVIs`; every VI whose `Exec.State` is "Run top level", except itself, is aborted (`Abort VI`) (d6). |
| 2 | "'INIT' VARIABLES" | Globals `EXPO MODE LO` and `EXPO MODE HI` = "EXPO. SOFT" (d9). |
| 3 | "INIT UI" | `alox_manager_titlebar` "Init"; display "Zoom to Fit" (d10). |
| 4 | "'BOOT' CYCLE" | Bundles the refnums `This VI, Settings Sub Panel, HL Sub Panel, LL Sub Panel, ALARM, Display` (typedef `alox_typedef_boot_ctrls_refnums`) and calls `alox_core_boot_cycle`. "Check Dongle ?" = (`App.Kind` = "Run Time System") AND global `DONGLE`; result copied to global `Use Dongle` (d11). |
| 5 | "POST 'BOOT' : UPDATE CORE INFOS" | Shows ALARM; reads properties `general.core` ("CORE #%d"), `general.coretype` (0 → "PROCESS"), `general.mode` (0 → "LOCAL", otherwise "REMOTE"; the visibility of the REMOTE MODE control is also set from this test) (d12). |
| 6 | "'INIT' CYCLE" | Bundles `MONO_WL_CTRL, X_CTRL, Y_CTRL, VISION PROFILE, Camera type` (`alox_typedef_init_ctrls_refnums`) and calls `alox_core_init_cycle` (d16). |
| 7 | "SYSTEM READY (IDLE)" | `alox_manager_system_status_ID` "Set" 500 ("System Idle"); `alox_manager_log` "Clear Alarm" (d17). |
| 8 | "MAIN EVENTS LOOP" | Producer and consumer loops (d0, see 2.4 and 2.5). |
| 9 | "'EXIT' CYCLE" | (0) modules heartbeat "Get Report"; "Clear Alarm"; `alox_tools_select_camera` "Set default camera" Low Mag.; if the low-mag camera is up, one last `alox_function_ll_get_image` "Run Once" with Fixed exposure; global `F (ms)` = 0. (1) if runtime engine, joystick state = true; `alox_core_exit_cycle`; "Clear Alarm". (2) `alox_manager_server_module` "Kill 'em All" (d538, d541, d544). |
| 10 | "EXIT LABVIEW" | (0) `alox_core_shutdown_cycle`. (1) aborts all running VIs again. (2) reads `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders` "AppData", derives `...\Local\Temp`, lists that folder and deletes its files and sub-folders (the pattern is not visible). (3) if runtime engine, `Quit LabVIEW` (d546..d549). |

Boot cycle `alox_core_boot_cycle` (29 frames, observed d0, d2..d37, d124..d137; splash messages "BOOT: ..."):

0 splash "Init" + "Start" · 1 hostname "Init" · 2 "Load Alox properties": if `<Documents>\alox\settings\alox.ini` exists → properties
"Load", then "Check" (adds missing keys with defaults) · 3 reads the `FileVersion` of `AloxVision.exe` → splash "Version" · 4
language "Init", "Set Language" from `general.language` · 5 inner sequence: system / mesa / tilt / bowing status managers "Init" +
"Set Language" (default "en") · 6 system status 11 "Checking Dongle" · 7 "Verify valid dongle presence": `alox_gen2_dongle_check`
("Check at least one dongle is a ALOX2 one", fills global `Dongle Infos`) and `alox_ssd__serial_key_unlocking_dongle` with the
string "RSVDALOXTEC" and a file next to the EXE; if no valid dongle: system status 1515 "ERR Expired" or 1514 "ERR Invalid" · 8
status 12 "Initializing heartbeat" · 9 "Init Log": `alox_manager_floating_log_ui` "Init/Start/Get UI Refnum", `alox_manager_log`
"Init", "Set Log Window", "Set Alarm Window" · 10 functions status "Init", "Reset Status" · 11 "Init Popup panels manager":
`alox_manager_panels` "Init", "Set References" · 12 HL results panels "Init", "Set References" · 13 LL results panels "Init",
"Set References" · 14 "Init Alox folders, create folders if necessary" (28 inner frames, see 4.4) · 15 XY coordinate system "Init",
"Set Wafer Coord. Sys" from `hardware.xy.waferxorg`, `hardware.xy.waferyorg`, `hardware.xy.stagescoordsys` · 16 "Init SEMI
(SECS/GEM data) infos manager" · 17 tilt manager "Init", "Reset Tilt" · 18 calibration "Init", "Get From Properties" (Low Mag.,
High Mag.) · 19 core mode "Init", "Set Mode" Local · 20 image manager "Init" · 21 display "Init", "Set Disp. Refnum" · 22 "Init
Alox processing queue" (process queue "Init") · 23 "Init notifiers for websocket server" (`alox_manager_api_notifiers` "Init") · 24
"Init user events for functions user interfaces" (functions UI events "Init") · 25 "Init heartbeat service" (`alox_manager_heartbeat`
"Init") · 26 status 13 "Init webservice", 14 "Init websocket" · 27 "Init Websocket server" (`alox_api_manager_websocket_server` "Init")
· 28 splash "Stop", "Exit".

Init cycle `alox_core_init_cycle` (36 frames, observed d2..d92; splash messages "INIT: ..."):

0 splash · 1 version · 2 status 50 · 3 "Launch camera server executable": for Camera, XY Stages and Monochromator, `alox_manager_server_module`
"Server Running ?" → "Kill Server" if running → "Load Server" · 4 wait 7000 ms ("#TODO: check AloxCamServer.exe has started and
running") · 5 "Init Camera Client": camera manager "Init", wait 1000 ms, "Show UI" · 6/7 status 51, high-mag camera "Setup Camera",
"Start Camera", "Set LED" · 8/9 status 52, same for low-mag · 10/11 status 53, "Setup Z Stage" (a disabled block holds "Z Do
Reference", "Move home at startup") · 12 status 54 · 13 disabled duplicate of the XY server start · 14 status 55 · 15 wait 0 ms
(TODO check AloxXYServer.exe, the 5000 ms constant is not wired) · 16 XY client "Init", wait 1000, "Show UI", "Setup", "Get Position
XY" ("Do Reference" disabled) · 17 status 56 · 18 disabled duplicate of the monochromator server start · 19 wait 0 ms (TODO) · 20
monochromator client "Init", wait 1000, "Show UI" · 21 status 57 · 22 monochromator "Setup", "Set Units" Nanometers, "Set Position"
0 · 23 manual-measure ("LENGTH") "Init" · 24 status 58 · 25 "Init Profile": `general.profile` → profile "Load", "Check"; titlebar
"Update"; "Get Profiles List" → VISION PROFILE ring · 26 "Create default table if none available": exposure LUT "Get
Wavelengths", if empty "Create Default" · 27 UI values: `general.camera` → Camera type; profile `wavelength.wlmanual` → MONO_WL_CTRL
and monochromator "Set Position"; XY "Get Position XY" converted to wafer coordinates → X_CTRL, Y_CTRL · 28 `hardware.z.speed`
(default 5) → "Set Z Speed"; `hardware.xy.speed` (default 10) → XY "Set Speed" X and Y · 29 "Init Streaming manager" (websocket image
stream "Init") · 30 "Init Low-Level + High-Level functions": 14 inner frames, each calls `alox_function_<x>.vi` "Init" (example
frame 11 "Init 'Mesa' HL function") · 31 status 59 · 32 "Start Alox vision core watchdog if enabled": if `general.watchdog` and
runtime engine, `alox_manager_watchdog` "Start" · 33 `hardware.camera.highmag.Gain` / `lowmag.Gain` (coerced 1..10) → "Set Camera
Gain" · 34 status 100 "System Initialized" · 35 splash "Stop", "Exit".

Exit cycle `alox_core_exit_cycle` (30 frames, observed d2..d35): splash · "Set default camera" Low Mag. · "EXIT: Saving Calibration":
calibration "Set To Properties" (Low Mag., High Mag.) then properties **"Save"** · watchdog "Stop" · functions status "Stop" (stops the
update service) · modules heartbeat "Exit" · image stream "Exit" · for each of the 13 functions `alox_function_<x>` "Stop" then "Exit"
(MESA, TILT, BOW, DETECT CIRCLE, MEASURE MESA, REFOCUS, REFOCUS OXYD, AUTOFOCUS, TRACKING, Wavelength, Detect Object, Auto Exposure, Get
Image) · functions condition "Exit" · XY "Stop" X and Y, "Cleanup" · server module "Unload Server" XY Stages, XY "Exit" · camera "Z Stop",
"Cleanup Z Stage" · "Cleanup Camera" High Mag. and Low Mag. · "Unload Server" Camera, camera "Exit" · monochromator "Cleanup",
"Unload Server" Monochromator, "Exit" · splash "Stop", "Exit". Comments: "We send the 'Unload Server' TCP command to exit server
properly, before exiting the client. #TODO: Exit Client + Kill Server ?".

Shutdown cycle `alox_core_shutdown_cycle` (27 frames, observed d2..d34): splash · "SHUTDOWN: Closing windows": closes
`alox_properties_ui.vi`, `alox_profiles_ui.vi`, `alox_about_ui.vi` if their `Exec.State` shows them running · then "Exit" on: panels,
HL results panels, LL results panels, websocket server, heartbeat service, API notifiers, process queue, functions UI events,
functions status, bowing / tilt / mesa / system status, SEMI, tilt, XY coordinate system, manual measure, calibration, image,
display; floating log UI "Hide", "Stop", "Exit"; log "Exit"; titlebar "Exit"; hostname "Exit"; splash "Stop", "Exit".

#### 2.4 Producer loop (UI events) — `alox_core_ui d0, d19..d435`

Registers the `Function UI Event` user event (functions UI events "Get Event" → `Reg Events`) and gets the process queue ("Get
Queue"). The Event structure has 73 cases (all labels read from d19..d435):

- `[0] Timeout`, timeout = property `general.displaytimer`. Runs a 6-frame sequence (d20, d35, d36, d57, d58, d61, d19):
  (0) modules heartbeat "Get Report"; if the WL server is up, monochromator "Get Position" → `MONO_IND`; XY / Z "Joystick Enabled?"
  → globals `joystick XY`, `XY JOYSTICK`, `Z JOYSTICK`, `joystick Z`. (2) enables or greys the joystick and ThermalMeas controls
  from functions condition "Get Any Running ?" / "Get Running" (Tracking, Search Object, Bowing Measure) and the hardware report;
  if a function runs, Tab Control = "HIGH-LEVEL MEASUREMENTS". (3) global `F (ms)` = loop period. (4) settings tab caption
  "SETTINGS (<panel title>)" from panels "Last open"; Camera type disabled while the Get Image panel is open. (5) system status
  "Status timer"; if it returns "CamStatusChanged", the Camera type control is reloaded.
- `[1] <Function UI Event>: User Event`: only "Exit Alox Events Loop" is handled ("EXIT APP should be called when
  '/system/shutdown' (PUT) API received"): enqueues `Exit` and stops the producer; other events fall into "Default" (d64..d66).
- `[2] Key Down`: arrow keys jog the XY stage ("Get Position XY" → "Move Absolute XY") when no function runs (d67).
- `[3] PROFILES_REFRESH`, `[12] VISION PROFILE`, `[13] EDIT PROFILES`, `[14] SAVE PROFILE`: profile handling. EDIT PROFILES
  checks right "Edit Profile", then opens `alox_profiles_ui.vi` by path and runs it without waiting (Auto Dispose Ref) (d120).
- `[4] "Pane Panels": Pane Size`, `[19] ZOOM FIT`, `[20] SAVE IMAGE`, `[8..10] "Display": Mouse Down / Mouse Up / Mouse Move`
  (manual length measurement through `alox_manager_manual_measure`).
- `[5] ABOUT`: runs `alox_about_ui.vi` the same way (d87). `[6] USER`: `alox_login_ui` → titlebar "Update" → Generate Event "User
  Profile Changed" → log "Login (clicked)" (d92). `[7] REMOTE MODE`: updates the CORE_MODE label LOCAL/REMOTE from `general.mode`,
  log "Core mode" (d95). `[11] SETTINGS`: right "Edit Properties" → panels "Open Panel" "Vision Core" (the properties UI goes into
  the Settings sub-panel), Tab Control = SETTINGS, log "General properties (clicked)" (d112). `[15] EXIT`: right "Exit App" → dialog
  "CONFIRM EXIT ?" → enqueue `Exit`, stop the producer (d136). `[16] Panel Close?`, `[17] LOG` (floating log "Show"), `[18] CLEAR ALARM`.
- `[21..63]` run / start / stop / options buttons of each function: GET_IMAGE (RUN, START, STOP, OPT), AF (autofocus), AF_FINE
  (refocus low-mag), AF_OXYD (refocus oxyd), DET_CIRCLE, MES_VCSEL (measure oxyd = "DETECT OXID MESA"), TRACK, WL (RUN, RUN_AUTO,
  STOP, OPT), SEARCH_OBJ, MES_MESA (RUN_SINGLE, RUN_CONT, STOP, OPT), MES_BOW, MES_TILT, AUTO_EXPO (RUN_AUTO, RUN, STOP, OPT). The
  `_OPT_` buttons open the function's settings panel through `alox_manager_panels` "Open Panel" (inferred from panels d0).
- `[64] XY JOYSTICK`, `[65] Z JOYSTICK`, `[66] ThermalMeas`, `[67] Camera type`, `[68] MONO_MOVE`, `[69] X_MOVE`, `[70] Y_MOVE`,
  `[71] OPTICAL VIEWFINDER`, `[72] Jump Button`.

#### 2.5 Consumer loop (process queue) — `alox_core_ui d0, d436..d536`

Comment: "Consume events generated by the user interface (local/manual mode) or the REST/webservice API (remote mode)". The queue is
the named queue `alox.process.queue`, element `alox_typedef_process_queue_item` = {`Method` (enum U16), `Variant`}, created by
`alox_manager_process_queue` "Init" with **max size 3** (`alox_manager_process_queue d0`). Cases (labels and comments from d444..d535):

| Method (value) | Variant payload | Sender | Handling (observed unless marked) |
|---|---|---|---|
| Init (0) | – | – | empty |
| Exit (1) | – | producer (EXIT button, Exit Alox Events Loop) | "Exit Consumer loop command sent by Producer Loop": stops the consumer |
| Measure Mesa (2) | `alox_typedef_process_queue_item_mesa_params` | REST `PUT /v1/functions/measuremesa` | see 2.6 |
| Measure Tilt (3) | `..._tilt_params` | REST measuretilt | "Start 'Tilt Measurement' function, refresh user interface (job infos, ...)" |
| Measure Bowing (4) | `..._bowing_params` | REST measurebowing | same for bowing |
| Stop (5) | – | REST `PUT /v1/functions/stop` | system status 802; log string 001651 "stop current function"; 5-frame sequence; frame 3: panels "Close Panel", "Clear Alarm", disable Z joystick, camera "Z Move Absolute" 0, log "Camera goes up" (d473) |
| Load Profile (6) | profile name (string) | REST `PUT /v1/system/profile` | profile "Load", "Check", "Get Profiles List"; if loaded: property `general.profile` = name, Generate Event "Profile Changed", VISION PROFILE ring, titlebar "Update", log "Profile loaded" (d497) |
| Profiles Folder Changed (7) | – | properties UI (inferred) | "Profiles folder changed, update profiles list" |
| Get Image (8) | camera | REST grabhighmag / grablowmag | "Supervisor called '/camera/grabhighmag' or '/camera/grablowmag' API" |
| Module Heartbeat (9) | report | `alox_modules_heartbeat_service` | "The hardware module service reported a hardware module error; event has been also sent directly ... to the supervisor through websocket" |
| Abort (10) | – | REST `GET /v1/functions/abort` | "Emergency stop of all running functions" |
| Refresh Profiles (11) | – | profiles UI (inferred) | refresh the profile list |
| Refresh Core Infos (12) | – | properties UI / REST mode (inferred) | refresh core number / type / mode |
| Log Window Hidden (13) | – | `alox_log_ui` (inferred) | – |
| System Reset (14) | – | REST `PUT /v1/system/reset` | status 801 "System Reset", log, status 500. The bundle of init refnums is built but not wired to anything: the re-init call is gone (d525) |
| Axis Referencing (15) | – | ? | comment copied from System Reset |
| System Shutdown (16) | – | REST `PUT /v1/system/shutdown` | "shutdown SYSTEM": ends with the Exit path (inferred: Generate Event "Exit Alox Events Loop") |
| Show UI (17) | bool | REST `PUT /v1/system/showui` | shows or hides the UI |
| Module Resetted (18) | module | modules heartbeat service | "The hardware module service has resetted a hardware module" |
| Mesa Job Finished (19) | SEMI Job Infos | mesa state machine | see 2.6 |
| Bowing Job Finished (20), Tilt Job Finished (21) | SEMI Job Infos | bowing / tilt state machines | same as Mesa (their comments are copies of the Mesa one) |

#### 2.6 How a function (measurement) is started, run and reported

The 13 functions (enum `alox_typedef_functions_condition_manager_funcname`: Mesa Measure, Tilt Measure, Bowing Measure, Get Image,
Autofocus Large, Refocus, Refocus Oxyd, Detect Circle, Tracking, Measure Oxyd, Search Wavelength, Search Object, Auto Exposure)
each have a wrapper FGV `alox_function_<x>.vi` (part 3). Observed on `alox_function_hl_mesa_measurement d3..d15`, methods
`alox_typedef_mesa_measurement_function_method` (Init, Exit, Start, Stop, Get Status, Get Last Measure, Abort):

- **Init** (called by init cycle frame 30): `Open VI Reference` on `alox_function_hl_mesa_measurement_state_machine.vi`, or on
  `..._state_machine_simulation.vi` if property `simulation.mesa.enabled`; status Off / Stopped.
- **Start**: if not already "Running", writes the input parameters into the state-machine VI and runs it without waiting
  (inferred for the hidden frame; the wrapper is a "Running"/"Start" state machine).
- **Stop**: `Ctrl Val.Set` "Stop" = T, waits until `Exec.State` is Idle or 5 s timeout (a label says "30 sec"), then functions
  condition "Set Running" Mesa Measure = F ("Force Stop Status").
- **Exit**: closes the VI reference.

Local start (operator), example MES_MESA_RUN_SINGLE_BTN (`alox_core_ui d315`): `alox_manager_users` "User Rights" (action "Mesa")
→ if allowed: system status "Set" 200 "Starting", mesa status "Set" 200 → `alox_function_hl_mesa_measurement_bundle_profile_parameters`
(reads the current profile) → `alox_manager_semi` "Get" → parameters overridden (Tilt Correction = F, Singleshot = T, positionsctrl,
analysis) → `alox_function_hl_mesa_measurement` "Start" → Tab Control = HIGH-LEVEL MEASUREMENTS → log "MESA Measurement (start
single clicked)" → functions status "Reset Status". The XY and Z joysticks are switched off.

Remote start (supervisor): the REST VI `alox_api_v1_put_functions_measure_mesa` (part 5) checks functions condition, sets the mesa
status, reads the profile parameters and enqueues `Measure Mesa` with {SEMI Job Infos, Measure Mesa Params}. Consumer
(`alox_core_ui d451..d458`): (0) if property `log.webapi`, log string 001652 "function 'mesa measurement' starts"; if `analysis`,
joystick off; ROI reset; globals `LAUNCH FROM HMI AE` / `LAUNCH FROM HMI WL` = F. (1) functions status "Reset Status", panels "Close
Panel", Tab Control = HIGH-LEVEL MEASUREMENTS, "Clear Alarm" ("Reset and close all panel"). (2) mesa status 200; `alox_manager_semi`
"Set" (job infos of the request); profile parameters overridden by the request ("Use x,y,tilt,target REST API Parameters": Start
Position, Tilt Correction, Oxyd Target, Singleshot, positionsctrl, analysis); function "Start"; log "MESA Measurement (Start)";
functions UI events "Generate Event" "SEMI Data Changed" ("Update SEMI data in opened config UI").

Run (part 3, seen only from the calls): the state machine (mesa states: Start, Move Start, Wait Move, Search Init, Search Object,
Autofocus Large, Refocus, Detect Circle Init, Detect Circle, Track MESA, Auto Expo, Search Wavelength, Set Wavelength, Measure MESA Init,
Measure MESA Refocus, Measure MESA, Display Save, Display Save ERROR, Finished, Error, Stopped) calls the low-level function wrappers,
sets functions condition "Set Running", updates `alox_manager_mesa_status_ID` (each change is pushed to the `mesa_status` notifier, observed
`alox_manager_mesa_status_ID d0`), generates Function UI Events ("MESA Started", "MESA State Changed", "MESA Measured", "MESA Finished"),
sends `started` / `measure mesa` / `failed` notifications (calls `alox_tools_cast_started_semi_job_to_json_report`, `..._failed_...`,
`alox_tools_cast_measure_mesa_to_json_report`), shows the HL results panel, saves images and job files.

Report: at the end the state machine enqueues `Mesa Job Finished` with the SEMI job infos (inferred from the call to
`alox_manager_process_queue` and the consumer case). Consumer (`alox_core_ui d531`): `alox_tools_cast_finished_semi_job_to_json_report`
with function name "measure_mesa" → typedef `alox_typedef_semi_job_finished_event_json_report` {Event, Function, Job {Control_Job_ID,
Process_Job_ID, Wafer_ID, Lot_ID}} → flattened to JSON → `alox_manager_api_notifiers` "Get Notifier" "finished" → `Send Notification`
→ `alox_api_websocket_service` sends the websocket `finished` event (part 5); log "Mesa (Finished)"; joystick re-enabled when no error.
The changelog (1.0.0.9) explains the reason: "function 'finished' websocket event sent before the function has really finished".

Concurrency rule (`alox_manager_functions_condition d5, d12..d45`): an FGV with one "running" Boolean per function. "Get Condition OK"
returns true only when **no** function at all is running ("Cannot start function if any other HL/LL function running"; the LL cases
apply the same test inside "Enabled" diagram-disable blocks; an unknown function gives error 9002 "Invalid function"). "Get
Joystick OK" = no HL function running. "Get Image" has its own case; global `Get Image Run Once` is a "special case" in
`alox_manager_functions_status` (continuous Get Image when nothing else runs, changelog 1.0.0.11).

Function status display: `alox_manager_functions_status` stores the refnums of all buttons and indicators of the core UI ("Store all
functions buttons/status references in variant attribute"). "Update" queries, for the 13 functions, functions condition "Get
Condition OK" and the wrapper "Get Status", then enables the buttons and sets the indicators. "Start" = "Start continuous function
status updating in a background task (until 'Stop' called)": that task is `alox_functions_status_update_service.vi` (loop: if not
`Stop`, functions status "Update"; wait `Frequency (ms)`), started dynamically (inferred).

#### 2.7 Communication summary

| Mechanism | Name / owner | Used for |
|---|---|---|
| Named queue | `alox.process.queue` (`alox_manager_process_queue`), max 3 elements | commands to the core consumer (2.5) |
| User event | `Function UI Event` {Event (44 values of `alox_typedef_functions_ui_event_type`), Param variant} (`alox_manager_functions_ui_events`) | state machines and core → function panels, properties UI, core producer |
| Named notifier | name in global `Close Settings UI Notifier Name` | "Generate Event" "Close Settings Panels" then waits up to 5000 ms for the open settings UI to confirm it closed (`alox_manager_functions_ui_events d0`) |
| Notifiers | `alox_manager_api_notifiers` (types of `alox_typedef_notifiers_type`: heartbeat, status, measure tilt/bowing/mesa, finished, failed, error, started, camz/xy/monochro_hardware_warning, hardware_reset, mesa/tilt/bowing_status, image) | events pushed to the websocket service (part 5) |
| Notifiers (one per request) | "synchronous calls" (2.8) | request/response between hardware command VIs and client loops |
| FGVs | all managers | shared state (singletons) |
| Global variables | `alox_core_global_variables` (see 4.6) | flags, queue names, dongle info, default exposure tables, web service settings |
| VI Server | `Open VI Reference`, `Run VI`, `Ctrl Val.Set`, `SubPanel Insert VI`, `Abort VI`, `FP.Close` | dynamic UIs and state machines |
| TCP | hardware clients ↔ server EXEs (part 2) | hardware |
| HTTP REST / WebSocket | `alox.lvws` port 8002 in the build; websocket port property `websocket.port` (6123) | supervisor (part 5) |

#### 2.8 Synchronous calls (folder `synchronous calls`)

Request/response over single-use named notifiers (observed `Synchronous Raw Response Wait d0`, `Synchronous Wait for Raw Create d0`,
`Synchronous Response Post d0..d2`, `Generate Unique Notifier Name d0`, `Synchronous Response Wait U32 d0..d2`):

1. A hardware command VI (`alox_camera_command_*`, `alox_xy_command_*`, `alox_wl_command_*`, part 2) calls `Synchronous Wait for Raw
   Create` → `Generate Unique Notifier Name` (GUID from `tools_create_guid`) and sends the name with its command to the client loop.
2. It calls the typed `Synchronous Response Wait <T>` (polymorphic `Synchronous Response Wait`: Boolean, DBL, DBL 2D Array, Error
   Cluster, Image, STR, U32) → `Synchronous Raw Response Wait`: obtains the notifier by name, `Wait on Notification` with `timeout in
   ms (-1)`, returns the string `Response Data` and `timed out?`, releases the notifier; the typed VI unflattens the string into the
   type.
3. The client loop (`alox_camera_client`, `alox_xy_client`, `alox_wl_client`) answers with `Synchronous Response Post` {Notifier
   Name, Response (Flattened)}: if the name is not empty, obtain, `Send Notification`, release.

The response is the LabVIEW flattened form of the value: a rewrite must replace this with its own futures / promises.

### 3. Components

| VI | Role (one line) | Used by (apps / callers) |
|---|---|---|
| `alox_core_ui.vi` | Top-level VI: 11-frame lifecycle, producer (73 UI events) and consumer (process queue) loops | Alox (top level) |
| `alox_core_boot_cycle.vi` | Boot: properties, language, dongle, log, panels, folders, software managers, websocket (29 frames) | Alox / core_ui |
| `alox_core_init_cycle.vi` | Init: starts server EXEs and clients, cameras, Z, XY, monochromator, profile, functions, watchdog (36 frames) | Alox / core_ui |
| `alox_core_exit_cycle.vi` | Exit: saves calibration to properties, stops services and functions, cleans up and unloads servers | Alox / core_ui |
| `alox_core_shutdown_cycle.vi` | Shutdown: closes dialogs, calls "Exit" on every software manager | Alox / core_ui |
| `alox_core_global_variables.vi` | Global variables (no diagram), see 4.6 | Alox, AloxSim / 54 VIs |
| `alox_main_vi_reference.vi` | Opens a VI reference to `alox_core_ui.vi` (path = own path, stripped twice, + `alox_core_ui.vi`) and returns path and reference | Alox / 3 function ui_config VIs |
| `alox_manager_process_queue.vi` | FGV; methods `Init, Exit, Get Queue`; owns the queue `alox.process.queue` (max 3) | Alox, AloxSim / 28 (core, REST VIs, state machines, services, dialogs) |
| `alox_manager_functions_ui_events.vi` | FGV; `Init, Exit, Generate Event, Get Event`; owns the user event `Function UI Event` | Alox, AloxSim / 43 |
| `alox_manager_functions_condition.vi` | FGV; `Init, Exit, Set Running, Get Running, Get Condition OK, Get Joystick OK, Get Any Running ?`; one-function-at-a-time rule | Alox, AloxSim / 58 |
| `alox_manager_functions_status.vi` | FGV; `Init, Exit, Update, Get Ctrl Refnum, Reset Status, Start, Stop`; drives the run buttons and status indicators of the core UI | Alox / core_ui, cycles, update service |
| `alox_functions_status_update_service.vi` | Background loop calling functions status "Update" every `Frequency (ms)` until `Stop` | Alox (started dynamically, inferred) |
| `alox_manager_system_status_ID.vi` | FGV; `Init, Exit, Set, Get, Set Language, Status timer`; system status code; idle timer moves the camera up and selects low-mag | Alox, AloxSim / 24 |
| `alox_manager_mesa_status_ID.vi`, `alox_manager_tilt_status_ID.vi`, `alox_manager_bowing_status_ID.vi` | FGVs; `Init, Exit, Set, Get, Set Language, Get Description`; status code of each HL function, pushed to `mesa_status` / `tilt_status` / `bowing_status` notifiers on change | Alox, AloxSim / cycles, core_ui, REST, state machines |
| `alox_manager_properties.vi` | FGV; application settings ("properties"), `alox.ini`; see 4.1 | Alox, AloxSim / 153 |
| `alox_manager_profile.vi` | FGV; recipes ("profiles"), zip archives; see 4.2 | Alox, AloxSim / 68 |
| `alox_manager_users.vi` | FGV; `Init, Exit, Get User, Login, Logout, User Rights, Check Password`; see 4.3 | Alox / core_ui, login UI, titlebar, properties UI |
| `alox_manager_semi.vi` | FGV; `Init, Exit, Set, Get`; stores the current SEMI Job Infos (5 strings) | Alox / core_ui, cycles, HL ui_config/ui_run, properties UI |
| `alox_manager_log.vi` | FGV; `Default, Init, Exit, Set Log Window, Set Alarm Window, Clear Log, Clear Alarm, Log Info, Log Warning, Log Error, Clear All, Save Log, Log Error In`; timestamped log and alarm windows, file save | Alox, AloxSim / 123 |
| `alox_manager_floating_log_ui.vi` | FGV; `Init, Exit, Start, Stop, Show, Hide, Get UI Refnum`; runs `alox_log_ui.vi` dynamically | Alox / core_ui, boot, shutdown |
| `alox_manager_language.vi` | FGV; `Init, Exit, Set Language, Get String`; string IDs from `.lang` files | Alox, AloxSim / 90 |
| `alox_manager_titlebar.vi` | FGV; `Init, Exit, Update`; window title (user, profile; inferred from calls) | Alox / core_ui, init, shutdown, properties UI |
| `alox_manager_splash_ui.vi` | FGV; `Init, Exit, Start, Stop, Notify` (Version, Message, Exit); runs `alox_splash_ui.vi` dynamically | Alox / 4 cycles |
| `alox_manager_panels.vi` | FGV; `Init, Exit, Set References, Open Panel, Close Panel, Last open`; puts a function settings VI (`functions\alox_function_<x>_ui_config.vi`) or the properties UI into the Settings sub-panel (15 panel types) | Alox / 24 (core, ui_config VIs) |
| `alox_manager_hl_results_panels.vi` | FGV; `Init, Exit, Set References, Open Panel, Close Panel`; HL results sub-panel (Bow, Tilt, Mesa) | Alox, AloxSim / core, HL state machines |
| `alox_manager_ll_results_panels.vi` | Same for LL results (10 panel types) | Alox / core, LL state machines |
| `alox_manager_display.vi` | FGV; `Init, Exit, Set Disp. Refnum, Get Disp. Refnum, Display Image, Get Image, Get ROI`; main image control | Alox, AloxSim / 25 |
| `alox_manager_image.vi` | FGV; `Init, Exit, Get Image, Get Merged Overlays, Merge Overlays`; last image and overlays (IMAQ) | Alox, AloxSim / 26 (core, REST jpeg image, state machines) |
| `alox_manager_calibration.vi` | FGV; `Init, Exit, Set Calib, Get Calib, Reset Calib, Pixels To Real, Real To Pixels, Is Calibrated?, Get From Properties, Set To Properties`; pixel size per camera | Alox / 16 |
| `alox_manager_exposure_wl_lut.vi` | FGV; wavelength → exposure table (14 methods, see 4.2) | Alox, AloxSim / 14 |
| `alox_manager_manual_measure.vi` | FGV; length tool on the display (`alox_typedef_length_manager_method`: Clear, Set Start Point, Set End Point, Get Results, Overlay Results; inferred) | Alox / core_ui, init, shutdown |
| `alox_manager_tilt.vi` | FGV; `Init, Exit, Set Tilt, Get Tilt, Reset Tilt`; last measured wafer tilt | Alox, AloxSim / REST tilt, tiltreset, mesa SM, simulator |
| `alox_manager_core_mode.vi` | FGV; `Init, Exit, Set Mode, Get Mode` (Local / Remote); set to Local at boot, no other reader in Alox | Alox, AloxSim / boot, simulator |
| `alox_manager_hostname.vi` | FGV; `Init, Exit, Update, Get`; host name used in REST URLs | Alox / REST liveimage, events, properties UI |
| `alox_manager_configuration.vi` | Empty VI (error in → error out) | none |
| `alox_error.vi` | Error helper (translates ALOX error codes through the language manager, dialog) (from name/calls) | Alox, AloxSim / 58 |
| `alox_get_font_properties.vi` | Reads overlay font properties (`overlays.font*`) for the current camera | Alox, AloxSim / 17 state machines and ui_config |
| `alox_tools_select_camera.vi` | Current camera from profile/properties (`alox_typedef_camera_selection_method`: Profile Current, Set default camera) | Alox, AloxSim / 7 |
| `alox_login_ui.vi` | Login dialog: on OK, users "Login" (USER, PASSWORD) | Alox / core_ui |
| `alox_profiles_ui.vi` | Profile editor dialog: list, create, delete (`<profiles>\<name>.zip`, with confirmation), preview, edit | Alox (run dynamically by core_ui) |
| `alox_profile_preview_ui.vi` | Profile preview window | Alox / profiles UI |
| `alox_properties_ui.vi` | Core settings panel ("Vision Core" panel, 12-frame sequence, 300 ms refresh of status, tilt, users, hardware, network, overlays tabs) | Alox (inserted by panels manager) |
| `alox_change_password_ui.vi` | Writes a new base64-encoded password into properties | Alox / properties UI |
| `alox_calibration_ui.vi` | Camera pixel-size calibration dialog (camera, get image) | Alox / properties UI |
| `alox_vignetting_ui.vi` | Flat-field (vignetting) image acquisition dialog; image saved under the user Documents folder | Alox / properties UI |
| `alox_wl_auto_exposures_table_ui.vi` | Editor for the wavelength/exposure table (calls 6 GUID-named VIs, probably Express VI instances, not in the export) | Alox / 3 ui_config VIs, properties UI |
| `alox_wl_fixed_exposures_table_ui.vi` | Older fixed-exposure table editor | not built; only caller is `..._search_wavelength_ui_config_.vi` |
| `alox_log_ui.vi` | Floating log window (enqueues "Log Window Hidden", inferred) | Alox (dynamic) |
| `alox_splash_ui.vi` | Splash window | Alox (dynamic) |
| `alox_about_ui.vi` | About dialog (version, dongle info) | Alox (run dynamically by core_ui) |
| `alox_browser_ui.vi` | AloxViewer: PNG file/folder browser with slider, IMAQ calibration data (file, pixel size X/Y, units) and the key/value data embedded in the PNG | AloxViewer (top level) |
| `alox_tool_extract_mesa_fit_data.vi`, `alox_tools_filter_invalid_measures.vi`, `alox_tools_fit_diameters_measures.vi` (RANSAC line fit), `alox_tools_get_mesa_remaining_time.vi`, `alox_tools_convert_mesa_measures_to_micrometers.vi` | Mesa analysis helpers: fit of oxide diameter versus time, remaining time to the target, pixel → µm (from name/calls) | Alox, AloxSim / mesa state machines, core_ui, measure_mesa ui_config |
| `alox_tools_analyze_mesa_simulation.vi`, `alox_tools_mesa_measurement_generate_simulation_data.vi`, `alox_tools_get_overlays_states.vi`, `alox_tools_set_overlays_states.vi` | Simulation and overlay helpers | AloxSim only |
| `alox_tools_output_folder_path.vi` | Output folder relative to the EXE | none |
| `Generate Unique Notifier Name.vi`, `Synchronous Wait for Raw Create.vi`, `Synchronous Raw Response Wait.vi`, `Synchronous Response Wait.vi` (polymorphic) + 7 typed `Synchronous Response Wait <T>.vi` (Boolean, DBL, DBL 2D Array, Error Cluster, Image, STR, U32), `Synchronous Response Post.vi` (12 VIs) | Request/response helpers (2.8) | Alox, AloxSim / ~90 hardware command VIs, 3 hardware clients |

### 4. Data and configuration

#### 4.1 Properties (application settings) — `alox_manager_properties`

- Methods (`alox_typedef_properties_manager_method`): `New, Load, Save, Get Property, Set Property, Add Property, Del Property, Get
  Properties Names, Clear, Set Default Property, Property Exists?, Check`. Storage in memory: variant attributes ("store properties
  keys/values pairs into a 'red-black' tree"), key = `section.key` (for example `general.displaytimer`), value = variant
  (`alox_manager_properties d0`).
- "New" creates the default value of every key, section by section: 00 general, 01 heartbeat, 02 log, 03 hardware.camera, 04
  hardware.z, 05 hardware.camera.highmag, 06 hardware.camera.lowmag, 07 hardware.monochromator, 08 hardware.xy, 09 hardware.chuck, 10
  websocket, 11 simulation.mesa, 12 overlays, 13 user.password, 14 user.rights, 15 hardware.pyro, 16 hardware.bandit. "Check" adds
  every missing key with its default ("IMPORTANT NOTE: Add new properties into 'New' & 'Check' cases"). The defaults are therefore
  hard-coded in this VI (1312 diagram images).
- File: `<Windows user Documents>\alox\settings\alox.ini`. At boot, if missing, it is copied from `alox_default.ini` in the
  application folder (`alox_core_boot_cycle d39`). Format: INI, each key is written as `<key>_<TYPE>` in its `[section]`; "Load" splits
  the key at its last `_` and converts by type (an unknown type raises error 9006 "Invalid property '%s'"); "Save" rebuilds
  section/key from the name and writes arrays as `%f;`-separated lists (`alox_manager_properties d1282, d1294`). Types seen in
  `alox_default.ini`: `STR, I32, U32, DBL, BOOL, PATH`.
- Written by: exit cycle (calibration "Set To Properties" + "Save"), properties UI, change-password UI, REST `PUT
  /v1/system/configuration`, Load Profile (`general.profile`).
- Important keys used by this part: `general.profile, general.language, general.mode (0 = LOCAL), general.core, general.coretype,
  general.displaytimer, general.camera, general.watchdog, general.modehotkey (121), log.webapi, hardware.xy.waferxorg/waferyorg/
  stagescoordsys/speed, hardware.z.speed, hardware.camera.highmag.Gain/lowmag.Gain, hardware.camera.delaybulbswitchoff, overlays.*,
  user.password.*, user.rights.*, simulation.mesa.enabled`.
- `alox_default.ini` (418 lines) has numbers written with a decimal **comma** (`100,000000`), duplicated keys (`xaxis_I32`,
  `nbpoints9_U32` ×4, `slowspeed_I32`/`_DBL`), and six empty `[]` sections holding `bowchuckA/B`.

#### 4.2 Profiles (recipes) — `alox_manager_profile`

- Methods (`alox_typedef_profile_manager_method`): `Get Profiles List, Get Current Profile, New, Load, Save, Get Properties List, Get
  Property, Get Property (String), Set Property, Set Property Default, Add Property, Del Property, Get All Properties, Set All
  Properties, Clear, New To Variant Out, Load to Variant Out, Save From Variant In, Check Variant In, Check`. The "...Variant"
  methods let the profile editor work on a copy without touching the active profile.
- 17 sections (`alox_manager_profile d0`): 00 general, 01 get.image, 02 get.image.highmag, 03 get.image.lowmag, 04 autofocus, 05
  refocus, 06 refocus.oxyd, 07 detect.vcsel (Detect Circle), 08 measure.vcsel, 09 measure.oxyd, 10 search.object, 11 wavelength,
  12 tracking, 13 autoexpo, 14 measure.mesa, 15 measure.bowing, 16 measure.tilt. Defaults are hard-coded in "New" / "Check" (1222
  images); a default wavelength/exposure table (700..950 nm, step 25) is a constant of this VI.
- Files: `<profiles folder>\<name>.zip` (profile manager comment: `<ALOX DATA>/profiles/gen2/*.zip`; boot comment: default
  `D:\Alox\profiles\gen2`). "Load": clears `<Documents>\alox\profile`, unzips the archive into it, reads `<name>.ini` (same typed-key
  INI format). "Save": writes `<Documents>\alox\profile\<name>.ini` and zips the folder to `<profiles folder>\<name>.zip`
  (`tools_zip_profile_folder`). The variant methods use `<Documents>\alox\profile_temp` (`alox_manager_profile d1149..d1178`). The
  zip may also hold other files of the recipe (for example the pattern-matching models referenced by the tilt "Dual Objects"
  parameters) (inferred).
- Active profile name is the property `general.profile` ("DEFAULT" in `alox_default.ini`). If no profile exists at boot, one is
  created ("Check if at least one profile exists, if not create a default one, and set it as default", `alox_core_boot_cycle d46`).
- Exposure table: `alox_manager_exposure_wl_lut` keeps the wavelength → exposure table in the profile property `general.exposures`
  (2D array). Methods: `Create Default, Import CSV, Export CSV, Get Exposure(WL), Get Fixed Expo, Set Fixed Expo, Get Auto Expo, Set
  Auto Expo, Reset Auto Expo, Save calib values, Has Auto Expo ?, Has Fixed Expo ?, Get Wavelengths, Set Wavelengths`. CSV columns
  (diagram note): 1 WL (nm), 2 ExpoTh (µs), 3 ExpoMin, 4 ExpoMax, 5 BMin, 6 BMax (grey levels), 7 ExpoAuto; columns 1 and 2 mandatory;
  if 3 and 4 are missing, ExpoMin/Max = ExpoTheoric ∓ 20 %. TODO in the diagram: "review the default theoretical exposure table,
  not suited to the cameras of the OPTO module" (`alox_manager_exposure_wl_lut d0`).

#### 4.3 Users and rights — `alox_manager_users`

- Users (`alox_typedef_user_type`): Operator (0), Engineer (1), Admin (2). The FGV holds the current user.
- "Login" / "Check Password": property `user.password.<user>` → Base64 decode → compared with the typed password; on success
  `User Out` = `User In`, otherwise an error status and the user does not change (`alox_manager_users d6, d174`). Passwords are only
  Base64-encoded (`alox_default.ini`: engineer `QUxPWA==` = "ALOX", operator and administrator empty). Changed with
  `alox_change_password_ui`.
- "User Rights": case on `User Action` (32 values of `alox_typedef_user_action`: Exit App, Create/Delete/Edit/Change Profile, Edit
  Properties, then `<function>` and `<function> Setup` for the 13 functions) × user → Boolean property
  `user.rights.<admin|engineer|operator>.<action>` (default F) (`alox_manager_users d12`). `alox_default.ini` also has nine
  `properties<tab>` rights (admin, camera, chamber, datalogging, modules, monochromator, network, overlays, xyzstages), used by the
  properties UI (inferred).
- Checked on UI actions only (run buttons, SETTINGS, EDIT PROFILES, EXIT). The REST API does not check users (part 5: no
  authentication on any route).

#### 4.4 Folders created at boot (`alox_core_boot_cycle` frame 14, d38..d123)

`<Documents>\alox\settings` (alox.ini), `<Documents>\alox\profile` (unzipped current profile), `<Documents>\alox\profile_temp`,
`<ALOX DATA>` (default `D:\Alox`), profiles (default `D:\Alox\profiles\gen2`), `<ALOX DATA>\exposures`, `<ALOX DATA>\data\coreX`,
`...\logs`, `<ALOX DATA>\images\{Misc, refocus, autoexpo, mapping, oxid, tilt, bowing}`, `<ALOX DATA>\files\{autofocus, refocus,
autoexpo, wavelength, mesa, bowing}`, `<ALOX DATA>\JobHistory`. The per-core sub-folder (`core%d` from `general.core`) is in disabled
blocks, so all cores share the same folders. The folder roots come from the `tools_alox_shared_*_folder` VIs (part 4);
`alox_setup.ini` defines `[folders] vision=/D/alox/02-Vision, data, profiles, settings, exposures, jobhistory=/D/alox/04-JobHistory`,
which differs from the comments (inferred: the tools read `alox_setup.ini`).

#### 4.5 Other files

- Log: `alox_manager_log` writes lines `%d/%m/%Y - %H:%M:%S%3u - <Info|Warning|Error> -- <text>` at the top of the log window,
  counts lines (Max Lines 65535; `log.maxlines` = 1000), "Save Log" appends the window text to a file (open or create, end of file)
  (`alox_manager_log d12, d68`). The changelog names `vision_events.log`; the log manager calls `tools_alox_shared_vision_folder`
  (inferred location). `[log]` keys select which data and images the functions save.
- Strings: `<application>\translations\alox_<lang>.lang`, plus `system_en.lang`, `mesa_en.lang`, `tilt_en.lang`, `bowing_en.lang`
  for the status codes (format `NNNNNN;text;comment`, ranges in `translations/readme.txt`) (inferred path).
- Status codes (system_en.lang and the diagram note "SPECS AET POUR CODES DE STATUS"): 0 initial; 10..99 stopped, waiting for a
  condition (11 Checking dongle, 12 heartbeat, 13 web server, 14 websocket, 50..59 init steps); 100 initialized / ready; 200 starting;
  201..499 sequenced steps (201 Bowing, 202 Tilt, 203 Mesa, 204 low-level function running); 500 idle / finished OK; 600..799 waiting,
  paused; 800..899 stop actions (800 exit, 801 reset, 802 stop, 803 abort); 1000..1999 errors (1001..1063 bit field of failed
  hardware: High mag, Low mag, X, Y, Z, Monochromator; 1513..1516 cannot boot: no / invalid / expired dongle, missing resource).
  A diagram TODO asks for a CSV of messages per code.
- Error codes: `errors/alox-errors.txt` defines 9000, 9001, 9100, 9200, 9300, 9301, 9400, 9500. The diagrams also use 9002
  ("Invalid function") and 9006 ("Invalid property '%s'"), which are not in that file.
- Images: PNG files with IMAQ calibration and custom key/value data (what AloxViewer displays); folders in 4.4.
- Calibration: pixel size per camera in `hardware.camera.<lowmag|highmag>.pixelsize / units`; flat-field images
  `hardware.camera.*.flatfieldimage` (PNG paths under the user Documents folder).
- Temporary files: the whole content of `%LOCALAPPDATA%\Temp` is deleted at exit (section 2.3, frame 10).

#### 4.6 Global variables (`alox_core_global_variables p.png`)

Web service / SSE: `Timeout_to_abort_VI` (5000), `Session_timeout` (14400000), `Reconnect_session_timeout` (12000),
`SSE_frequency` (1000), `Access-Control-Allow-Origin` ("*"), `Access-Control-Allow-Credentials` ("false"), `Web Service Version`, LEDs
`Web Service, SSE Client, WS Server, WS Client`, `exit`. System: `Use Dongle`, `Dongle Infos` {All OK?, Serial Number, Dongle Detected,
Key1..3 OK, Data1/2 read OK, Time not expired, Time not resetted, Expiration Date, Last Execution Date, Licence Extra Infos}, `Core
Heartbeat`, `Alox Def WL Table` (700..950 nm), `Alox Def Exp Table (µs)` (27000..628000), `Disable Func Status`, `Disable Live
Display`, `EXPO MODE LO/HI`, `DONGLE`, `X Stage Invert`, `Y Stage Invert` ("USE FOR DEBUG ONLY - OFF: XY moves cameras (AET); ON: XY
moves wafer (OPTO)"), `Get Image Run Once`, `Chuck Calibration`, `ThermalMeasurement`, `MEAS MESA RUNNING`, `detect mesa running`,
`joystick XY`, `joystick Z`, `Cam/Z Server Queue Name`, `XY Server Queue Name`, `Monochromator Queue Name`, `Close Settings UI
Notifier Name`, `DisplayActual` (image). `alox_core_ui` also writes `F (ms)`, `LAUNCH FROM HMI AE`, `LAUNCH FROM HMI WL`, `XY
JOYSTICK`, `Z JOYSTICK` (owner not checked).

### 5. External dependencies

- LabVIEW 2018 64-bit run-time; VI Server, Event structure, user events, queues, notifiers (`alox_readme.txt`).
- NI Vision 18.0 (IMAQ images, overlays, calibration, PNG custom data, display control) and NI-IMAQdx 18.0 (through the camera
  server, part 2).
- `vi.lib/Utility` (config file VIs, error, file), `vi.lib/registry` (Shell Folders read at exit), `vi.lib/zip` (profiles),
  `vi.lib/Analysis` (fits), `vi.lib/dlg_ctls.llb`, `vi.lib/event_ctls.llb`, OpenG (`user.lib/_OpenG.lib`).
- Websockets toolkit (Media Mongrels Ltd) 1.7.4.26 and JKI JSON Serialization (part 5).
- USB licence dongle (`alox_gen2_dongle_check`, `alox_ssd__serial_key_unlocking_dongle`, part 6).
- External executables started by the core: `AloxCamServer.exe`, `AloxXYServer.exe`, `AloxWLServer.exe` (TCP, part 2 and the camera
  server documents), `AloxWatchdog.exe` (inferred from `alox_manager_watchdog`).
- No SECS/GEM or HSMS library is used: no such dependency in `call-graph.json`.

### 6. Interfaces with the other parts

- **Part 2 (hardware)**: init / exit cycles call `alox_manager_camera`, `_xy`, `_monochromator`, `_server_module`, `_modules_heartbeat`,
  `_heartbeat`, `_watchdog`, `_xy_coordinate_system`. The hardware command VIs use the synchronous-call helpers of this part; the
  modules heartbeat service enqueues `Module Heartbeat` / `Module Resetted`. Queue names of the clients are globals of this part.
- **Part 3 (functions)**: core_ui and the cycles call the 13 wrappers `alox_function_<x>.vi` (Init / Start / Stop / Exit) and the
  `..._bundle_profile_parameters` VIs. The state machines use the managers of this part (functions condition, functions UI events,
  status IDs, process queue, profile, properties, log, display, image, results panels, SEMI). Panels manager loads their
  `_ui_config` VIs; `alox_main_vi_reference` gives them the core front panel.
- **Part 4 (tools)**: folder helpers `tools_alox_shared_*_folder`, `tools_windows_user_documents_folder`,
  `tools_application_executable_folder`, `tools_zip_profile_folder`, `tools_clear_folder`, Base64, colour, joystick, filename tools.
- **Part 5 (API)**: REST VIs (hosted by the same process, build option `App_webService` = `alox`, port 8002) call the managers of this
  part directly and enqueue commands (Measure Mesa / Tilt / Bowing, Stop, Abort, Load Profile, Get Image, System Reset / Shutdown,
  Show UI). Events go out through `alox_manager_api_notifiers` → `alox_api_websocket_service`. SEMI JSON helpers
  `alox_tools_cast_*_semi_job_to_json_report`.
- **Part 6 (other)**: `alox_core_simulator_ui` (AloxSim) reuses the globals, process queue, core mode, tilt, system status and the
  simulation tools of this part; dongle VIs.

### 7. Unused or legacy code

- `alox_manager_configuration.vi`: empty VI, no caller.
- `alox_tools_output_folder_path.vi`: no caller.
- `alox_wl_fixed_exposures_table_ui.vi`: only called by `alox_function_ll_search_wavelength_ui_config_.vi` (trailing underscore,
  an old copy outside the build).
- `alox_manager_core_mode.vi`: set to Local at boot and never read in Alox (the mode actually used is property `general.mode`); used
  by AloxSim.
- AloxSim-only tools: `alox_tools_analyze_mesa_simulation`, `alox_tools_mesa_measurement_generate_simulation_data`,
  `alox_tools_get_overlays_states`, `alox_tools_set_overlays_states`.
- `Generate Unique Notifier Name.vi` is also called by the test VI `alox_test_guids.vi`.
- Disabled diagram code: splash start in core_ui frame 0; per-core data folders; "Z Do Reference" and XY "Do Reference" at init;
  second copies of the XY and monochromator server start (init frames 13, 18); in properties "Save", a copy of `alox.ini` to the
  registry "Personal" folder.
- Placeholders: init waits of 7000 ms / 0 ms instead of the "#TODO: check AloxXXServer.exe has started and running" checks;
  consumer case "Axis Referencing" (comment copied from System Reset) and "System Reset", whose re-init call was removed.
- `alox_function_*_state_machine_old.vi` callers of `alox_tools_convert_mesa_measures_to_micrometers` (part 3) are legacy copies.

### 8. Notes for the migration

1. **Singletons with implicit locking.** Every FGV serializes its callers. The REST threads, the core loops, the state machines and
   the services all call the same managers concurrently; a rewrite needs explicit thread-safe services (a lock per manager, or one
   actor per manager), otherwise races appear that LabVIEW hides.
2. **Bounded command queue.** `alox.process.queue` holds 3 items: a 4th enqueue blocks the caller (a REST request or a state
   machine) while the consumer is busy. Decide whether to keep a small bound, add a timeout, or reject with an error.
3. **One function at a time.** `Get Condition OK` refuses any start while any of the 13 functions runs; HL functions call LL
   functions internally. Keep this global mutex (or an explicit resource model) and the special case of continuous Get Image.
4. **"Finished" is sent by the core, not by the function.** The state machine enqueues `<X> Job Finished` and the consumer sends
   the websocket event, so the event comes after the function has really ended (changelog 1.0.0.9). Keep this ordering.
5. **Dynamic launch and brute-force cleanup.** Startup and exit abort every running top-level VI, kill server EXEs ("Kill 'em
   All"), and wait fixed delays (7 s) instead of checking that servers are up. A rewrite should use process supervision and
   health checks.
6. **Exit deletes `%LOCALAPPDATA%\Temp`.** The shutdown frame deletes the files and folders of the user's Temp folder (pattern not
   visible in the image). This is dangerous; check its intended scope before reproducing it.
7. **Typed-key INI with locale numbers.** Keys carry their type suffix (`_DBL`, `_I32`, ...), DBL values use the Windows locale
   (decimal comma in the default file), arrays are `;`-separated, unknown types raise 9006, missing keys are silently added by
   "Check". Defaults live in code (properties "New"/"Check", profile "New"/"Check"). A rewrite needs a schema with defaults and a
   reader tolerant of the existing files (comma decimals, duplicated keys, empty `[]` sections).
8. **Profile working copy.** Loading a profile wipes `<Documents>\alox\profile` and unzips into it; saving re-zips the whole
   folder. Only one active profile per Windows user; the editor uses `profile_temp`.
9. **Security.** Passwords are Base64 (reversible), admin and operator passwords are empty by default, operator has every right
   in the default file, and the REST API (including shutdown and reset) has no authentication.
10. **Side effects of Stop and idle.** "Stop" moves Z to 0 ("Camera goes up"); the system status "Status timer" moves the camera
    up and switches to the low-mag camera after `hardware.camera.delaybulbswitchoff` of inactivity (units not confirmed: the value
    is multiplied by 60 and 1000, so minutes). These hardware moves must be kept or consciously removed.
11. **System Reset is a no-op** (status 801 → log → 500); the re-initialization was removed. "Axis Referencing" has no visible
    action.
12. **Copy-paste comments**: Bowing / Tilt Job Finished cases say "Mesa Job Finished"; check that the function name in their JSON
    is "measure_bowing" / "measure_tilt" (part 5).
13. **Status code ranges** (0 / 10-99 / 100 / 200-499 / 500 / 600-799 / 800-899 / 1000-1999) and the hardware error bit field
    (1001..1063) are part of the supervisor contract (REST status routes and websocket events): keep them.
14. **SEMI**: the application only stores and echoes the five SEMI job identifiers (Control Job, Process Job, Lot, Wafer, Zone)
    received with REST measurement requests, shows them in the function panels and puts four of them in the started / finished /
    failed JSON events. The SECS/GEM communication itself is done by the external supervisor (no SECS library here).
15. **Mode**: LOCAL / REMOTE is the property `general.mode`; the UI shows it but no observed code blocks local actions in REMOTE
    mode. Decide the intended semantics.
16. **Language-dependent strings** come from `.lang` files by 6-digit ID; logs and status descriptions depend on them.

### 9. Open questions for the owner

1. Is the deletion of `%LOCALAPPDATA%\Temp` at exit intended, and for which files?
2. Which folder settings are authoritative: `alox_setup.ini` (`/D/alox/02-Vision`, `/D/alox/04-JobHistory`) or the defaults in
   the boot comments (`D:\Alox`, `D:\Alox\profiles\gen2`)?
3. Should REMOTE mode lock the operator UI? Who sets `general.mode` in production (UI button, hotkey `general.modehotkey` = 121, REST
   `PUT /v1/system/mode`)?
4. Which user is logged in after boot, and should passwords keep the Base64 storage?
5. Should "System Reset" re-run the init cycle as the comment says? What should "Axis Referencing" do?
6. Is the process-queue size of 3 deliberate?
7. Units and default of `hardware.camera.delaybulbswitchoff` (not present in `alox_default.ini`).
8. Version: frame 0 says "ALOX CORE GEN1.4 (01/06/2022)", the build is 1.2.6.2 and the changelog stops at 1.0.0.11 (2020). Is there
   a newer changelog?
9. Error codes 9002 and 9006 are used but missing from `alox-errors.txt`: add them or map them?
10. Web service port: property `webservice.port` = 8001, build and `niembeddedws.conf` = 8002. Which one does the supervisor use?

---

## Part 2: Hardware layer (managers, TCP clients, hardware servers, heartbeats, watchdog)

Conventions: `<vi> dN` cites hidden frame N of the VI's HTML export (`d0` = the visible diagram). **(inferred)** marks a
deduction; "(from name/calls)" marks a helper whose diagram was not opened. The camera server (`alox_camera_server.vi`,
AloxCamServer) is specified in `../01-…10-*.md` and is only referenced here.

### 1. Purpose

This part sits between the Vision Core application (AloxVision.exe) and the instrument hardware. The hardware is driven by
three separate LabVIEW executables that the main application starts with `cmd /c START /B`: AloxCamServer (2 cameras +
PI Z stage, port 503), AloxWLServer (Spectral Products monochromator over RS-232, port 502/505) and AloxXYServer (two PI
Mercury XY axes over one RS-232 daisy chain, port 504). In the main application each server has a manager (a functional
global that owns a TCP client) and a family of one-VI-per-command client VIs. A modules-heartbeat service polls the three
servers and kills/restarts a server that stops answering. A separate AloxWatchdog program polls the main application's
REST heartbeat and kills/restarts the whole set of processes. The part also contains the client-side XY coordinate-system
converter (stages / wafer / chuck) and the wafer-map display.

### 2. Architecture

#### 2.1 Processes and links

```
                        AloxWatchdog.exe (alox_watchdog_ui.vi)
                           | HTTP GET http://<host>:8002/alox/v1/system/heartbeat (+heartbeatfreq, heartbeatretries,
                           |          watchdog/visibility)  -- JKI REST client
                           v
 AloxVision.exe  ------------------------------------------------------------------------------------------------
  alox_heartbeat_service  --writes--> global "Core Heartbeat" (I64 s) + API notifier "heartbeat"   (period heartbeat.frequency)
  alox_modules_heartbeat_service (3 loops) --> *_async wrappers --> command VIs --> client queues
  managers (FGV):  alox_manager_camera      alox_manager_monochromator      alox_manager_xy
                       | Init: Start Asynchronous Call (0xC0, call-and-forget)
                       v
  client VIs:      alox_camera_client.vi    alox_wl_client.vi               alox_xy_client.vi   (queue "alox.<x>.queue")
                       | TCP localhost, framing = extshared "HW TCP" protocol
  alox_manager_server_module: cmd /c START /B <exe>, tasklist, taskkill /t /F /PID
 ----------------------|------------------------------|-------------------------------|---------------------------
                       v 503                          v 502 (INI 505)                 v 504
              AloxCamServer.exe                AloxWLServer.exe                 AloxXYServer.exe
              IMAQdx cams + PI Z (docs 01-10)  VISA serial COMn -> Spectral     PI GCS: Mercury_GCS_Configuration_Setup,
                                               Products monochromator           RS232 daisy chain (X dev 1, Y dev 2)
```

All TCP traffic is on the loopback interface: every server listens on `localhost` (`alox_wl_server d17`) and every
client opens `localhost` (`alox_xy_client d2`).

#### 2.2 Shared TCP protocol (extshared)

The WL and XY servers are clones of the camera server's TCP layer (same typedefs, same `HW TCP …` VIs, even the camera
comments: the WL consumer loop is labelled "Z STAGE CONSUMER LOOP" and its router "Route command to XY Queue" /
"Camera consumer loop…", `alox_wl_server d0, d18`). Everything in `02-tcp-protocol.md` applies; summary:

- Frame: `I32 N (big-endian) | U16 datatype | payload (N-2 bytes)`. `HW TCP Datatype` = {0 Command, 1 Response, 2 Image,
  3 Heartbeat, 4 Statut} (typedefs.md). `Statut` is decoded by the clients only (`alox_xy_client d9`, U16 "Statut serveur");
  no server sends it.
- Command payload: flattened `<Server> Queue Data` = {command enum U16, `Notifier Name` string, `Command Data` variant,
  3 U32 timestamps}. Enums: `typedef_ext_wl_command` (23 items), `typedef_ext_xy_command` (29 items),
  `typedef_ext_camera_camera_command` (camera, see 03-command-reference.md).
- Response payload: `HW TCP Response Data` = {`Notifier Name` (echoed), `Response (Flattened)` = flattened
  `{value, error}` or error cluster, 6 U32 timestamps}.
- Server TCP state machine `HW TCP Server Command` {Listen, Read, Send Heartbeat, Send Response, Quit}: Listen on
  localhost:TCP Port with 2000 ms timeout (`alox_wl_server d17`); Read: code 56 → re-queue Read (`d19`), other error →
  TCP Close + Listen (`d20`), Command frame → route to the consumer queue and keep a 500-entry command history (`d18`,
  `d21`); empty queue for 50 ms → enqueue Read (`d29`); Send Response stamps `TCP Sending timestamp` (`d27`); Send
  Heartbeat is unreachable ("#Question : Ajouter une bloucle Heartbeat ?", `d26`); TCP queue max size 5 ("#Question :
  Borner TCP Queue Size ?", `d0`). The XY server TCP loop has the same structure (`alox_xy_server d0`; frames d33–d46 not
  opened, **inferred** identical).
- Reply VIs `Enqueue Send <type> Response.vi` push `{Send Response, Response Data}` into the server's TCP queue by name.
  Per 02-tcp-protocol.md §8.4, `Enqueue Send Error Cluster Reponse.vi` probably does **not** enqueue when the error it
  should report is set: error acknowledgements are lost and the client times out. This affects every WL/XY command that
  answers with an error cluster (including error 9404 "move outside wafer", see §8).
- Client TCP state machine `HW TCP Client Command` {Connect, Send Command, Read, Quit} (`alox_xy_client d0–d17`, the
  camera and WL clients have the same callee set, **inferred** identical):
  - Connect: `TCP Open(localhost, remote port, 10000 ms)`; success → Read, failure → Connect again (endless retry) (`d2`).
  - Read: `HW TCP Extract TCP Data`; Response/Image → unflatten `HW TCP Response Data`, record timestamps
    (`alox_investigation_process communication timestamps.vi`, "COMM PROFILE") and `Synchronous Response Post`
    (notify the waiting caller by notifier name) (`d7`); Heartbeat → increment a counter (`d8`); code 56 → Read again
    (`d4`); code 66 (peer closed) → TCP Close + Connect (reconnection) (`d14`); other codes → nothing (`d13`).
  - Send Command: variant → `<x> Queue Data` → `<x>_client_send_command` (`d11`): flatten datatype `Command`, stamp
    `Command Sending Timestamp`, prepend I32 length, one `TCP Write` with 1000 ms timeout (`alox_xy_client_send_command d0`).
  - Quit: sends the `Exit` command to the server, then stops (`d0`, `d12`). **Closing a client therefore terminates its
    server process.**
  - Dequeue timeout 50 ms → enqueue Read (polling, `d15`).
- Command VIs (`alox_<x>_command_<cmd>.vi`): obtain the client queue, optionally create a notifier with
  `Synchronous Wait for Raw Create` ("SYNC RAW RESP CREATE"), enqueue `{Send Command, {cmd, notifier name, Command Data}}`,
  then if `Wait Response` is TRUE wait with `Synchronous Response Wait <type>` and `timeout in ms (-1)` → `timed out?`
  (`alox_xy_command_move_absolute d0–d4`). `launch` VIs open the client VI by path (same folder) and `Start Asynchronous
  Call` with options 0xC0 = reentrant + call-and-forget, passing the queue name and port
  (`alox_xy_command_launch_client d0`, `alox_camera_command_launch d0`). `close_client` enqueues Quit then polls up to
  10 × 300 ms for the queue to disappear (`Timeout` output) (`alox_xy_command_close_client d0`). `exit` enqueues
  `{Send Command, Exit}` without waiting (`alox_xy_command_exit d0`).

#### 2.3 Main-application managers

All three hardware managers are non-reentrant functional globals (while loop executed once, uninitialised shift
registers, `Method` enum input, `Data In`/`Data Out` variants) (`alox_manager_xy d0`, `alox_manager_camera d0`):

| Manager | Shift registers (state) | Init | Exit | Timeouts |
|---|---|---|---|---|
| `alox_manager_camera` (43 methods, `alox_typedef_camera_client_manager_method`) | Client⇒Server queue name, Low-/High-Angle Acquisition Started?, Low-/High-Angle Camera Inited?, Z Stage Inited? (`d0`) | `hardware.camera.port` (default 503) → `alox_camera_command_launch` with queue `alox.camera.queue`, global `Cam/Z Server Queue Name` (`d5`) | `alox_camera_command_close_client`, clear global (`d6`) | `hardware.camera.timeout` (5000); Setup Camera 60000 → error 9401 (`d7`); Refocus Oxyd: acquisition timeout = 2000 ms × #steps, 5 retries of `autofocus_getdata`, error 9035 (`d0`) |
| `alox_manager_monochromator` (21 methods) | queue name, last position | `hardware.monochromator.port` (default **502**) → `alox_wl_command_launch_client`, queue `alox.monochromator.queue`, global `Monochromator Queue Name` (`d3`) | `alox_wl_command_close_client` (`d4`) | `hardware.monochromator.timeout` (5000), Setup 30000, timeout → error 9403 "WL Timeout" (`d5`, `d53`); Set Position then waits `hardware.monochromator.waitposition` ms (default 0) and stores the last position (`d48`); retry loops are inside disabled Diagram Disable frames (`d30`, `d51`) |
| `alox_manager_xy` (32 methods) | Client⇒Server queue name, XY stages inited (Setup)?, Store last known position (`d0`) | `hardware.xy.port` (default 504) → `alox_xy_command_launch_client`, queue `alox.xy.queue`, global `XY Server Queue Name` (`d3`) | `alox_xy_command_close_client` (`d4`) | `hardware.xy.timeout` (5000), Do Reference 60000, timeout → error 9402 "XY Timeout" with the method name as source (`d0`, `d5`) |

The `*_async` VIs (`alox_manager_xy_get_status_async`, `…_get_heartbeat_async`, `…_get_ref_async`,
`alox_manager_monochromator_get_heartbeat_async`, `…_get_serial_async`, `alox_manager_camera_get_*_async`) bypass the FGV:
they read the server-queue-name global and call the command VI directly with the manager's timeout and error code
(`alox_manager_xy_get_status_async d0`). Purpose **(inferred)**: let the heartbeat service and long functions talk to a
server without being serialised behind the non-reentrant manager (changelog 1.0.0.8: "make some hardware devices calls
asynchronous"). `alox_manager_camera_autofocus_async` starts the autofocus (Z range `hardware.z.zminautofocus`/
`zmaxautofocus`, defaults 100/120), then polls `autofocus_getprogress` + `autofocus_getdata` every 20 ms until done or
`AF Timeout`, fires the UI event "AF Large Measured" for live curves, errors 9008 / 9999 "Timeout" (`d0`).

#### 2.4 Server process management

`alox_manager_server_module` (methods Load Server, Unload Server, Kill Server, Kill 'em All, Any Server alive ?, Server
Running ?; server types Camera / Monochromator / XY Stages / Z Stage):

- Load Server: `System Exec("cmd /c START /B \"<exe folder>\<AloxCamServer|AloxWLServer|AloxXYServer>.exe\"")`, no wait
  (`d3–d6`).
- Unload Server: get the client queue from the manager (`Get Client Queue`) and send `Exit` (`d7–d10`).
- Kill Server: `cmd /c tasklist`, regex `<exe>\s+[0-9]+`, extract PID `\s+(\d+)`, `cmd /c taskkill /t /F /PID <pid>`
  (`d11–d16`); Kill 'em All loops over the server types (`d17`).
- Server Running ? / Any Server alive ?: `tasklist` output contains the exe name (`d0`, `d23–d30`).

Callers: `alox_core_init_cycle`, `alox_core_exit_cycle`, `alox_core_ui`, `alox_properties_ui`,
`alox_modules_heartbeat_service`. The exact start/stop order lives in the core cycles (part 1).

#### 2.5 Heartbeats and recovery

Three distinct mechanisms:

1. **Server liveness (main app → servers)**: `alox_modules_heartbeat_service` runs three parallel loops (Cameras/Z, XY,
   WL), each a 5-frame sequence repeated every 100 ms (`d0`, `d16`):
   - frame 0: if `hardware.<m>.heartbeat` ms (default 30000) elapsed since the last check: call the module's
     `Get Heartbeat` (U32 tick count), `Get Status` and reference queries (camera: High Mag., Low Mag., Z status + Z
     reference; XY: X/Y `Get Status` + `Get Reference`; WL: heartbeat + serial **(inferred from callees)**) and build
     `CAM/XY/WL SERVER STATUS` (`alox_typedef_hardware_module_heartbeat_report`). Any failure increments `ERROR COUNT`,
     success resets it to 0 (`d0`, `d3`).
   - frame 1: post `{Module Heartbeat, {Server Type, Data}}` to the Alox core queue for display/log (`d8`, `d10`).
   - frame 2: send `hardware_warning`/`hardware_error` API notifiers (e.g. `camz_hardware_warning`) when the count is not 0
     (`d11–d15`; codes 9095 "hardware comm warning"/9097 "hardware comm error" sit in a disabled frame, `d14`).
   - frame 4: if `ERROR COUNT ≥ hardware.<m>.maxfails` (default 3) AND `hardware.<m>.watchdog` (default TRUE): "Kill
     <module> server, restart <module> server, send hardware_reset event (websocket), send Module resetted event (alox
     consumer loop)": log "Restarting … module…", Unload Server (Exit), manager Exit (close client), Kill Server, wait
     5000 ms, Load Server, manager Init, wait 1000 ms, then re-initialise (XY: `Setup`; WL: `Setup` + `Set Units
     Nanometers`; camera: Setup/Start Camera High Mag.), then notifier `hardware_reset` and Alox-queue item
     `Module Resetted` (`d0`, `d17–d24`, `d50–d54`, `d72–d76`).
   `alox_manager_modules_heartbeat` starts it with VI Server `Run VI` (no wait, no auto-dispose), front panel Standard or
   Hidden from `general.extmodulesui`; `Get Report` reads the panel controls CAMERA/XY/WL; Exit sets `Stop` and waits up
   to 10 s for Idle (`d3`, `d4`, `d7`, `d10`).
2. **Core liveness (main app → watchdog)**: `alox_heartbeat_service` writes `get_system_time` (I64 seconds) to the global
   `Core Heartbeat` and to the API notifier `heartbeat` (formatted `%ld`) every `heartbeat.frequency` ms (default 30000)
   (`d0–d4`). The REST route `GET /v1/system/heartbeat` returns that global (part 5). `alox_manager_heartbeat` (Init/Exit/
   Is Running ?/Get Heartbeat) starts/stops it **(inferred: same pattern as the modules manager)**.
3. **Watchdog (separate program)**: see §2.6.

#### 2.6 Watchdog

`alox_manager_watchdog` (Start/Stop): Start = `cmd /c START /B "<exe folder>\AloxWatchdog.exe"` (`d2`); Stop = tasklist +
`taskkill /PID` without `/F` (`d0`, `d3`). Called from the init and exit cycles **(inferred: only when
`[general] watchdog_BOOL`, default FALSE)**.

`alox_watchdog_ui.vi` (AloxWatchdog.exe), outer sequence `[0..3]`:
- frame 0: URL = `http://<local host name>:<port>/alox/v1`, port 8002 when `App.Kind = Run Time System`, else 8001 (as
  drawn) (`d1`); the main executable's web server is configured on 8002 (`alox.lvproj`, `App_serverConfig.httpPort`).
- frame 1: read `alox.ini` `[general] core_I32`, `[log] maxlines_I32`; log "Starting alox watchdog on core #%d" (`d0`).
- frame 2: state machine `alox_typedef_watchdog_state` {Connect, Heartbeat Running, Heartbeat Get, Heartbeat Check,
  Restart Core, Exit}:
  - Connect: `GET /system/heartbeatfreq` (→ FREQ s), `/system/heartbeatretries` (→ TOL), `/system/heartbeat` (→ ALOX
    HEARTBEAT, TIMESTAMP), `/watchdog/visibility` (→ panel transparency 0/100), each with a 5000 ms REST timeout; repeat
    until success (`d3`).
  - Heartbeat Get: up to 3 attempts of the same three GETs with 100 ms between failures; failure → Restart Core, else
    Heartbeat Check (`d10–d14`).
  - Heartbeat Check: `now(s) > ALOX HEARTBEAT + TOL × FREQ` → ERROR → Restart Core, else Heartbeat Running; logs
    "Heartbeat OK/NOK (Freq=%d, value=%d)" (`d15–d20`).
  - Restart Core (run-time only; development system just logs): for each of `Alox.exe`, `AloxCamServer.exe`,
    `AloxWLServer.exe`, `AloxXYServer.exe`: log "Restarting %s…", kill it and poll `tasklist` until gone or 30000 ms
    (error 9098 "Watchdog cannot stop Alox Vision"); then `cmd /c "<exe folder>\Alox.exe"`; then Exit ("All Restarted:
    Exit Watchdog") or Restart Core again on error ("Retry Restart"). Comment "TODO: Kill & restart HW modules"
    (`d21–d27`).
- frame 3: quit if run-time. Log lines go through `alox_watchdog_log.vi` (shared data folder, trimmed to MAX LINES).

#### 2.7 WL server (AloxWLServer, `alox_wl_server.vi`)

Outer sequence `[0..4]`: 0 open panel fully transparent (always 100 %, unlike the camera server) (`d1`); 1 reset
`error out`, `Connected`, `WL Inited`, `WL Log Data` (`d2`); 2 read `alox.ini` (`d3–d14`); 3 two loops (`d0`); 4 quit
LabVIEW if run-time (`d70`).

- Config (`d3–d14`): `[hardware.monochromator] port_I32` (default **502**), `[general] core_I32` (1), `[log] maxlines_I32`
  (1000), `[hardware.monochromator] comport_I32` (default 2), `baudrate_I32` index (default 0; 0=9600, 1=19200,
  2=38400, 3=115200), `timeout_I32` (default 2000) → `WL Settings {Com, Baudrate, Timeout (ms)}`.
- Loops: TCP SERVER PRODUCER LOOP (§2.2) and the consumer loop; consumer dequeues `WL Queue Data` with an infinite
  timeout (`d0`). All handlers log through `Log Server Error.vi` with Server Type `Monochromator`.
- Device session `WL Session {COM Port Out (VISA), Serial Number}`; `WL Inited` front-panel flag.

Command handlers (reply type in brackets; "err" = error-cluster acknowledgement, "none" = no reply):

| Cmd (enum value) | Behaviour | Frame |
|---|---|---|
| Show UI (0) | Boolean data → panel transparency 0/100; other value → error 9002 [err] | `d32–d35` |
| Set Timeout (1) | `WL Timeout Data` → `WL Settings.Timeout` (memory only) [err] | `d68` |
| Get Heartbeat (2) | Tick Count [U32] | `d36` |
| Setup (3) | VISA resource `COM%d` from INI `Com`; `spectral_products_init` (baud, timeout), flush, query `Serial number` → WL Session; `WL Inited ← no error`. Command data ignored [err] | `d30`, `d37` |
| Cleanup (4) | `spectral_products_exit` (VISA close), `WL Inited ← F` [err] | `d38` |
| Is Connected ? (5) | COM refnum valid [BOOL] | `d39` |
| Get Serial (6) | flush + query `Serial number` [STR] | `d40` |
| Is Inited ? (7) | refnum valid AND serial ≠ "" [BOOL] | `d41` |
| Set Scan Speed (8) | "Select the scan speed of the monochromator [0..10] 0: fastest 10: slowest": queries `Groove/mm`, builds a 10-entry speed table (1800 vs 1200 gr/mm branches), sends byte 13 + U16, reads 2 bytes [err] | `d42–d44` |
| Get Scan Speed (9) | query `Scan speed` [U32] | `d45` |
| Get Position (10) | query `Position` → also `WL (nm)` indicator [U32] | `d46` |
| Set Position (11) | `{Position U32}` → bytes 16, hi, lo; read 2; on success `WL (nm) ← Position` [err] | `d47–d49` |
| Get Units (12) | query `Current units` [U32] | `d50` |
| Set Units (13) | bytes 50, unit (0 cm, 1 nm, 2 Å) [err] | `d51` |
| Scan (14) | bytes 12, start U16, end U16 [err] | `d52` |
| Get Groove/mm (15), Get Blaze (16), Get Grating (17) | queries `Groove/mm`, `Blaze (nm)`, `Current grating` [U32] | `d53–d55` |
| Reset (18) | flush; write 255 ("Reset Monochromator"), read 2; wait; repeat ECHO (byte 27, read 1) until answer or 30000 ms ("ECHO cmd used to check comm."); flush [err] | `d56–d63` |
| Calibrate (19) | bytes 18 + U16 "Allows for recalibration of gratings table, input (value1) = desired wavelength" [err] | `d64` |
| Select Gratings (20) | bytes 26 + U16 "Set new grating (1 or 2)" [err] | `d65` |
| Exit (21) | stop the consumer loop, release the queues (the TCP loop then stops on its dequeue error) [none] | `d66`, `d0` |
| Log (22) | store `WL Log Data` only ("#TODO: Log every WL actions into files …") [err] | `d67` |
| Default | nothing [none] | `d31` |

Serial protocol (`spectral_products_*`): binary bytes over VISA serial. `query`: write `56, n` ("STATUS?"), read 2 data
bytes (value = hi×256+lo), then a status byte and a cancel byte; for `Gr/mm` queries (n = 15, 17) the code 0..7 is mapped
to 3600, 2400, 1800, 1200, 600, 300, 150, 75 gr/mm (`spectral_products_query d0–d15`). `read`: VISA Read of `Response
Size` bytes; first byte > 127 → error "Command not accepted" (`spectral_products_read d0–d4`). `init` = `VISA Configure
Serial Port` (timeout, baud) (`spectral_products_init d0`); `write` = VISA Write (`spectral_products_write d0`); `flush`,
`exit` (from name/calls). The command codes (56 QUERY, 16 GOTO, 12 SCAN, 13 SPEED, 50 UNITS, 18 CALIBRATE, 26 SELECT,
27 ECHO, 255 RESET) match the Spectral Products CM110/CM112 serial protocol **(inferred)**. Query enum
`typedef_spectral_products_query` (21 items: Position, Type, Groove/mm, Blaze, Current grating, Scan speed, Step size, zero
offsets, calibrations, # of gratings, Current units, Gr/mm/Blaze per grating, Serial number, Novram #14).

#### 2.8 XY server (AloxXYServer, `alox_xy_server.vi`)

Outer sequence `[0..4]`: 0 transparent panel (`d1`); 1 reset `error out`, `Exit`, `Connected`, `X Inited`, `Y Inited`,
`XY Log Data` (`d2`); 2 read `alox.ini` (`d3–d31`); 3 three loops (`d0`); 4 quit if run-time (`d146`).

- Config: `[hardware.xy] port_I32` (504) (`d3`); `[general] core_I32`, `[log] maxlines_I32`, `[general] wafersize_DBL`
  (default 100) → `Wafer Size (mm)`, `[hardware.xy] waferlimit_BOOL` (default F) → `Wafer Limits`, `wafercheckfreq_I32`
  (default 500) → `Frequency (ms)` (`d4`); per axis (`alox_typedef_ext_xystage_properties`): `comport_I32` (2, the same
  key for X and Y), `baudrate_I32` index (3 = 115200), `xaxis_I32` (1, "Should be 1 (XY are daisy chained and X
  controller is the master)") / `yaxis_I32` (2, "… Y controller is the slave"), `speed_DBL` (10), `slowspeed_DBL` (1),
  `waferxorg_DBL`/`waferyorg_DBL` (0) → Wafer Origin, `chuckxorg_DBL`/`chuckyorg_DBL` (0) → Chuck Origin
  (`d5–d30`).
- Loops (`d0`): TCP SERVER PRODUCER LOOP; XY STAGE CONSUMER LOOP (queue `XY Stages Mgmt Queue Name`, size 5, infinite
  dequeue); WAFER XY LIMITS LOOP: every `Frequency (ms)`, if `Wafer Limits` AND `Connected` AND `X Inited` AND `Y Inited`
  AND NOT `Exit`, and wafer size and origin are non-zero: `POS?` on X and Y, `Inside Wafer? = sqrt((Xorg−X)² +
  (Yorg−Y)²) ≤ diameter × 0.5`; when outside: `STP`, `ERR?` and joystick off (`JON 0`) on the axes ("Stop Y Stage &
  Joystick").
- Sessions `X Session`, `Y Session` (`alox_typedef_ext_zstage_session {System, Axes, Identity, Serial Number}`).

| Cmd (value) | Behaviour (PI GCS calls) | Frame |
|---|---|---|
| Show UI (0) | panel transparency [err] | `d118–d121` |
| Get Heartbeat (1) | Tick Count [U32] | `d117` |
| Setup (2) | `Mercury_GCS_Configuration_Setup` twice, "RS232 DaisyChain", port COM<Com>, 115200, 5000 ms, device 1 (X) and 2 (Y), "Default speed/range settings" → X/Y Session; `X/Y Inited ← no error` [err] | `d49` |
| Cleanup (3) | `Close connection if open` on both, sessions reset, Inited ← F [err] | `d115` |
| Get Serial (4) | `{Axis, Type}`: Identity or Serial Number [STR] | `d50–d52` |
| Get Status (5) | `ERR?` → Boolean [BOOL] | `d53–d55` |
| Get Error (6) | `ERR?` + `GCSTranslateError` [STR] | `d56–d58` |
| Set Speed (7) | `VEL` + `ERR?` [err] | `d59–d61` |
| Get Speed (8) | `VEL?` [DBL] | `d62–d64` |
| Get Position (9) | `POS?` [DBL] | `d68–d70` |
| Move Absolute (10) | check references, check destination inside wafer (absolute), `MOV`; outside → error 9404 "X/Y Move outside wafer"; logs "Move Absolute X (X=%f)" or "Ref. Warning Move Absolute …" [err] | `d72–d82` |
| Move Relative (11) | same with relative destination, `MVR` [err] | `d89–d99` |
| Wait End Move (12) | PI `General wait for movement to stop` `{Axis, Add. Wait, Polling Cycle (default 10 ms), Add. Wait Only}` [err] | `d100–d102` |
| Stop (13) | `STP`, `ERR?`, clear errors [err] | `d103–d105` |
| Enable / Disable Joystick (14/15) | `JAX` (joystick 1, axis 1) + `JON 1/0` [err] | `d106–d111` |
| Joystick Enabled ? (16) | `JON?` [BOOL] | `d112–d114` |
| Exit (17) | `Exit ← T`, stop consumer [none] | `d116` |
| Log (18) | store `XY Log Data` ("#TODO: Log every XY actions into files …") [err] | `d122` |
| Get Joystick Button (19) | `JBS?` [BOOL] | `d123–d125` |
| Move Absolute XY (20) | both checks, `MOV` X then `MOV` Y ("Move X AND Y with single command" but two calls); 9404 "XY Move outside wafer" [err] | `d84–d88` |
| Get Position XY (21) | `POS?` X and Y [DBL 2D array] | `d71` |
| Set Chuck Origin (22) / Set Wafer Origin (23) | point {X, Y} → X/Y Stage Settings (memory only) [err] | `d126`, `d127` |
| Set Wafer Diameter (24) | DBL → `Wafer Size (mm)` [err] | `d128` |
| Set Wafer Limit (25) | Boolean → `Wafer Limits` [err] | `d129` |
| Set Wafer Check Freq (26) | I32 → `Frequency (ms)` [err] | `d130` |
| Get Reference (27) | `FRF?` [BOOL] | `d65–d67` |
| Do Reference (28) | `FRF` X, wait, `ERR?`, then `FRF` Y, wait, `ERR?`; log "Do Ref. Reference performed XY" [err] | `d83` |
| Default | String ← "DEFAULT" [none] | `d48` |

Helpers: `alox_xy_check_axis_references` reads `FRF?` on X and Y; if either is not referenced ("X/Y REF LOST, REINIT ALL
AXIS") it closes both connections, re-runs `Mercury_GCS_Configuration_Setup` with option "Reference (FRF)" for X and Y
and returns `REF WARNING? = TRUE` (`d0–d2`): **a move command can trigger an automatic homing of both axes**
(changelog 1.0.0.8 "auto-reset x/y/z stages if stage reference lost"). `alox_xy_check_wafer position` computes the
destination (current `POS?` + move, or move), returns `In Wafer = sqrt((Xorg−X)²+(Yorg−Y)²) ≤ wafer radius`, with a
`x+10` constant on the radius and constants 105/80 whose role is not legible; if diameter or origin is 0 ("Wafer size
and/or origin is undefined") it returns TRUE (`d0–d6`).

#### 2.9 XY coordinate systems and wafer map (main application)

`alox_manager_xy_coordinate_system` (FGV, methods Init, Exit, Set/Get Wafer Coord. Sys, Set/Get Chuck Coord. Sys,
Convert, Wafer Coord OK ?; registers Wafer Origin Defined, XY Stages coordinate system (default `Coord. Sys. 4`), XY Stages
wafer origin (REF), XY Stages chuck origin (REF)) (`d0`). Diagram comments (`d0`):

- Wafer: cartesian (X left→right, Y bottom→top); wafer (0,0) = (Xorg, Yorg) in stage coordinates.
- Stage systems (`alox_typedef_stages_coordinate_system_control` U16, INI `[hardware.xy] stagescoordsys_U32 = 3`):
  #1 X direct, Y direct; #2 X indirect, Y direct; #3 X direct, Y indirect; #4 X indirect, Y indirect (indirect: X right→
  left, Y top→bottom).
- Per axis: same direction → `wafer = stage − ref`, `stage = wafer + ref`; opposite → `wafer = ref − stage`,
  `stage = ref − wafer` ("Si axes stages et wafer même direction / direction inverse").
- `Convert` takes `Src`/`Dst` (`alox_typedef_xy_coordinate_system_type` {XY Stages, Wafer, Chuck}) and a point, one case
  per system #1–#4 (`d7–d19`); chuck conversions use the chuck origin the same way **(inferred)**.

`alox_manager_xy_map` (Init, Exit, Set Refnum, Update): Init creates an RGB IMAQ image "ALOX_XYZ_TARGET" and displays it in
an image control (`d3`); Update, when the wafer coordinate system is defined, converts the current stage point to wafer
coordinates, scales it (x/2 into a 160-px map), draws a cross marker and annotations with
`alox_vision_overlay_line_with_arrows_simple` (constants 150/100/76 = the three standard wafer sizes **(inferred)**)
(`d0`, `d6`). Used by `alox_properties_ui` and `alox_bandit` only.

`alox_xyz_stages_ui` (properties sub-panel): shows/edits XY and Z speeds and limits; reads `hardware.xy.slowspeed`
(default 10), `hardware.xy.speed` (5), `hardware.z.slowspeed` (5), `hardware.z.speed` (10) and logs "XYZ Setup (Speeds)"
(`d0`); it drives `alox_manager_xy`, `alox_manager_camera` (Z) and the coordinate system (from calls).

### 3. Components

| VI | Role | Used by (apps / callers) |
|---|---|---|
| `alox_manager_camera` | FGV owning the camera/Z client: 43 methods (Init … Cleanup Z Stage), inited/started flags | Alox, AloxSim; core cycles, core UI, 12 function state machines, API status routes, joystick tool, properties |
| `alox_manager_camera_autofocus_async`, `…_refocus_async` | start AF/refocus and poll progress/data with live UI events | Alox; ll autofocus / refocus / refocus-oxyd state machines |
| `alox_manager_camera_get_heartbeat_async`, `…_get_status_async`, `…_get_z_ref_async`, `…_get_z_status`, `…_get_image_async` | reentrant direct calls bypassing the FGV | Alox; modules heartbeat service (image: get-image state machine) |
| `alox_manager_monochromator` | FGV owning the WL client (21 methods), last position | Alox, AloxSim; core cycles, functions, properties |
| `alox_manager_monochromator_get_heartbeat_async`, `…_get_serial_async` | direct calls for the heartbeat loop | Alox; modules heartbeat service |
| `alox_manager_xy` | FGV owning the XY client (32 methods) | Alox, AloxSim; core cycles, functions, joystick tool, xyz UI |
| `alox_manager_xy_get_heartbeat_async`, `…_get_ref_async`, `…_get_status_async` | direct calls for the heartbeat loop | Alox; modules heartbeat service |
| `alox_manager_xy_coordinate_system` | stage/wafer/chuck point conversion, wafer origin state | Alox, AloxSim; core cycles, bowing/mesa/tilt/detect-object/thermal functions, xy map, overlays |
| `alox_manager_xy_map` | wafer map image with current position | Alox; properties UI, bandit |
| `alox_manager_server_module` | start/stop/kill the server executables (System Exec) | Alox; init/exit cycles, core UI, properties, modules heartbeat |
| `alox_manager_watchdog` | start/stop AloxWatchdog.exe | Alox; init/exit cycles |
| `alox_manager_heartbeat` | start/stop/query the core heartbeat service | Alox, AloxSim; boot/shutdown cycles, simulator UI |
| `alox_manager_modules_heartbeat` | start/stop the modules heartbeat service, read its report | Alox; init/exit cycles, core UI, properties, thermal function, bandit |
| `alox_heartbeat_service` | publish core heartbeat (global + notifier) | Alox, AloxSim (always-included, started by VI Server) |
| `alox_modules_heartbeat_service` | poll the three servers, alarm, kill/restart | Alox (always-included, started by VI Server) |
| `alox_xyz_stages_ui` | XYZ speed/limit settings panel | Alox; properties UI, autofocus config UI |
| `alox_camera_client`, `alox_wl_client`, `alox_xy_client` | TCP client state machines (Connect/Send/Read/Quit) | Alox (always-included, started by `launch` VIs) |
| `alox_camera_client_send_command`, `alox_wl_client_send_command`, `alox_xy_client_send_command` | frame and write one command | the client VI |
| `alox_camera_command_*` (45 VIs: autofocus, autofocus_getdata/getprogress, flip, get/set exposure, gain, led, get_image, get_status, initialize, release, start, stop, log, refocus, set_flatfield, show_ui, get_heartbeat, launch, close_client, exit, 19 `z_*`) | one camera-server command each (see 03-command-reference.md) | Alox, AloxSim; `alox_manager_camera`, async wrappers |
| `alox_wl_command_*` (25 VIs) | one WL command each + launch/close_client/exit | Alox, AloxSim; `alox_manager_monochromator` |
| `alox_xy_command_*` (31 VIs) | one XY command each + launch/close_client/exit | Alox, AloxSim; `alox_manager_xy` |
| `alox_watchdog_ui` | watchdog program (REST heartbeat check, restart) | AloxWatchdog |
| `alox_watchdog_log` | append a line to the watchdog log (max lines) | AloxWatchdog |
| `alox_wl_server` | monochromator TCP server | AloxWLServer |
| `alox_wl_globals` | queue-name globals of the WL server (from name) | AloxWLServer |
| `spectral_products_init/write/read/query/flush/exit` | Spectral Products serial driver over VISA | AloxWLServer; also `alox_test_monochromator`, `scan_wl_function_state_diagram` (R&D) |
| `alox_xy_server` | XY stage TCP server | AloxXYServer |
| `alox_xy_server_globals` | queue-name globals (from name) | AloxXYServer |
| `alox_xy_check_axis_references` | FRF? check, reconnect + re-reference when lost | AloxXYServer |
| `alox_xy_check_wafer position` | destination-inside-wafer test | AloxXYServer |
| `alox_xy_do_axis_references` | reconnect with referencing (from calls) | none (unused) |
| `HW TCP Extract TCP Data` | read one frame | all servers and the three clients |
| `HW TCP Send Response`, `HW TCP Send Heartbeat` | write a Response / Heartbeat frame | the three servers |
| `Enqueue Send Boolean / DBL / DBL 2D Array / Error Cluster / Image / STR / U32 Response` (7 VIs) | queue a typed reply to the server TCP loop | servers (Image: camera only; DBL, 2D: camera + XY) |
| `Log Server Error`, `Log Server Infos` | daily server event logs (09-configuration-logging-errors.md §7) | servers (Infos: camera only) |
| `alox_camera_server`, `…_autofocus`, `…_get_image_state_machine`, `…_globals`, `…_refocus`, `…_simulation`, `alox_z_check_axis_reference` | camera server, specified in docs 01–10 | AloxCamServer |
| `alox_camera_server_tools` | not reached | none |

### 4. Data and configuration

All three servers and the watchdog read `alox.ini` through `tools_windows_user_documents_folder.vi` + "alox.ini" (the
folder actually comes from `alox_setup.ini [folders] settings`, see 01-architecture.md §3.4.1); none writes it. Server-side
settings changed by commands (timeouts, wafer origin/diameter/limit/frequency) live in memory only. The main application
reads the same keys through `alox_manager_properties` with dotted names (`hardware.xy.timeout` = `[hardware.xy]
timeout_I32` **(inferred)**).

| Key | Reader | Code default | `alox_default.ini` |
|---|---|---|---|
| `[hardware.camera] port_I32`, `timeout_I32`, `heartbeat_I32`, `maxfails_U32`, `watchdog_BOOL` | camera server; manager; modules heartbeat | 503 / 5000 / 30000 / 3 / T | 503 / 50000 / 60000 / 10 / TRUE |
| `[hardware.camera.highmag|lowmag] name` | `alox_manager_camera` Setup Camera | "" | — |
| `[hardware.monochromator] port_I32` | WL server, manager | **502** | **505** |
| `… comport_I32`, `baudrate_I32` (index), `timeout_I32` | WL server | 2 / 0 (9600) / 2000 | 1 / 0 / 5000 |
| `… timeout`, `waitposition`, `heartbeat`, `maxfails`, `watchdog` | manager / heartbeat service | 5000 / 0 / 30000 / 3 / T | 5000 / 500 / 60000 / 10 / TRUE |
| `[hardware.xy] port_I32` | XY server, manager | 504 | 504 |
| `… comport_I32`, `baudrate_I32`, `xaxis_I32`, `yaxis_I32`, `speed_DBL`, `slowspeed_DBL` | XY server | 2 / 3 (115200) / 1 / 2 / 10 / 1 | 2 / 3 / **1 and 10 (duplicate key)** / 2 / 10 / 10 |
| `… waferxorg_DBL`, `waferyorg_DBL`, `chuckxorg_DBL`, `chuckyorg_DBL` | XY server (and core, **inferred**) | 0 | 77.38 / 77.22 / 77.63 / 88.22 |
| `… waferlimit_BOOL`, `wafercheckfreq_I32` | XY server | F / 500 | TRUE / 500 |
| `… timeout`, `heartbeat`, `maxfails`, `watchdog`, `stagescoordsys_U32` | manager, heartbeat service, coordinate system | 5000 / 30000 / 3 / T / — | 10000 / 60000 / 10 / TRUE / 3 |
| `[general] wafersize_DBL` | XY server | 100 | 100 |
| `[general] core_I32`, `[log] maxlines_I32` | all servers, watchdog | 1 / 1000 | 1 / 1000 |
| `[general] watchdog_BOOL`, `watchdogmaxfails_I32`, `watchdogui_BOOL`, `extmodulesui_BOOL` | core / REST routes / modules heartbeat | — | FALSE / 10 / FALSE / FALSE |
| `[heartbeat] frequency_I32` | `alox_heartbeat_service` | 30000 | 1000 |
| `[hardware.xyz] port_I32`, `[hardware.camera.lowmag] port_I32` | no reader in this part | — | 2003 / 504 |

Files written: server event logs `<vision>\logs\<yymmdd>_wavelength_events.log` and `…_xy_events.log` (Log Server Error,
Server Type Monochromator / XY Stages; see 09 §7.3); watchdog log under the shared data folder via
`alox_watchdog_log.vi` (file name not read); main-application log lines ("Restarting XY Stages module…", "XY Stages
Restarted", …) via `alox_manager_log`.

Error codes used here (family 9400 "Alox hardware error" in `alox-errors.txt`; the individual codes are not in the file):
9401 camera timeout, 9402 XY timeout, 9403 WL timeout, 9404 X/Y/XY move outside wafer, 9035 refocus data, 9008 autofocus,
9999 AF timeout, 9002 invalid Show UI value, 9098 watchdog cannot stop process, 9095/9097 hardware comm warning/error
(disabled).

### 5. External dependencies

- **PI GCS LabVIEW driver** (`PI/Low Level/*.llb`, `PI/Mercury_GCS_Configuration_Setup.vi`): `MOV`, `MVR`, `POS?`, `STP`,
  `VEL`, `VEL?`, `ERR?`, `FRF`, `FRF?`, `JAX`, `JON`, `JON?`, `JBS?`, `General wait for movement to stop`,
  `GCSTranslateError`, `Close connection if open`. Hardware: two PI Mercury-class controllers (X = device 1 master,
  Y = device 2 slave) on one RS-232 daisy chain, 115200 baud, joystick on the controllers. Underneath: the PI GCS DLL
  **(inferred)**.
- **NI-VISA serial** (`vi.lib/Instr/_visa.llb`): Spectral Products monochromator (CM110/CM112 class **(inferred)**), binary
  byte protocol, resource `COM<n>`.
- NI TCP (`tcp.llb TCP Listen`), `config.llb` (INI), VI Server (`Open VI Reference`, `Start Asynchronous Call` 0xC0,
  `Run VI`, `Ctrl Val.Get/Set`, `Exec.State`), `System Exec` (`cmd /c START /B`, `tasklist`, `taskkill`), NI Vision (IMAQ
  point/image types, wafer map image), JKI REST Client + JKI JSON (watchdog), OpenG string library (`Log Server Error`,
  `alox_watchdog_log`).
- Windows only: process control relies on `cmd`, `tasklist`, `taskkill` and on executable names.

### 6. Interfaces with the other parts

- Part 1 (core): `alox_core_boot/init/exit/shutdown_cycle` and `alox_core_ui` call the managers, the server-module and
  watchdog managers; the modules-heartbeat service posts `Module Heartbeat` and `Module Resetted` items to the Alox core
  queue; `alox_manager_properties` supplies all settings; `alox_manager_log` receives restart messages; the
  `Synchronous Response *` library (folder `synchronous calls`) implements notifier waits for the command VIs.
- Part 3 (functions): ll/hl state machines (autofocus, refocus, refocus oxyd, get image, auto-exposure, search wavelength,
  detect object, tracking, thermal, bowing, mesa, tilt) call `alox_manager_camera`, `…_monochromator`, `…_xy`,
  `…_xy_coordinate_system` and the `*_async` wrappers.
- Part 5 (API): REST `GET /v1/system/heartbeat`, `/heartbeatfreq`, `/heartbeatretries`, `/v1/watchdog/visibility` feed the
  watchdog; API notifiers `heartbeat`, `hardware_reset`, `camz_hardware_warning` (and the monochromator/XY warning events
  sent by `alox_api_ws_send_*_warning_event`) go to websocket clients; camera status routes call `alox_manager_camera`.
- Part 4 (tools): `tools_alox_joystick`, `tools_update_system_status_from_xy_state` /
  `…_from_monochromator_state`, `alox_vision_overlay_hardware_infos`, `alox_vision_overlay_line_with_arrows_simple`.
- Part 6: `alox_investigation_process communication timestamps.vi` (sources/tests) is called by the three clients in the
  production build.

### 7. Unused or legacy code

- Never called: `alox_camera_command_autofocus_asynchronous`, `alox_camera_server_tools`, `alox_xy_do_axis_references`,
  `alox_xy_command_get_error`, `alox_wl_command_calibrate`, `…_log`, `…_reset`, `…_scan`, `…_set_gratings` (the servers
  implement these commands; nothing in the main application sends them). `alox_camera_command_get_acquisition_status` is
  used only by `alox_test_unit_camera_client` (tests).
- Unreachable in all servers: `Send Heartbeat` state and `HW TCP Send Heartbeat.vi`; the `Heartbeat` and `Statut`
  datatypes are handled by the clients but never sent.
- Disabled code: monochromator retry loops (`alox_manager_monochromator d30, d51`), hardware comm error notifier
  (`alox_modules_heartbeat_service d14`).
- Server type `Z Stage` exists in `alox_typedef_server_module_manager_type` but the Z stage lives in the camera server;
  INI keys `[hardware.xyz] port_I32 = 2003` and `[hardware.camera.lowmag] port_I32 = 504` have no reader here.
- `spectral_products_query_old.vi` (part 6) is an older query driver.
- AloxWatchdog has a build specification but is not in the "Alox GEN 1.4" installer (which ships AloxVision, AloxViewer,
  AloxWLServer, AloxXYServer, AloxCamServer).

### 8. Notes for the migration

1. **Error replies are probably never sent** (02-tcp-protocol.md §8.4): every WL/XY command whose result is an error
   cluster loses its acknowledgement when the command failed. The client then fails with its own timeout (9402/9403,
   after `hardware.xy.timeout` = 10 s in the INI). In particular a refused move (9404 outside wafer) reaches the core as an
   XY timeout **(inferred)**. Decide whether the new servers send error replies (recommended) and adapt the client.
2. **Automatic homing**: any XY move first runs `alox_xy_check_axis_references`; if a reference is lost, both
   controllers are reconnected and referenced (`FRF`) without operator confirmation. Same for the Z stage in the camera
   server (08-z-stage.md). A rewrite must keep or explicitly gate this safety-relevant behaviour.
3. **Concurrent hardware access**: the XY WAFER LIMITS LOOP calls `POS?`, `STP`, `JON` on the same PI connections as the
   consumer loop (e.g. during `Wait End Move`), without a lock. Serialise device access in the rewrite.
4. **Wafer limit semantics**: the move check passes when wafer diameter or origin is 0; the limit loop only stops the
   stages after the fact (period 500 ms) and only when `waferlimit_BOOL` is TRUE; the radius margin (`x+10`) and the
   constants 105/80 in `alox_xy_check_wafer position` must be confirmed. Wafer origin/diameter sent by the core are not
   persisted by the server; after a server restart only the INI values apply until the core re-sends them.
5. **Closing a client kills its server**: the client's Quit state sends `Exit`. The reset sequence sends Exit twice, then
   `taskkill /F`, waits a fixed 5 s, restarts, waits 1 s and re-runs Setup. The client reconnects forever (10 s connect
   timeout) and silently drops commands while disconnected (TCP Write on an invalid refnum); callers only see timeouts.
6. **Process control by name**: servers are found with `tasklist` + regex on the exe name and killed with `taskkill /t /F`.
   Any process with the same name is affected; a server that is still starting can be killed. The watchdog kills and
   restarts **`Alox.exe`** whereas the main build produces **`AloxVision.exe`** (`alox.lvproj`): unless the file is renamed
   on site, the watchdog cannot restart the core (open question).
7. **Two independent heartbeats with different clocks**: server heartbeat = `Tick Count` ms (U32, wraps after 49.7 days);
   core heartbeat = system time in seconds compared by the watchdog with `now > heartbeat + retries × freq`. Code defaults
   (30000 ms) differ from the shipped INI (1000 ms for the core, 60000 ms for modules).
8. **Port defaults disagree**: WL server and monochromator manager default to 502, the shipped INI says 505; both sides
   read the same key, so they agree only through the INI. The watchdog picks port 8002 in run-time, 8001 otherwise, while
   `[webservice] port_I32 = 8001`.
9. **INI duplicates**: `[hardware.xy]` contains `xaxis_I32` twice (1 and 10) and both I32 and DBL variants of speed/limits;
   LabVIEW's `Read Key` returns one of them. Clean the file during migration.
10. **Copy-paste defects to not reproduce**: `Set Chuck Origin` logs "Set Wafer Origin", `Set Wafer Check Freq` logs "Set
    Wafer Limit", `Get Position XY` sets the string "Get popoposs", XY `Get Status` timeout errors in the async wrappers
    are labelled "Get Status" for every method, the WL consumer loop is labelled "Z STAGE CONSUMER LOOP".
11. **Command handlers ignore payloads**: WL `Setup` uses the INI settings, not `Command Data`; WL/XY `Log` only stores the
    settings (TODO). `Set Timeout`, `Set Wafer …` are memory-only.
12. **Monochromator scan speed** (`Set Scan Speed`) converts a 0–10 level into a device speed with a table that depends on
    the grating (1800 vs 1200 gr/mm); the exact formula must be read from `alox_wl_server d42–d44` at higher resolution
    before re-implementation.
13. **Simulator**: AloxSim includes the managers and command VIs but not the client VIs (they are "always included" only
    in the Alox build); a manager `Init` in AloxSim would fail to open the client **(inferred)**.

### 9. Open questions

1. Is the shipped executable renamed `Alox.exe` on the instrument, or is the watchdog restart broken? Is AloxWatchdog
   deployed at all (not in the installer, `watchdog_BOOL = FALSE` by default)?
2. Which monochromator model and firmware (CM110/CM112?) and which PI controllers (C-863 Mercury?) are installed; does the
   Y controller really share the X serial port (single `comport_I32`)?
3. Do the clients ever receive error acknowledgements from the WL/XY servers (network capture of a 9404 move)?
4. Should automatic re-referencing on a lost reference stay automatic, given that it moves the stages?
5. What are the constants 105 and 80 and the `x+10` term in the wafer test, and should the margin follow
   `[general] wafersizeoffset_DBL`?
6. Which order do the core init/exit cycles use for Load Server / Init / Setup / Kill 'em All (part 1), and is a stale
   server killed at start-up?
7. The `AloxXYServer` build folder is named `v1.8_210mm`: is there a wafer-size-specific variant of the XY server?
8. Should the server-side wafer settings be persisted (they are lost on every server restart)?

---

## Part 3: Measurement functions (`sources/functions`)

Scope: the 88 VIs of group `3_functions`. Evidence marks: "observed `<vi> dN`" = seen in that block-diagram image
(`vc_export/html/NNNN <vi>dN.png`); **(inferred)** = deduced from names, call graph, typedefs, translations or changelog.
State, method and status names are quoted exactly from `typedefs.md`. Camera naming: since 1.0.0.8 the code says
"High Mag." for the *low-angle* camera and "Low Mag." for the *high-angle* camera (diagrams carry red labels "LOW ANGLE /
HIGH MAG"; `changelog.txt` 1.0.0.8).

### 1. Purpose

This part holds every measurement the Vision Core can execute. Eleven low-level (LL) functions each wrap one elementary
step: acquire an image (`get_image`), focus the Z stage coarsely (`autofocus`) or finely (`refocus`, `refocus_oxyd`),
find the mesa (`detect_circle`), find a reference pattern on the wafer while moving XY (`detect_object`, called "Search
Object" in the UI), keep the mesa centred (`tracking`), compute an exposure time (`auto_exposure`), choose the best
wavelength (`search_wavelength`), and measure the mesa and oxide diameters (`measure_mesa`, called "Measure Oxyd").
Three high-level (HL) functions chain the LL functions into the jobs a supervisor requests through the REST API: wafer
**bowing**/parallelism (`hl_bowing`), wafer **tilt** (`hl_tilt`) and the long-running **mesa oxidation monitoring**
(`hl_mesa`), which measures the oxide aperture diameter repeatedly, computes the oxidation rate and the remaining time,
and sends every result to the supervisor. The thermal (pyrometer) measurement is not in this group: see group 6
(`alox_function_ll_thermal_measurement_state_machine.vi`, `sources/Pyro/Bandit`); it has no wrapper or status VI and does
not follow the pattern below.

### 2. Architecture

#### 2.1 The common pattern (all 13 functions)

Every function `F` is a family of up to six VIs:

| VI | Kind | Role |
|---|---|---|
| `alox_function_<ll/hl>_F.vi` | functional global ("wrapper") | the only entry point; Method enum; keeps the state machine VI reference in shift registers |
| `alox_function_<ll/hl>_F_state_machine.vi` | worker VI | runs asynchronously; loop over a State enum; started by VI Server |
| `alox_function_<ll/hl>_F_status.vi` | helper | reads the worker's front panel and maps it to a Status enum |
| `alox_function_<ll/hl>_F_bundle_profile_parameters.vi` | helper | reads the current profile keys and bundles the Params cluster |
| `alox_function_<ll/hl>_F_ui_config.vi` | settings panel | edits the profile keys; loaded into a sub-panel by `alox_manager_panels` |
| `alox_function_<ll/hl>_F_ui_run.vi` | run panel (9 functions only) | shows live progress; opened by `alox_manager_ll_results_panels` / `alox_manager_hl_results_panels` |

The state machines, settings panels and run panels have no static caller (`called_by` empty). They are in the
"always included" sources of the Alox build specification (`alox.lvproj`, `Alox` EXE, Source[10..48]; the AloxSim
specification includes the three HL workers and their simulation variants) and are opened by name at run time.

**Wrapper (functional global)** (observed `alox_function_ll_autofocus d1-d35`, `alox_function_hl_mesa_measurement d3,
d15, d16, d18`). Uninitialised shift registers hold: State Machine VI refnum, Run Occurrence, Function Status, Function
State, Params, Last Report (HL: also SEMI Job Infos, Session). Methods (`alox_typedef_*_function_method`):

| Method | LL | HL | Behaviour (observed in `alox_function_ll_autofocus` unless stated) |
|---|---|---|---|
| `Init` | yes | yes | builds the path `<own folder>/alox_function_..._state_machine.vi`, Open VI Reference, creates an occurrence, resets Status=`Off`, State=`Stopped`, Params = typedef default (d3). HL mesa picks `..._state_machine_simulation.vi` instead when profile/INI property `simulation.mesa.enabled` is true (`alox_function_hl_mesa_measurement d3`). |
| `Exit` | yes | yes | Close Reference, reset registers (d4). |
| `Start` | yes | yes | only when Status=`Off` (case `"Off", Default`, d5): a 6-iteration loop writes the worker's controls by `Ctrl Val.Set` (`Stop`=F, `Autofocus Profile`=Params (d12), image, occurrence, run flag) then `Run VI` with Wait Until Done=F, Auto Dispose=F (d14). Sets Status=`Running`, State=`Start`. |
| `Stop` | yes | yes | `Ctrl Val.Set` `Stop`=T and `Running`=F, polls `Exec.State` every 10 ms until `Idle` or timeout (LL 30000 ms; HL 5000 ms although the diagram comment says "30 sec"), then `alox_manager_functions_condition` `Set Running`(F) with the function's name, Status=`Off`, State=`Stopped` (d1, d15; HL d15). |
| `Abort` | no | yes | same as Stop but writes the `Abort` control, timeout 30000 ms (`alox_function_hl_mesa_measurement d16`). |
| `Run Once` | yes | no | synchronous: Start, then "Wait End of Function" with timeout (autofocus: `Duration(s)*1000+10000` ms, 60000 ms if Duration is 0), then reads `Autofocus Report` and `error out` from the worker by `Ctrl Val.Get` (d16, d17). This is how HL functions and other LL functions call an LL function. |
| `Get Status` | yes | yes | calls `..._status.vi` and returns Status + State (d33). |
| `Get Last Measure` / `Get Results` / `Get Image` | yes | yes | `Ctrl Val.Get` of the worker's report indicator(s) and `error out` (d34; HL: `MESA Session`, `Mesa Report`, `error out`, d18). |
| `Set Params`, `Force Results` | detect_circle, measure_mesa, tracking | no | **(inferred)** write parameters/results into the registers without running (used by settings panels and by HL mesa to pass the last circle). |

**Status VI** (observed `alox_function_ll_autofocus_status d`): reads the worker's `Running`, `Stop` and `State` controls by
`Ctrl Val.Get` and maps them to `alox_typedef_*_function_status`: `Off`, `Off Cond OK`, `Running`, `Finished`, `Failed`.
Not running, not stopped and State=`Error` gives `Failed`. It also calls `alox_manager_functions_condition`, probably to
return `Off Cond OK` when the function is allowed to start **(inferred)**.

**State machine (worker)** (observed `alox_function_ll_autofocus_state_machine d`, `alox_function_ll_get_image_state_machine
d, d138`). Controls: input image (`Image SRC/DST`), `Occurence`, `<F> Profile` (Params), `Run Loop`, `Stop` (HL also
`Abort`, `Job` = SEMI Job Infos); indicators: `State`, `Running`, report, `error out`. Structure: a 3-frame flat
sequence. Frame 0 initialises; frame 1 is a While loop around a Case structure on the State enum with a 5-10 ms wait per
iteration; each state computes the next state and every state checks `Stop` (and `Abort`) first and jumps to `Stopped`.
Frame 2 sets `Running`=F and `alox_manager_functions_condition Set Running(<func>, F)` (observed get_image d138) and
signals the occurrence **(inferred)**. All states share the names `Start` (init session, close the settings panel:
`alox_manager_panels Close Panel`, autofocus d), `Started` (post-start initialisation: remember initial Z / exposure /
wavelength), ..., `Finished`, `Error`, `Stopped`. Errors are logged with `alox_manager_log Log Error In` and a source string
such as `Autofocus (Measure)`, `MESA (Search Object)`.

**Start/stop interlock**: `alox_manager_functions_condition` (group 1) keeps one "running" flag per
`alox_typedef_functions_condition_manager_funcname` (`Mesa Measure`, `Tilt Measure`, `Bowing Measure`, `Get Image`,
`Autofocus Large`, `Refocus`, `Refocus Oxyd`, `Detect Circle`, `Tracking`, `Measure Oxyd`, `Search Wavelength`,
`Search Object`, `Auto Exposure`) and answers `Get Condition OK`, `Get Joystick OK`, `Get Any Running ?`. The API
`put_functions_measure_*` VIs check it before queueing a job; the error text 9005 is "function not ready to start, or
already running".

**Progress and result reporting** (four channels):

1. Polling: `alox_functions_status_update_service.vi` → `alox_manager_functions_status.vi` (group 1) calls every wrapper's
   `Get Status`; the result feeds the UI, the websocket "status" events and the REST status.
2. Function UI user event: `alox_manager_functions_ui_events Generate Event` with `alox_typedef_functions_ui_event`
   (`AF Large Started/Measured/Finished`, `WL Started/Measured/Finished`, `DO Started/Autofocus/Refocus/Moving/
   Searching/Finished`, `MESA Started/State Changed/Measured/Finished`, `BOW ...`, `TILT ...`, `Auto Expo ...`,
   `Refocus ...`, `Refocus Oxyd ...`) and a Variant payload (the report). The `_ui_run` panels and the core UI register
   for it (observed `alox_function_ll_refocus_ui_run d`: case `"Refocus Measured"` unpacks the Refocus Report into focus
   position, score, found, progress and the Z/score graph).
3. Supervisor: HL state machines send `alox_manager_api_notifiers` notifications (types `started`, `measure mesa`,
   `measure tilt`, `measure bowing`, `finished`, `failed`, `mesa_status`...) whose payload is a JSON string built by
   `alox_tools_cast_measure_*_to_json_report`, `alox_tools_cast_started/failed_semi_job_to_json_report` (observed
   `alox_function_hl_mesa_measurement_state_machine d` "Send measurement to Supervisor"), and enqueue
   `Mesa/Bowing/Tilt Job Finished` items on the core process queue (`alox_manager_process_queue`).
4. Status codes: HL functions publish a numeric step code through `alox_manager_mesa_status_ID`,
   `alox_manager_bowing_status_ID`, `alox_manager_tilt_status_ID` (texts in `translations/mesa_en.lang`,
   `bowing_en.lang`, `tilt_en.lang`: e.g. mesa 404 "Search Object", 408-410 "Refocus (n-th try)", 411-413 "Refocus Oxyd",
   414-418 "Detect Mesa", 431-435 "Measure MESA (n-th try)", errors 1500-1511); LL functions use
   `alox_manager_system_status_ID`.

```
REST PUT /v1/functions/measuremesa ──► alox_api_v1_put_functions_measure_mesa (group 5)
        │   checks functions_condition, bundles profile params, enqueues "Measure Mesa"
        ▼
alox_core_ui (group 1) ──Start──► alox_function_hl_mesa_measurement.vi (FGV)
                                     │ VI Server: Ctrl Val.Set + Run VI (async)
                                     ▼
                         alox_function_hl_mesa_measurement_state_machine.vi
                           │ Run Once                    │ notifiers / user events / status IDs
                           ▼                             ▼
     LL wrappers (get_image, autofocus, refocus, ...)   core UI, ui_run panels, websocket, REST
           │ Run Once ► LL state machines ► managers: camera (TCP 503), xy, monochromator, exposure LUT
```

#### 2.2 Concurrency

Each worker runs in its own VI Server thread; the caller (core UI loop or an HL worker) does not block, except in
`Run Once`, which blocks the caller until the LL worker finishes or times out. An HL worker therefore executes its LL
steps strictly sequentially. Several functions can technically run at the same time (e.g. `get_image` live display and
`detect_circle`), which caused image flicker (changelog 1.0.0.3 bug fix); the functions-condition manager is the only
guard. Because each wrapper is a single non-reentrant FGV with one worker refnum, **a function can only have one
instance**: calling `Run Once` on `alox_function_ll_autofocus.vi` from the tilt worker while the UI runs it would collide
(the `Start` case ignores calls when Status is not `Off`). Hardware access is delegated to the managers of group 2 and,
for Z and image acquisition, to the camera server (`alox_manager_camera_*_async` → TCP 503; see
`docs/05-camera-acquisition.md`, `docs/06-autofocus-refocus.md`).

#### 2.3 Lifecycle

`alox_core_init_cycle` calls `Init` on every wrapper, `alox_core_exit_cycle` calls `Exit`; `alox_core_ui` calls
`Start` from user actions (`alox_typedef_user_action`: `Autofocus`, `Autofocus Setup`, ... `Mesa Setup`) and from
process-queue items (`Measure Mesa`, `Measure Tilt`, `Measure Bowing`, `Stop`, `Abort`, `Get Image`).
`tools_alox_stop_all_functions.vi` (group 4) calls `Stop` on 12 wrappers (not get_image) but is not reached by any build.
`get_image` runs continuously for the live display when no other function runs (changelog 1.0.0.11).

### 3. Function details

Hardware moves listed per function: XY = `alox_manager_xy` (XY server, group 2), Z and image = `alox_manager_camera` /
camera server, WL = `alox_manager_monochromator` (WL server), exposure = `alox_manager_camera` + `alox_manager_exposure_wl_lut`
(wavelength → exposure table).

#### 3.1 Get Image (`alox_function_ll_get_image*`, 5 VIs, no ui_run)

- Measures: nothing; acquires one processed image from the selected camera. Called with `Run Once` by almost every other
  function and by the REST `camera/grab*` routes; method `Get Image` returns the last image.
- Params `alox_typedef_get_image_function_parameters`: Camera Type, Led, Exposure Mode (`Fixed`/`Auto`/`Current`), Low Mag
  Exposure, Optional Rectangle, ZOOM, Brightness/Contrast/Gamma, Simulation (+ Path, Timer ms), six overlay switches
  (Name, Type, Timestamp, Index, HW Infos, Scale), Camera LEDcmd, Gain. The bundle VI also reads properties
  (`alox_manager_properties`).
- States `alox_typedef_get_image_function_state`: `Start`, `Started`, `Setup Hardware`, `Acquire`, `Finished`, `Error`,
  `Stopped`.
- `Setup Hardware` (observed `..._get_image_state_machine d`): per Exposure Mode, sets the exposure from the current
  wavelength through the exposure/WL LUT (`Fixed`, default) or keeps the current one; switches the LED of the selected
  camera on/off (`Get LED`/`Set LED`, 100 ms wait when the LED changed).
- `Acquire` (observed d75, header "ACQ => ROI => SUBSAMPLING => BCG"): `alox_manager_camera_get_image_async` (camera
  server), optional crop to Optional Rectangle (`tools_alox_crop_size_definition`), subsampling (ZOOM), brightness/
  contrast/gamma, then up to 8 overlays and calibration (`alox_manager_calibration`, `alox_vision_overlay_scale`,
  `alox_vision_overlay_hardware_infos`). In simulation an image file is read and "the overlay stored in png file" is used.
- Errors 9009-9014 (`alox_en.lang`). Output: IMAQ image (+ overlays).

#### 3.2 Autofocus "large" (`alox_function_ll_autofocus*`, 6 VIs; condition name `Autofocus Large`)

- Measures the Z position of best focus over the full autofocus range.
- Params `alox_typedef_autofocus_function_parameters`: Camera Type, Autofocus Method (`FFT`, `Sobel`, `Prewitt`, `Diff`,
  `Gradient`, `Sigma`, `FULL`), Autofocus Speed (`Slow`/`Fast`), Focus Detection Method (`Curve Maximum`, `Fit Maximum`,
  `Local Maximum`), Duration (s), Z Step (mm), Subsampling, Optional Rectangle, Show UI, overlays. Profile keys also read
  in the worker: `autofocus.fitpolyalgo` (default `Givens`), `autofocus.tolerancepc` (default 5); properties
  `hardware.z.zminautofocus` / `zmaxautofocus` (INI `[hardware.z]` 33.0 / 34.5 mm).
- States `alox_typedef_autofocus_function_state`: `Start` → `Started` → `Goto Z Pos` → `Focus Measure` → `Focus Finished`
  → `Focus Display` → `Finished`; `Error`, `Stopped`.
- `Start` (observed d): closes the settings panel, reads Z min/max. `Started` (observed d11, d30): 8-frame sequence that
  stores Init WL/Expo/Z; in Fast mode calls `get_image Run Once` "to set exposure time & LED state (needed because image
  acquisition is done by the camera/Z external module)"; note "JCB 14/01/2020 NO MORE WL RESET". `Goto Z Pos`
  (observed d38): waits until the camera-server Z position is within ±0.001 mm of `zminautofocus`; 30 s timeout →
  error 9401 "Z Timeout", logged "Autofocus (Check Z Start) / Z ="; on Stop sends `Z Stop`. `Focus Measure` (observed
  d45, "Autofocus (done in camera server)"): `alox_manager_camera_autofocus_async` with timeout
  `Duration*1000+20000` ms; the camera server scans Z and returns the score curve; saves the curve with
  `tools_save_autofocus_data` when `log.saveautofocusdata` (with SEMI job infos). `Focus Finished` (observed d54):
  polynomial fit of score = f(Z) with orders 4, 5 and 6 (`General Polynomial Fit`), maximum by the Focus Detection
  Method, validation by `tools_check_autofocus_large_data` → `Focus Display` (run panel, `AF Large Measured`) or `Error`
  "Autofocus (Detect)".
- Output `alox_typedef_autofocus_function_report`: Best Z Position, Best Score, Focus Data, Graph Data (X, Y, Y Polynom),
  Valid, Progress %. Moving Z to the best position is done by the camera server **(inferred; see docs/06)**.

#### 3.3 Refocus = "autofocus fine" (`alox_function_ll_refocus*`, 6 VIs + `alox_function_II_refocus_filter.vi`)

- Short Z scan around the current Z, in the camera server.
- Params `alox_typedef_refocus_function_parameters`: Camera Type, Autofocus Method, Steps, Step (mm), ROI Width/Height
  (px), Wait (ms), Show UI, overlays, Offset (mm).
- States `alox_typedef_refocus_function_state`: `Start`, `Started`, `Measure`, `Finished`, `Error`, `Stopped`.
- `Measure` (observed `..._refocus_state_machine d19`): builds `Camera Refocus Data` (method constant `Fin`; focus metric
  selected by camera type: `FFT` for High Mag., else `Sobel`; centred rectangle ROI `alox_vision_refocus_roi`, Get Calib
  for µm→px), calls `alox_manager_camera_refocus_async` (timeout 60000 ms), receives Zs and Scores, takes the maximum,
  **error if the best position is one of the first or last two points** (changelog 1.0.0.11), event `Refocus Measured`,
  saves data (`log.saveautofocusdata`) and every image to `<data>/Misc/images/refocus` when `log.saverefocusimages`.
- Output `alox_typedef_refocus_function_results`: Best Z Position, Zs, Best Score, Scores, Valid, Progress %.

#### 3.4 Refocus Oxyd (`alox_function_ll_refocus_oxyd*`, 7 VIs)

Same as Refocus with method `Oxyd` and a circular ROI of `ROI Diameter (µm)` centred on the last detected circle
(`alox_vision_oxyd_roi`; µm→px via calibration, error 9080 "invalid calibration"); camera-server errors become 9035
"refocus oxyd error"; the score curve is smoothed by `alox_function_II_refocus_filter.vi` (sort, mean, polynomial fit,
from calls) before the maximum is taken (observed `..._refocus_oxyd_state_machine d21`). States identical to Refocus.
In V1.2.6.2 **no HL function calls this wrapper** (callers: core UI, status manager, stop-all, REST overlay); HL mesa
refocuses with the plain Refocus function although its state comments still say "Refocus OXYD" and the changelog
(1.0.0.1) says "always use refocus oxyd".

#### 3.5 Detect Circle (`alox_function_ll_detect_circle*`, 6 VIs; condition name `Detect Circle`)

- Finds the circular mesa (VCSEL) in an image supplied by the caller (the worker does not acquire).
- Params `alox_typedef_detect_circle_function_parameters`, profile keys `detect.vcsel.*` (observed
  `..._detect_circle_bundle_profile_parameters d`: `method`, `bestcirclemethod`, `minradius` 20, `maxradius` 50,
  `downsamplemethod`, `morphopen/morphclose/dilate/erode`, `edgethreshold` 15, `edgeminlength` 15, `edgeocclusion`,
  `edgeminscore` 70, `extendshape`, `rectangles`, `ellipses`, `circles`, `binarymethod`, `objecttype`, `detectkernel`,
  `removesmalls(count)`, `binaryinvert`, `removeborder`, `fillhole`, `mincircfactor` 0.75, `maxcircfactor` 1.25,
  `overlaybestcircle/allcircles/objects`, `dp`, `mindist`, `param1`, `param2` (1.25 each), `scoreresult` 75,
  `occlusionresult` 30, `modelpath`, `maskpath`).
- States `alox_typedef_detect_circle_function_state`: `Start`, `Started`, `Detect Circle`, `Finished`, `Error`, `Stopped`.
- Algorithm (observed `..._detect_circle_state_machine d10, d39, d44`): radius range µm→px; optional downsampling
  (`IMAQ Resample`); grey morphology open/close/dilate/erode; then by Algo Method:
  `Shape` = `IMAQ Detect Circles` and/or `IMAQ Detect Ellipses` (ellipse radius = mean of minor/major) with edge
  threshold, min length, min score ×10, occlusion, "extend research"; `Obj learning` = geometric pattern matching
  (`IMAQ Setup/Match Geometric Pattern 2`, rotation 0-360°, score ×10) against a model PNG read from
  `<Windows Documents>\profile\...mesa_model.png` (`tools_windows_user_documents_folder`), positions flipped with
  `hardware.camera.highmag.hflip/vflip`; `Blobs` = `IMAQ Local Threshold`, remove particles/border, fill holes,
  particle filter on circularity, `IMAQ Find Circles` (from calls). Best circle by `Centered`, `Score` or `Largest`
  (the `Score` case seen in d10 computes the minimum distance to the image centre). Errors: 9015 "no circular vcsel found".
- Output `alox_typedef_detect_circle_function_result`: Best Circle (centre, radius), All Circles, Best Circle Index,
  Found, Process Time; overlays via `alox_vision_overlay_thick_circle`.
- **Python/Hough/DLL**: not used in V1.2.6.2. The parameters still carry the OpenCV `HoughCircles` values (`MRQT`: dp,
  minDist, param1, param2; typedef `alox_typedef_detect_circle_function_hough.ctl`), but the Algo Method enum has only
  `Shape`/`Obj learning`/`Blobs`, and neither the call graph nor the frames viewed show a Python Node, a DLL call or a
  PythonLV VI. The Python path lives in group 6: `sources/PythonLV` (`alox_function_ll_detect_circle_PythonNode_*`,
  `test_py_corr_batch.vi` → build `Python_fct_dectectcircle`, scripts `PythonLV/Hough/*.py`, `Correlation/py/corr_fct.py`)
  and `vc_text/dll/hough_circle_detection_DLL.cpp` (OpenCV `GaussianBlur 9x9 σ=2` + `HoughCircles(HOUGH_GRADIENT, dp=1,
  minDistance, param1, param2, minRadius, maxRadius)`, exports `calc_HoughCircles`, `get_CircleResults`). None of them is
  reached by the Alox build. `alox_function_ll_detect_circle_ui_config_py.vi` is an older copy of the settings panel
  (writes `detect.vcsel.*`, observed d) with no Python call; it is in the project but in no build.

#### 3.6 Search Object / Detect Object (`alox_function_ll_detect_object*`, 9 VIs; condition name `Search Object`)

- Finds a learned pattern (alignment mark) by moving XY on a search pattern and centres it.
- Params `alox_typedef_detect_object_function_parameters`: Start Search Pos (mm), Search Model Path, Learn Mask Path, Edge
  Threshold, Min Length, Min Score (%), Max Occlusion (%), Morph Open/Close, Max Moves, Step (mm), Autofocus, Refocus,
  overlays, Offset X/Y.
- States `alox_typedef_detect_object_function_state`: `Start`, `Started`, `Move To Pos`, `Position OK?`,
  `Autofocus Init`, `Autofocus`, `Autofocus Finished`, `Refocus Init`, `Refocus`, `Refocus Finished`, `Detect Object`,
  `Move To Object`, `Object Pos OK ?`, `Finished`, `Error`, `Stopped`.
- Algorithm: `Move To Pos` moves XY to the next of the Search Positions computed from Start Pos, Step and Max Moves
  **(pattern inferred)**; `Position OK?` waits for the stages; optional LL autofocus / refocus `Run Once`; `Detect Object`
  (observed d66): Low Mag. image by `get_image Run Once`, preprocessing (`alox_vision_detect_object_preprocessing`),
  `IMAQ Match Geometric Pattern 2` (≤50 matches, rotation 0-360°, score ×10), flips from `hardware.camera.lowmag.*flip`,
  position in mm by `alox_function_ll_detect_object_get_position.vi` (image centre, calibration, coordinate system,
  offsets); found → `Move To Object` → `Object Pos OK ?` → `Finished`; not found → `Move To Pos`; event `DO Searching`.
  Errors 9045-9047 ("no object found").
- Output `alox_typedef_detect_object_function_results`: Search Positions, Current XY Pos, Autofocus Z/Score/graph,
  Found Pos (mm), Found Object ?, Best Match, All Matches.
- Settings helpers: `_ui_model` (learn model/mask from a drawn ROI, `IMAQ Draw`, overlay), `_ui_offset` (define the
  offset between the matched pattern and the target), `_ui_run`.

#### 3.7 Tracking (`alox_function_ll_tracking*`, 5 VIs; no ui_run)

- Re-centres the mesa under the camera by relative XY moves.
- Params `alox_typedef_tracking_function_parameters`: Tracking Diam. (µm), Overlay Tracking Zone.
- States `alox_typedef_tracking_function_state`: `Start`, `Started`, `Track`, `Finished`, `Error`, `Stopped`.
- `Track` (observed `..._tracking_state_machine d12`): `tools_tracking_move_relative` computes the pixel and mm offset of
  the circle centre from the image centre ("Relative XY Move to center object ?"), threshold = Tracking Diam/2, flips from
  `hardware.camera.highmag.*flip`; if outside, `Move Relative` X, `Move Wait`, then Y, logged "Tracking (Move X=%f)".
  The circle comes from the caller (HL mesa after Detect Circle; `Set Params`) **(inferred)**.
- Output `alox_typedef_tracking_function_results`: Move X/Y (mm, pix), Moved X/Y, Process Time. Errors 9025-9028.

#### 3.8 Auto Exposure (`alox_function_ll_auto_exposure*`, 6 VIs)

- Finds the exposure that gives a target brightness in a circular ROI on the mesa, for the current wavelength or for all
  wavelengths of the LUT, and stores it in the exposure/WL table.
- Params `alox_typedef_auto_exposure_function_parameters`: Mode (`Current Wavelength`/`All Wavelengths`), Exposure Min/Max
  (µs), Target Brightness, ROI Diameter (µm), Show Window, overlays, BypassCircle. Uses the search-wavelength profile too.
- States `alox_typedef_auto_exposure_function_state`: `Start`, `Started`, `Set Wavelength`, `Check Wavelength`,
  `Set Exposure`, `Wait`, `Measure Expo`, `Measure Finished`, `Set Auto Expo`, `Finished`, `Error`, `Stopped`.
- Algorithm (state comments observed in the header frames; `Measure Expo` observed d74): for each wavelength
  (`tools_alox_autoexpo_WL_selection`): set WL, check it, set exposure, wait 2 × exposure, measure the mean grey level
  in a circle of ROI Diameter centred on the image or on the detected circle (unless BypassCircle) at two exposures
  (dark/bright; the second is rescaled and retried up to 5 times when the brightness is outside the usable window), fit a
  line brightness = f(exposure) (`geometry_simple_line_equation_from_two_points`) and solve for the target brightness;
  `Set Auto Expo` writes the LUT; exposure limited to the camera range 33 µs-1.9 s (changelog 1.0.0.11). `Error` and
  `Stopped` restore the original wavelength and exposure. Data saved by `tools_save_auto_exposure_data`
  (`log.saveautoexpodata`). Errors 9089-9094.
- Output `alox_typedef_auto_exposure_report`: Exposure1/2, Brightness1/2, Exposure, Brightness, Found, wavelengths.

#### 3.9 Search Wavelength (`alox_function_ll_search_wavelength*`, 10 VIs)

- Scans the monochromator and selects the wavelength of maximal contrast between the oxidised and non-oxidised zones.
- Params `alox_typedef_search_wavelength_function_parameters`: WL Mode (`MANUAL`/`SCAN`), Manual WL, Start/End/Step WL, Min.
  Contrast, WL ROI Type (`Dual Spots`/`Spot-Annular`) and its geometry, overlays, DETECT CIRCLE METHOD (`SINGLE (AT START
  WL)`/`FULL (AT EVERY WL)`), Step Refocus, WL shift (nm).
- States `alox_typedef_search_wavelength_function_state`: `Start`, `Started`, `Init WL`, `Move WL`, `WL Pos OK ?`,
  `Refocus`, `Detect circle`, `Measure`, `Move Opt WL`, `Check Opt WL`, `Finished`, `Error`, `Stopped`.
- Algorithm: `Move WL` sets the monochromator and the exposure from the LUT, waiting 2 × max(old, new exposure) (changelog
  1.0.0.11); optional LL refocus every Step Refocus wavelengths; `Detect circle` uses `tool_alox_detectCircle_lessFar_and_found`
  and `measure_mesa Run Once` to place the OXYD / NOXYD ROIs (`tool_alox_wavelength_ROI_selection`); `Measure` (observed
  d59): **contrast = mean(OX) − mean(NOX)** from `IMAQ Histogram` in the two masks, keeps the optimum, progress =
  (WL − Start)/(End − Start)·100, event `WL Measured`, optional image `<images wavelength>/<wl>nm_<expo>us.png`
  (`log.savewavelengthimage`), next WL = WL + Step until End → `Move Opt WL` → `Check Opt WL`. Contrast below Min.
  Contrast makes the result invalid. `_concatening` and `_search_gradient_evalutation` post-process the spectrum (std dev /
  gradient, from calls); `_overlays` draws ROIs and results. Data: `tools_save_wavelength_data` into the job-history WL
  folder (`log.savewavelengthdata`). Errors 9040-9043.
- Output `alox_typedef_search_wavelength_function_report` (current/optimal WL, exposure, contrast, spectrum and histogram
  graphs, Valid).

#### 3.10 Measure Oxyd / Measure Mesa (`alox_function_ll_measure_mesa*`, 5 VIs; condition name `Measure Oxyd`)

- Measures the external (mesa) diameter and the oxide aperture diameter in one image supplied by the caller.
- Params `alox_typedef_measure_mesa_function_parameters` (mesa edge: search direction, polarity, strength, kernel, width,
  step, min/max diameter; oxyd edge: same; Oxyd Measure Type `Mean`/`Median`/`Fit`/`Surface`/`Circumscribed`/`Inscribed`;
  Target Diam; fit max distance/min score/max iterations; Reject Outliers; Meas Mesa / Meas Oxid; Mesa Shape Type
  `Circular`/`Line`/`Rectangle`; oxid result offset; overlay switches).
- States `alox_typedef_measure_mesa_function_state`: `Start`, `Started`, `Measure`, `Finished`, `Error`, `Stopped`.
- `Measure` is a 4-frame sequence (observed d10-d13, d145). Frame 1 switches on Mesa Shape Type; the `Line` case (d13)
  builds radial/linear search lines (`tool_alox_measLine_ROI_builder`), finds first+last edges for the mesa
  (`IMAQ Edge Tool 3` / `Find Edge`; error 9022 "No Mesa found"), then the first oxide edge on each line from the mesa
  edge inward, fits a circle (`IMAQ Fit Circle 2`, outlier rejection), and computes diameters: fitted, median and mean of
  the chord lengths, surface- and perimeter-equivalent, inscribed and circumscribed (`geometry_*`), circ/insc ratio, std
  dev (`RoundnessCalculations-NI`), gradient strength along the lines (`tool_alox_strength_analysis`). Other shapes use
  `IMAQ Spoke 4`, `Find Circular Edge 3`, `Rake 3` (from calls). Frame 3 (d145): `Finished` when (Mesa Found or not
  `measure.vcsel.MeasMesaMandatory`) and (Oxyd Found or not `measure.oxyd.MeasOxydMandatory`) and no error, otherwise
  `Error`; error −1074395318 from `IMAQ Fit Circle 2` is cleared ("Oxyd missing is normal when starting process").
  Since 1.0.0.11 a missing mesa is not an error and the last Detect Circle circle is used (changelog).
- Output `alox_typedef_measure_mesa_function_results` (external and oxyd circles, Diameters Measurements, chord lines,
  points used, search lines, Mesa/Oxyd Found, gradient values). Errors 9020-9023.

#### 3.11 HL Mesa measurement (`alox_function_hl_mesa_measurement*`, 8 VIs incl. `alox_functions_hl_mesa_measurement_status.vi` and `alox_global_mesa_measurement_function.vi`)

- Monitors the lateral oxidation of a VCSEL mesa during the process (or one measurement in Singleshot): oxide aperture
  diameter over time, oxidation rate (µm/min) and remaining time to reach the Oxyd Target.
- Inputs: `Job` (SEMI Job Infos), Params `alox_typedef_mesa_measurement_function_parameters`: Start Position (mm), Tilt
  Correction, Refocus Time (s), Refocus Retries, Oxyd Target (µm), Line Reg. Time (s), Singleshot, # Averages, Show
  Window, overlays, positionsctrl, analysis, Tracking, WL Mode, EXPOSURE MODE.
- States `alox_typedef_mesa_measurement_function_state` (all 21 seen in the header frames). Transitions (observed unless
  marked):
  - `Start` (d6): sets global `MEAS MESA RUNNING`, prepares the job-history folder (`path_process_lot_wafer_job`), resets
    globals `WL RETRY`/`AUTO EXPO RETRY`, resets the session; next = `Measure MESA Init` if `analysis`, else `Move Start`
    if `positionsctrl`, else `Autofocus Large`.
  - `Move Start` → `Wait Move`: XY move to Start Position relative to the wafer centre; `Search Init` → `Search Object`
    (d41): start position corrected by the measured tilt (`alox_manager_tilt Get Tilt`, `tools_alox_get_tilted_coordinates`)
    when Tilt Correction, `detect_object Run Once`, then WL set to profile `wavelength.wlmanual`; success → `Refocus`.
  - `Autofocus Large` (LL autofocus) → `Refocus` (LL refocus) → `Detect Circle Init` → `Detect Circle` (d81: High Mag.
    `get_image Run Once` + `detect_circle Run Once`, status 414; on success → `Track MESA` or `Auto Expo`, retries counted)
    → `Track MESA` (LL tracking) → `Auto Expo` (LL auto exposure, skipped once `Auto Expo Done`) → `Search Wavelength`
    (d124: `MANUAL` → `Set Wavelength` (or `Measure MESA Init` when `WL RETRY`); `SCAN` → LL search wavelength) →
    `Set Wavelength` → `Measure MESA Init`.
  - Measurement loop: `Measure MESA Refocus` (d151: when `Refocus Time` has elapsed since the last refocus, LL refocus
    `Run Once` (status 408/411); success → `Detect Circle Init`; failures counted against Refocus Retries, "restart large
    autofocus if 3 successive errors") → `Measure MESA` (d, d4: averaged measurement `tools_mesa_averaged_measurement`
    = `get_image` + `detect_circle` + `measure_mesa` × # Averages; position (WL, XY in wafer coordinates, Z) recorded;
    oxidation-rate line fit on the samples of the last Line Reg. Time with ≥10 points (`alox_tool_extract_mesa_fit_data`,
    `alox_tools_fit_diameters_measures`, outliers rejected) and remaining time (`alox_tools_get_mesa_remaining_time`,
    errors 9003/9004 tolerated); image with oxyd overlay; notification `measure mesa` (JSON, sample in
    `vc_text/measure_mesa.txt`) and event `MESA Measured`; next = `Display Save` if found and no error, else retry
    `Measure MESA` up to 5 times then `Display Save ERROR`) → `Display Save` (d173: saves the image every
    `log.imagesfrequency` ms in process-chamber mode (`general.coretype` 0) when `log.savemesaimages`, file
    `<PJ>_<Wafer>_<Zone>_%y%m%d_%H%M%S` under `<data>/images/mapping`, overlays burnt in if `general.saveOverlay`) →
    back to `Measure MESA Refocus` until Stop/Abort, or `Finished` when Singleshot **(loop-back and Singleshot exit
    inferred)**.
  - `Finished`, `Error` ("Finished with error"), `Stopped` ("Stopped by user"): job notifications, process-queue
    `Mesa Job Finished`, job data files (`tools_alox_job_data_filename`, `tools_alox_shared_jobhistory_folder_mesa_path`).
- Output: `alox_typedef_mesa_measurement_report` (SEMI job, timestamp, external fit diameter, oxyd diameters, oxyd rate
  and remaining time, linear fit, positions, wavelength, raw/filtered values), `MESA Session`, display report, graph.
- `alox_global_mesa_measurement_function.vi`: global booleans `WL RETRY`, `AUTO EXPO RETRY`, `LAUNCH FROM HMI WL`,
  `LAUNCH FROM HMI AE` (observed p.png), written by the core UI and the settings panel to force a new wavelength search or
  auto exposure during a running job.

#### 3.12 HL Bowing measurement (`alox_function_hl_bowing_measurement*`, 8 VIs)

- Measures wafer bowing and wafer/chuck parallelism from autofocus Z values at 9 points.
- Inputs: `Job`, Params `alox_typedef_bowing_measurement_parameters` (Camera Type, Measure Distance (mm), Max Bowing (µm),
  Show Window, overlays, Fast method; profile `measure.bowing.*`, e.g. `measure.bowing.methodFast`), chuck properties
  `alox_typedef_bowing_measurement_chuck_properties` from INI `[hardware.chuck]` (dz1..dz5, maxparallelism, bowchuckA/B)
  via `_bundle_chuck_properties`, properties `general.bowingOffset`, `general.wafersize`, `general.bowingAngle`.
- States `alox_typedef_bowing_measurement_function_state`: `Start`, `Started`, `Move to Pos Zi`, `Wait Pos Zi`,
  `Autofocus Large`, `Autofocus Fine`, `Measure Bowing`, `Finished`, `Error`, `Stopped`.
- Algorithm (observed d24, d93; position names from `bowing_en.lang`): positions ZW2 (wafer centre), ZW3 (NE), ZC3, ZW1
  (SW), ZC1, ZW4 (SE), ZC4, ZW5 (NW), ZC5 computed from wafer size/2 + bowing offset, the bowing angle (deg→rad),
  Measure Distance and the chuck origin. For each row of `Z Measures`: `Move to Pos Zi` (absolute XY move in the wafer
  coordinate system, retried 3 times, status 203-211, errors 1002-1010) → `Wait Pos Zi` → `Autofocus Large` (LL
  autofocus Run Once) → `Autofocus Fine` (LL refocus Run Once; both loop on error, changelog 1.0.0.7) → `Measure Bowing`
  (stores Z, recomputes the report, event `BOW Measured`) → next position, `Finished` after the last.
  Formulas (diagram text): `Bowing A = ((z1+z3)/2 − z2)·(wafersize/(2·dx))`, `Bowing B = ((z4+z5)/2 − z2)·(wafersize/(2·dx))`
  (×1000 → µm, valid if ≤ Max Bowing); `Parallelism A = ((Zc1+DZ1−Zw1) − (Zc3+DZ3−Zw3))·1000` (valid if ≤ Max
  Parallelism); B likewise with points 4/5 (`tools_alox_bowing_computation`, group 4).
- Output `alox_typedef_bowing_measurement` (Zwafer1-5, Zchuck1/3/4/5, Bowing/Parallelism A/B and validity, dZchuck);
  JSON `measure bowing`; job file `tools_save_job_wafer_` / `tools_alox_job_data_filename_bowing` in the bowing
  job-history folder; images if `log.savebowingimages`; initial wavelength restored (session `Initial WL`).

#### 3.13 HL Tilt measurement (`alox_function_hl_tilt_measurement*`, 7 VIs)

- Measures the wafer rotation angle (tilt) either on a straight edge or between two learned objects; the result is
  stored by `alox_manager_tilt Set Tilt` and later used by HL mesa (Tilt Correction).
- Inputs: `Job`, Params `alox_typedef_tilt_measurement_parameters` (Camera Type, Start Search Pos, Tilt Method `Straight
  Edge`/`Dual Objects`, Max Moves, Step, Straight Edge Detection cluster with Edge Method `First/Best/Hough Edge Rake`,
  `First/Best Edge Projection`, Dual Objects Detection cluster with two model/mask paths and scores, Autofocus, Refocus,
  overlays).
- States `alox_typedef_tilt_measurement_function_state`: `Start`, `Started`, `Move To Pos`, `Position OK?`,
  `Autofocus Init` ("Select high angle camera"), `Autofocus`, `Autofocus Finished`, `Refocus Init`, `Refocus`,
  `Refocus Finished`, `Measure Tilt (Straight Edge)`, `Measure Tilt (Objects)`, `Notify Client`, `Display & Save`,
  `Finished`, `Error`, `Stopped`.
- Algorithm: XY move to the start position, optional LL autofocus/refocus `Run Once`, then either
  `alox_vision_measure_tilt_straight_edge` (group 4) or, for Dual Objects (observed d84), `get_image Run Once`,
  preprocessing, geometric match of object 1, mask it (`alox_vision_mask_matched_object`), match object 2, convert both
  pixel positions to wafer mm through the current XY position (`tools_tracking_move_relative`, `Get Position XY`,
  `Convert` to `Wafer`); objects may be found at different XY positions (changelog 1.0.0.7). Then `Notify Client`
  (JSON `measure tilt`, `TILT Measured`), `Display & Save` (tilt job-history folder, `log.savetiltimages`). Tilt not found
  → status 1499 / error 9086.
- Output `alox_typedef_tilt_measurement_report` (straight-edge report, best matches in px and wafer mm, Tilt Angle (°) and
  validity, process times).

#### 3.14 Simulation state machines (AloxSim)

`alox_function_hl_*_state_machine_simulation.vi` (3 VIs, AloxSim only) replace the real workers: no hardware, synthetic
results from INI `[simulation.mesa]` (duration, noise, mesa/oxyd diameters, samples: `alox_tools_analyze_mesa_simulation`)
or fixed values (bowing: wafer diameter hard-coded 150 with "TODO: Get Wafer Diameter from profile"), then the same
notifier/JSON and process-queue messages (observed `..._bowing_measurement_state_machine_simulation d`).

### 4. Components

| VI | Role | Used by (apps / callers) |
|---|---|---|
| `alox_function_ll_{get_image, autofocus, refocus, refocus_oxyd, detect_circle, detect_object, tracking, auto_exposure, search_wavelength, measure_mesa}.vi` (10) | LL wrapper FGVs (Method enum, VI Server control of the worker) | Alox (+AloxSim except refocus_oxyd) / core init/exit/ui, `alox_manager_functions_status`, HL and LL workers, settings panels, stop-all, REST grab (get_image) |
| `alox_function_hl_{mesa, bowing, tilt}_measurement.vi` (3) | HL wrapper FGVs; mesa chooses real or simulation worker | Alox, AloxSim / core ui, simulator ui, init/exit, status manager, settings panels, `alox_properties_ui` (bowing) |
| `alox_function_ll_*_state_machine.vi` (10) | LL workers, run asynchronously | Alox (dynamic) |
| `alox_function_hl_*_state_machine.vi` (3) | HL workers | Alox, AloxSim (dynamic) |
| `alox_function_hl_*_state_machine_simulation.vi` (3) | simulated HL workers | AloxSim (dynamic) |
| `alox_function_ll_search_wavelength_state_machine_old.vi` | previous search-wavelength worker (same calls) | none |
| `alox_function_ll_*_status.vi` (10), `alox_function_hl_{bowing,tilt}_measurement_status.vi`, `alox_functions_hl_mesa_measurement_status.vi` | map worker controls to Status | own wrapper |
| `alox_function_*_bundle_profile_parameters.vi` (13) | read profile keys into the Params cluster | workers, settings panels, core ui, REST overlay/measure VIs |
| `alox_function_hl_bowing_measurement_bundle_chuck_properties.vi` | read `[hardware.chuck]` properties | bowing worker and panel |
| `alox_function_*_ui_config.vi` (13) | settings panels (profile edition, learning, tests) | Alox (dynamic, `alox_manager_panels`) |
| `alox_function_ll_detect_circle_ui_config_py.vi` | old copy of the detect-circle panel | none (in project, no build) |
| `alox_function_{ll_autofocus, ll_refocus, ll_refocus_oxyd, ll_auto_exposure, ll_search_wavelength, ll_detect_object, hl_bowing, hl_mesa, hl_tilt}_ui_run.vi` (9) | run panels driven by Function UI events | Alox (dynamic, results-panel managers) |
| `alox_function_II_refocus_filter.vi` | smooths the refocus-oxyd score curve (sort, mean, poly fit) | refocus_oxyd worker |
| `alox_function_ll_detect_object_get_position.vi` | match pixel position → stage mm (calibration, coordinate system) | detect_object worker and panel |
| `alox_function_ll_detect_object_ui_model.vi`, `..._ui_offset.vi` | learn model/mask; define target offset | detect_object, detect_circle, tilt panels |
| `alox_function_ll_search_wavelength_concatening.vi`, `alox_function_ll_search_gradient_evalutation.vi` | spectrum post-processing (std dev, gradient) (from calls) | search_wavelength workers |
| `alox_function_ll_search_wavelength_overlays.vi` | ROI/result overlays | search_wavelength worker and panel |
| `alox_global_mesa_measurement_function.vi` | global booleans WL/AE retry and "launch from HMI" | core ui, mesa worker and panel |

### 5. Data and configuration

- **Profiles** (folder INI `[general] profilefolder_PATH`, default `/D/ALOX/profiles`, profile `DEFAULT`) through
  `alox_manager_profile` (group 1): one key namespace per function, e.g. `autofocus.*`, `detect.vcsel.*`,
  `measure.vcsel.*`, `measure.oxyd.*`, `measure.bowing.*`, `wavelength.wlmanual`. Defaults are the constants wired
  in the bundle VIs (see 3.5).
- **Properties / INI** (`alox_manager_properties`): `[hardware.z]` zmin/zmaxautofocus; `[hardware.camera.highmag|lowmag]`
  hflip/vflip; `[hardware.chuck]`; `[general]` coretype, wafersize, bowingAngle, bowingOffset, saveOverlay,
  saveOverlayRes, outputfolder (`D:/alox/data`); `[log]` saveautofocusdata, savewavelengthdata(image), saveautoexpodata,
  savebowing/mesa/tilt data and images, saverefocusimages, imagesfrequency, datafrequency; `[simulation.*]`.
- **Files written**: autofocus/refocus curves (`tools_save_autofocus_data`, `tools_save_refocus_data`), wavelength data and
  `<wl>nm_<expo>us.png`, auto-exposure data, refocus images (`<data>/Misc/images/refocus`), mesa images
  (`<data>/images/mapping`, name `<PJ>_<Wafer>_<Zone>_%y%m%d_%H%M%S`), job files per wafer in the shared job-history
  folders (`tools_alox_shared_jobhistory_folder_{mesa,bowing,tilt,wl}_path`), oxide strength data (measure-mesa panel).
- **Files read**: pattern models and masks (PNG with vision info) from the Windows Documents folder or the paths in the
  profile; simulation images (get_image).
- **Exposure/WL LUT**: `alox_manager_exposure_wl_lut` (group 1/2) read by get_image, autofocus, search wavelength, written
  by auto exposure; edited in `alox_wl_auto_exposures_table_ui`.

### 6. External dependencies

- NI Vision (IMAQ): image management, overlays, geometric/pattern matching (`Learn/Setup/Match Geometric Pattern`),
  shape detection (`Detect Circles/Ellipses/Shapes`), morphology and particle analysis, edge tools (`Find Edge`, `Edge
  Tool 3`, `Rake 3`, `Spoke 4`, `Find Circular Edge 3`, `Fit Circle 2`), histogram, calibration, image files.
- NI Analysis (`General Polynomial Fit`, mean/median/std dev), OpenG array tools, `System Exec.vi` (measure-mesa worker;
  purpose not located), LabVIEW example `Overlay Points with User Specified Size.vi`.
- An external subVI outside the source tree: `D:/AET Group/OneDrive - AET Group/Images/test_correlation/SubVIs/Format
  Geometric Patterns Results.vi` (detect-circle worker).
- Hardware only through group 2 managers: camera server (TCP 503: images, Z, autofocus/refocus scans, LEDs, exposure),
  XY server, monochromator (WL) server. No direct driver call in this group.
- Python/OpenCV only in group 6 (not used by the production build, see 3.5).

### 7. Interfaces with the other parts

- Group 1 (core): lifecycle (init/exit cycles), start from `alox_core_ui` and the process queue, status polling
  (`alox_manager_functions_status`), interlock (`alox_manager_functions_condition`), UI events, panels and results-panel
  managers, profile/property/log/SEMI/status-ID managers, globals.
- Group 2 (hardware managers): camera (incl. `_autofocus_async`, `_refocus_async`, `_get_image_async`), xy,
  xy_coordinate_system, monochromator, calibration.
- Group 4 (vision tools): `alox_vision_*` overlays and algorithms, `tools_*` (bowing computation, tilt straight edge,
  mesa averaged measurement, tracking move, data saving, job file names).
- Group 5 (API): `alox_api_v1_put_functions_measure_{mesa,tilt,bowing}`, `stop`, `abort`, `camera/grab*` and the
  `camera/overlay/*` routes call the bundle VIs and wrappers; HL workers feed websocket/REST via notifiers.
- Group 6: thermal measurement worker, PythonLV/Hough experiments.

### 8. Unused or legacy code

- In this group: `alox_function_ll_search_wavelength_state_machine_old.vi` (no build), `alox_function_ll_detect_circle_ui_config_py.vi`
  (no build); `alox_function_ll_refocus_oxyd*` is reachable from the UI/API only (no HL use).
- Related, outside the project tree (`?` folder): `alox_function_ll_autofocus_ui.vi`, `alox_function_ll_detect_circle_state_machine_old.vi`,
  `..._proto.vi`, `alox_function_ll_search_wavelength_ui_config_.vi`, `alox_manager_hl_functions_condition.vi`.
- `tools_alox_stop_all_functions.vi` (group 4) is not reached by any build.
- Vestiges: Hough parameters in the detect-circle params/profile; `alox_typedef_search_wavelength_function_mode_mesa`
  (`Manual`/`Auto`) beside `MANUAL`/`SCAN`; mesa state comments "Refocus OXYD" and "Should use Low Angle camera" no longer
  match the code; `alox_typedef_*_function_status` repeated 13 times with identical items.
- The bowing simulation worker shows broken wires (dashed with red crosses) around a greyed `Display Bowing Measure`
  constant (observed `..._bowing_measurement_state_machine_simulation d`): it may not run in this export.

### 9. Notes for the migration

- Replace the FGV + VI Server pattern by one class per function with `start()`, `stop()`, `abort()`, `run_once(timeout)`,
  `status()`, `last_result()`, a worker thread/task and a shared "running" registry. Keep the exact Status semantics
  (`Off`, `Off Cond OK`, `Running`, `Finished`, `Failed`) and the State names, because they are exposed to the supervisor.
- Timeouts to keep or revise: LL Stop 30 s, HL Stop 5 s (comment says 30 s), Abort 30 s, autofocus Run Once
  `Duration+10 s` (60 s if 0), camera autofocus `Duration+20 s`, refocus 60 s, Z start position 30 s, ±1 µm Z tolerance.
- `Start` is silently ignored when the function is not `Off`; a rewrite should return an explicit "busy" error (9005).
- `Run Once` reads results by control name strings (`Autofocus Report`, `error out`); renaming a control breaks it
  silently. Typed return values remove this risk.
- Single-instance functions: the HL workers and the UI share the same LL instances; concurrent use is only prevented by
  the condition manager. Decide whether the rewrite allows parallel LL runs (e.g. live get_image during measurements).
- Hard-coded numbers in diagrams: polynomial orders 4/5/6, min 10 points for the oxidation-rate fit, 5 measure retries,
  3 move retries, 3 refocus errors before re-autofocus, brightness window and 5 retries in auto exposure, 2 × exposure
  settle time, ≤50/100 matches, score ×10 scaling, wafer diameter 150 in the bowing simulation.
- Units: profile in µm, stages in mm, conversions via the camera calibration (error 9080 if missing); camera flips from
  properties must be applied to every pixel→mm conversion; coordinate systems (XY stages / wafer / chuck) via
  `alox_manager_xy_coordinate_system`; mesa start position is tilt-corrected.
- Errors: IMAQ −1074395318 must be tolerated in measure_mesa; 9003/9004 tolerated in the rate fit; refocus fails when the
  peak is on the first/last two Z points.
- Model images are read from the Windows user Documents folder (per user account), not from the profile folder.
- The external `D:/AET Group/...` subVI and `System Exec.vi` call must be recovered or re-specified.
- Camera naming inversion (High Mag. = low angle) also exists in REST route ↔ VI names; keep one convention.

### 10. Open questions

1. Is the Python/Hough (or the OpenCV DLL) circle detection meant to replace NI Vision in production, or is it abandoned?
2. Which program does `System Exec.vi` launch in the measure-mesa worker?
3. How do the bowing and tilt wrappers choose their simulation workers (only mesa reads `simulation.mesa.enabled`)?
4. Is `alox_function_ll_refocus_oxyd` still needed (no HL use), and should HL mesa use it as the changelog states?
5. When does HL mesa stop in process-chamber mode: only on supervisor Stop/Abort, or also when Oxyd Target is reached?
6. What is the exact search pattern of Search Object (spiral, raster) built from Max Moves and Step?
7. Which HL bowing positions are skipped by "Fast method"?
8. Is the bowing simulation worker known to be broken in V1.2.6.2?

---

## Part 4: Shared libraries (vision, geometry, tools, datetime)

Scope: 139 VIs (the group file holds 141 records; `get_system_time.vi` and `alox_tools_convert_to_imaq_units.vi` appear
twice) from `sources/vision` (35 `alox_vision_*`), `sources/geometry` (17, incl. `geometry model/`), `sources/tools`
(84 `tools_*`, `tool_alox_*`, `alox_tools_convert_to_imaq_units`) and `sources/datetime` (3).

Evidence tags: `<vi> d` / `dN` = seen in that block-diagram image; **(inferred)** = deduced from name, calls, callers or
NI documentation; "(from name/calls)" = role taken from the name and the call graph only. Camera-server VIs that are
already specified in `../` are only summarised here, with a pointer.

---

### 1. Purpose

These folders hold the reusable building blocks of every ALOX program: image-processing primitives on top of NI Vision
(flip, morphology pre-processing, masks, ROIs, sharpness metrics, straight-edge tilt), non-destructive overlays drawn on the
displayed images (alarm boxes, scale bar, hardware read-out, pattern-matching results), plane-geometry routines used to turn
detected contours into diameters (equivalent, inscribed, circumscribed, minimum-zone circles, RANSAC line fit), numeric
post-processing of the measurements (NaN/outlier filter, averaging, bowing, tilt, XY recentring), the wavelength ↔ exposure
lookup-table helpers, and the file-system layer (data/profile/job-history folders from `alox_setup.ini`, file names,
CSV/binary/PNG writers). Nothing here runs on its own: every VI is a synchronous subVI called by the functions (part 3),
the core/managers (part 1), the hardware servers (part 2) or the REST API (part 5).

### 2. Architecture

- **No processes, loops or queues of their own.** All VIs are stateless, reentrancy unknown, called inline. The only
  internal state is the uninitialised shift register holding the CRC-32 table in `tools_file_crc32.vi` (filled on first
  call, `tools_file_crc32 d`).
- **Error-cluster style.** Almost every VI wraps its body in a `No Error`/`Error` case on `error in`; in the `Error` case the
  outputs are defaults (empty path, NaN circle, unchanged image). Some helpers have no error case (`tools_best_scale_width d`,
  `geometry_circle_center_from_3_points d`, `tool_alox_strength_analysis d`).
- **Not a leaf layer.** Several "tools" call managers of part 1 (functional-global/queue managers) and even function VIs of
  part 3, so the dependency graph is cyclic at folder level:

```
 part 3 functions ──► vision/* , geometry/* , tools/*  (pure: geometry, NaN filter, ROI, focus, flip, overlays…)
                          │
                          ├─► alox_manager_calibration   (overlay_scale, wavelength_ROI_selection: pixel size/units)
                          ├─► alox_manager_properties    (overlay_size_green, update_system_status_*: alox.ini keys)
                          ├─► alox_manager_camera / _xy / _monochromator / _xy_coordinate_system
                          │        (overlay_hardware_infos, joystick, tracking_move_relative)
                          ├─► alox_manager_log, alox_error, alox_core_global_variables
                          └─► alox_function_ll_get_image / _detect_circle / _measure_mesa (+ manager_profile/display/image)
                                   (tools_mesa_averaged_measurement, tools_alox_select_highmag_for_hmi,
                                    tools_alox_stop_all_functions)
```

- **Configuration access** is direct and uncached: each folder resolver opens and parses
  `<exe>\alox_setup.ini` at every call (`tools_alox_shared_*_folder d`, see also `09-configuration-logging-errors.md` §3).
- **Concurrency**: none inside; the callers run in parallel (function state machines, camera server loops), so the only
  shared resources are files and folders (same file names to the second, see §8).

### 3. Components

Abbreviations in "used by": apps `Alox`, `Sim` (AloxSim), `Cam` (AloxCamServer), `WL`, `XY`, `WD` (AloxWatchdog),
`Demo`/`DongleGen` (dongle builds), `—` = no build; callers `fn_` = `alox_function_`, `.vi` dropped, `(+n)` = more callers.

#### 3.1 Focus (sharpness) metrics

| VI | role | used by |
|---|---|---|
| `alox_vision_measure_focus_fft.vi` | Sum of the magnitudes of the 2-D DFT of an optionally cropped, nearest-neighbour-decimated ROI (DC included). Fully specified in `07-focus-metrics.md` §3–5. | Cam · camera_server_autofocus, camera_server_refocus, scan_wl_function_state_diagram (R&D) |
| `alox_vision_measure_focus_sobel.vi` | Mean grey level of an `IMAQ EdgeDetection` image (Sobel, Prewitt, …; FULL = product of six). `07-focus-metrics.md` §6. | Cam · camera_server_autofocus, camera_server_refocus |
| `alox_vision_measure_focus_fft_oxyd.vi` | Variant for an arbitrary ROI: optional resample (Zero Order) + `alox_vision_scale_roi`, pixels inside the ROI flattened to a 1-D U8 array, **1-D** FFT, score = Σ abs(X[k]) for k from `N·0.5+1` ("Skip border") to the end (`… fft_oxyd d`). | — · alox_test_fft_1d_refocus_oxyd (test) |
| `alox_vision_measure_focus_sobel_oxyd.vi` | Same front end, `IMAQ ROIToMask 2` + `EdgeDetection` + `Histogram` mean inside the mask (from name/calls). | — · alox_test_fft_1d_refocus_oxyd |
| `alox_vision_roi_to_u8_array1d_edvr.vi` | ROI → mask → pixels of the ROI as 1-D U8 array via EDVR (fast, no copy) (from calls). | — · measure_focus_fft_oxyd |
| `alox_vision_roi_to_u8_array1d.vi` | Same with `IMAQ ImageToArray` (older, slower) (from calls). | — · no caller |
| `alox_vision_scale_roi.vi` | Rescales every ROI coordinate about the image centre: `x' = (x − W/2)·s + W·s/2` (even indices = X, odd = Y), I32 (`… scale_roi d`). | — · the two `_oxyd` metrics |

Used by: only the camera server (part 2) and tests/R&D; the main application never computes a focus score itself.
NI → Python: `IMAQ Extract 2`/`IMAQ Resample (Zero Order)` → slicing + `cv2.resize(…, INTER_NEAREST)`; `IMAQ FFT` +
`ComplexPlaneToArray(Magnitude)` → `np.abs(np.fft.fft2(img)).sum()`; `FFT.vi`/`Real FFT.vi` + `1D Rectangular to Polar
PtByPt` → `np.abs(np.fft.fft(v))`; `IMAQ EdgeDetection` → `cv2.Sobel`/`cv2.filter2D` with Prewitt/Roberts kernels (exact NI
definitions and border handling: `07-focus-metrics.md` §6.3–6.5); `IMAQ Histogram` mean → `np.mean`.

#### 3.2 Image transforms, pre-processing and masks

| VI | role | used by |
|---|---|---|
| `alox_vision_flip_image.vi` | Software flip with `IMAQ Symmetry` (H → "Horizontal" = top↔bottom, V → "Vertical", both → "Central"). `05-camera-acquisition.md` §7.2. | Cam · camera_server, _autofocus, _get_image_state_machine, _refocus (+ test) |
| `alox_vision_detect_object_preprocessing.vi` | `#Open`, `#Close` rounded up to odd `k`; for each `k ≥ 3`: `IMAQ ImageBorderSize`, then `IMAQ GrayMorphology` Open then Close with a k×k structuring element of ones (`… detect_object_preprocessing d`). | Alox/Sim · fn_hl_tilt_measurement_state_machine/_ui_config, fn_ll_detect_object_state_machine/_ui_config, fn_ll_detect_circle_ui_config |
| `alox_vision_detectcircle_preprocessing_conf.vi` | Same pattern, four stages in order POpen (`#Open`), PClose (`#Close`), Erode (`erode`), Dilate (`dilate`) (`… detectcircle_preprocessing_conf d`). | Alox · fn_ll_detect_circle_ui_config |
| `alox_vision_mask_matched_object.vi` | If at least one match: polygon of the first match's `Bounding Box` → `IMAQ ROIToMask 2` (value 255, temp image `TMP_MASK_OBJ`) → `IMAQ Mask` (pixels outside → 0) (`… mask_matched_object d`). | Alox/Sim · fn_hl_tilt_measurement_state_machine |
| `alox_vision_detect_object_learn_mask_to_bitmap.vi` | Learn-mask image → boolean/U8 bitmap via `IMAQ ImageToArray` (from calls). | Alox · tilt/detect-circle/detect-object UIs (4) |
| `alox_vision_detect_object_learn_mask_to_bitmap_2.vi` | Same via `IMAQ ImageToEDVR` (newer, faster) (from calls). | Alox · tilt/detect-circle/detect-object UIs (5) |

Used by: object detection, tilt and detect-circle functions (part 3); flip only in the camera server.
NI → Python: `IMAQ Symmetry` → `img[::-1]`, `img[:, ::-1]`, `img[::-1, ::-1]`; `IMAQ GrayMorphology` Open/Close/Erode/Dilate →
`cv2.morphologyEx(img, cv2.MORPH_OPEN, np.ones((k,k), np.uint8))` etc.; NI `POpen`/`PClose` (proper opening/closing) have no
OpenCV primitive: `POpen = min(I, open(close(open(I))))`, `PClose = max(I, close(open(close(I))))` **(inferred, NI Vision
Concepts)**; `ImageBorderSize` → `cv2.copyMakeBorder` / `borderType` choice; `ROIToMask` + `Mask` → `cv2.fillPoly` +
`cv2.bitwise_and`; `ImageToArray`/`ImageToEDVR` → the NumPy array itself.

#### 3.3 ROI builders and converters

| VI | role | used by |
|---|---|---|
| `alox_vision_circle_to_roi.vi` | `Circle` {Center X,Y SGL; Radius DBL} → External Oval ROI, bounding box `[x−r, y−r, x+r, y+r]` as I32 (`… circle_to_roi d`). | Alox · fn_ll_auto_exposure_state_machine/_ui_config |
| `alox_vision_oxyd_roi.vi` | Oxide refocus ROI: if `Circle Center` = (0,0) (no circle found) → default oval centred in the image, bounding box `[W/2−d/2, H/2−d/2, W/2+d/2, H/2+d/2]` with `d = Diameter (Pix)` (description: 256 px); else the given circle (`… oxyd_roi d`). | Alox · fn_ll_refocus_oxyd_state_machine |
| `alox_vision_refocus_roi.vi` | Rectangle of `Size (Pixels)` centred in the image, rotation 0 → `IMAQ Convert Rectangle to ROI` (`… refocus_roi d`). | Alox · fn_ll_refocus_state_machine |
| `alox_vision_check_roi.vi` | `Valid?` = global rectangle has 4 values with right>left and bottom>top AND contour type = `ROI Type` (or "No Selection") AND ≥1 coordinate (`… check_roi d`). | Alox · tilt/detect-circle/detect-object `ui_config` |
| `alox_vision_get_points_bounding_rect.vi` | Min/max of a point array → `[L, T, R, B]` I32 (`… get_points_bounding_rect d`). | Alox · same three UIs |
| `tool_alox_measLine_ROI_builder.vi` | Rotated-rectangle ROI descriptor from 4 coordinates + `Angle (Pixel)·1000` (NI stores the angle in thousandths of a degree) (`… ROI_builder d`). | Alox · fn_ll_measure_mesa_state_machine, measStraightLine_generated_edgedetector_rotarect |
| `tool_alox_wavelength_ROI_selection.vi` | Search-wavelength ROIs. "Spot-Annular" mode: pixel size from `alox_manager_calibration` (Get Calib, High Mag; 1 if units = pixels); `r = SpotSize/2/px`, `r1 = Diam1/2/px`, `r2 = Diam2/2/px`; "Non Oxyd" ROI = circle r at the measured circle centre, "Oxyd" ROI = annulus [min(r1,r2), max(r1,r2)], angles 0–359; next state `Measure`. Checks that the input circle is not empty in automatic SCAN mode (`… wavelength_ROI_selection d`). "Dual Spots" frame not opened. | Alox · fn_ll_search_wavelength_state_machine (+ _old) |
| `alox_tools_convert_to_imaq_units.vi` | `alox_typedef_calibration_units` (0 pixels, 1 µm, 2 mm) → IMAQ unit (frame "mm" → Millimeter seen) (`… convert_to_imaq_units d`). | Alox · REST camera status (2), fn_ll_detect_circle_ui_config (+_py), fn_ll_get_image_state_machine |
| `tools_alox_crop_size_definition.vi` | Zoom crop: if `Zoom ≠ 1`: `reduc = w − w/(2−Zoom)` (same for h); output `[top=reducY, left=reducX, width=w−reducX, height=h−reducY]`, each coerced to [0, 2048] / [0, 2448] (`… crop_size_definition d`). | Alox · fn_ll_get_image_state_machine |

Used by: refocus, auto-exposure, search-wavelength, measure-mesa, get-image and the configuration UIs (part 3); REST camera status.
NI → Python: ROI descriptors → plain dataclasses (rectangle, rotated rectangle, oval, annulus, polygon) rendered to masks with
`cv2.rectangle`/`cv2.ellipse`/`cv2.fillPoly`; `IMAQ Convert Annulus to ROI` → two concentric `cv2.circle` (filled) XOR.

#### 3.4 Measurements on images (edges, tilt, pattern matching, circles)

| VI | role | used by |
|---|---|---|
| `alox_vision_measure_tilt_straight_edge.vi` | Straight-edge tilt: ROI = whole image, `IMAQ Find Straight Edges 3` (Edge Options, Search Direction, Straight Edge Options {Orientation 0, Angle Range 360, Angle Tolerance 1}); overlays in group `HL_TILT`: search lines (blue), all edges (orange), found line (red), used edges (yellow); checks EDGE FOUND?, MIN POINTS OK? (`used edges / search lines·100 ≥ Min Points (%)`), ANGLE RANGE OK?; text "TILT ANGLE: %.1f°" or "TILT NOT FOUND/INVALID"; report `alox_typedef_tilt_measurement_straight_edge_report` + process time (ms) (`… measure_tilt_straight_edge d`). | Alox/Sim · fn_hl_tilt_measurement_state_machine, _ui_config |
| `alox_vision_bundle_setup_geometric_matching_options.vi` | Builds `IMAQ Match Range Setting[]`: Rotation Angle [0, 360]°, Occlusion [0, `Obj1 Max Occl (%)`] (`… bundle_setup_geometric_matching_options d`). | Alox/Sim · fn_hl_tilt_measurement_state_machine |
| `tools_tilt_function_object_detection_status.vi` | Angle of the line Match1→Match2 (wafer mm) against the X axis with `IMAQ Get Angles`, folded modulo 180 to (−90, 90]; `Tilt Valid` = angle in [Expected − Tol, Expected + Tol]; message "TILT FOUND: %.1f°" / "TILT NOT FOUND/INVALID" (`… object_detection_status d`). | Alox/Sim · fn_hl_tilt_measurement_state_machine |
| `tool_alox_measLine_distance_computation.vi` | Distance first-edge ↔ last-edge for each search line (`IMAQ Point Distances`, pixel locations), lines as `alox_typedef_line` I32; final `mean` = mean of the distances inside `mean ± 1σ` (`… distance_computation d`). | Alox · fn_ll_measure_mesa_state_machine, measStraightLine_… |
| `tool_alox_strength_analysis.vi` | Edge-strength profile along a radius: `abs(s)/max(abs(s))·100`, positions `OxydMinDiam/2 + i·PixSize` (µm); counts samples above `Control Value` → `size 50%` (%), `PeakWidth (µm)`, XY graph (`… strength_analysis d`). | Alox · fn_ll_measure_mesa_state_machine |
| `tool_alox_detectCircle_lessFar_and_found.vi` | `found AND distance(Best.Center, Old.Center) < Max Radius` (`… lessFar_and_found d`). | Alox · fn_ll_search_wavelength_state_machine (+ _old) |
| `tool_alox_FIR_wl_centering.vi` | Averages circle centres: first 10, then (if >10) rejects centres outside mean ± X·σ (→ NaN) and returns the mean of the rest (`… FIR_wl_centering d`). | — · no caller |

Used by: tilt measurement (hl), measure mesa (ll), search wavelength (part 3).
NI → Python: `IMAQ Find Straight Edges 3` has no direct equivalent: rake of parallel search lines, 1-D edge detection along
each (gradient + threshold, polarity), then a robust line fit (`cv2.fitLine` with `DIST_HUBER`, or `skimage.measure.ransac`);
`IMAQ Get Angles` → `np.degrees(np.arctan2(dy, dx))`; `IMAQ Point Distances` → `np.hypot`; geometric matching options belong to
the matcher of part 3 (no OpenCV equivalent; shape-based matching or `cv2.matchTemplate` + `cv2.minAreaRect`).

#### 3.5 Overlays

| VI | role | used by |
|---|---|---|
| `alox_vision_overlay_alarm.vi` | Coloured alarm box (fill green if `State` else red, black frame) with centred text (Top alignment if the string has a line feed, else Baseline); box from `overlay_size_green` (`… overlay_alarm d`). | Alox/Sim · bowing, tilt (sm, ui_config), detect_object_ui_config, search_wavelength_overlays, measure_tilt_straight_edge |
| `alox_vision_overlay_size_green.vi` | Box geometry from `alox.ini` `[overlays]` `alarmleft/alarmtop/alarmwidth/alarmheight` (defaults 1600/50/700/300 in `alox_default.ini`), scaled by a factor that depends on the get-image `ZOOM` (1 or 0.5 as read), rounded to I32 (`… size_green d`). | Alox/Sim · the three result overlays |
| `alox_vision_adjust_boolean_indicator_text_alignment.vi` | Text origin centred in a rectangle (description). | Alox/Sim · the three result overlays |
| `alox_vision_overlay_oxyd_measurement.vi` | Mesa result box: "Avg Oxid Diam.: %.2f µm\nAvg Ext Diam.: %.2f µm"; builds a log string ("Measure keeped: %d %%", mean/STDev/max/min of filtered and raw values, median) returned as `log value overlay`; box `Left = X−50, Right = X+W+50, CenterY = Y+H/2+7` (`… overlay_oxyd_measurement d`). | Alox/Sim · fn_hl_mesa_measurement_state_machine, _ui_config |
| `alox_vision_overlay_auto_exposure_measurement.vi` | Same kind of box for `alox_typedef_auto_exposure_report` (from calls). | Alox · fn_ll_auto_exposure_state_machine |
| `alox_vision_overlay_hardware_infos.vi` | Five text lines at x = 50 from `y0 = H − 6·line space`: "X (mm): %.3f", "Y (mm): %.3f" (XY stage position converted to the wafer coordinate system), "Z (mm)", "Expo (µs)", "WL (nm): %d"; "-" when a read fails (`… hardware_infos d`). | Alox · fn_ll_get_image_state_machine |
| `alox_vision_overlay_scale.vi` | Scale bar: pixel size/units from `alox_manager_calibration`; `tools_best_scale_width`; text "%d %s" centred above a double-arrow line at `y = 0.98·H`, near the right edge (`… overlay_scale d`). | Alox · fn_ll_get_image_state_machine |
| `tools_best_scale_width.vi` | `um = px·approx_width`; `nearest_um = ceil(um/multiple)·multiple`; `nearest_pix = nearest_um·approx_width/um` (`tools_best_scale_width d`). | Alox · overlay_scale |
| `alox_vision_overlay_line_with_arrows_simple.vi` | Line with arrow heads (`Overlay Line` + `Overlay Multiple Lines 2`) (from calls). | Alox · alox_manager_xy_map, overlay_scale |
| `alox_vision_overlay_thick_circle.vi` | Thick circle: thickness rounded up to odd, draws `thickness` concentric `Overlay Oval` frames offset ±1, ±2, … px (`… thick_circle d`). | Alox · detect_circle sm (+ _old, _proto), refocus_oxyd sm, tracking sm, test |
| `alox_vision_overlay_thick_rectangle.vi` | Same with `Overlay Rectangle` (from calls). | Alox · fn_ll_refocus_state_machine |
| `alox_vision_overlay_geometric_pattern_matching_results.vi` | For the first match: landmark + text "POS.X: %d, Y:%d / ANGLE: %.1f (°) / SCALE: %.1f (%) / SCORE: %.1f (%) / OCCLUSION: %.1f (%)" at the bounding box + (100,100); NI score (0–1000) divided by 10 (`… gpm_results d`). | Alox/Sim · tilt (sm, ui_config), detect_object (sm, ui_config) |
| `alox_vision_overlay_landmark.vi` | Cross (±10/±20 px lines), small oval `[-5,-5,6,6]` at the position, bounding-box polygon (`… overlay_landmark d`). | Alox/Sim · gpm_results |
| `alox_vision_overlay_geometric_pattern_matching_contours.vi` | Draws matched contour curves with `Overlay Multiple Lines 2` (from calls). | Alox/Sim · tilt, detect_circle (+ _proto), detect_object |
| `alox_vision_overlay_points_with_user_specified_size.vi` | Dots of a given radius (`Overlay Oval`) (description). | Alox/Sim · measure_tilt_straight_edge |
| `alox_vision_overlay_angle.vi` | Angle drawing (`Overlay Arc` + `Overlay Line`) (from calls). | — · no caller |

Used by: all measurement functions and the get-image display path (part 3), XY map `alox_manager_xy_map` (part 2). Colours are U32 RGB; overlays
carry a `Group` name so a caller can clear its own layer.
NI → Python: NI overlays are non-destructive vector layers stored with the image (and saved in the PNG by `IMAQ Write Image
And Vision Info File 2`). Keep an overlay list per image (shape, colour, group) and render with `cv2.line/circle/
rectangle/putText` or in the GUI; `alox_get_font_properties` fonts map to a GUI font, not to `cv2.putText`.

#### 3.6 Geometry: lines

| VI | role | used by |
|---|---|---|
| `geometry_fit_line_ransac2.vi` | "RANSAC" line fit (see algorithm below) → `Line Equation` (a,b,c), `Residual`, `Score`, `Valid Fit`. | Alox/Sim · alox_tools_fit_diameters_measures (part 1), geometry_fit_line_ransac |
| `geometry_fit_line_ransac.vi` | Thin wrapper around `ransac2` (from calls). | — · no caller |
| `geometry_least_square_distance_points_line.vi` | For a line `ax+by+c=0`: `d_i = abs(a x_i + b y_i + c) / sqrt(a²+b²)`, `Sum distance = Σ d_i` (all points), `Inliers` = points with `d_i ≤ Max Distance`, `Score (%) = #inliers/#points·100` (`… least_square_distance_points_line d`). | Alox/Sim · ransac2 |
| `geometry_line_equation_from_two_points.vi` | `a = y1−y2, b = x2−x1, c = x1·y2 − x2·y1`, normalised by `1/sqrt(a²+b²)`; if `x1 = x2` **OR** `y1 = y2` → error −1 "Same Points", line `[1,0,0]` (`… two_points d, d2, d3`). | Alox/Sim · ransac2 |
| `geometry_simple_line_equation_from_two_points.vi` | Slope/intercept: `a = abs(y2−y1) / abs(x2−x1)`, `b = y − a·x` (`… simple_line d`). | Alox · fn_ll_auto_exposure_state_machine/_ui_config/_ui_run |
| `geometry_line_equation_from_points.vi` | Line from a point set (from name). | — · no caller |
| `tools_linear_extrapolation.vi` | `y = y1 + (x−x1)/(x2−x1)·(y2−y1)` (`tools_linear_extrapolation d`). | — · no caller |

**`geometry_fit_line_ransac2` algorithm (`… ransac2 d`).** `Fit Options` = {I64, DBL, DBL}, read as {iterations, max distance,
minimum score} **(field names inferred)**.
1. `iterations` times: two indices `round(random()·N)` (I32 conversion = round to nearest; no `−1` visible, so index `N`
   is possible and yields the default point (0,0)); couples with equal indices are dropped.
2. For each couple: line through the two points; if no error, `Σ d_i` over **all** points; keep the couple with the
   smallest sum ("Keep couple of points which minimizes distance").
3. Recompute the line of the best couple (frame `Enable`), `Residual` = its sum, `Score` = inlier percentage;
   `Valid Fit = Score ≥ third option AND #inliers ≥ 2` (as read).
This is a least-absolute-deviation search over candidate pairs, not a consensus (inlier-count) RANSAC. Because
`line_equation_from_two_points` rejects any pair with equal X **or** equal Y, horizontal and vertical candidate lines can never
be chosen (frequent with integer pixel coordinates or repeated values).
Python: `skimage.measure.ransac(…, LineModelND)` or `sklearn.linear_model.RANSACRegressor` for the intended behaviour; to
reproduce exactly, implement the loop above with a seeded RNG.

#### 3.7 Geometry: circles and roundness (mesa diameters)

| VI | role | used by |
|---|---|---|
| `geometry_circumscribed_circle.vi` | Minimum enclosing circle, Elzinga–Hearn ("Uses the Elzinga Hearn Smallest Circle Algorithm"), at most 20 000 iterations, else `{NaN,NaN,NaN}` (`… circumscribed_circle d`). | Alox · fn_ll_measure_mesa_state_machine |
| `geometry_check_points_in_circle.vi` | All points inside the candidate circle? (from name). | Alox · circumscribed_circle |
| `geometry_check_triangle_obtuse.vi` | Obtuse-triangle test with `IMAQ Get Angles` (from calls). | Alox · circumscribed_circle |
| `geometry_circle_center_from_3_points.vi` | Circumcentre (formula node): `D = 2(x1(y2−y3)+x2(y3−y1)+x3(y1−y2))`, `xc = ((x1²+y1²)(y2−y3)+(x2²+y2²)(y3−y1)+(x3²+y3²)(y1−y2))/D`, `yc = ((x1²+y1²)(x3−x2)+(x2²+y2²)(x1−x3)+(x3²+y3²)(x2−x1))/D`; radius (SGL) = distance centre–point 1 when `Return Radius`; no `D = 0` guard (`… center_from_3_points d`). | Alox · circumscribed, inscribed |
| `geometry_inscribed_circle.vi` | Largest empty circle: Delaunay triangulation of the points, circumcircle of each triangle, keep the largest whose centre is inside the convex hull (`Point in Polygon`, ε = 1e-8); if none and `Allow Center Outside Convex Hull`, the largest outside; else NaN (`… inscribed_circle d`). | Alox · fn_ll_measure_mesa_state_machine |
| `geometry_circle_diameter_equivalent_surface.vi` | `IMAQ Polygon Area` A → `d = 2·sqrt(A/π)`; also outputs A (`… equivalent_surface d`). | Alox · fn_ll_measure_mesa_state_machine |
| `geometry_circle_diameter_equivalent_perimeter.vi` | Closed-polygon perimeter `P = Σ dist(p_i, p_{i+1})` (wrap-around). As read the VI returns `P`; no `/π` is visible inside (`… equivalent_perimeter d`). | Alox · fn_ll_measure_mesa_state_machine |
| `RoundnessCalculations-NI.vi` | NI example adapted: LSC (least-squares circle, `Fitting on a Sphere.vi`), feasible radii `0.99·min r_i`, `1.01·max r_i` about the LSC centre as starting values, MZC (minimum-zone) by `Constrained Nonlinear Optimization.vi` with objective `MZC cost cno.vi` → outputs `Cir` (outer) and `Ins` (inner) circles {X, Y, Radius}, `MZC minimum`, `LSC minimum`, graphs; MIC and MCC optimisations sit in a `Disabled` frame (`RoundnessCalculations-NI d`). | Alox · fn_ll_measure_mesa_state_machine |
| `MZC cost cno.vi` | Objective for MZC (NI CNO template). Reached through the strictly-typed VI reference "MZC model" in `RoundnessCalculations-NI d`, hence no static caller **(inferred)**. | (Alox, dynamic) |
| `MCC cost cno.vi`, `MIC cost cno.vi` | Objectives for minimum circumscribed / maximum inscribed circles; referenced only inside the `Disabled` frame. | — |

Used by: the measure-mesa state machine (part 3), which assembles `alox_typedef_measure_mesa_diameters_report`
{External, Fit, Median, Mean, Surface, Inscribed, Circumscribed, Circ/Insc Ratio, STDDEV, Accuracy}.
NI → Python: Elzinga–Hearn → `cv2.minEnclosingCircle` (Welzl; same unique circle); `Delaunay Triangulation.vi`,
`Convex Hull.vi`, `Point in Polygon` → `scipy.spatial.Delaunay`, `scipy.spatial.ConvexHull`, `matplotlib.path.Path.
contains_points`; `IMAQ Polygon Area` → shoelace / `cv2.contourArea`; `Fitting on a Sphere` (2-D) → algebraic (Kåsa) or
geometric least-squares circle (`scipy.optimize.least_squares`); `Constrained Nonlinear Optimization` → `scipy.optimize.
minimize(method="SLSQP")` minimising `max r_i − min r_i` over the centre (or an LP formulation).

#### 3.8 Measurement post-processing (statistics, bowing, tilt, tracking, autofocus curves)

| VI | role | used by |
|---|---|---|
| `tools_mesa_averaged_measurement.vi` | Averages N oxide/mesa measurements and returns µm statistics (see below). | Alox/Sim · fn_hl_mesa_measurement_state_machine |
| `tools_alox_NaN_filter.vi` | Three-stage NaN/outlier filter (see below). | Alox/Sim · mesa_averaged_measurement |
| `tools_alox_bound_measured_values.vi` | Converts the profile limits `Mesa Min/Max Diam (µm)`, `Oxyd Min/Max Diam (µm)` to pixels (÷ PixelSize) and bounds the diameter report: values outside the limits replaced (by 0, **inferred**) (`… bound_measured_values d`). | Alox · fn_ll_measure_mesa_state_machine |
| `tools_replace_zero_by_nan.vi` | 0 → NaN in an array (from name). | Alox · fn_ll_measure_mesa_state_machine |
| `tools_filter_invalid_chart_measures.vi` | Drops points whose value is NaN (`Filter NaN`) or 0 (`Filter Null`), keeping timestamps aligned (`… invalid_chart_measures d`). | Alox/Sim · alox_tools_fit_diameters_measures |
| `tools_filter_oldest_chart_measures.vi` | Keeps the last `# Samples` points of plot 0 (oxide data) and the matching part of plot 1 (fitted data) (`… oldest_chart_measures d`). | Alox · fn_hl_mesa_measurement_ui_run |
| `tools_alox_bowing_computation.vi` | Wafer bowing and chuck parallelism (formulas below). | Alox/Sim · fn_hl_bowing_measurement_state_machine |
| `tools_alox_get_tilted_coordinates.vi` | Rotation about the wafer centre: `x' = x cosθ − y sinθ`, `y' = x sinθ + y cosθ`, θ in degrees (`… get_tilted_coordinates d`). | Alox/Sim · fn_hl_mesa_measurement_state_machine |
| `tools_tilt_position.vi` | Same description and size as above; older copy (from description). | — · no caller |
| `tools_tracking_move_relative.vi` | XY move (mm) that brings an object at `Object Position (Pix)` to the image centre (see below). | Alox/Sim · fn_hl_tilt (sm, ui_config, ui_run), fn_ll_tracking_state_machine |
| `tools_process_autofocus_large_data.vi` | `General Polynomial Fit` (order, method, algorithm) of AF log {Z, score}; focus by `alox_typedef_focus_detection_method` (0 Curve Maximum = argmax of raw scores, seen; 1 Fit Maximum, 2 Local Maximum not opened); outputs MAX SCORE, POS FOCUS, RESIDUAL, graphs (`… process_autofocus_large_data d`). | Alox · fn_ll_autofocus_ui_config |
| `tools_check_autofocus_large_data.vi` | `span = Z_last − Z_first`, `tol = span·Tolerance%/100`; OK if `Z_first + tol < Z_found < Z_last − tol` (as read), else ALOX error 9008 "focus position near min/max limit" (`… check_autofocus_large_data d`). | Alox · fn_ll_autofocus_state_machine/_ui_config, scan_wl (R&D) |
| `tools_focus_array_to_xy_graph_cluster.vi`, `tools_refocus_arrays_to_xy_graph_cluster.vi` | Score arrays → XY-graph clusters; `06-autofocus-refocus.md` §7. | Cam · camera_server_autofocus / _refocus |
| `tools_round_u32_to_upper_odd_value.vi` | `n` even → `n+1` (`… upper_odd_value d`). | Alox/Sim · morphology/overlay VIs, tilt/measure-mesa/detect-object UIs |
| `tools_round_u32_to_lower_odd_value.vi` | `n` even → `n−1` **(inferred)**; `0 − 1` on U32 wraps to 4 294 967 295. | Alox · tilt and measure-mesa `ui_config` |

**`tools_mesa_averaged_measurement` (`… d`, split into two crops).** Loop `N = # Averages` (1 if 0). In each iteration:
`alox_function_ll_get_image` (High Mag., `Run Once`); on the first iteration only `alox_function_ll_detect_circle`
(`Force Results`), then `Get Results` → `Best Circle` (initial constant {X 1224, Y 1024, R 30}, i.e. the centre of a 2448×2048
sensor); `alox_function_ll_measure_mesa` (`Run Once`, `Display Image`) → `Get Results` → `alox_tools_convert_mesa_
measures_to_micrometers`; `Mesa Found`/`Oxyd Found` are forced TRUE unless the profile keys `measure.vcsel.MeasMesaMandatory`
/ `measure.oxyd.MeasOxydMandatory` are TRUE; then a wait of `exposure/1000` ms (camera exposure in µs). After the loop each
diameter series (External when mesa mandatory, Fit, Median, Mean, Surface, Inscribed, Circumscribed, Circularity) goes through
`tools_alox_NaN_filter` and is averaged into `alox_typedef_oxyd_averaged_measurement_report`; `Oxyd Diam.` = the series
selected by `Oxyd Measure Type` (0 Mean, 1 Median, 2 Fit, 3 Surface, 4 Circumscribed, 5 Inscribed); `Oxid Filtered` and
`Oxid Row` feed the overlay; `Oxyd found?` / `Mesa found?` from `# Samples > 0` and `External Diameter ≠ 0`.

**`tools_alox_NaN_filter` (`… d, d6, d9, d10, d11`).**
```
x1 = [v for v in x if not isnan(v)]                          # "NaN filter", mean -> "NAN filter mean"; backupVal = mean(x)
if len(x1) > 5:
    m, s, med = mean(x1), std(x1), median(x1)
    x2 = [v for v in x1 if med-0.25 <= v <= med+0.25 or m-2*s <= v <= m+2*s]    # "Fix Filter" (+ mean)
    x3 = x2 if len(x2) > 4 else x2   # a third pass (mean ± 2σ) exists only in the Default frame of a case selected by 0
else: x2 = x3 = x1 (inferred)
return x1, mean(x1), x2, mean(x2), x3 ("Filter Final"), mean(x3)
```
The 0.25 band is absolute, in the unit of the input (µm here, since the caller converts first). NI `Std Deviation and
Variance` defaults to sample weighting (N−1) **(inferred, NI documentation)** → `np.std(ddof=1)`.

**`tools_alox_bowing_computation` (formula node, `… bowing_computation d`).** `Z Measures` is a 2-D array; column 2 of rows
0…8 = ZW2, ZW3, ZC3, ZW1, ZC1, ZW4, ZC4, ZW5, ZC5 (W = wafer, C = chuck); `Dist` = `measure Distance`, `waferDiam` =
`Wafer Diameter (mm)` from `alox_typedef_bowing_measurement_chuck_properties`:
```
BowA  = ((ZW1+ZW3)/2 − ZW2) · waferDiam/(2·Dist) · 1000
BowB  = ((ZW4+ZW5)/2 − ZW2) · waferDiam/(2·Dist) · 1000
ParaA = ((ZC1+DZ1−ZW1) − (ZC3+DZ3−ZW3)) · 1000
ParaB = ((ZC4+DZ4−ZW4) − (ZC5+DZ5−ZW5)) · 1000
bow valid A/B = BowA/B ≤ max Bowing (as read) ; para valid A/B = |ParaA/B| ≤ Max Parallelism
```
Outputs `alox_typedef_bowing_computation_results` twice (BOW RESULTS, PARA RESULTS). `DZ2`, `bowChuckA/B` (BCA/BCB) are wired
into the node but unused in the visible code; a comment keeps an older chuck-corrected formula and a "parabolic" variant
(`wafersize²/(2·dx²)`), neither implemented. A `ChuckCalibration` case selects between two identical outputs.

**`tools_tracking_move_relative` (`… d`).** Pixel size from `IMAQ Get Calibration Info 3` (frame "Micrometer": ÷1000 → mm);
`dx = obj.x − W/2`, `dy = obj.y − H/2` (pixels) → mm; `Min. Move` = `|dx|,|dy| ≥ Min. Distance (mm)`; optional sign inversion
by the globals `X Stage Invert`/`Y Stage Invert` (comment: "FOR DEBUG ONLY (SHOULD BE OFF FOR AET) – OFF: XY moves cameras (AET),
ON: XY moves wafer (OPTO)"); then axis inversion for the wafer coordinate system from `alox_manager_xy_coordinate_system`
(frame "3" seen) and negation for `Flip Options` H/V (`alox_typedef_flip_options`). Outputs `Relative Move (mm)`,
`Relative Move (pixels)`, `Min. Move`.

NI → Python for this family: `Median`, `Mean`, `Std Deviation and Variance` → NumPy; `General Polynomial Fit` →
`numpy.polynomial.polynomial.polyfit` (+ residual = mean squared error, check NI definition); `IMAQ Get Calibration Info` →
own calibration record (pixel size + unit).

#### 3.9 Wavelength / exposure tables

| VI | role | used by |
|---|---|---|
| `tools_alox_interpolate_data_from_wavelength.vi` | Linear interpolation in a (WL[], Data[]) table: invalid if empty or sizes differ; clamped below the first / above the last WL; exact match returns that value; else first `i` with `WL ≤ WL[i]`, `D[i−1] + (WL−WL[i−1])·(D[i]−D[i−1])/(WL[i]−WL[i−1])`; outputs `Interpolated`, `Array Index` (`… interpolate d`). | Alox/Sim · alox_manager_exposure_wl_lut, scan_wl (R&D) |
| `tools_alox_extrapolate_data_from_wavelength.vi` | Despite the name: inserts or replaces the row of a wavelength in the 7-column exposure table `[WL, Expo Theo., Expo Min, Expo Max, BMin, BMax, ExpoAuto]` from `alox_typedef_auto_exposure_data`, at the upper-row index, then sorts the 2-D array (3 inlined sort subVIs) (`… extrapolate d`; comment gives `y = EXPO1 + (x−WL1)/(WL2−WL1)·(EXPO2−EXPO1)`). | Alox/Sim · alox_manager_exposure_wl_lut |
| `tools_alox_autoexpo_WL_selection.vi` | List of wavelengths to auto-expose: `Current Wavelength` mode → the current one; `All Wavelengths` → session wavelengths within [Start WL, End WL], with nearest-table handling when start/end are not in the table; logs "Auto-Exposure WL table: …nm," via `alox_manager_log` (`… autoexpo_WL_selection d`). | Alox · fn_ll_auto_exposure_state_machine |
| `tools_alox_exposure_verif.vi` | Coerces a table row: WL [0, 3000] nm, exposures [33, 1 999 800] µs (AutoExpo lower bound 0 as read), brightness [1, 255] (`… exposure_verif d`). | Alox · alox_wl_auto_exposures_table_ui |

Used by: the exposure/wavelength LUT manager (part 1) and the auto-exposure function (part 3).
Python: `np.interp` reproduces the clamping and interpolation (but not the `Interpolated`/`Array Index` outputs); the table
update is a sorted insert (`bisect`) on a NumPy/pandas table.

#### 3.10 Folders and paths

| VI | role | used by |
|---|---|---|
| `tools_application_executable_folder.vi` | Folder of the .exe (run-time) or `sources` (development). `09-configuration-logging-errors.md` §3.1. | all apps · 32 callers (boot/init cycles, managers, API, all folder resolvers) |
| `tools_alox_application_root_vi_path.vi` | Root for dynamically loaded VIs: run-time `<exe dir>/alox.exe/` (inside the exe), development `<app path>` (`… application_root_vi_path d`, description). | Alox/Sim · alox_api_manager_websocket_server, floating_log_ui, functions_status, splash_ui |
| `tools_windows_user_documents_folder.vi` | `alox_setup.ini [folders] settings`, default `D:\alox\settings`; registry branch disabled. `09-…` §3.2. | all apps · 20 callers (camera server, boot, core_ui, detect functions, profile/properties managers, servers, watchdog) |
| `tools_alox_shared_vision_folder.vi` | `[folders] vision`, default `D:\alox\02-vision`. | Alox/Cam/Sim/WL/XY · Log Server Error, alox_core_boot_cycle, alox_manager_log |
| `tools_alox_shared_data_folder.vi` | `[folders] data`, default `D:\alox\02-Vision\data`. | Alox/Cam/Sim/WD · 12 (Log Server Infos, boot, bowing/mesa/tilt/auto-exposure/refocus functions, properties_ui, watchdog_log, save_strength) |
| `tools_alox_shared_profiles_folder.vi` | `[folders] profiles`, default `D:\alox\02-Vision\profiles` (`c10`). | Alox/Sim · REST configuration/profiles, boot, manager_profile, profiles_ui |
| `tools_alox_shared_exposures_folder.vi` | `[folders] exposures`, default `D:\alox\02-Vision\exposures`. | — · alox_profiles_edition_ui (not in project) |
| `tools_alox_shared_images_folder.vi` | `[folders] data` (default `D:\alox\02-Vision\data`) + `\images` by string concatenation. | — (REST build) · alox_api_v1_get_system_imagesfolder |
| `tools_alox_shared_jobhistory_folder.vi` | `[folders] jobhistory`, default `D:\alox\02-Vision\JobHistory`. | — · no caller |
| `tools_alox_shared_jobhistory_folder_mesa_path.vi` | Job tree for mesa (below). Outputs `images mesa Folder` (`…\Vision\images\oxid` or `…\mapping` when `Zone ID ≠ "0"`, as read), `files Folder`, `files log Folder`, `files mesa Folder`; all created. | Alox/Sim · fn_hl_mesa_measurement_state_machine |
| `tools_alox_shared_jobhistory_folder_FOCUS_path.vi` | Same tree, outputs `…\files`, `…\files\focus`, `…\files\refocus` (`… FOCUS_path d, d4, d5`). | Alox · save_autofocus_data, save_refocus_data |
| `tools_alox_shared_jobhistory_folder_bowing_path.vi`, `tools_alox_shared_jobhistory_folder_tilt_path.vi`, `tools_alox_shared_jobhistory_folder_exposure_path.vi`, `tools_alox_shared_jobhistory_folder_wl_path.vi` | Same pattern for bowing, tilt, auto-exposure and wavelength data (from calls; identical layout to FOCUS). | bowing/tilt sm (Alox/Sim); save_auto_exposure_data, search_wavelength sm, save_wavelength_data (Alox) |
| `tools_alox_shared_jobhistory_folder_complete_path.vi` | All sub-folders at once, returns `alox_typedef_path_images_save` {images oxid, mapping, bowing, tilt} (from calls). | — · no caller |
| `tools_alox_job_filname_filter.vi` | Sanitises up to 4 job strings for use as folder names (two Search-and-Replace on the 4th string, frame "3"; others unchanged) (`… job_filname_filter d`). | Alox/Sim · the 7 job-history path VIs |
| `tools_save_job_wafer_.vi` | `input path` + `\<Process Job ID>\<Wafer ID>` unless Process Job ID is empty (`c12`). | Alox/Sim · fn_hl_bowing_measurement_state_machine |
| `tools_windows_app_data_folder.vi` | Registry `HKLM\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders`, value `Common AppData` (`… app_data_folder d`). | — · no caller |

**Job-history tree (observed in `… mesa_path d` and `… FOCUS_path d4/d5`).** Both VIs read `[folders] jobhistory` (default
`D:\alox\JobHistory`) and `[folders] data` (default `D:\alox\data`). If `Process Job ID` is `""` or `"Pj"` the tree is rooted at
`<data>` (`<data>\files\…`); otherwise at `<jobhistory>\<A>\<B>\Vision\…`, where `A` and `B` are concatenations of the
filtered Process Job ID, Lot ID and Wafer ID with `"_"` (exact composition not legible). Sub-folders `images\oxid|mapping`,
`files`, `files\log|mesa|focus|refocus` are created with OpenG `Create Dir if Non-Existant`.

Used by: every part. NI → Python: `Open/Read Key/Close Config Data` → `configparser` (LabVIEW path syntax `"/D/alox/…"` must be
converted); `Application Directory` → `Path(sys.executable).parent`; OpenG create dir → `os.makedirs(exist_ok=True)`;
registry → `winreg`.

#### 3.11 File names and data writers

| VI | role | used by |
|---|---|---|
| `tools_alox_job_data_filename.vi` | `<ts>_mesa`, or `<PJ>_<Lot>_<Wafer>_<ts>_Zone<z>_mapping` / `…_<ts>_oxidation`; `ts = FormatDateTime(fmt, now)` minus first "," and "." (Timestamp input not wired). `06-autofocus-refocus.md` §8.2. | Alox/Cam/Sim · bowing sm, mesa sm, tools_log_refocus_image |
| `tools_alox_job_data_filename_bowing.vi`, `tools_alox_job_data_filename_tilt.vi` | Same layout with bowing / tilt suffixes (from calls; same diagram size). | Alox/Sim · bowing sm / tilt sm |
| `tools_alox_img_data_filename.vi` | Image name: here `Timestamp` **is** wired; if the job is not empty `"%s_%s_%s_%s_%s_"` of the five SEMI fields (empty → "0", zone prefixed "Z") + ts, then `+ "_image"` (`… img_data_filename d`). | Alox · alox_core_ui |
| `tools_alox_save_img_type.vi` | Writes `Output Filename` in `path_process_lot_wafer_job`: selector constant `"PNG"` → `IMAQ Write Image And Vision Info File 2` (`.png`, overlays + calibration embedded); `"BMP"` frame writes BMP data under a `.jpg` name (dead) (`… save_img_type d, d2, d3`). | Alox/Sim · fn_hl_mesa_measurement_state_machine |
| `tools_log_refocus_image.vi` | Refocus image `<ts>_refocus_NN.png`. `06-autofocus-refocus.md` §8.1. | Cam · camera_server_refocus |
| `tools_save_autofocus_data.vi` | `<FOCUS>\%y%m%d_%H%M%S.foc`, LabVIEW binary write of `[{Z, score}]` (read/write, replace or create) (`c12`). | Alox · fn_ll_autofocus_state_machine |
| `tools_save_refocus_data.vi` | `.focf` (binary, same layout, write-only) and `.focfraw` (delimited text of the raw 2-D array) (`… save_refocus_data d`). | Alox · fn_ll_refocus_state_machine, _refocus_oxyd_ |
| `tools_save_wavelength_data.vi` | `.wal`: header `WL NOXYD OXYD CONTRAST`, one row per wavelength from the 11-plot spectrum graph (plots 0 NOX, 1 OX, 2 CONT.), delimited text, to `file wavelength` or the WL job folder (`c11`). | Alox · fn_ll_search_wavelength_state_machine (+ _old) |
| `tools_save_auto_exposure_data.vi` | `.ae`: the `alox_typedef_auto_exposure_report` values as a delimited 2-D DBL array (`c11`). | Alox · fn_ll_auto_exposure_state_machine |
| `tools_save_strength_oxid_data.vi` | `<data>\files\mesa\<ts>.strength`, `"%.4f"`, array of {x, y} (`c12`). | Alox · fn_ll_measure_mesa_ui_config |

Common: `ts = %y%m%d_%H%M%S` local time, no milliseconds; the frames comment "<ALOXDATA>/data/coreX/process/" is
obsolete (the `general.core` property branch is in a `Disabled` frame).
NI → Python: `Write Delimited Spreadsheet` → `csv.writer(delimiter="\t")`; its default format is `%.3f` when unwired
**(inferred, NI default)**, so `.focfraw`, `.wal`, `.ae` keep 3 decimals; `Write to Binary File` → `struct.pack(">i" +
"dd"*n, …)` (LabVIEW default big-endian with an I32 element count prefix, **inferred**); `IMAQ Write File 2` → `cv2.imwrite`;
`IMAQ Write Image And Vision Info File 2` → PNG plus NI private chunks (overlays, calibration) that Pillow can read but not
interpret.

#### 3.12 File, string and system helpers

| VI | role | used by |
|---|---|---|
| `tools_file_crc32.vi` | Standard CRC-32 (reflected polynomial 0xEDB88320, table built on first call, final XOR 0xFFFFFFFF) of the whole file read as text; `CRCBuffer` = initial value (`c5`). | Alox · tools_match_files |
| `tools_match_files.vi` | Compares files by CRC-32 (from calls). | Alox · alox_core_ui |
| `tools_clear_folder.vi` | Deletes all files of a folder (OpenG `List Directory Recursive`) (description). | Alox/Sim · alox_manager_profile |
| `tools_zip_profile_folder.vi` | Zips a profile folder: all files, but among `.ini` files only `<Profile Name>.ini` ("Ignore other profiles .ini") (`c6`). | Alox/Sim · alox_manager_profile |
| `tools_get_exe_version.vi` | .NET `System.Diagnostics.FileVersionInfo.GetVersionInfo` of `Exe Path` (default: running application) → FileVersion, Major/Minor/Build/Private, ProductName, … (`c13`). | Alox · about_ui, REST version, boot/init cycles |
| `tools_create_guid.vi` | `ole32.dll CoCreateGuid` → 16 bytes → hex string (`c13`). | Alox/Sim · Generate Unique Notifier Name |
| `tools_convert_to_base64.vi` | Hand-written Base64 encoder (standard alphabet, `=` padding) (`c13`). | Alox/Sim/Demo/DongleGen · change_password_ui, dongle_generation (+_v2), manager_properties, websocket_images_stream |
| `tools_convert_from_base64.vi` | Base64 decoder (description). | Alox/Demo/DongleGen · alox_manager_users, serial-key unlocking |
| `tools_convert_from_base64_and_store.vi` | Decode and write to a file (from name). | — · no caller |
| `tools_color_to_string.vi`, `tools_string_to_color.vi` | U32 colour ↔ text via `Color to RGB` / `RGB to Color` (string format not opened). | Alox/Sim · manager_properties, properties_ui, alox_get_font_properties, test |
| `tools_parse_xml_from_argument.vi` | From name. | — · no caller |
| `tools_abort_all_running_vis.vi` | Aborts running VIs through VI Server (from name/calls). | — · no caller |

Python: `zlib.crc32`, `zipfile`, `shutil`, `base64`, `uuid.uuid4()`, `win32api.GetFileVersionInfo` or `pefile`.

#### 3.13 System status, module errors and application control

| VI | role | used by |
|---|---|---|
| `tools_update_system_status_from_xy_state.vi` | From the XY heartbeat (`alox_typedef_xy_heartbeat_event`): error when `ERROR COUNT ≥ hardware.xy.maxfails` (property; VI default 3, `alox_default.ini` 10); `System Status ID` = 1000 + 9-bit field ("1000-1512 ERROR"); the XY module sets its bit (loop frame "7" seen) (`c4`). | Alox · alox_core_ui |
| `tools_update_system_status_from_cameras_z_state.vi`, `tools_update_system_status_from_monochromator_state.vi` | Same for camera/Z (`alox_typedef_camera_heartbeat_report`) and WL (`alox_typedef_wl_heartbeat_event`) (same diagram layout). | Alox · alox_core_ui |
| `tools_camera_module_error.vi`, `tools_wl_module_error.vi`, `tools_xy_module_error.vi` | Older error-message builders from the same heartbeats (OpenG `1D Array to String`) (from calls). | — · no caller (listed under project Dependencies) |
| `tools_is_high_level_function_caller.vi` | TRUE when the system status is 201 Bowing / 202 Tilt / 203 Mesa running (500 = Idle) (`c10`). | — · no caller |
| `tools_alox_stop_all_functions.vi` | Calls the 12 function VIs (bowing, mesa, tilt, auto-exposure, autofocus, detect circle/object, measure mesa, refocus, refocus oxyd, search WL, tracking) with their method typedefs, presumably Stop (from calls). | — · no caller |
| `tools_alox_joystick.vi` | Enables/disables the XY joystick (`alox_manager_xy`, X and Y) and the Z joystick (`alox_manager_camera` Z Enable/Disable Joystick); logs "JoyReactivation fail" (`c6`). | Alox · alox_core_ui |
| `tools_alox_select_highmag_for_hmi.vi` | At HMI start: creates a U8 image, grabs twice (High Mag., exposure `Fixed`, `Run Once`, 100 ms wait), then sets the default camera to High Mag. (`c9`). | Alox · alox_core_ui |

#### 3.14 Date and time

| VI | role | used by |
|---|---|---|
| `get_system_time.vi` | `kernel32 GetSystemTimeAsFileTime` (+ `FileTimeToLocalFileTime` when `Local Time?`), FILETIME − 116 444 736 000 000 000 → ms (`Unix Timestamp ms`, I64), s (`Unix Timestamp`), `Excel = s/86400 + 25569`, `LabVIEW = (Excel − 1462)·86400` (`c5`). | Alox/Sim/WD · alox_heartbeat_service, alox_manager_heartbeat, alox_watchdog_ui, fn_hl_mesa_measurement_state_machine |
| `timestamp_converter.vi` | Excel days ↔ LabVIEW seconds (`(days − 1462)·3600·24`), enum `typedef_timestamp_convert_method` (`c5`). | — · no caller |
| `8601 UTC Date-Time String.vi` | ISO 8601 UTC string (third-party utility, description). | — · no caller |

Python: `time.time_ns()`, `datetime`; note that with `Local Time?` TRUE the "Unix" values are local wall-clock values, not UTC.

### 4. Data and configuration

| Item | Where | Notes |
|---|---|---|
| `<exe>\alox_setup.ini` `[folders]` | read by every folder resolver at every call | keys `vision`, `data`, `profiles`, `settings`, `exposures`, `jobhistory`; the shipped file (`vc_text/alox_setup.ini`) uses `/D/alox/02-Vision/…` and `jobhistory = "/D/alox/04-JobHistory"` |
| Hard-coded defaults | resolvers | `D:\alox\02-vision`, `D:\alox\02-Vision\data`, `…\profiles`, `…\exposures`, `…\JobHistory` (shared_*); `D:\alox\JobHistory` and `D:\alox\data` (jobhistory_*_path); `D:\alox\settings` |
| `alox.ini` (via `alox_manager_properties`) | overlays, status | `[overlays] alarmleft/alarmtop/alarmwidth/alarmheight`; `hardware.xy.maxfails` (and the camera/WL equivalents, **inferred**) |
| Profile keys (via `alox_manager_profile`) | `tools_mesa_averaged_measurement` | `measure.oxyd.MeasOxydMandatory`, `measure.vcsel.MeasMesaMandatory`, oxide measure type, mesa/oxide min/max diameters |
| Job-history tree | §3.10 | `…\Vision\images\oxid` or `\mapping`, `…\files\{log,mesa,focus,refocus}` |
| Output files | §3.11 | `.foc`, `.focf` (binary), `.focfraw`, `.wal`, `.ae`, `.strength` (text, tab), `*.png` with NI vision info, refocus PNGs (camera server), profile `.zip` |
| Image size assumptions | crop, mesa averaging | 2448 × 2048 (coercion limits and default circle centre 1224, 1024) |

### 5. External dependencies

- **NI Vision (IMAQ)**: Symmetry, Extract 2, Resample, FFT, ComplexPlaneToArray, EdgeDetection, Histogram, GrayMorphology,
  ImageBorderSize, ROIToMask 2, Mask, Copy, Create, Dispose, GetImageSize, ImageToArray, ImageToEDVR, Find Straight Edges 3,
  Convert Rectangle/Annulus to ROI, Point Distances, Get Angles, Polygon Area, Get Calibration Info 3, Overlay
  Line/Multiple Lines 2/Oval/Rectangle/Arc/Text, Clear Overlay, Write File 2, Write Image And Vision Info File 2, GM match
  report and Match Range Setting types.
- **LabVIEW Analysis / gmath**: Mean, Median, Std Deviation and Variance, General Polynomial Fit, FFT, Real FFT, PtByPt
  rectangular-to-polar, Delaunay Triangulation, Convex Hull, Point in Polygon, Fitting on a Sphere, Constrained Nonlinear
  Optimization, Ramp Pattern, 2D Cartesian Coordinate Shift.
- **LabVIEW utility**: Config File VIs, Write Delimited Spreadsheet, Write to Binary File, Zip VIs, registry VIs,
  `Application Directory`, `Color to RGB`/`RGB to Color`, `Search and Replace Pattern`.
- **OpenG** (`user.lib/_OpenG.lib`): Create Dir if Non-Existant, List Directory Recursive, 1D Array to String.
- **Windows**: `kernel32.dll` (GetSystemTimeAsFileTime, FileTimeToLocalFileTime), `ole32.dll` (CoCreateGuid), .NET
  `System.Diagnostics.FileVersionInfo`, registry.
- No hardware, no network, no Python in this part.

### 6. Interfaces with the other parts

- **Part 3 (functions)** is the main consumer: measure mesa (geometry circles, roundness, strength, bound, zero→NaN, measLine),
  high-level mesa (averaging, NaN filter, tilted coordinates, oxide overlay, image save, job paths), tilt (straight edge,
  matching options, mask, GPM overlays, tilt status, tracking move), bowing (bowing formula, job paths), auto-exposure
  (simple line, circle ROI, WL selection, overlay, `.ae`), autofocus/refocus (process/check AF data, refocus/oxide ROIs, thick
  overlays, `.foc/.focf`), search wavelength (WL ROI, lessFar check, `.wal`), get image (crop, scale bar, hardware read-out,
  IMAQ units).
- **Part 2 (hardware servers)**: camera server uses flip, FFT/Sobel metrics, refocus image logging, job file name, graph
  helpers, folder resolvers; WL/XY servers use the folder resolvers; heartbeat uses `get_system_time`.
- **Part 1 (core)**: boot/init cycles (folders, exe version), core UI (system-status codes, joystick, HMI camera selection,
  CRC file match, image file name), managers (profiles zip/clear, properties colour/base64, exposure LUT interpolation/update,
  `alox_tools_fit_diameters_measures` → RANSAC and chart filter).
- **Part 5 (REST API)**: images/profiles folders, exe version, IMAQ unit conversion for camera status, application root path.
- **Upward calls from this part** (to keep in mind when layering the rewrite): managers calibration, properties, camera, xy,
  monochromator, xy_coordinate_system, log, profile, display, image; `alox_error`; `alox_core_global_variables`; the LL
  functions get_image, detect_circle, measure_mesa.

### 7. Unused or legacy code

- **No caller and no build (22)**: `8601 UTC Date-Time String`, `timestamp_converter`, `geometry_fit_line_ransac`,
  `geometry_line_equation_from_points`, `MCC cost cno`, `MIC cost cno` (only in a Disabled frame), `tool_alox_FIR_wl_centering`,
  `tools_abort_all_running_vis`, `tools_alox_shared_jobhistory_folder`, `tools_alox_shared_jobhistory_folder_complete_path`,
  `tools_alox_stop_all_functions`, `tools_camera_module_error`, `tools_wl_module_error`, `tools_xy_module_error`,
  `tools_convert_from_base64_and_store`, `tools_is_high_level_function_caller`, `tools_linear_extrapolation`,
  `tools_parse_xml_from_argument`, `tools_tilt_position`, `tools_windows_app_data_folder`, `alox_vision_overlay_angle`,
  `alox_vision_roi_to_u8_array1d`.
- **Used, although `apps` is empty**: `MZC cost cno` (VI reference in `RoundnessCalculations-NI`, inferred);
  `tools_alox_shared_images_folder` (REST web-service build).
- **Only tests / R&D / unbuilt callers**: `alox_vision_measure_focus_fft_oxyd`, `…_sobel_oxyd`, `alox_vision_roi_to_u8_array1d_edvr`,
  `alox_vision_scale_roi` (test `alox_test_fft_1d_refocus_oxyd`); `tools_alox_shared_exposures_folder` (`alox_profiles_edition_ui`,
  not in the project).
- **Old/new pairs**: `learn_mask_to_bitmap` vs `_2` (both still called), `roi_to_u8_array1d` vs `_edvr`, `tools_tilt_position` vs
  `tools_alox_get_tilted_coordinates`, `ransac` vs `ransac2`, `*_module_error` vs `update_system_status_from_*`. Several callers
  are themselves legacy (`*_old`, `*_proto`, `*_py`).
- Dead branches: BMP/".jpg" frame of `tools_alox_save_img_type`; MIC/MCC in `RoundnessCalculations-NI`; core-number folder
  branch of the save VIs; third pass of `tools_alox_NaN_filter`.

### 8. Notes for the migration

1. **Line equation rejects horizontal and vertical lines** (`x1 = x2 OR y1 = y2` → error −1 "Same Points", `geometry_line_
   equation_from_two_points d`). The "RANSAC" built on it minimises the total point-to-line distance over random pairs, can draw
   index `N` (out of range → point (0,0)) and returns an error when no valid pair exists. Decide: reproduce for result
   continuity or replace by a proper RANSAC/least-squares fit (recommended, with a regression check on recorded data).
2. **`geometry_simple_line_equation_from_two_points` uses absolute values** for the slope (always ≥ 0) and divides by zero
   when `x1 = x2`. Harmless only while exposure and brightness grow together.
3. **Diameter definitions must be reproduced exactly**: perimeter-equivalent VI returns the perimeter (check where `/π` is
   applied), surface-equivalent `2·sqrt(A/π)`, inscribed = largest Delaunay circumcircle with centre inside the convex hull
   (not the distance-transform maximum), circumscribed = minimum enclosing circle (NaN after 20 000 iterations), MZC via a
   nonlinear optimiser seeded with 0.99/1.01 × LSC radii. Optimiser settings (`stopping criteria`, `cno settings`,
   `bounds MZC`) are front-panel defaults of `RoundnessCalculations-NI` and must be extracted.
4. **NaN filter constants**: the 0.25 band is absolute (µm), thresholds `> 5` and `> 4` samples, sample std (N−1, inferred).
5. **Folder defaults disagree** (`D:\alox\02-Vision\JobHistory` vs `D:\alox\JobHistory` vs `/D/alox/04-JobHistory` in the
   shipped INI; `D:\alox\02-Vision\data` vs `D:\alox\data`; `02-vision` vs `02-Vision`). Centralise in one configuration
   object, read once, and decide which default wins. The magic job ID `"Pj"` sends data to the data folder.
6. **File names have one-second resolution** (`%y%m%d_%H%M%S`) and no uniqueness check: two saves in the same second
   overwrite each other (`replace or create`). `tools_alox_job_data_filename` ignores its `Timestamp` input,
   `tools_alox_img_data_filename` uses it.
7. **Binary `.foc/.focf` files** are LabVIEW-flattened (big-endian, I32 length prefix, inferred); text files use the NI
   default `%.3f`. Keep readers for existing files.
8. **PNG with NI vision info** (`IMAQ Write Image And Vision Info File 2`) embeds overlays/calibration in private chunks;
   standard tools see only the pixels. Decide whether overlays are burned in, stored as sidecar JSON, or dropped.
9. **Hard-coded sensor size** 2448 × 2048 in `tools_alox_crop_size_definition` and in the default circle of
   `tools_mesa_averaged_measurement`; the crop formula `w − w/(2−Zoom)` gives negative values for `Zoom > 1` (coerced to 0)
   and returns `w − reduc` as "width" (a right edge for a left-anchored crop). Re-derive from the intended zoom behaviour.
10. **`tools_tracking_move_relative`** depends on debug globals `X/Y Stage Invert` ("should be OFF for AET") and on the
    wafer coordinate-system number; make these explicit configuration with tests per machine type (AET: XY moves cameras;
    OPTO: XY moves wafer).
11. **`get_system_time` "Unix" values are local time when `Local Time?` is TRUE**; heartbeat and watchdog compare them.
    Use monotonic clocks for timeouts and UTC for stored timestamps.
12. **`tools_round_u32_to_lower_odd_value`** wraps to 4 294 967 295 for input 0 (U32), if implemented as `n−1` (inferred).
13. **Overlay geometry is resolution- and zoom-dependent** (alarm box from `alox.ini` in pixels, ×0.5 when zoomed, scale
    bar at 0.98·H, text offsets in pixels). Prefer GUI-side overlays in a resolution-independent coordinate system.
14. **Layering**: tools that call managers or function VIs (`tools_mesa_averaged_measurement`, `tools_alox_select_highmag_
    for_hmi`, overlays reading properties/calibration) belong to the function/service layer in the rewrite; keep the pure
    geometry/statistics/ROI code as a dependency-free library with unit tests.
15. **NI internals** to validate numerically before switching libraries: EdgeDetection kernels and borders, Resample phase,
    POpen/PClose definitions, `Find Straight Edges 3` (rake spacing, edge strength/threshold, line fit), `General Polynomial
    Fit` residual definition. The focus-metric validation plan in `07-focus-metrics.md` §10 can be extended to these.

### 9. Open questions

1. `geometry_circle_diameter_equivalent_perimeter`: is the division by π done in `alox_function_ll_measure_mesa_state_machine`,
   or is the "perimeter diameter" reported as a perimeter?
2. Is rejecting horizontal/vertical candidate lines in the RANSAC intentional? What data does `alox_tools_fit_diameters_measures`
   fit (time series of diameters?), and is a true RANSAC acceptable?
3. Which job-history root is authoritative (`04-JobHistory`, `02-Vision\JobHistory`, `JobHistory`)? What does Process Job ID
   `"Pj"` mean (placeholder from the host?), and what is the exact folder naming `<A>\<B>`?
4. `tools_alox_bound_measured_values`: are out-of-range diameters set to 0 (then NaN) or clamped to the limit?
5. `alox_vision_overlay_oxyd_measurement`: is the alarm box colour forced to green (constant labelled "toujours OK") or driven by
   Oxyd Diam. vs Oxyd Target?
6. `tools_alox_crop_size_definition`: intended crop geometry for Zoom > 1 and < 1, and are other sensor sizes possible?
7. `get_system_time`: which value of `Local Time?` do the heartbeat/watchdog callers use, and do they compare against
   timestamps from other machines?
8. `tools_tracking_move_relative`: which coordinate-system numbers and stage-invert settings are used on each delivered system
   (AET vs OPTO)?
9. Are `.foc/.focf/.wal/.ae/.strength` files read back by anything (customer tools, AloxViewer), so that their formats must stay
   compatible?
10. Can the unused VIs listed in §7 (and the `_oxyd` focus metrics) be dropped from the rewrite?

---

## Part 5: API: REST web service, websocket events, JSON reports

Evidence markers used in this section:

- `<vi> d`, `<vi> dN`: seen in the block diagram image (main diagram or hidden frame N) of the HTML export.
- **(lvws)**: string constants and control/cluster labels extracted from the compiled route VIs inside the built
  web service `alox.lvws` (zip, `internal.llb/*.vi`, zlib blocks). This is exact text (key names, property names,
  defaults) but does not show the wiring.
- **(ini)**: `WebService.ini`, `WebService.lvwsdsc`, `niembeddedws.conf`, `alox.lvproj`, `alox_default.ini`.
- **(inferred)**: deduced from names, calls, callers or LabVIEW behaviour, not seen.

### 1. Purpose

This part is the external interface of Vision Core for a supervisor (host / SECS-GEM side, the AloxWatchdog program,
a browser). It has two channels:

- a **REST web service** (LabVIEW web service `alox`, 80 routes, one VI per route) used to read system state and
  settings, switch overlays, start/stop/abort the three high-level measurements (mesa, tilt, bowing), grab images and
  get JPEG snapshots;
- a **websocket server** (port 6123) that pushes events as JSON: heartbeat, system and function status, function
  started/finished/failed, measurement results, hardware warnings/errors/resets and a live JPEG image stream.

It also contains the JSON "report casts" that convert internal measurement reports into the websocket payloads.

### 2. Architecture

#### 2.1 Where the web service runs and how it exchanges data with the core

The web service is **not a separate process**. The build specifications `Alox` (AloxVision.exe) and `AloxSim`
include it (`App_webService[0].itemID = /My Computer/alox`, `App_serverConfig.httpPort = 8002`) **(ini)**, so the
route VIs run in the embedded LabVIEW web server inside AloxVision.exe. `niembeddedws.conf` (copied by the installer
next to the exe with `alox.lvws`) configures it: `Listen 8002`, `LimitWorkers 10`, `LimitRequestBody 50000000`,
`InactivityTimeout 60`, `NI.WSRuntime.Configuration embedded`, `LoadWebService alox.lvws` **(ini)**.
`WebService.ini` says `Standalone=true`, `ServiceName="alox"`, LabVIEW 18.0.1f4 x64, `NI_WSVersion 1.0.0.514`.

Consequently the route VIs talk to the application **in-process, through the same FGVs, named queue, named notifiers
and global variables as the core** (they would not work otherwise; the design relies on sharing the application
instance, which is LabVIEW behaviour for a web service embedded in an EXE **(inferred)**). There is no TCP, shared
variable, file or HTTP hop between the web service and the core, except:

- `PUT /v1/system/configuration` writes `alox_setup.ini` (`[folders] profiles=`) in the exe folder
  (`alox_api_v1_put_system_configuration d`).
- `GET /v1/system/profiles` lists `*.zip` files of the shared profiles folder **(lvws)**.

Mechanisms used by the routes (from `calls_alox` and diagrams):

| Mechanism | Used for | Evidence |
|---|---|---|
| `alox_manager_process_queue` "Get Queue" + Enqueue `Alox Queue Item {Method, Variant}` | every command: Measure Mesa/Tilt/Bowing, Load Profile, Get Image, Stop, Abort, System Reset, System Shutdown, Show UI, Profiles Folder Changed, mode change | `alox_api_v1_put_functions_measure_mesa d`, `alox_api_v1_put_functions_abort d` |
| `alox_manager_functions_condition` "Get Condition OK" | refuse a start (HTTP 503) when the function cannot run | `alox_api_v1_put_functions_measure_mesa d1` |
| `alox_manager_properties` (alox.ini properties), `alox_manager_profile` (current profile) | read settings, write overlay flags into the profile | `alox_api_v1_put_camera_overlay_mesa d` |
| `alox_manager_system_status_ID`, `_mesa_status_ID`, `_tilt_status_ID`, `_bowing_status_ID`, `alox_manager_tilt`, `alox_manager_camera`, `alox_manager_calibration`, `alox_manager_image` | read state, last image (merged overlays) | `alox_api_v1_get_system_status d`, `alox_api_v1_get_camera_jpegimage d` |
| `alox_core_global_variables` ("Core Heartbeat", "Web Service Version", "Web Service") | heartbeat counter, web service running flag | `alox_api_v1_get_system_heartbeat d`, `alox_api_v1_startup d` |
| `alox_manager_websocket_images_stream` (FGV) | start/stop the websocket image stream | `alox_api_v1_put_stream_image_start d` |
| `alox_manager_language` (Get String), `alox_manager_log` | localized response texts, API call log | all route diagrams |

#### 2.2 Websocket server

```
 core boot cycle --Init--> alox_api_manager_websocket_server (FGV)
                              | VI Server: open <app root>/api/alox_api_websocket_service.vi,
                              | set "Stop"=F, Run (no wait), front panel hidden
                              v
 alox_api_websocket_service.vi (one running instance, one client at a time)
   outer loop: TCP Create Listener(websocket.port, 6123) -> Wait On Listener (5000 ms) -> close listener
               -> WebSockets "Server Handshake" (1000 ms)
     read loop  : WebSockets Read (500 ms); opcode/error case: 0 data (ignored), 56 timeout, 66 client closed
                  -> ws_stop_notifier; other error -> disconnect if websocket.disconnectonerror
     write loop : Wait on Notification from Multiple (8 named notifiers, 10 ms)
                  -> for each notification: case on Websocket Event Type -> Variant To Data
                  -> alox_api_ws_send_<type>_event.vi (JKI Flatten To JSON + WebSockets Write, 10 ms)
                  ws_stop_notifier -> WebSockets Close, TCP Close, back to listening
 producers (other parts): alox_manager_api_notifiers "Get Notifier" + Send Notification
   {Websocket Event Type, Variant}
```

- `alox_api_manager_websocket_server` methods (`alox_api_typedef_websocket_manager_method`: Init, Exit, Is Running ?,
  Client Connected ?, Get Status): Init launches the service VI by path (`d`); Exit sets `Stop`=T, closes the front
  panel and polls `Exec.State` until Idle with a 5000 ms timeout (`d6`); Is Running ? = Exec.State is "Run top level"
  (`d9`); Client Connected ? reads the `Client?` indicator (`d12`); Get Status returns `error out.source` (`d13`).
- `alox_api_websocket_service`: reads properties `websocket.port` (default 6123) and
  `websocket.disconnectonerror` (default T) (`d`), creates notifier `ws_stop_notifier`, gets all API notifiers
  (`Get All Notifiers`). Sequence frame 0 resets `Stop` (`d1`), frame 2 releases the stop notifier and sets
  `Running?`=F (`d33`). The listener is created, accepted and closed inside the outer loop, so **only one client is
  served at a time**; a second client cannot connect until the first leaves (`d2`, interpretation of the three TCP
  nodes). Messages received from the client are only shown in a `Received Data` indicator and are otherwise ignored
  (`d7`): the websocket is push-only. Handshake failure/timeout leads to case "Invalid websocket connection" which
  clears `Client?` and loops back (`d32`).
- `alox_manager_api_notifiers` (FGV, methods Init, Get Notifier, Get All Notifiers, Exit): Init obtains 8 named
  notifiers `alox_heartbeat`, `alox_status`, `alox_measure_mesa`, `alox_measure_tilt`, `alox_measure_bowing`,
  `alox_finished`, `alox_failed`, `alox_error` (`d3`); element type `alox_api_typedef_websocket_event`
  {Websocket Event Type (enum, 17 values), Variant}. Get Notifier maps the type to an index: heartbeat 0, status 1,
  measure mesa/tilt/bowing 2..4, finished 5, failed 6, error 7, and **every other type (started, hardware warnings,
  hardware_reset, mesa/tilt/bowing_status, image) to index 0 = `alox_heartbeat`** (case `"heartbeat", Default`,
  `d5`, `d6`, `d10`, `d11`, `d12`). Exit releases all notifiers.
- `alox_manager_websocket_images_stream` (FGV, methods Init, Exit, Start Stream, Stop Stream, Is Started?,
  Stream Image): Init creates IMAQ images `ALOX_STREAM_JPEG`, `ALOX_RESAMPLE_JPEG`, default options
  {Width 1024, Height 768, Quality 750}, Started?=F (`d3`); Start Stream stores the options and sets Started?=T
  (`d5`); Stream Image (called by function state machines and the core UI with the current image) runs only if the
  websocket server is running, a client is connected and the stream is started; it merges overlays, **center-crops**
  (no scaling) to Width x Height when the size differs, encodes JPEG (`IMAQ Write JPEG String`, quality 0..1000),
  Base64-encodes it and sends an `image` event through the API notifiers (`d8`). Exit disposes the images (`d4`).

#### 2.3 REST route VI pattern

All 80 route VIs follow the same template (observed in `alox_api_v1_get_system_status d`,
`alox_api_v1_get_camera_overlay_mesa d`, `alox_api_v1_put_camera_overlay_mesa d`):

1. (PUT) read arguments with `Read Form Data` (one key) or `Read All Form Data` (several keys / defaults).
2. call the manager(s); for a GET, bundle a cluster and `Flatten To JSON String` (JKI JSON Serialization);
   set MIME type `application/json`.
3. HTTP code = 200 if no error (and, for PUT, the argument is not empty), else 500; measurement starts and grabs
   return 503 when the function condition is not OK.
4. PUT body = localized text `009000` ("no error") or `009002` ("invalid arguments error"); 503 body = `009005`
   ("function not ready to start, or already running") or `009013` ("get image error (not ready condition)").
5. If property `log.webapi` is true, log "api received" (string IDs 0015xx/0016xx with the arguments) and
   "api status" (0091xx with status and body) through `alox_manager_log`.
6. `Write Response`, `Flush Output`, `Clear Errors` (LabVIEW errors never reach the web server).

`alox_api_v1_startup.vi` is the service's startup VI (`ws.type 2`): it reads service attribute `NI_WSVersion` into
global "Web Service Version", then loops every 500 ms writing the global "Web Service" (running flag) until
`Get Web Service Status` reports a stop (`alox_api_v1_startup d`).

#### 2.4 Lifecycle and concurrency

- Init: the LabVIEW embedded web server starts with the EXE and loads every route VI at startup
  (`ws.loadAtStartup`, `ws.keepInMemory`) **(ini)**; the core boot cycle initializes the API notifiers and the
  websocket server manager (system status texts 000013 "Initializing web server", 000014 "Initializing websocket
  server") (callers of `alox_manager_api_notifiers` / `alox_api_manager_websocket_server`). The image stream FGV is
  initialized in `alox_core_init_cycle`.
- Run: up to 10 concurrent HTTP workers; each route call serializes on the non-reentrant FGVs it calls
  **(inferred)**. Commands are asynchronous: a PUT only enqueues an item into the core process queue and returns;
  progress and results arrive on the websocket.
- Exit: shutdown cycle calls websocket manager Exit and notifiers Exit; exit cycle calls image stream Exit.

### 3. Components

| VI | Role | Used by (apps / callers) |
|---|---|---|
| `alox_api_v1_get_camera_overlay_*` (15: autoexposure, autofocus, bowing, detectcircle, detectobject, diameters, edges, mesa, oxyd, refocus, refocus_oxyd, target, tilt, tracking, wavelength) | GET one overlay flag from the current profile parameters (`alox_function_*_bundle_profile_parameters`), returns `{"<name>":"on"\|"off"}` | web service routes (Alox, AloxSim) |
| `alox_api_v1_get_{lowangle,highangle}_camera_overlay_{camera,infos,scale}` (6) | same for the Get Image overlays of the high-mag (lowangle VI) / low-mag (highangle VI) camera | web service |
| `alox_api_v1_put_camera_overlay_*` (14) + `alox_api_v1_put_camera_overlays_refocus` | PUT `state=on\|off`, writes one or more boolean profile properties (Set Property) | web service |
| `alox_api_v1_put_{lowangle,highangle}_camera_overlay_{camera,infos,scale}` (6) | same for `get.image.highmag.*` / `get.image.lowmag.*` properties | web service |
| `alox_api_v1_get_{lowangle,highangle}_camera_status` (2) | camera status JSON (type, running/stopped, image count, timestamp, pixel size and units, exposure) via camera and calibration managers | web service |
| `alox_api_v1_put_camera_grab{lowangle,highangle}` (2) | check Get Image condition, enqueue Get Image for the high-mag / low-mag camera, 503 if not ready | web service |
| `alox_api_v1_get_camera_jpegimage` | current image with merged overlays, resampled, returned as binary JPEG | web service |
| `alox_api_v1_get_camera_liveimage` | returns an HTML page that refreshes `jpegimage` every 100 ms | web service |
| `alox_api_v1_get_camera_overlays` | list of overlay names (JSON) | web service |
| `alox_api_v1_put_stream_image_{start,stop}` (2) | start (with width/height/quality) / stop the websocket image stream | web service |
| `alox_api_v1_get_system_*` (15: bowing_status, configuration, events, heartbeat, heartbeat_frequency, heartbeat_retries, imagesfolder, mesa_status, mode, profile, profiles, status, tilt, tilt_status, version) | read-only system information (see API table) | web service; heartbeat/frequency/retries also polled by AloxWatchdog (inferred) |
| `alox_api_v1_get_watchdog_visibility` | `{"visible": general.watchdogui}` for the watchdog | web service; AloxWatchdog (inferred) |
| `alox_api_v1_put_system_{configuration,mode,profile,reset,showui,shutdown,tiltreset}` (7) | change profiles folder, local/remote mode, load profile, reset/shutdown the system, show UI, reset tilt | web service |
| `alox_api_v1_put_functions_measure_{mesa,tilt,bowing}` (3) | start a high-level measurement with SEMI job ids and parameters (enqueue), 503 if not ready | web service |
| `alox_api_v1_put_functions_{stop,abort}` (2) | enqueue Stop / Abort (abort is served as GET) | web service |
| `alox_api_v1_startup` | web service startup VI: publishes web service version and running flag in globals | web service (startup VI) |
| `alox_api_manager_websocket_server` | FGV: launches/stops the websocket service VI, reports running/client state | Alox, AloxSim / `alox_core_boot_cycle`, `alox_core_shutdown_cycle`, `alox_core_simulator_ui`, `alox_properties_ui`, `alox_manager_websocket_images_stream` |
| `alox_api_websocket_service` | websocket server loop (listen, handshake, read loop, write loop dispatching events) | Alox, AloxSim / started dynamically by the manager |
| `alox_api_ws_send_*_event` (17: heartbeat, status, mesa_status, tilt_status, bowing_status, started, finished, failed, error, measure_mesa, measure_tilt, measure_bowing, camz_warning, xy_warning, monochromator_warning, hardware_reset, image) | build one event JSON object and write it as a websocket frame | Alox, AloxSim / `alox_api_websocket_service` |
| `alox_manager_api_notifiers` | FGV owning the 8 named event notifiers | Alox, AloxSim / boot/shutdown cycles, core UI, heartbeat services, status ID managers, HL state machines (and simulations), images stream, websocket service |
| `alox_manager_websocket_images_stream` | FGV: image stream options/state, JPEG+Base64 encoding of the current image into `image` events | Alox, AloxSim / the two stream routes, init/exit cycles, core UI, HL and LL function state machines |
| `alox_tools_cast_measure_{mesa,tilt,bowing}_to_json_report` (3) | convert measurement reports into `alox_api_typedef_measure_*` (all values as strings, "." decimal point, units) | HL state machines and their simulations |
| `alox_tools_cast_{started,finished,failed}_semi_job_to_json_report` (3) | bundle {Function, SEMI Job} into the started/finished/failed payloads | HL state machines (started, failed); `alox_core_ui` (finished, Alox only) |

### 4. Data and configuration

Properties read through `alox_manager_properties` (alox.ini; defaults from `alox_default.ini`):

| Key | Default | Use |
|---|---|---|
| `websocket.port` | 6123 (diagram default 6123) | websocket listener port |
| `websocket.disconnectonerror` | TRUE | close the client on an unknown read error |
| `log.webapi` | TRUE (diagram default F) | log every API call and response |
| `log.websplit` | diagram default `_-_` | used when formatting the configuration path (role not visible) |
| `heartbeat.frequency` | 1000 | returned by `GET /v1/system/heartbeatfreq` |
| `general.watchdogmaxfails` | 10 | returned by `GET /v1/system/heartbeatretries` |
| `general.watchdogui` | FALSE | returned by `GET /v1/watchdog/visibility` |
| `general.mode` | 1 | local/remote mode (GET/PUT `/v1/system/mode`) |
| `general.profile` | "DEFAULT" | current profile (GET `/v1/system/profile`) |
| `hardware.camera.maxfails`, `hardware.xy.maxfails` | 10 (diagram default 3) | threshold hardware_warning -> hardware_error in the websocket events |
| `[webservice] port_I32` | 8001 | not read by this part; the real port is the build setting 8002 |

Profile properties written by the overlay PUT routes (current profile, via `alox_manager_profile` Set Property)
**(lvws)**: `measure.oxyd.overlay{mesa,oxyd,target,diameters,points}`, `measure.bowing.overlay{bowing,parallelism}`,
`measure.tilt.overlay{matches,straightedge,edges,tilt}`, `autofocus.overlay{score,position}`,
`refocus.overlay{score,position,roi}`, `refocus.oxyd.overlay{score,position,roi}`,
`detect.vcsel.overlay{bestcircle,allcircles,objects}`, `search.object.overlay{match,contours}`,
`tracking.overlayzone`, `wavelength.overlay{zones,results}`, `autoexpo.overlay{roi,results}`,
`get.image.{highmag,lowmag}.overlay{name,type,index,time,hwinfos,scale}`. Whether the profile is saved to its zip
file afterwards is not visible.

Files: `alox_setup.ini` in the exe folder, section `[folders]`, key `profiles` (written by PUT configuration);
profiles folder `*.zip` (listed); `alox.lvws` and `niembeddedws.conf` installed next to the exe.

Localized texts: `translations/alox_en.lang` / `alox_fr.lang`: 0015xx-0016xx (api received), 0091xx (api status),
009000/009002/009005/009013 (response bodies), 000028/000029 (Local/Remote), 000030/000031 (On/Off, log only),
000032 (Degrees).

### 5. External dependencies

- NI LabVIEW Web Services runtime (`vi.lib/wsapi`: Read Form Data, Read All Form Data, Set HTTP Response Code, Set
  HTTP Response MIME Type, Write Response, Flush Output, Get Web Service Status, Read Service Attribute), NI LabVIEW
  Web Server 2018 (installer dependency), embedded server config `niembeddedws.conf`.
- JKI JSON Serialization (`Flatten To JSON String`; `Unflatten From JSON String` is used by the watchdog client).
- MediaMongrels WebSockets toolkit 1.7.4.26 (`DoHandshake`, `Read`, `Write`, `Close`) over LabVIEW TCP.
- NI Vision: IMAQ Create/Dispose/GetImageSize/Merge Overlay/Extract 2/Resample/Write JPEG String/Write String.
- OpenG (string split in the version route), `tools_convert_to_base64.vi`.
- No authentication: every route has `RequireAPIKey=false`, no permissions, `RequireNIAuthentication=False`
  **(ini)**; no TLS; no CORS headers are set by any route.

### 6. Interfaces with the other parts

- Core (group 1): process queue items consumed by `alox_core_ui` (Measure Mesa/Tilt/Bowing, Load Profile, Get Image,
  Stop, Abort, System Reset, System Shutdown, Show UI, Profiles Folder Changed, ...); status ID managers, properties,
  profiles, log, language, global variables; boot/init/exit/shutdown cycles start and stop the websocket server,
  notifiers and image stream.
- Functions (group 3): HL state machines publish `started`, `failed`, `measure_*`, `*_status` events (through the
  cast VIs and status managers) and push images to the stream; `alox_core_ui` publishes `finished` when it handles
  "Mesa/Tilt/Bowing Job Finished" queue items; `alox_function_*_bundle_profile_parameters` supply the GET overlay
  values; `alox_manager_functions_condition` decides 503.
- Hardware (group 2): `alox_heartbeat_service` / `alox_modules_heartbeat_service` publish heartbeat and hardware
  warning/error/reset events **(inferred from callers)**; camera status via `alox_manager_camera`.
- Other programs (group 6): AloxWatchdog (`alox_watchdog_ui`) is a REST client (JKI REST Client + Unflatten From JSON
  with the heartbeat, heartbeat_frequency, heartbeat_retries and watchdog visibility typedefs) **(inferred)**.
  The camera server is not involved (see docs 01-10).

### 7. Unused or legacy code

- Route `GET /alox_api_v1_get_system_imagesfolder/` (default VI-name URL, Terminal output mode): leftover; returns
  the shared data folder **(lvws)**.
- `alox_api_typedef_version.ctl` and `alox_api_typedef_camz_hardware_warning.ctl` are referenced by no VI (the version
  route uses its own cluster `major/minor/build/api/release`; the camz event uses `alox_typedef_camera_heartbeat_report`).
- `[webservice] port_I32 = 8001` and the `[version]` section of `alox_default.ini` are not used by this part.
- Global variable fields `Access-Control-Allow-Origin`, `Access-Control-Allow-Credentials`, `SSE_frequency`,
  `SSE Client`, `Session_timeout`, `Reconnect_session_timeout` exist in `alox_core_global_variables` **(lvws)** but no
  route sets HTTP headers: template leftovers.
- `alox_function_ll_search_wavelength_state_machine_old` still calls the image stream (other group, legacy).
- Log string IDs are copy-pasted in several routes (e.g. `GET overlay/autoexposure` logs 001585 "camera/overlay/target",
  `GET system/tilt` uses an invalid ID "609") **(lvws)**: cosmetic.

### 8. Notes for the migration

1. Base URL is `http://<host>:8002/alox/v1/...` (service name prefix `/alox`, observed in the URL that
   `alox_api_v1_get_camera_liveimage d` generates; port 8001 when run from the LabVIEW development system).
   `GET /alox/` serves the static `Public` folder (`index.html`, not in the export).
2. PUT arguments are **query-string/form parameters** (`Read Form Data`), not JSON bodies. LabVIEW also accepts an
   `application/x-www-form-urlencoded` body **(inferred)**; a rewrite should accept both. Unknown keys are ignored.
3. PUT responses are plain localized text ("no error", "invalid arguments error"; French "pas d'erreur",
   "arguments invalides"), no MIME type set (LabVIEW default `text/html` **(inferred)**). Invalid arguments give
   HTTP 500, not 400. Keep or deliberately change; clients may test the text.
4. Booleans: value compared case-insensitively with `on`; anything else (including `true`) means off
   (`alox_api_v1_put_camera_overlay_mesa d`). Exception: `showui` accepts `visible=true` or `on` **(lvws)**.
5. Numbers in result JSON are mostly **strings** with a forced "." decimal separator (cast VIs replace "," by ".");
   status codes in `/v1/system/status` are JSON integers; the image event uses integers.
6. Key naming is inconsistent and must be reproduced exactly: `Control Job ID` (started/finished/failed) vs
   `Control_Job_ID` (measure events), `Heartbeat`, `Frequency`, `Retries` capitalized, `STDDEV FET/#` in mesa,
   `zwafer Unit`, `Zwafer5`.
7. `alox_api_typedef_measure_bowing` contains **duplicate labels** (`bowinga` twice, `parallelismb` twice): JKI emits
   duplicate JSON keys and a standard parser keeps only the last value (typedefs.md). Decide on `bowinga/bowingb`,
   `parallelisma/parallelismb` with the owner.
8. **`GET /v1/system/version` enqueues an `Alox Queue Item` whose Method constant is `Abort`** into the core process
   queue (same Enqueue primitive as `alox_api_v1_put_functions_abort d`; `alox_api_v1_get_system_version d`). If
   confirmed, reading the version aborts a running function; do not reproduce.
9. `/v1/functions/abort` is a **GET** with a side effect and is in Terminal output mode while the VI writes a
   streamed response; its actual HTTP body is uncertain (probably empty, status 200) **(inferred)**.
10. Websocket: one client at a time; no subscription protocol (client messages ignored); events are text frames
    with a JSON object containing `"event"`. Delivery is **lossy**: notifiers keep only the latest value, the write
    loop polls every 10 ms, and nine event types share the `alox_heartbeat` notifier, so a heartbeat can overwrite an
    unread warning/status/started/image event. A rewrite should use per-client queues.
11. The websocket write timeout is 10 ms per frame (`Send timeout (ms) 10`, `alox_api_websocket_service d`): large
    image frames (Base64 JPEG) can time out on a slow link.
12. The `status` websocket event replaces any system status code in 1001..1999 (hardware ERROR bitmask codes of
    `system_en.lang`) by `"500"` (idle) (`alox_api_ws_send_status_event d`); the REST `/v1/system/status` returns the
    real code. The second key name of that event is not visible (unnamed bundle input).
13. `GET /v1/system/events` hard-codes `ws://<host>:6123` although the port is configurable, and its list omits the
    `error` event that the server can send.
14. JPEG snapshot (`jpegimage`) **resamples** (zero order, aspect ratio not kept) to width x height (default 612 x
    512, quality 750); the websocket stream **center-crops** without scaling (default 1024 x 768 in the FGV, 612 x 512
    when started from the route). Requests larger than the image produce a negative crop rectangle **(inferred)**.
15. `jpegimage` never sets an HTTP code: errors still return 200 with whatever body was produced.
16. Measurement starts return immediately (200) after enqueueing; completion is only reported by websocket events
    (`started`, `measure_*`, `finished`/`failed`). Mesa accepts `profile=` and enqueues Load Profile before Measure
    Mesa (`alox_api_v1_put_functions_measure_mesa d`). Parameters not given in the request come from the current
    profile.
17. Number parsing: mesa wires "use system decimal point = F" (always "."); for tilt/bowing the setting is not
    visible: on a French Windows locale "1.5" could parse as 1 **(inferred risk)**.
18. Security: no authentication, including `shutdown`, `reset`, `abort`, profile and folder changes. Any rewrite
    exposed on a network needs at least an allow-list or token.
19. The JSON serializer may emit multi-line JSON (JKI option `Multiline (T)` default) **(inferred)**; clients must not
    depend on formatting.

### 9. Open questions for the owner

1. Is the `Abort` enqueued by `GET /v1/system/version` intentional (note 8)?
2. Which clients use the API (host software, SECS/GEM bridge, watchdog, browser)? Do they parse the plain-text PUT
   responses or only the HTTP code? Is the French text ever sent to them?
3. Must the websocket support several clients and lossless delivery? Is the `"500"` mapping of hardware error codes
   in the `status` event intentional, and what is its second key name on the wire?
4. Bowing event duplicate keys (note 7): which values do clients actually read?
5. Should overlay PUTs persist to the profile file or only to the in-memory profile?
6. Meaning and use of `indexID` in `PUT /v1/functions/measuremesa` (read but not used in the log).
7. What is in the static `Public` folder served at `/alox/` (not in the export)?

---

### Appendix A. REST API reference

Base: `http://<host>:8002/alox`. All routes: no authentication. "Codes": HTTP status codes produced. Evidence column
as defined at the top. "highmag" routes are served by `lowangle` VIs and "lowmag" by `highangle` VIs.

#### A.1 System

| Method | Path | Request | Response 200 body | Codes | Core action | Evidence |
|---|---|---|---|---|---|---|
| GET | `/v1/system/status` | none | `{"status": <int>, "description": "<localized text>"}` (codes in `system_en.lang`: 0 initial, 10-59 booting, 100 initialized, 200 starting, 201-204 function running, 500 idle, 600 paused, 800-803 exit/reset/stop/abort accepted, 1001-1999 hardware ERROR bitmask) | 200/500 | system status manager Get | `get_system_status d` |
| GET | `/v1/system/mesastatus`, `/v1/system/tiltstatus`, `/v1/system/bowingstatus` | none | `{"status": <int>, "description": "<text>"}` (codes in `mesa_en.lang`, `tilt_en.lang`, `bowing_en.lang`) | 200/500 | status ID manager Get / Get Description | lvws labels `status`, `description`; I for types |
| GET | `/v1/system/heartbeat` | none | `{"Heartbeat": "<counter>"}` (global "Core Heartbeat", I64 as `%ld`, updated by the heartbeat manager) | 200/500 | read global | `get_system_heartbeat d` |
| GET | `/v1/system/heartbeatfreq` | none | `{"Frequency": "<heartbeat.frequency>"}` | 200/500 | property | lvws, typedef |
| GET | `/v1/system/heartbeatretries` | none | `{"Retries": "<general.watchdogmaxfails>"}` | 200/500 | property | lvws, typedef |
| GET | `/v1/watchdog/visibility` | none | `{"visible": true\|false}` (`general.watchdogui`) | 200/500 | property | lvws, typedef |
| GET | `/v1/system/version` | none | `{"major":..,"minor":..,"build":..,"api":..,"release":"<date> - <time>"}` from the FileVersion and modification date of `AloxVision.exe` (numbers probably JSON integers) | 200/500 | reads exe info; **enqueues Abort** (note 8) | `get_system_version d`, lvws labels |
| GET | `/v1/system/mode` | none | `{"mode": "local"\|"remote"}` (`general.mode`) | 200/500 | property | lvws labels `mode`, `local`, `remote`; value format I |
| PUT | `/v1/system/mode` | `type=local\|remote` | text 009000/009002 | 200/500 | set `general.mode`, enqueue a queue item | lvws; queue item type I |
| GET | `/v1/system/profile` | none | `{"path": "...", "filename": "..."}` (current profile `general.profile`) | 200/500 | property | lvws labels; content I |
| GET | `/v1/system/profiles` | none | `{"profiles": ["<name>", ...]}`: `*.zip` in the shared profiles folder, names without extension | 200/500 | file listing | lvws labels `profiles`, `*.zip`; I for exact shape |
| PUT | `/v1/system/profile` | `filename=<profile>` | text | 200/500 | enqueue Load Profile | lvws, calls |
| GET | `/v1/system/configuration` | none | `{"path": "<shared profiles folder>"}` | 200 (500 if error or empty) | `tools_alox_shared_profiles_folder` | `get_system_configuration d` |
| PUT | `/v1/system/configuration` | `path=<existing folder>` | text | 200/500 | write `alox_setup.ini [folders] profiles=<path>`, then enqueue (Profiles Folder Changed, I) | `put_system_configuration d` |
| GET | `/alox_api_v1_get_system_imagesfolder/` | none | `{"path": "<shared data folder>"}` (Terminal mode, body uncertain) | ? | `tools_alox_shared_images_folder` | ini, lvws |
| GET | `/v1/system/events` | none | `{"url": "ws://<host>:6123", "events": ["heartbeat","status","measure_tilt","measure_mesa","measure_bowing","finished","started","failed","hardware_warning","hardware_error","hardware_reset","mesa_status","tilt_status","bowing_status","image"]}` | 200 | hostname manager | `get_system_events d` |
| GET | `/v1/system/tilt` | none | `{"Tilt": "<angle %f, '.' decimal>", "Units": "degrees"}` | 200/500 | tilt manager Get Tilt | lvws (`%f`, `degrees`, typedef) |
| PUT | `/v1/system/tiltreset` | none | text | 200/500 | tilt manager Reset Tilt | calls, lvws |
| PUT | `/v1/system/reset` | none | text | 200/500 | enqueue System Reset | calls; I |
| PUT | `/v1/system/shutdown` | none | text | 200/500 | enqueue System Shutdown | calls; I |
| PUT | `/v1/system/showui` | `visible=true\|on` (else hide) | text | 200/500 | enqueue Show UI (boolean) | lvws; I |

#### A.2 Functions

| Method | Path | Request | Response | Codes | Core action | Evidence |
|---|---|---|---|---|---|---|
| PUT | `/v1/functions/measuremesa` | `ctrljobid, procjobid, lotid, waferid, zoneid` (SEMI job), `x, y` (start position mm, "." decimal), `target` (oxyd target µm), `tilt, singleshot, positionsctrl, analysis` (`on`/other), `profile` (optional, must exist in profiles list), `indexID` (read, use unknown); missing values from the current profile | text 009000/009002; 503 body = 009005 | 200/500/503 | if condition OK: [enqueue Load Profile], enqueue Measure Mesa with `alox_typedef_process_queue_item_mesa_params`; else set mesa status 10 and 503 | `put_functions_measure_mesa d`, `d1`, lvws |
| PUT | `/v1/functions/measuretilt` | `ctrljobid, procjobid, lotid, waferid, x, y` | same | 200/500/503 | enqueue Measure Tilt (`..._tilt_params`, rest from profile) | lvws, calls; I for wiring |
| PUT | `/v1/functions/measurebowing` | `ctrljobid, procjobid, lotid, waferid, x` (logged as "x=%f mm"; probably measure distance) | same | 200/500/503 | enqueue Measure Bowing (`..._bowing_params`) | lvws, calls; I |
| PUT | `/v1/functions/stop` | none | text | 200/500 | enqueue Stop | calls; I |
| GET | `/v1/functions/abort` | none | Terminal mode, body uncertain | 200? | enqueue Abort | `put_functions_abort d` |

#### A.3 Camera and images

| Method | Path | Request | Response | Codes | Core action | Evidence |
|---|---|---|---|---|---|---|
| GET | `/v1/camera/highmagstatus`, `/v1/camera/lowmagstatus` | none | `{"Type","Status":"Running"\|"Stopped","ImageTotal","Timestamp","PixelSize","PixelUnits":"pixels"\|"um"\|"mm","ExposureTime"}` all strings | 200/500 | camera and calibration managers | lvws, typedef |
| PUT | `/v1/camera/grabhighmag`, `/v1/camera/grablowmag` | none | text 009000/009002, 503 body 009013 | 200/500/503 | condition Get Image OK -> enqueue Get Image (camera type) | lvws, calls |
| GET | `/v1/camera/jpegimage` | `quality` (default 750, 0..1000), `width` (612), `height` (512) | binary JPEG, `Content-Type: image/jpeg`; current image with merged overlays, resampled to width x height | always 200 | image manager Get Merged Overlays | `get_camera_jpegimage d` |
| GET | `/v1/camera/liveimage` | `quality` (750), `width` (640), `height` (480) | HTML page (`text/html; charset=us-ascii`) with an `<img>` refreshed by JavaScript every 100 ms from `http://<ip>:8002/alox/v1/camera/jpegimage?...` | 200 | none | `get_camera_liveimage d` |
| PUT | `/v1/camera/streamstart` | `quality` (750), `width` (612), `height` (512) | text | 200/500 | image stream Start Stream | `put_stream_image_start d` |
| PUT | `/v1/camera/streamstop` | none | text | 200/500 | image stream Stop Stream | calls |
| GET | `/v1/camera/overlays` | none | `{"overlays": [...]}`, names among `highmagcamera, lowmagcamera, highmaginfos, lowmaginfos, highmagscale, lowmagscale, bowing, tilt, mesa, oxyd, target, diameters, edges, autofocus, refocus, refocusoxyd, detectcircle, tracking, wavelength, detectobject, autoexposure` (possibly filtered) | 200/500 | none | lvws; shape I |
| GET | `/v1/camera/overlay/<name>` | none | `{"<name>": "on"\|"off"}` | 200/500 | profile parameters of the function | `get_camera_overlay_mesa d`, lvws |
| PUT | `/v1/camera/overlay/<name>` | `state=on\|off` | text | 200/500 | profile Set Property (list in section 4) | `put_camera_overlay_mesa d`, lvws |

`<name>` and property written by the PUT **(lvws)**: `mesa` measure.oxyd.overlaymesa; `oxyd` overlayoxyd; `target`
overlaytarget; `diameters` overlaydiameters; `edges` measure.oxyd.overlaypoints; `bowing` measure.bowing.overlaybowing
+ overlayparallelism; `tilt` measure.tilt.overlaymatches + overlaystraightedge + overlayedges + overlaytilt;
`autofocus` autofocus.overlayscore + overlayposition; `refocus` refocus.overlayscore + overlayposition + overlayroi;
`refocusoxyd` refocus.oxyd.*; `detectcircle` detect.vcsel.overlaybestcircle + overlayallcircles + overlayobjects;
`detectobject` search.object.overlaymatch + overlaycontours; `tracking` tracking.overlayzone; `wavelength`
wavelength.overlayzones + overlayresults; `autoexposure` autoexpo.overlayroi + overlayresults; `highmagcamera`
get.image.highmag.overlayname + overlaytype + overlayindex + overlaytime; `highmaginfos` get.image.highmag.overlayhwinfos;
`highmagscale` get.image.highmag.overlayscale; `lowmag*` the same with `get.image.lowmag.*`. The GET reports one flag of
the function's parameters (for mesa: "Overlay External").

### Appendix B. Websocket protocol

- Endpoint: `ws://<host>:<websocket.port>` (default 6123), any path, plain TCP (no TLS), RFC 6455 handshake by the
  MediaMongrels toolkit, server to client text frames only **(inferred: opcode constant not legible)**; one client.
- Every message is one JSON object with `"event"` first. Payloads (from `alox_api_ws_send_*_event d` and typedefs):

| event | JSON fields after `event` | Source of data | Evidence |
|---|---|---|---|
| `heartbeat` | `timestamp`: string (Heartbeat value) | heartbeat service | `send_heartbeat_event d` |
| `status` | system status code as string, codes 1001..1999 replaced by `"500"`; key name not visible | system status manager | `send_status_event d` |
| `mesa_status`, `tilt_status`, `bowing_status` | `State`: string (status code) | function status managers | `send_mesa_status_event d`; others I |
| `started`, `finished`, `failed` | `Function`: string, `Job`: {`Control Job ID`, `Process Job ID`, `Lot ID`, `Wafer ID`, `Zone ID`} | HL state machines / core UI via cast VIs | `send_finished_event d`, `cast_started d` |
| `error` | `Source`: string, `Code`: integer, `Description`: string | core / state machines (alox_api_typedef_error users) | `send_error_event d` |
| `measure_mesa` | `Job` {`Control_Job_ID`,`Process_Job_ID`,`Wafer_ID`,`Lot_ID`,`Zone_ID`}, `Timestamp` {`Unix`}, `External_Diameter` {`Fitted`,`Unit`:"um"}, `Oxyd_Diameter` {`Fitted`,`Median`,`Average`,`Surface`,`Inscribed`,`Circumscribed`,`Circularity`,`Type_Measure_Rate` (e.g. "circumscribed"),`STDDEV FET/#`,`Unit`:"um"}, `Oxyd_Rate` {`Rate`,`Unit`:"um/min",`Remaining_time`:"HH:MM:SS"}, `Mesa_Positions` {`x`,`y`,`z`,`unit`:"mm"}, `Wavelength` {`wl`,`unit`:"nm"}; all values strings | mesa state machine via cast | `send_measure_mesa_event d`, `cast_measure_mesa d` |
| `measure_tilt` | `Job` {as above}, `Tilt` {`Angle`, `Unit`:"degrees"} | tilt state machine via cast | `cast_measure_tilt d`; key event name I |
| `measure_bowing` | `Job`, `Bowing` {`zwafer1`..`zwafer4`,`Zwafer5`,`bowinga`,`bowinga`,`bowingunit`:"um",`zwafer Unit`:"mm"}, `Parallelism` {`zchuck1`,`zchuck3`,`zchuck4`,`zchuck5`,`parallelismb`,`parallelismb`,`parallelismunit`:"um",`zchuck unit`:"mm"} | bowing state machine via cast | `cast_measure_bowing d`, typedef |
| `hardware_warning` / `hardware_error` | `source`: array of strings among `cameras_z_stage_module`, `camera_high_mag_status`, `camera_low_mag_status`, `z_stage_status` (camera/Z) or `xy_stages_module`, `x_stage_status`, `y_stage_status` (XY), monochromator equivalents (I); `count`: string (error counter). `hardware_error` when count reaches `hardware.<module>.maxfails` | module heartbeat service (I) | `send_camz_warning_event d`, `send_xy_warning_event d` |
| `hardware_reset` | `source`: `cameras_z_stage_module` (also default) \| `xy_stages_module` \| `monochromator_module` | module heartbeat / core (I) | `send_hardware_reset_event d`, `d1`, `d3` |
| `image` | `width`, `height`, `quality`: integers, `jpeg`: Base64 JPEG string | image stream FGV | `send_image_event d` |

### Appendix C. API typedefs (typedefs.md)

`alox_api_typedef_camera_status` (7 strings), `_camz_hardware_warning` (unused), `_error` {Source, Code I32,
Description}, `_failed` / `_finished` / `_started` {Function, Job{Control Job ID, Process Job ID, Lot ID, Wafer ID,
Zone ID}}, `_heartbeat` {Heartbeat}, `_heartbeat_frequency` {Frequency}, `_heartbeat_retries` {Retries}, `_image`
{width, height, quality I32, jpeg string}, `_measure_bowing`, `_measure_mesa`, `_measure_tilt` (see Appendix B),
`_status` {State}, `_tilt` {Tilt, Units}, `_version` {Major, Minor, Build, API, Release Date} (unused),
`_watchod_ui_visibility` {visible Boolean}, `_websocket_event` {Websocket Event Type enum U16: 0 heartbeat, 1 status,
2 measure tilt, 3 measure bowing, 4 measure mesa, 5 finished, 6 failed, 7 error, 8 started, 9 camz_hardware_warning,
10 xy_hardware_warning, 11 monochro_hardware_warning, 12 hardware_reset, 13 mesa_status, 14 tilt_status,
15 bowing_status, 16 image; Variant}, `_websocket_manager_method` {Init, Exit, Is Running ?, Client Connected ?,
Get Status}. Related core typedefs: `alox_typedef_notifiers_type` (same 17 values),
`alox_typedef_api_notifiers_manager_method` {Init, Get Notifier, Get All Notifiers, Exit},
`alox_typedef_websocket_image_stream_options` {Width, Height, Quality}, `alox_typedef_websocket_stream_manager_method`
{Init, Exit, Start Stream, Stop Stream, Is Started?, Stream Image}.

---

## Part 6: Other parts: thermal (Pyro/Bandit) measurement, licensing, simulator, tools, tests, R&D

Group `6_other`: 182 records, 172 distinct VI names (10 names occur twice: the `sources/dongle/lv15` copies of the
dongle VIs, two `SHA256.vi`, two `main.vi`, two `GetExampleModulePath.vi`/`ToPythonVersionString.vi`).
Images cited as `<vi> dN` (hidden frame N of the HTML export) or `<vi> d` (main diagram) / `p` (front panel).

### 0. Verdict per sub-folder (what the migration must keep)

| Sub-folder / family | Verdict | Reason |
|---|---|---|
| `Pyro/Bandit/alox_function_ll_thermal_measurement_state_machine.vi` + 9 helpers (`pyrometer_module`, `Get Checksum`, `get_Bandit_TempValue_Modbus`, `ConvertionTempVoltage`, `get_pyrometer_measures_positions_tablesorted_v2`, `update_pyrometer_measures_table_v5`, `MeasureArrayBundle`, `plot3Dscatter`, `tool_plot_3D_map`) | **Product code** (AloxVision.exe) | Static subVI of `alox_core_ui.vi`, opened by the "THERMAL MEASUREMENT" button (`alox_core_ui d397`) |
| Rest of `Pyro/` (`alox_bandit`, `alox_pyro`, `alox_MeasurementTools_bandit_pyro`, `alox_ChuckMeasurement`, `pyro_calibrate_xyz`, table v1–v4, 37 gen-1 cycle helpers, `framework.llb`) | **Dead / legacy** | Not in the project or not reached; earlier standalone versions of the thermal tool and leftovers of the older ALOX furnace software |
| `dongle/` (gen2 check, keys, SSD licence, licence info, timestamp) | **Product code** (boot licence check) | `alox_core_boot_cycle.vi` frame 7 |
| `dongle/` generators, demo dongle editor, gen1 check, `lv15/` copies, `SHA256.vi` | **Tool** (licensing back-office) / dead copies | Separate builds `Alox Demo Dongle`, `ALOXVISIONdonglegenerator` |
| `simulator/` | **Tool** (AloxSim.exe, demo/integration simulator) | Separate build; reuses core managers and the REST service |
| `LED_alox_control_tool(_vUB).vi` | **Tool** (bench diagnostics) | Builds acquireCameraTemperature(_usb) |
| `LYNRED/` | **Customer-specific tool** (OF44108) | Separate build/installer; unrelated to the Vision Core runtime |
| `PythonLV/` | **R&D / test** | Only the test build Python_fct_dectectcircle; the product does not call Python for vision |
| `rd/` | **R&D** | Not in any build |
| `tests/` | **Test** (manual test harnesses) | Not in any build, except `alox_investigation_process communication timestamps.vi` which is in the product (instrumentation) |
| `_Project Documentation Folder`, `generate project documentation.vi`, `print cld.vi`, `print conf.vi` | **Not product code** | Project-documentation/export utility |
| VIs with folder `?` in product folders (`*_old`, `*_proto`, `*_v2`, `..._ui_config_.vi`, etc.) | **Dead** | Not in the project, no caller, not in any build's "always included" list |

### 1. Purpose

This group holds everything outside the five main parts. Three pieces matter for the product:
(a) a **thermal map measurement** of the heated chuck/susceptor of the ALOX furnace: a temperature probe (an Optris
pyrometer on a serial port, or a kSA BandiT reading through Modbus TCP) is moved by the XY stages to points on concentric
circles, the temperature is sampled at each point, and a table with statistics is exported to CSV and plotted in 3D;
(b) the **licence check at boot**: AloxVision.exe refuses to boot without a valid WireFlow USB dongle (gen2 keys) or a
valid "virtual dongle" licence file bound to the PC;
(c) the **simulator** (AloxSim.exe), a reduced main application that serves the same REST/websocket API with simulated
camera images and simulated high-level functions.
Everything else is tooling (dongle programming, LED/temperature bench tool, a customer recorder for LYNRED), R&D,
manual tests, dead copies and a documentation generator.

### 2. Architecture

#### 2.1 Thermal measurement (product)

```
alox_core_ui.vi  event [66] "ThermalMeas": Value Change (d397)
   └─ if TRUE: log "Thermal measurement clicked", call (synchronously)
      alox_function_ll_thermal_measurement_state_machine.vi   (own front panel, flat sequence 0..5)
        0  global ThermalMeasurement := TRUE (d1); header "Projet ALOX - Bandit, October 2019, Version 3.0"
        1  read INI via alox_manager_properties (d10)
        2  init UI: positions, graph scales, table headers, enable controls (d15)
        3  three parallel loops (d):
             ┌ UI loop: event structure, 23 cases, timeout ≈100 ms
             ├ "EMERGENCY STOP XYZ" loop (polls XYZ_STOP, 100 ms)
             └ "PROC LOOP": measurement state machine, runs while PROC RUNNING
        4  write diameters/points/settings back to INI (d171)
        5  ThermalMeasurement := FALSE, FP.State Hidden, FP.Close (d176)
```

- **Lifecycle**. The VI is called as a plain subVI inside the core UI event case (d397). Its loops communicate through
  front-panel controls and local variables (START, STOP, EXIT, PROC RUNNING, MeasEnable, TOOL_CONNECTED), no queues.
  **(inferred)** The core UI event loop does not handle other UI events while the thermal window is open, since the call
  does not return until EXIT.
- **Hardware access**. XY moves go through the existing XY client: `alox_manager_xy` ("Move Absolute XY", "Get Position
  XY") after converting coordinates with `alox_manager_xy_coordinate_system` (from "Chuck" to "XY Stages", comment "Wafer ->
  XY stages coordinate system", d151). Z moves and the Z joystick go through `alox_manager_camera` (Z stage of the camera
  server; Z typedefs `typedef_ext_z_*`) **(from calls)**. The probe is accessed directly: `pyrometer_module.vi` (VISA
  serial) or `get_Bandit_TempValue_Modbus.vi` (Modbus TCP). `alox_manager_modules_heartbeat` "Get Report" is read in the
  timeout event (d21).
- **Tool selector** (`Selector tool` ring, 0 = BANDIT Ksa, 1 = PYROMETER; default PYROMETER, d10). The selector is made
  visible only if INI `hardware.bandit.enableBandit` is TRUE (d10). Selecting BANDIT disables the pyrometer controls,
  switches the tab and runs `ping <IP address>` with System Exec; exit code 0 sets TOOL_CONNECTED (d, case [16]).
  With PYROMETER, the timeout event calls `pyrometer_module` Init + "Get Obj Temp." and sets TOOL_CONNECTED when no error;
  START is enabled once connected; the live temperature indicator is refreshed in each timeout (d21).
- **UI events** (d, label sheet): [0] Timeout, [1] "# nbPOINTS"/"Diameters"/"ZERO POS.X"/"OffsetAngle" value change
  (recompute positions), [2] X_MOVE, [3] Y_MOVE, [4] Z_MOVE, [5] XYZ_JOYSTICK, [6] ZERO_SUSC ("SET DEVICE ORIGIN",
  sets the thermal origin **(inferred)**), [7] RHO, [8] THETA, [9] X_CTRL, [10] Y_CTRL, [11] START, [12] EXPORT, [13] STOP,
  [14] EXIT, [15] PLOT SETTINGS, [16] Selector tool, [17] SET EMISSIVITY, [18] SET TRANSMIS., [19] GET EMISSIVITY,
  [20] GET TRANSMIS., [21] LASER, [22] Refresh Button.
- **Measurement state machine** (PROC LOOP, d149–d159), once per point, in table order:

| State | Action (observed) | Next |
|---|---|---|
| START | PROGRESS Meas := 0 | MOVING |
| MOVING | point (X,Y) offset by ZERO POS, converted Chuck → XY Stages, `alox_manager_xy` Move Absolute XY | WAIT MOVE |
| WAIT MOVE | poll Get Position XY; reached when \|ΔX\| and \|ΔY\| ≤ 0.01 mm; after 30 000 ms re-issue the move | WAIT TEMP / MOVING / WAIT MOVE |
| WAIT TEMP | wait `hardware.bandit.wait` ms (default 2000; the frame comment still says 0.1 s) | MEASURING |
| MEASURING | `NB Meas Pts` samples, 50 ms apart: PYROMETER → `pyrometer_module` "Get Obj Temp."; BANDIT → `get_Bandit_TempValue_Modbus` + `ConvertionTempVoltage`; mean and standard deviation (d155, d156) | WRITING |
| WRITING | `update_pyrometer_measures_table_v5` fills the MEASURES table | END |
| END | PROGRESS Meas := 0, next point | START |

  After the last point (d161): PROGRESS := 100, buttons re-enabled, a file dialog proposes
  `Thermal_measurement_%y%m%d_%H%M%S.csv`, the table is written with Write Delimited Spreadsheet, and if the table has
  more than 3 rows `tool_plot_3D_map.vi` launches the external Python script with the CSV path (System Exec, no wait).
- **Measurement points** (`get_pyrometer_measures_positions_tablesorted_v2 d`): for circle i with diameter Dᵢ and nᵢ
  points, r = Dᵢ/2, angle_j = j·360/nᵢ + OffsetAngle (deg), X = r·cos, Y = r·sin, plus the susceptor position; the list is
  sorted on one column (sorting via NI `vi.lib/Array` malleable VIs; the GUID-named callees in the call graph are their
  instances). Outputs XY and polar (rho, theta) positions. Up to 9 circles in INI, 8 on the panel.
- **Result table** (`p`): columns X, Y, Rho, Teta, Temp. (°C), Std Dev Temp; rows Point 1..N, then Global (Mean, Median,
  Std.Dev, Max, Min, Delta Max-Min), then per circle (Mean, Max, Min) **(computation from the v5 name and panel; not
  opened)**. A 3D scatter (`plot3Dscatter`, NI Math Plots) shows the map in the panel.

**What it measures.** The temperature distribution of the heated wafer holder ("susceptor", "ZERO_SUSC", "Thermal
Origin"); it is a thermal-uniformity map, not a chuck parallelism measure. The chuck parallelism/flatness values
(`[hardware.chuck]` dz1–dz5, maxparallelism, bowchuckA/B, chuck calibration file) belong to the **bowing measurement**
(`alox_function_hl_bowing_measurement_bundle_chuck_properties.vi`, typedef `alox_typedef_bowing_measurement_chuck_properties`,
see part 3), not to the Pyro code. The changelog, translations and error list do not mention the thermal function.

**Probe protocols.**
- Optris pyrometer (`pyrometer_module.vi`, FGV holding a VISA session; methods `Optris - Method.ctl`: Init, Exit, Get
  Firmware Rev, Get Hardware Rev, Get Serial, Get Sensor Infos, Get Obj Temp., Get Emissivity, Set Emissitivity, Get Head
  Temp., Get Spot Illumination, Set Spot Illumination, Get Box Temp, Get Actual Temp, Set/Get Transmissivity).
  Init: `COM<hardware.pyro.comport>`, `hardware.pyro.baudrate` (default 115200), 8N1, no flow control, timeout 10 s
  (d1). Binary commands (d5, d9, d11, d22): Get Obj Temp. writes 0x01 (+ XOR checksum byte from `Get Checksum.vi`), reads 2
  bytes, T = (b0·256 + b1 − 1000)/10 °C, NaN on error; Get Emissivity 0x04 → 2 bytes /1000; Set Emissivity 0x84 + U16
  (ε·1000) + checksum; Set Spot Illumination (laser) 0xA5 + 0x01/0x00 + checksum. This matches the Optris CT binary
  protocol **(inferred)**.
- kSA BandiT (`get_Bandit_TempValue_Modbus d`): creates a Modbus TCP master to `hardware.bandit.ipadress`, port 502,
  waits 1000 ms, reads N registers at an address built from `hardware.bandit.startregister` and a constant 12288, takes the
  first value as millivolts, closes. `ConvertionTempVoltage d`: V = mV/1000, slope = (Tmax−Tmin)/(Vmax−Vmin) (0 if
  degenerate), T = linear map of V onto [minTemperature, maxTemperature] (defaults 0–5 V → 300–500 °C). **(inferred)** The
  Modbus device is an analog-input module or the BandiT analog output; the export does not say which.

#### 2.2 Licensing (product + tools)

```
alox_core_boot_cycle.vi frame 7 "Verify valid dongle presence" (d17), executed when a boolean is TRUE
(probably the global "Use Dongle", see alox_about_ui d):
  ├─ WireFlow: FindDonglesInSystem → for each dongle alox_gen2_dongle_check.vi until "All OK?"
  │            ("Check at least one dongle is a ALOX2 one") → global Dongle Infos
  └─ Virtual dongle: first file of folder <application dir>\RSVDALOXTEC → alox_ssd__serial_key_unlocking_dongle.vi
               → valid? + remaining days (I32)
  valid = WireFlow OK OR (virtual valid AND remaining days > 0)
  else: system status error 1513/1514/1515 "System cannot boot (no / invalid / expired dongle)" (system_en.lang)
```

- **Hardware dongle** (`alox_gen2_dongle_check d`): WireFlow WF-SecuritySuite USB dongle (serial "" = first one). Init;
  CheckCryptoKey for three product keys from `alox_gen2_keys.vi` (Key 1: read all data fields, Key 2: write DF 1, Key 3:
  write DF 2 & 3); Data Field 1 = expiry date (compared with now; an all-zero field means no expiry **(inferred)**); Data
  Field 2 = last execution date (must be older than now: clock-rollback check), rewritten with the current time when
  valid; Data Field 3 = "Licence Extra Infos" (≤32 bytes). Output `alox_dongle_infos.ctl` (All OK?, Serial Number,
  Expiration Date, Last Execution Date, Licence Extra Infos, Dongle Detected, Key1/2/3 OK, Data1/2 read OK, Time not
  expired, Time not resetted). Dates are encoded/decoded by `alox_timestamp_convert.vi`. Gen1 (`alox_gen1_*`) is the same
  for the "ALOX GEN1" product keys and is only used by the demo-dongle editor.
- **Virtual dongle / SSD licence** (`alox_ssd__serial_key_unlocking_dongle d`): reads the licence file (binary,
  little-endian), Base64-decodes it twice, gets the volume serial of drive D: from `cmd /c vol d:`, the NI Vision run-time
  serial number and the NI License Manager computer ID from `alox_licence_info.vi` (registry
  `HKLM\SOFTWARE\National Instruments\IMAQ Vision\…` values "Run-time SerialNo"/"RegisteredOwner"; computer ID from
  `generateComputerId.exe` in the License Manager `ClientPath`), then cuts the decoded string at fixed marker strings and
  compares each part (SSD serial 9 chars, computer ID 20 chars, NI licence 9 chars). An optional timeout date gives
  "Remaining day before expired" = (timeout − now)/86400. The literal markers are visible in that diagram and in
  `alox_dongle_generation d`; they are part of the licence secret and are not copied here.
- **What is locked**: boot only. No per-feature gating was found; `alox_about_ui d` shows "Dongle Detected" (global Use
  Dongle), "Demo Version" (expiry date set) and the expiry date.
- **Back-office tools**: `alox_dongle_generation.vi` / `_v2.vi` (builds ALOXVISIONdonglegenerator and Alox Demo Dongle)
  run on the target PC, collect the same three identifiers, build the marker string with an optional timeout, Base64 it
  twice and write `GENERATED DONGLE ALOXTEC\PDPROTD[_timeout_…].licalox` (frame 4). They are gated by a hard-coded
  password; v1 and v2 are pixel-identical except for that password constant (d2). `alox_demo_dongle_ui.vi` ("ALOX demo
  dongle editor tool", `p`) programs a WireFlow dongle (product type GEN1/GEN2, time-limited or not, expiry date into DF1,
  current date into DF2, extra info into DF3, Commit/Query) after the AET master/product keys were set with WireFlow's
  own software.

#### 2.3 Simulator (AloxSim.exe)

`alox_core_simulator_ui.vi` ("ALOX VISION SIMULATOR Version 0.2, 11/01/2018", `p`) is a reduced copy of the core: a
producer loop (event structure: Timeout refreshes HL function status/state, WS Start/Stop, Exit, Overlays, Output Image
Type, Heartbeat Frequency, Set Status, Status ID, version fields) and a consumer loop on `alox_manager_process_queue`
(cases Init, Exit, Measure Mesa, Measure Tilt, Measure Bowing, Stop, Load Profile, Profiles Folder Changed, Get Image),
plus the websocket server (`alox_api_manager_websocket_server`), heartbeat, log, language, properties, system status and
API notifier managers (d, label sheet). The build adds the `alox` web service (REST, HTTP port 8002), the websocket and
heartbeat services and the three HL state machines with their `_simulation` variants (lvproj AloxSim spec; see part 3
§3.14). Simulated camera (`alox_simulator_manager_camera(_simulation).vi`, FGV, methods Create/Dispose/Get Image/Set
Output Type, output `Clock` or `Vcsel`): Create loads every `*.png` of `<exe dir>\alox_simulation` into IMAQ images and a
2448×1536 "clock" image; Get Image returns either the clock image with a date/time text or the next VCSEL image, changed
every 1 s (d3, d7). The two manager VIs are identical apart from their typedef. `aloxsim_readme.txt`: REST
`http://localhost:8002/alox/v1/request`, websocket `ws://localhost:6123`; `aloxsim_changelog.txt` v0.1–v0.3 lists the
routes it serves.

#### 2.4 Other tools

- **LED / camera temperature tool** (`LED_alox_control_tool.vi`, build acquireCameraTemperature; `_vUB` for USB cameras,
  build acquireCameraTemperature_usb): opens the HighMag and LowMag IMAQdx cameras in Controller mode, every 1 s reads
  `CameraAttributes::DeviceControl::DeviceTemperature` of both and plots them, and drives the LED through
  `DigitalIOControl` (LineSelector 1, LineSource 1, UserOutputSelector 0, UserOutputValue = LED/Blink) (`d`, `p`).
- **LYNRED recorder** (`LYNRED/main.vi`, build OF44108_exe → `vision_OF44108.exe`, installer `main_OF44108.exe`, v1.0.3,
  Aloxtec): IMAQdx grab (RGB U32) in an event-timeout loop; while recording saves `img_<%y%m%d_%H%M%S>.png` into a job
  folder (`d`). `vc_text/LYNRED/generate_video.py` (OpenCV, MJPG, 10 fps) turns a folder of images into an AVI; main.vi
  uses System Exec **(inferred: to run that script)**. Note: the second `main.vi` record (PI Z stage, page 0504) is an R&D
  VI under `rd/`, not LYNRED; the index merged the two (so the overview's "PI GCS driver" for OF44108 is wrong).
- **Python correlation test** (build Python_fct_dectectcircle, top VI `test_py_corr_batch.vi`): grabs camera images,
  saves them and times an external `corr_fct.py` (OpenCV `matchTemplate`, prints X, Y) via System Exec; outputs the mean
  duration (`d`). Despite the build name it is template matching, not circle detection.

#### 2.5 R&D

- `rd/scan_wl_exposure` (`scan_wl_main`, `scan_wl_function`, `scan_wl_function_state_diagram`, `scan_wl_manager_events`):
  standalone bench that scans wavelengths WL1..WL2 by ΔWL with the monochromator driver (`spectral_products_*`) and the PI
  Z stage directly, autofocuses with `alox_vision_measure_focus_fft` and records data per wavelength (states
  `scan_wl_function_state.ctl`: Start, Set WL, Wait WL, Set High Expo, …, AF Move Zmin … AF Error, Set Wanted Expo,
  Acquire, Finished?, Finished, Error, Stopped). Origin of `tools_alox_interpolate_data_from_wavelength` and the
  wavelength/exposure tables.
- `rd/scan_elasto` (`scan_elasto_*`, typedefs copied from scan_wl in "New folder") and the PI-stage `main.vi` (page 0504):
  logs timestamped PI Z positions to `.txt` during mechanical tests (data files with Instron/MTS triangle profiles and a
  Dewesoft export, July 2024).
- `rd/test_MeasStightLine`: Vision Assistant–generated C (`Main.c`, `ImageProcessing.c`) and LabVIEW variants
  (`measStraightLine*`); the last one calls the product tools `tool_alox_measLine_ROI_builder` and
  `tool_alox_measLine_distance_computation` (now used by measure mesa). `RD_Mesa_mesure.vi`: mesa measurement experiment.

### 3. Components

| VI | Role (one line) | Used by (apps / callers) |
|---|---|---|
| **Thermal measurement (product)** | | |
| `alox_function_ll_thermal_measurement_state_machine.vi` | Thermal map tool: UI + measurement state machine (§2.1) | Alox / `alox_core_ui` event [66] |
| `pyrometer_module.vi` | Optris pyrometer FGV over VISA serial | Alox / thermal SM (also dead `alox_MeasurementTools_bandit_pyro`) |
| `Get Checksum.vi` | XOR checksum of a byte array | Alox / `pyrometer_module` |
| `get_Bandit_TempValue_Modbus.vi` | One Modbus TCP register read (mV) from the BandiT side | Alox / thermal SM (+ dead `alox_bandit`, `alox_MeasurementTools_bandit_pyro`) |
| `ConvertionTempVoltage.vi` | Linear mV → °C conversion from voltage/temperature scale arrays | Alox / same |
| `get_pyrometer_measures_positions_tablesorted_v2.vi` | XY and polar point list on concentric circles, sorted | Alox / thermal SM |
| `update_pyrometer_measures_table_v5.vi` | Writes one point into the result table and updates statistics | Alox / thermal SM |
| `MeasureArrayBundle.vi` | Bundles X/Y/temperature arrays (from name/calls) | Alox / thermal SM |
| `plot3Dscatter.vi` | 3D scatter plot helper (NI Math Plots) | Alox / thermal SM (+ dead `alox_bandit`) |
| `tool_plot_3D_map.vi` | System Exec `<script path> <csv path>` (Python 3D map), no wait | Alox / thermal SM |
| **Pyro legacy (dead)** | | |
| `alox_bandit.vi` | Earlier Bandit-only version of the thermal tool using core managers (incl. `alox_manager_xy_map`) | none |
| `alox_MeasurementTools_bandit_pyro.vi` | Earlier standalone Bandit + pyrometer tool on the Pyro framework (own INI) | none |
| `alox_pyro.vi` | Earlier pyrometer-only tool on the Pyro framework | none |
| `alox_ChuckMeasurement.vi` | Unfinished copy: motion subVIs missing (greyed "?" placeholders), "Measure Temperature" state empty (d87–d100) | none |
| `pyro_calibrate_xyz.vi` | XYZ calibration helper of the old tools (calls only Clear Errors) | dead tools above |
| `update_pyrometer_measures_table.vi`, `update_pyrometer_measures_table_v2.vi`, `update_pyrometer_measures_table_v3.vi`, `update_pyrometer_measures_table_v4.vi` | Older table writers (v3 used by dead tools, others by none) | none in builds |
| `get_pyrometer_measures_positions_table.vi`, `get_Chuck_measures_positions_table.vi` | Older position generators | dead tools / none |
| `Checksum OK.vi`, `F 1.vi` | Leftovers (checksum test; `F 1` uses vi.lib/NI) | none |
| Gen-1 cycle helpers in `Pyro/tools` (37): `check_conds_cycle`, `check_large_autofocus`, `circle_to_roi_descriptor`, `roi_to_circle_descriptor`, `clear_current_cycle_infos`, `load_current_cycle_infos`, `save_current_cycle_info`, `save_batch_info`, `save_bowing_info`, `save_end_cycle_report`, `format_end_cycle_measures_to_list`, `copy_cycle_file_from_plc` (FTP), `convert_ellipse_roi_to_annulus`, `convert_pixels_to_um`, `convert_um_to_pixels`, `convert_xy_coordinates`, `expand_fit_line`, `filter_and_fit_diameters_mesures`, `filter_invalid_chart_measures`, `filter_invalid_measures`, `find_zero_crossings`, `fit_diameters_mesures_outliers`, `get_cam_exposure_from_wavelength`, `get_data_folder`, `get_infos_overlays_font_size`, `get_polar_positions`, `get_same_line_point_at_distance`, `get_tilted_coordinates`, `get_wafer_diagonals_coordinates`, `get_wafer_tilt_angle`, `image_refresh`, `schedule_image_refresh`, `record_wavelength_data`, `reload_wavelength_exposure_data`, `replace_zero_by_nan`, `round_u32_to_lower_odd_value`, `round_u32_to_upper_odd_value` | Cycle/recipe/report utilities of the older ALOX furnace software (from names/calls) | only each other / dead tools |
| `Pyro/framework.llb` (10): `application_filepath`, `camera_module` (IMAQdx), `display_module`, `error_module`, `images_module`, `language_module`, `log_module`, `properties_module` (INI), `ui_error_dialog`, `user_module` (users/rights, actions such as recipes, vacuum, vent, cycles) | FGV framework of the older software (exported from `D:/ALOX/ALOX_export/framework`); enums in typedefs.md "Pyro/framework.llb" | dead Pyro tools only |
| **Licensing** | | |
| `alox_gen2_dongle_check.vi` | WireFlow dongle check with ALOX2 keys, expiry and clock-rollback check | Alox / `alox_core_boot_cycle`, `alox_demo_dongle_ui` |
| `alox_gen2_keys.vi` | Constant crypto keys of the gen2 product (secret) | Alox / gen2 check, demo editor |
| `alox_ssd__serial_key_unlocking_dongle.vi` | Validates the virtual-dongle licence file against PC identifiers | Alox, both generator builds / boot cycle, generators |
| `alox_licence_info.vi` | Reads NI Vision run-time serial and NI computer ID (registry + generateComputerId.exe) | Alox and generators |
| `alox_timestamp_convert.vi` | Date ↔ dongle data-field encoding | Alox / dongle checks, demo editor |
| `alox_gen1_dongle_check.vi`, `alox_gen1_keys.vi` | Same for the gen1 product | demo editor only |
| `alox_demo_dongle_ui.vi` | "ALOX demo dongle editor tool": programs WireFlow dongles | listed in Alox Demo Dongle build (not as startup VI) |
| `alox_dongle_generation.vi` | Virtual-dongle licence file generator (password protected) | ALOXVISIONdonglegenerator (startup) |
| `alox_dongle_generation_v2.vi` | Same, different password | Alox Demo Dongle (startup) |
| `sources/dongle/lv15/*` (6 duplicate records: demo UI, gen1/gen2 check and keys, timestamp) | LabVIEW 2015 copies | none |
| `SHA256.vi` (2 records) | WireFlow toolkit SHA-256 VIs exported by accident | none |
| **Simulator** | | |
| `alox_core_simulator_ui.vi` | AloxSim top-level (§2.3) | AloxSim |
| `alox_simulator_manager_camera.vi`, `alox_simulator_manager_camera_simulation.vi` | Simulated camera image FGV (Clock / Vcsel) | AloxSim / simulator UI |
| `alox_core_simulator_global_variables.vi` | Unused simulator global (contains dongle infos) | none |
| **Tools** | | |
| `LED_alox_control_tool.vi`, `LED_alox_control_tool_vUB.vi` | Camera temperature logging + LED control (GigE / USB) | acquireCameraTemperature, acquireCameraTemperature_usb |
| `main.vi` (`LYNRED/`, page 0427) | LYNRED image recorder | OF44108_exe |
| `tools_lynred_data_filename.vi` | Builds the LYNRED data file/folder name | OF44108_exe / `main.vi`, `Untitled 1.vi` |
| `save AVI.vi`, `Untitled 1.vi` | LYNRED scratch VIs (AVI save test, file-name test) | none |
| `test_py_corr_batch.vi` | Times an OpenCV template-matching script | Python_fct_dectectcircle |
| **PythonLV (R&D/test)** | | |
| `alox_function_ll_detect_circle_PythonNode_getcircle.vi`, `alox_function_ll_detect_circle_PythonNode_test.vi`, `alox_function_ll_detect_circle_PythonNode_test_lib.vi`, `___.vi` | Hough circle detection through the LabVIEW Python Node (`ExamplePythonModule_2.py`, Python 2.7/3.6) | none |
| `GetExampleModulePath.vi`, `ToPythonVersionString.vi` (2 records each) | Helpers of the NI Python Node example | PythonNode VIs |
| `test_py_corr.vi`, `test_circle_by_prompt.vi`, `test_py_lv.vi`, `HoughCircles_test_LV2010.vi` | Python / Hough experiments | none |
| **R&D** | | |
| `scan_wl_main.vi`, `scan_wl_function.vi`, `scan_wl_function_state_diagram.vi`, `scan_wl_manager_events.vi` | Wavelength × focus scan bench (§2.5) | none |
| `scan_elasto_main.vi`, `scan_elasto_function.vi`, `scan_elasto_function_state_diagram.vi`, `scan_elasto_manager_events.vi`, `main.vi` (page 0504) | Z-stage position logging for mechanical tests | none |
| `RD_Mesa_mesure.vi` | Mesa measurement experiment | none |
| `measStraightLine.vi`, `measStraightLine_generated.vi`, `measStraightLine_generated_edgeAngle.vi`, `measStraightLine_generated_edgedetector.vi`, `measStraightLine_generated_edgedetector_rotarect.vi` | Straight-line edge measurement prototypes (Vision Assistant) | none |
| **Tests (`sources/tests`, manual harnesses)** | | |
| `alox_investigation_process communication timestamps.vi` | Computes command/response timing from a TCP response packet (its open-panel code is in a Disabled structure) | Alox, servers' clients / `alox_camera_client`, `alox_wl_client`, `alox_xy_client` |
| `alox_test_unit_camera_client.vi`, `alox_test_unit_wl_client.vi` | Drive the 14 `alox_camera_command_*` / 10 `alox_wl_command_*` client VIs by hand | none |
| `alox_test_unit_autofocus.vi`, `alox_test_unit_kill_process.vi`, `alox_test_unit_mesa_measurement_line_fit.vi`, `alox_test_unit_profiles.vi`, `alox_test_unit_rgb_conversion.vi`, `alox_test_unit_save_image_custom_data.vi` | Unit-style manual tests of the named feature | none |
| `alox_test_camera.vi`, `alox_test_camera_exposure.vi`, `alox test low grab histogram.vi` | IMAQdx camera tests | none |
| `alox_test_xy_stages.vi`, `alox_test_xy_stages_joystick.vi`, `alox_test_xy_stages_reinit.vi`, `alox_test_z_stage.vi`, `alox_test_z_stage_joystick.vi`, `troobleshoot_axis_pi.vi` (calls `alox_z_check_axis_reference`) | PI GCS stage tests / troubleshooting | none |
| `alox_test_monochromator.vi` | Monochromator driver test | none |
| `alox_test_dead_band.vi`, `alox_test_fft_1d_refocus_oxyd.vi`, `alox_test_guids.vi`, `alox_test_image_flip.vi`, `alox_test_learn_geometric_match.vi`, `alox_test_overlays_lock.vi`, `alox_test_rw_expo_table_profile.vi`, `alox_test_thick_circle.vi`, `test_dc_pattern_recognition.vi`, `alox_edit_model_mask.vi`, `alox_investigation_flattened image FGV.vi` | Algorithm / helper experiments | none |
| `Geometric Matching.vi` | Copy of an NI example from a OneDrive folder | none |
| **Orphans in product folders (folder `?`, dead)** | | |
| `alox_manager_hl_functions_condition.vi` | Old variant of `alox_manager_functions_condition` | none |
| `alox_profiles_edition_ui.vi` | Old profile editor | none |
| `alox_tools_abort_all_running_vis.vi`, `tools__module_error.vi`, `tools_windows_public_documents_folder.vi` | Unused tool variants | none |
| `alox_tools_convert_calib_to_imaq.vi`, `alox_tools_set_overlay.vi` | Unused core tools (replaced by `..._overlays_states`) | none |
| `alox_wl_auto_exposures_table_ui_v2.vi` | Unused v2 of the auto-exposure table dialog | none |
| `alox_camera_server_extract_zoom.vi` | Camera-server experiment (see camera server docs) | none |
| `spectral_products_query_old.vi` | Old monochromator query | none |
| `alox_function_ll_autofocus_ui.vi` | Old autofocus panel | none |
| `alox_function_ll_detect_circle_state_machine_old.vi`, `alox_function_ll_detect_circle_state_machine_proto.vi` | Old/prototype detect-circle workers (proto references `D:/AET Group`) | none |
| `alox_function_ll_search_wavelength_ui_config_.vi` | Backup copy (trailing underscore) of the wavelength panel | none |
| **Documentation tool (not product code)** | | |
| `generate project documentation.vi`, `recursive Documentation.vi`, `HTMLFormat.vi`, `Class Font.vi`, `Library Font.vi`, `Project Font.vi`, `Set Folder Font.vi`, `Get Item Path.vi`, `String Equals Insensitive.vi` | Walks the project tree and writes an HTML report (NI Report) | none |
| `print cld.vi`, `print conf.vi` | Print/export utilities | none |

### 4. Data and configuration

- **alox.ini `[hardware.pyro]`**: `comport_I32` (7 → "COM7"), `baudrate_I32` (115200).
- **alox.ini `[hardware.bandit]`** (read in d10, written back in d171): `ipadress_STR` (" 192.168.1.2", leading space in
  the default file), `minVoltage_I32`/`maxVoltage_I32` (0/5), `minTemperature_I32`/`maxTemperature_I32` (300/500),
  `startregister_I32` (10), `enableBandit_BOOL`, `diam1..9_DBL` (mm), `nbpoints1..9` (U32/I32), `numberMeasure` (samples
  per point), `offsetAngle` (deg), `wait_I32` (ms after each move), `ScriptPythonPath` (`py "D:/ALOX/software/Device
  Software/Python/readdata_arg.py" --path `). Also `general.wafersize`. `[hardware.chuck]` is bowing (part 3), not thermal.
- **Output**: CSV chosen in a file dialog, default `Thermal_measurement_<yymmdd_HHMMSS>.csv`; the external Python script
  reads it for the 3D map.
- **Licence**: virtual licence file = first file in `<application dir>\RSVDALOXTEC\` (generated as `*.licalox`, double
  Base64); WireFlow dongle data fields DF1 (expiry), DF2 (last run, rewritten at each boot), DF3 (extra info); registry
  `HKLM\SOFTWARE\National Instruments\IMAQ Vision\…`, `HKLM\SOFTWARE\National Instruments\License Manager` (ClientPath).
  Global `Dongle Infos` and `Use Dongle` in `alox_core_global_variables`. `Alox No Dongle.ini` suggests a former
  no-dongle build **(inferred)**.
- **Simulator**: `<exe dir>\alox_simulation\*.png`; INI `[simulation.mesa|tilt|bowing]`, `[webservice] port`, `[websocket]
  port 6123`.
- **LYNRED**: `img_<timestamp>.png` per job folder; AVI via `generate_video.py`.
- **Default INI quirks** (`alox_default.ini`): duplicate keys with different type suffixes (`numberMeasure_U32` and
  `_I32`, `offsetAngle_U32`/`_I32`, `nbpoints9` four times, `ScriptPythonPath_PATH`/`_STR`) and several empty `[]`
  sections holding `bowchuckA/B`.

### 5. External dependencies

- NI-VISA serial (Optris pyrometer, RS-232/USB-serial at 115200 Bd).
- NI Modbus Library (`vi.lib/NI/Modbus Library`, TCP master, port 502) and Windows `ping` (System Exec) for the BandiT.
- NI Math Plots 3D scatter, Analysis (std dev), point-by-point polar conversion, `vi.lib/Array` malleable VIs.
- Python: the thermal 3D map needs the `py` launcher and an external `readdata_arg.py` (not in the export); the
  project carries `python-3.8.1-amd64.exe`, `tool_plot_3D_map` holds an unused constant `C:\Program Files\Python38\python.exe`.
  PythonLV experiments: OpenCV, NumPy, LabVIEW Python Node; `dll/hough_circle_detection_DLL.cpp` (OpenCV HoughCircles DLL,
  not called by any VI in this group).
- WireFlow WF-SecuritySuite (USB dongle driver, CheckCryptoKey, Read/WriteDataField, FindDonglesInSystem, SHA256),
  Windows registry VIs, `cmd /c vol d:`, NI License Manager `generateComputerId.exe`, OpenG string/file VIs.
- IMAQdx (LED tool, LYNRED, Python test), PI GCS driver (R&D, stage tests), monochromator driver `spectral_products_*`.
- Hardware: Optris pyrometer (CT-type binary protocol, laser sighting), kSA BandiT + Modbus TCP device, PI XY/Z stages
  through the existing servers, WireFlow USB dongle.

### 6. Interfaces with the other parts

- Core UI (part 1): button `ThermalMeas` → thermal SM (d397); global `ThermalMeasurement` set TRUE/FALSE around the
  tool; boot cycle frame 7 → licence check, globals `Dongle Infos`, `Use Dongle`; `alox_about_ui` displays them;
  system status errors 1513–1515.
- Managers used by the thermal tool: `alox_manager_properties` (INI), `alox_manager_log`, `alox_manager_xy`,
  `alox_manager_xy_coordinate_system` ("Chuck" and "XY Stages" systems), `alox_manager_camera` (Z), 
  `alox_manager_modules_heartbeat`. It does not use the process queue, notifiers, REST or websocket: the thermal map is
  **not** available through the API.
- Hardware servers (part 2): XY moves through AloxXYServer, Z through AloxCamServer.
- Simulator: reuses the core managers, the HL functions and their `_simulation` workers (part 3 §3.14) and the REST
  service/websocket (part 5).
- Tools: `tools_convert_to/from_base64` (licence), `tools_application_executable_folder` (simulator).
- Instrumentation: `alox_investigation_process communication timestamps.vi` is called by the three TCP clients.

### 7. Unused or legacy code

Everything in §3 marked "none" is not reached by any build and is not in any build's "always included" list (checked
against the Alox and AloxSim build specifications); none of it is started dynamically. In particular: the four earlier
Pyro tools (`alox_bandit`, `alox_pyro`, `alox_MeasurementTools_bandit_pyro`, `alox_ChuckMeasurement` – the last one is
broken), table writers v1–v4, the 37 gen-1 cycle helpers and `framework.llb`; the `lv15` dongle copies and `SHA256.vi`;
`alox_core_simulator_global_variables.vi`; all PythonLV VIs except the separate test build; all `rd/` and `tests/` VIs
(except the investigation VI); the orphans `*_old`, `*_proto`, `*_v2`, `..._ui_config_.vi`; the documentation tool.
A search of all exported diagrams for the thermal SM icon found it only in `alox_core_ui d397`.

### 8. Notes for the migration

- **Keep the thermal map** if the furnace still needs it: it is reachable from the shipped UI. It is self-contained
  (one window, local-variable loops) and is a good candidate for a separate module behind the XY/Z clients.
- The thermal tool blocks the core UI event loop while open (synchronous subVI call in an event case) **(inferred)**;
  decide whether the rewrite keeps it modal and whether other functions must be locked out (`ThermalMeasurement`
  global; its readers were not identified).
- WAIT MOVE retries the move every 30 s forever if the position is never reached; no overall timeout, no error.
- A BandiT sample opens a Modbus TCP connection, waits 1 s, reads, closes: with 5 samples per point a point costs >5 s
  plus the 2 s settle time. The register address uses a hard-coded 12288 offset; the IP default has a leading space.
- Pyrometer temperature errors become NaN and enter the mean/std dev unfiltered **(inferred)**.
- On exit the tool writes `diam9` and `nbpoints9` from constants 0, not from the panel (d171).
- The 3D plot depends on an external Python script and the `py` launcher that are not in the sources; the CSV path is
  appended to an INI string without quoting.
- Licence: the rewrite must decide whether to keep the WireFlow dongle (needs the WireFlow driver and the secret product
  keys in `alox_gen2_keys.vi`) and/or the virtual licence (needs the exact marker format, double Base64, D: volume serial,
  NI Vision serial and NI computer ID; a new runtime without NI software has no NI serial/computer ID, so existing licence
  files cannot be validated as-is). The DF2 rewrite at every boot is an anti-rollback measure to keep.
- Secrets live in diagrams (gen1/gen2 WireFlow keys, generator passwords, licence markers); `alox_default.ini` also
  stores the engineer password as Base64. Do not copy them into new source code; move them to a secure store.
- `AloxDemoDongle.exe` starts `alox_dongle_generation_v2.vi` (TopLevel) although its name and product strings say "demo
  dongle"; the editor `alox_demo_dongle_ui.vi` is only listed as a source.
- The simulator duplicates the core main loop (version "0.2" 2018) and will drift from the product; a rewrite can instead
  run the real application against simulated device servers.
- `corr_fct.py` prints `Y = max_loc[0] + h/2` (uses the X index for Y) – only relevant if the experiment is revived.

### 9. Open questions

1. Is the thermal map still used on delivered furnaces, and with which probe (Optris pyrometer, kSA BandiT, or both)?
   Which Modbus device sits at 192.168.1.2 and what does register `startregister + 12288` hold?
2. Where is `readdata_arg.py` (3D map) and must it be kept?
3. Which VIs read the `ThermalMeasurement` global, and should other functions/API calls be refused during a thermal map?
4. What sets the global `Use Dongle` (boot frame 7 selector)? Is there still a no-dongle build?
5. Are virtual-dongle licence files (`RSVDALOXTEC` folder) deployed at customers, and must the new software accept
   existing licence files and dongles?
6. Is AloxSim still delivered to integrators (supervisor/SECS-GEM testing), or can it be dropped?
7. Are LYNRED OF44108, the LED/temperature tool and the R&D benches (scan_wl, scan_elasto) still maintained?

