# Vision Core migration plan

## Summary

This plan turns the [map](map.md) into a route for rewriting the whole ALOX Vision Core application (V1.2.6.2).

- **Language:** Python 3.12+ (3.13 recommended; PySide6 6.12 crashes on 3.11). Images: NumPy, OpenCV, SciPy. REST and websocket: FastAPI (uvicorn) and a websocket server.
  Hardware: pyserial (PI GCS, monochromator, pyrometer), Modbus TCP, a GenICam or vendor SDK for the cameras. One code base,
  packaged as a Windows application.
- **Architecture: one integrated application** (owner's decision, 25/11/2026). The three hardware server programs
  (AloxCamServer, AloxXYServer, AloxWLServer) are not migrated as servers. Cameras, Z stage, XY stages, monochromator and the
  thermal probes become **device modules inside the application**, each running in its own thread. The TCP protocol between
  programs, the process management (`cmd START`, `taskkill`), the inter-process heartbeats and the watchdog disappear.
- **What is reused from the camera server work:** the Z-stage driver (PI GCS over RS-232) with its fake controller and check
  tool, the simulators, the INI reader, the event log, the focus metrics, and the specification of the cameras and of the
  autofocus/refocus (`docs/05`–`08`). The TCP server and the LabVIEW wire format stay in the repository as reference and test
  tooling; the NI image serialisation (the hardest open point of the camera server) is **no longer needed**.
- **Order:** device layer first (Z, XY, monochromator, cameras), each with a simulator and a check tool run on the real
  machine; then the core and its API, headless; then the algorithms and the measurement functions; then the operator UI;
  then side-by-side test sessions on a machine, a parallel run, and the cutover machine by machine.
- **Compatibility:** the supervisor contract (REST port from `alox.ini [webservice] port`, 8001 on the machines; websocket 6123) and the files on disk (INI files, zip recipes,
  folders, job history, image files) stay compatible, except for the defects the owner decided to fix
  ([map §3.1](map.md#31-owners-answers-on-the-defects-25112026)). File compatibility makes rollback simple: start the
  LabVIEW version again.
- **Main risks:** a single cutover for the whole application (no step where Python and LabVIEW programs share production),
  the NI Vision algorithms without a direct open-source equivalent (geometric pattern matching), the size of the operator UI
  (43 panels), and behaviours not yet read in detail. Section 8 gives the mitigations.

## 1. Starting point

### 1.1 What exists and what becomes of it

| LabVIEW program | ALOX VIs | In the new application |
|---|---|---|
| AloxVision.exe (core, REST, websocket, functions, UI) | 436 + 82 REST VIs | Rewritten: core, API, functions, UI (map Parts 1, 3, 4, 5) |
| AloxCamServer (2 cameras, Z stage, LEDs, autofocus/refocus) | 30 | **Integrated** as device modules (camera, Z stage) and focus jobs. Python port phases 1–2 done (protocol, real Z stage): the drivers and simulators are reused; the server itself is not finished as a server |
| AloxXYServer (2 PI Mercury axes, RS-232 daisy chain) | 17 | **Integrated** as the XY device module (map Part 2) |
| AloxWLServer (Spectral Products monochromator, serial) | 19 | **Integrated** as the monochromator device module (map Part 2) |
| Hardware managers, TCP clients, server process management, heartbeats (inside AloxVision) | about 150 | **Removed**; replaced by the device layer (section 3.3) |
| Thermal measurement (inside AloxVision) | about 20 | Rewritten; its probes (Optris pyrometer, kSA BandiT) become device modules |
| AloxSim, AloxViewer, licence tools, LED tool, LYNRED build | small | Scope to confirm (map Part 6) |
| AloxWatchdog | 6 | **Removed** (not used) |

### 1.2 Constraints

- The machines are in production; every test on a machine must be reversible within minutes.
- The supervisor (external, SECS/GEM side) must keep working without change, unless the owner agrees to a change (bowing keys).
- Windows, LabVIEW 2018 run-time and NI Vision run-time licences are present on the machines today; the end state should need
  neither, unless the pattern-matching decision keeps NI Vision (section 4).
- Data written by years of production (recipes, settings, job history, images) must stay readable.

## 2. Strategy

```
 Today (LabVIEW)                                   End state (Python)
 ---------------------------------------------     ---------------------------------------------
 Supervisor                                        Supervisor
   | REST :8002 / WS :6123                           | REST :8002 / WS :6123 (same contract)
 AloxVision.exe                                    alox (one process)
   | TCP 503 / 504 / 505 (+ heartbeats, restarts)    core, API, functions, Qt UI
 AloxCamServer  AloxXYServer  AloxWLServer           device modules (one thread each):
   |              |             |                    cameras, Z, XY, monochromator, thermal probes
 cameras + Z    XY stages     monochromator          |  serial / GigE / Modbus TCP
                                                   same hardware
```

Without separate servers there is no intermediate production state where Python and LabVIEW programs run together: the new
application replaces the old one as a whole. The risk of that single step is reduced by validating each layer on the real
machine **before** the cutover:

1. **Device layer first, with a check tool per device** (as `tools/z_stage_check.py` today): read-only checks, then guarded
   moves, run at the fab while LabVIEW is stopped. A device only owns its port while the tool runs; LabVIEW is restarted
   afterwards.
2. **Side-by-side installation.** The new application installs next to the LabVIEW one; only one runs at a time. A test
   session on the machine = stop LabVIEW, start the new application, test, stop it, start LabVIEW. This is possible from the
   first headless release and is used at every phase, long before the cutover.
3. **The core is built headless first** (no UI), driven through the REST API, so it can run complete jobs on simulators and
   in test sessions before the UI exists.
4. **Recorded references:** the supervisor traffic (the API contract), the traffic between the LabVIEW core and its servers
   during jobs (which moves, speeds, waits, exposures a job uses: a behaviour reference for the functions), and images with
   LabVIEW's results (the algorithm reference).
5. **Parallel run** on a test machine with the same recipes and samples, then the cutover machine by machine.

**Detailed specification per phase.** The map is an overview. Before each phase is implemented, its part gets a detailed
specification, as the camera server got (chapters 01–09): every frame of the relevant diagrams, defects, questions. The HTML
export already contains everything needed.

## 3. Target architecture

### 3.1 Principles

1. **Keep the external contracts** where clients depend on them (REST routes, parameters, JSON keys, status codes, websocket
   events) and keep the file formats on disk.
2. **Replace implicit LabVIEW mechanisms by explicit ones:** functional globals become service objects with their own lock;
   VI Server tricks (opening a state machine by name, writing its controls) become method calls; the 3-item blocking process
   queue becomes a command bus that never blocks the caller.
3. **One owner per device:** each device module owns its port or SDK handle, serialises its commands and protects its
   background tasks with a lock (the XY wafer-limit supervision had none).
4. **A stuck device never freezes the application:** every device call has a timeout; recovery reconnects the device (close and
   reopen the port or the camera) instead of killing a process.
5. **Everything runs without hardware:** each device has a simulator (the camera server already has a camera simulator, a Z
   simulator and a fake PI controller), so complete jobs run in CI.
6. **Configuration in one place:** one typed reader/writer for the NI INI format (type suffixes, decimal comma; already
   written for the camera server), one table of defaults equal to the values in effect today.
7. **Fix the decided defects, reproduce the rest,** and document each choice (section 6).

### 3.2 Process

One process. Image processing runs in a thread pool (NumPy and OpenCV release the GIL), each device in its own thread, the
Qt UI in the main thread. If the camera vendor's SDK (native code) ever proves able to crash the process, the camera module
alone can be moved into a child process later behind the same interface; nothing else changes.

### 3.3 Device layer

```
             function / UI / API code
                       |  device.method(args, timeout) -> result or error (no TCP, no flattening)
                       v
 +---------------------------------------------------------------------------------------+
 | Device actor (one per device): command queue -> worker thread -> backend               |
 |   state (connected, ready, moving, error), timeout per call, reconnect on failure,     |
 |   events (position, status, "re-homed", errors) -> event bus                          |
 +---------------------------------------------------------------------------------------+
        | backend interface (one per device type)
        +-- real backend:  PI GCS / pyserial, GigE camera SDK, monochromator serial, Modbus TCP
        +-- simulator:     same interface, used in CI, demos and the "simulation" mode
```

| Device module | Real backend | Reused from | Notes |
|---|---|---|---|
| `zstage` | PI C-663 over RS-232 (GCS) | `aloxcam.zstage` (done, tested) | Macros `DH`/`HOME`, joystick, limits as specified in `docs/08` |
| `xy` | Two PI Mercury controllers, one RS-232 daisy chain (X = device 1, Y = device 2) | The Z GCS backend, extended with daisy-chain addressing | Wafer-limit supervision as a locked background task; re-homing kept but reported |
| `camera` (×2: High Mag, Low Mag) | IDS GigE Vision cameras (`GV-528x-M` firmware in the sources), GenICam or IDS SDK | Camera behaviour specified in `docs/05`; `aloxcam.camera` simulator | Exposure, gain, flip, flat-field/dark correction, LEDs through the camera outputs; frames as NumPy arrays in memory |
| `monochromator` | Spectral Products (CM110 class, to confirm), serial byte protocol | — | 23 server commands in the map give the needed operations |
| `pyrometer`, `bandit` | Optris over serial; kSA BandiT over Modbus TCP | — | Only if the thermal measurement stays in scope |

The focus jobs (autofocus and refocus, `docs/06`–`07`) become functions of the application that use the camera and Z devices;
they no longer run "inside the camera server".

### 3.4 Main application

```
 FastAPI (uvicorn) :8002 ----+                          +---- websocket server :6123 (event publisher)
                             v                          ^
 Qt UI ---------------> Command bus (22 commands) ---> Event bus (17 event types, lossless) ---> UI, log, websocket
                             |
          +------------------+-------------------------------+------------------------+
          v                  v                               v                        v
   Function runner     Services (settings, recipes,     Device layer             Background tasks
   (one at a time;     users, folders, status IDs,      (section 3.3)            (heartbeat for the API,
   13 functions +      job history, images)                                      function status, device
   focus jobs)                                                                   supervision)
```

- **Measurement functions:** one class per function with `start()`, `stop()`, `abort()`, `run_once(timeout)` and `status()`;
  state names from the LabVIEW state enums (`typedefs.md`); the high-level functions call the low-level ones as LabVIEW does
  with Run Once. "One function at a time" becomes an explicit lock in the runner.
- **Events:** payloads identical to LabVIEW's; delivery becomes lossless (one queue per subscriber instead of 8 shared
  notifiers). The REST heartbeat route stays (the supervisor may read it); its producer is a simple timer.
- **Concurrency model:** asyncio for the API, the websocket and timers; threads for devices and image processing. To confirm in
  the core foundations phase.

### 3.5 Code layout (proposed)

| Package | Content |
|---|---|
| `alox.devices` | Device actor framework and the device modules of section 3.3 (Z moved from `aloxcam.zstage`) |
| `alox.vision` | Algorithms: focus metrics (moved from `aloxcam.imaging`), geometry and diameter fits, bowing/tilt/recentring formulas, wavelength/exposure interpolation, matching and edge tools behind interfaces |
| `alox.core` | Settings (NI INI reader from `aloxcam.config`), folders, users, recipes, status IDs and translations, event log, command and event buses, function runner, measurement functions, thermal tool |
| `alox.api` | REST (`[webservice] port`, 8001) and websocket (6123) |
| `alox.ui` | Qt (PySide6) operator and engineering UI |
| `aloxcam` | Kept as reference and test tooling: LabVIEW flattening, TCP wire format, the camera server, capture extraction and replay tools |

### 3.6 Operator UI

The LabVIEW front panels are the operator and engineering UI: 43 panels (main window, 25 function settings/run panels,
recipes, properties, login, logs, calibration, vignetting, XYZ stages, WL exposure tables, about, splash). The engineering
"Show UI" panels of the three servers are replaced by device panels in the engineering UI.

| Option | For | Against |
|---|---|---|
| **Qt desktop UI (PySide6)** in the application **(recommended)** | Closest to the current panels; fast display of 2448×2048 images with overlays; works offline on the machine | Desktop only |
| Web UI served by the application | Remote access from any PC | Image display, overlays and about 25 settings panels in JavaScript: more work, and a second technology to maintain |

The UI is built in two steps: first the production UI (main window, image display, recipe selection, login, start/stop,
results, logs), then the engineering panels (function settings, calibration, vignetting, exposure tables, device panels).
Recipes keep the same zip format, so during the gap engineers can still prepare recipes with the LabVIEW application on the
same machine (not at the same time).

## 4. Library mapping (LabVIEW → Python)

| LabVIEW / NI | Used for | Replacement | Risk |
|---|---|---|---|
| NI Vision: image basics, extract, resample, histogram, morphology, masks, FFT | Everywhere | NumPy, OpenCV, SciPy | Low |
| NI Vision: overlays, PNG custom data, calibration info | Display, saved images | Overlay objects drawn by the UI and burned into saved images; NI PNG custom chunks written for compatibility (reader/writer to specify) | Medium |
| NI Vision: **geometric pattern matching** (Learn/Setup/Match Geometric Pattern) | Search object, tilt, detect circle | No direct open-source equivalent. Behind a `Matcher` interface; first implementation via the NI Vision C API (`nivision.dll`, ctypes) for identical results while the NI Vision run-time licence is present; an OpenCV implementation validated against it on recorded data before NI is dropped | **High** |
| NI Vision: shape detection, edge tools (Find Edge, Rake, Spoke, Find Circular Edge, Fit Circle), particle analysis | Detect circle, mesa diameters, tilt edge | OpenCV/scikit-image plus small own implementations with documented parameters; validated on recorded images | Medium |
| NI Analysis / gmath: polynomial fit, statistics, Delaunay, convex hull, constrained nonlinear optimisation, sphere fit | Diameters, bowing | NumPy, `scipy.spatial`, `scipy.optimize` (formulas in map Part 4) | Low |
| NI-IMAQdx | Cameras | IDS SDK or a GenICam stack (GenTL producer + Harvesters); frames in memory | Medium |
| NI image serialisation on the wire | Camera server replies | **Not needed any more** (images stay in memory) | — |
| PI GCS LabVIEW driver | Z, XY | The existing pyserial GCS backend (or `pipython`), extended with daisy-chain addressing for XY | Low |
| NI-VISA serial | Monochromator, Optris pyrometer | pyserial | Low (protocols to confirm) |
| NI Modbus | BandiT probe | pymodbus | Low |
| NI Web Services + JKI JSON | REST API | FastAPI; a JSON writer that reproduces LabVIEW's key names, order and number-as-string formatting | Medium (exactness) |
| MediaMongrels websockets | Websocket server | `websockets` / FastAPI websocket on its own port 6123 | Low |
| Config File VIs | INI files | NI INI reader from the camera server (`aloxcam.config`), plus a writer | Low |
| Zip VIs | Recipes | `zipfile` | Low |
| Registry, System Exec | Folders, the measure-mesa external command | `winreg`, `subprocess` | Low |
| `cmd START`, `tasklist`, `taskkill`, LabVIEW TCP, heartbeats | Server processes | **Removed** (no server processes) | — |
| WireFlow dongle SDK | Licence | WireFlow DLL via ctypes, or a new licence scheme (owner's decision 4) | Medium |
| LabVIEW front panels, event structure | UI | Qt (PySide6) | **High** (size) |

## 5. Phased plan

Relative sizes: S < M < L < XL. From the first headless release on, every phase ends with a version that can be tried in a
side-by-side test session on a machine.

| Phase | Content | Size | Exit criterion | Needs from the owner |
|---|---|---|---|---|
| 0 | Analysis: camera server specification, Vision Core map | — | **Done** | — |
| 1–2 | Camera server protocol, real Z stage | — | **Done**; reused as the Z device and the simulators (Z hardware checks at the fab pending) | `z_stage_check.py` reports |
| 3 | Application skeleton and device framework: package layout, device actor (thread, queue, timeouts, reconnect, events), settings reader/writer, event log, simulation mode; Z device moved in | M | **Done** | — |
| 4 | XY device: detailed spec, PI daisy chain, wafer-limit supervision with lock, reported re-homing, simulator, check tool | M | **Code done** (`alox.devices.xy`, `gcs_chain`, `tools/xy_stage_check.py`); check tool to pass at the fab (read-only, then guarded moves) | Controller and stage models; a capture of port 504 during a job (behaviour reference) |
| 5 | Monochromator device: detailed spec, serial protocol, simulator, check tool | M | **Code done** (`alox.devices.monochromator`, `cm11x`, `tools/wl_check.py`); check tool to pass at the fab | Monochromator model and manual; a capture of port 505 during a job |
| 6 | Camera devices: 2 GigE cameras, exposure/gain, flip, flat-field/dark correction, LEDs, simulator, check tool | L | **Code done** (`alox.devices.camera`, `camera_genicam`, `tools/camera_check.py`); images and settings identical to LabVIEW's (same exposure → same grey levels within tolerance) | Camera models and connection (network cards), SDK choice, the camera server capture 2 as behaviour reference |
| **M1** | **All devices driven by the new code on a real machine** | | Every check tool passed at the fab | Test slots at the fab |
| 7 | Core foundations, headless: settings, folders, users, recipes, status IDs and translations, command and event buses, function runner, device supervision | L | **Code done** (`alox.core`); boots on a machine in a test session, loads a recipe, grabs images from both cameras and moves Z/XY/WL through the API | Copy of a real `Documents\alox` and `D:\alox` tree (no secrets) |
| 8 | REST (8002) and websocket (6123) API, with simulated functions (the role of AloxSim) | M | **Code done** (`alox.api`, run by `alox`); golden tests recorded from the real supervisor pass; bench integration with the supervisor | Capture of supervisor traffic on 8002/6123 during mesa, bowing and tilt jobs |
| 9 | `alox.vision`: formulas, focus, geometry and diameters, edge and shape tools, `Matcher` (NI C API first) | L | **Code done** (formulas, focus metrics, exact roundness fits, edge tools, Matcher: OpenCV + NumPy, NI C API skeleton); each algorithm matches LabVIEW's results on a recorded dataset within agreed tolerances | Dataset: images plus LabVIEW results for each function |
| 10 | Functions: focus jobs (autofocus/refocus, `docs/06`–`07`), then the low-level functions, then high-level mesa, bowing, tilt; job history files | XL | **Code done** (all 13 functions, job files; validation on recorded data pending); full jobs in test sessions give the same results and files as LabVIEW | Which functions are used in production |
| **M2** | **Headless application runs complete supervisor jobs on a real machine** | | Same results as LabVIEW on the same samples | Test slots |
| 11 | Qt UI: production panels first, then engineering and device panels | XL | **Started** (main window); operators and engineers accept it on a test machine | UI review sessions |
| 12 | Thermal measurement, viewer, licence, installer, simulation mode for demos | M | Installer deploys side by side with LabVIEW on a clean PC | Licence decision; which tools are still needed |
| **M3** | **Parallel run, then cutover machine by machine** | | Same results over an agreed period; rollback (start LabVIEW) tested | Test machine, production schedule |

Phases 4, 5 and 6 are independent and can run in parallel. Phases 7 to 9 can start before M1, on simulators and recorded data.

## 6. Behaviour: reproduce or fix

| Behaviour | Decision | Source |
|---|---|---|
| REST routes, query/form parameters, plain-text PUT replies, status codes 200/500/503, JSON key names and string-formatted numbers | Reproduce exactly | Map Part 5 |
| `GET /v1/system/version` enqueues `Abort` | Fix: read-only | Owner, map §3.1 |
| `abort` served as GET | Reproduce (the supervisor uses it) | Map Part 5 |
| Bowing JSON duplicate keys | Fix to `bowinga/bowingb`, `parallelisma/parallelismb` after checking the supervisor | Owner |
| Websocket: one client, lossy notifiers | Same payloads and port; lossless delivery; one client unless the owner wants more | Map Part 5 |
| Process queue of 3 items that blocks the sender | Fix: non-blocking command bus, same commands | Map Part 1 |
| One measurement function at a time | Reproduce, as an explicit lock | Map Parts 1, 3 |
| Server programs, TCP between programs, heartbeats with kill/restart, watchdog | Removed; device reconnect replaces process restart | Owner (integrated architecture) |
| Hardware errors that reached the core only as a TCP timeout (for example XY "outside wafer") | Reported immediately as errors | Map Part 2 |
| `%LOCALAPPDATA%\Temp` emptied at exit | Not reproduced (LabVIEW workaround) | Owner |
| "RANSAC" line fit | Fix (all orientations, real inlier count) | Owner |
| XY silent re-homing | Keep, but log and report it | Owner |
| XY wafer-limit loop without lock | Fix (the XY device owns the link) | Map Part 2 |
| Ports and folders | REST from `alox.ini [webservice] port` (owner 2026-11-27: the supervisor calls 8001), websocket 6123, folders from `alox_setup.ini` (the WL port no longer exists) | Owner |
| File names with one-second resolution (same-second overwrite) | Decide: keep names (other tools may read them) or add milliseconds | Map Part 4 |
| Passwords stored Base64 in `alox.ini` | Owner's decision 5 (recommendation: hashed, with a migration on first login) | Map Part 1 |
| Camera and Z behaviour (flat field, flip, LEDs, autofocus/refocus, Z macros) | As decided in `docs/10` §6 and `docs/answers/`; decisions about the TCP protocol no longer apply | Camera server spec |
| Timings that included the TCP round trip (for example the fast autofocus Z estimate taken after `Get Image` returns, `docs/06` D16) | Re-validate: without TCP the latency is shorter, so the Z estimate and any timeout tuned on LabVIEW change | Camera server spec |

## 7. Validation strategy

1. **Device check tools** at the fab, one per device: read-only first, then guarded moves (as `tools/z_stage_check.py`).
2. **Behaviour references:** captures of the LabVIEW core ↔ server traffic (ports 503, 504, 505) during real jobs, decoded
   with `tools/extract_capture.py` (it takes any port); they show the exact sequence of moves, speeds, waits and exposures each
   function uses, and become scenario tests for the functions.
3. **API golden tests:** supervisor ↔ core traffic recorded on ports 8002 and 6123 during real jobs; requests replayed against
   the new application, responses and event sequences compared (known differences listed in section 6).
4. **Algorithm dataset:** for each function, recorded images with LabVIEW's results (job history files, saved images,
   reports); the Python algorithm must match within tolerances agreed with the owner (for example ±0.1 µm on diameters).
5. **Simulation:** every device has a simulator, so complete jobs run in CI.
6. **Side-by-side test sessions** on a machine at each phase from the first headless release.
7. **Parallel run** before the cutover: same recipes and samples on the LabVIEW and Python versions, reports compared.

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Single cutover for the whole application | A problem found late affects production | Devices validated first with check tools (M1); headless jobs in side-by-side sessions (M2); parallel run; rollback = start LabVIEW |
| Geometric pattern matching has no exact open-source equivalent | Search object, tilt, detect circle results differ | `Matcher` interface; NI Vision C API first; OpenCV version only after validation on recorded data |
| Operator UI size (43 panels) | Long phase 11; acceptance by operators | Production UI first; engineers keep LabVIEW for recipe editing meanwhile (same files) |
| A native SDK (camera) crashes or hangs the single process | Whole application stops | Timeouts on every device call; the camera module can move to a child process behind the same interface |
| Timing changes once TCP is gone | Focus estimates, waits and timeouts tuned on LabVIEW behave differently | Behaviour references and algorithm dataset; re-validate timing-dependent steps (section 6) |
| Behaviours not yet read in detail | Surprises during implementation | Detailed specification per phase from the HTML export before coding |
| Supervisor depends on undocumented quirks | Production stop after cutover | Recorded supervisor traffic as tests; bench integration with the real supervisor before cutover |
| Unknown monochromator model / protocol details | Monochromator device delay | Manual plus capture; read-only check tool first |
| Data compatibility (NI PNG custom data, `.foc` binary files, INI quirks) | Old or new data unreadable | Readers/writers specified and tested on real files |
| Licensing (WireFlow dongle) | Cannot start on customer machines | Decide early (phase 12 input); keep the dongle check via its DLL if needed |

## 9. Inputs needed from the owner

1. **Decisions:** the remaining ones from [map §3](map.md#3-decisions-for-the-owner) (API kept exactly or new version, licensing,
   passwords, scope of the tools, file formats), the UI technology (section 3.6) and the pattern-matching strategy (section 4).
   Decision 2 (process layout) is settled: one integrated application.
2. **Hardware facts:** camera models and how they are connected (network cards), XY controller and stage models,
   monochromator model and manual, whether the thermal measurement is used in production.
3. **Captures** (RawCap): supervisor ↔ core on 8002 and 6123 during a mesa, a bowing and a tilt job (needed); core ↔ servers on
   503, 504 and 505 during the same jobs (behaviour reference; one session can record all ports).
4. **Algorithm dataset:** for each function used in production, a few jobs with their images, the recipe used and LabVIEW's
   results (job history folder).
5. **A copy of real data trees** without secrets: `Documents\alox` (settings, current recipe) and `D:\alox` (structure and a
   sample of each folder).
6. **Scope:** functions really used in production (for example `refocus_oxyd`), and whether the simulator, viewer, LED tool,
   LYNRED build and licence generators must be migrated.
7. **Supervisor side:** who maintains it, and whether it can accept the bowing key fix.

## 10. Next steps

1. Phase 3: create the application skeleton and the device framework, and move the Z stage into it (no fab access needed).
2. Phase 4: detailed specification of the XY server's behaviour and the XY device with its simulator and check tool.
3. Prepare one capture procedure for ports 503, 504, 505, 8002 and 6123, so that one visit to the fab records all the
   references, and start collecting the algorithm dataset.
