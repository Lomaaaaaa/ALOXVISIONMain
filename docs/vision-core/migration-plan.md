# Vision Core migration plan

## Summary

This plan turns the [map](map.md) into a route for rewriting the whole ALOX Vision Core application (V1.2.6.2), not only the
camera server. It extends the camera server plan ([`../10-migration-plan.md`](../10-migration-plan.md)), which stays valid
for its own scope.

- **Language:** Python 3.11+, as for the camera server. Images: NumPy/OpenCV/SciPy. REST/websocket: FastAPI (uvicorn) and a
  websocket server. Hardware: pyserial/PI GCS, Modbus TCP. One code base, packaged as Windows executables.
- **Strategy: replace one program at a time along the interfaces that already exist.** Vision Core is already split into
  processes with clear contracts: the three hardware servers speak a TCP protocol, and the supervisor speaks REST and
  websocket. A Python program that honours one of these contracts can replace its LabVIEW counterpart while everything else
  stays in LabVIEW. The camera server is the first one, and it is well advanced.
- **Order:** camera server (in progress), then XY server, then WL server. At that point all the hardware is driven by Python
  under the unchanged LabVIEW main application. Then the main application (core, API, measurement functions, operator UI),
  which is the largest part and is replaced in one cutover, prepared by building and testing it headless first.
- **Compatibility:** the supervisor contract (REST port 8002, websocket 6123), the hardware-server protocol and the files on
  disk (INI files, zip recipes, folders, job history, image files) stay compatible, except for the defects the owner decided
  to fix ([map §3.1](map.md#31-owners-answers-on-the-defects-25112026)). File compatibility also gives a simple rollback:
  reinstall the LabVIEW version.
- **Main risks:** the NI Vision algorithms without a direct open-source equivalent (geometric pattern matching, some edge and
  shape tools), the size of the operator UI (43 panels), and behaviours not yet read in detail (the map read about 400 of the
  18 000 diagram images). Each is mitigated below.

## 1. Starting point

### 1.1 What exists

| Program | ALOX VIs | Contract with the rest | Migration status |
|---|---|---|---|
| AloxCamServer | 30 | TCP 503, specified in `docs/01`–`10` | Python port: protocol (phase 1) and real Z stage (phase 2) done; cameras (phase 3) and focus jobs (phase 4) to do |
| AloxXYServer | 17 | TCP 504, same framing; 29 commands; PI GCS (2 Mercury axes, RS-232 daisy chain) | Mapped (map Part 2) |
| AloxWLServer | 19 | TCP 505 (shipped INI), same framing; 23 commands; Spectral Products monochromator, serial | Mapped (map Part 2) |
| AloxVision.exe (core + REST + websocket + functions + UI) | 436 + 82 REST VIs | REST 8002 and websocket 6123 to the supervisor; TCP clients to the three servers; files on disk | Mapped (map Parts 1, 3, 4, 5) |
| Thermal measurement (inside AloxVision) | about 20 | Operator button; pyrometer (serial) or BandiT (Modbus TCP) | Mapped (map Part 6) |
| AloxSim, AloxViewer, licence tools, LED tool, LYNRED build | small | Standalone | Mapped (map Part 6); scope to confirm |
| AloxWatchdog | 6 | REST heartbeat | **Out of scope** (not used) |

### 1.2 Constraints

- The machines are in production. Every step must be installable, testable at the fab and reversible.
- The supervisor (external, SECS/GEM side) must keep working without change, unless the owner agrees to a change (bowing keys).
- Windows, LabVIEW 2018 run-time and NI Vision run-time licences are present on the machines today; the end state should
  need neither, unless a library decision keeps NI Vision (section 4).
- Data written by years of production (recipes, settings, job history, images) must stay readable.

## 2. Strategy: replacement along the existing interfaces

```
 Stage 0 (today)        Stage 1 (hardware in Python)          Stage 2 (end state)
 -----------------      ---------------------------------      ---------------------------------
 Supervisor             Supervisor                             Supervisor
   | REST/WS              | REST/WS                              | REST/WS (same contract)
 AloxVision (LV)        AloxVision (LV, unchanged)             alox-core (Python): core, API,
   | TCP 503/504/505      | TCP 503/504/505 (same protocol)      functions, Qt UI
 Cam/XY/WL servers (LV) Cam/XY/WL servers (Python)               | TCP 503/504/505 (same protocol)
                                                               Cam/XY/WL servers (Python)
```

- **Stage 1** is low risk: each server is replaced alone, and the LabVIEW main application does not notice. A server can be
  rolled back by restoring its LabVIEW executable. The camera server work (protocol skeleton, simulators, golden tests from a
  network capture, hardware check tools) is the template for XY and WL.
- **Stage 2** replaces the main application. It cannot be split across processes the way LabVIEW is built (the managers are
  in-process functional globals and the REST service runs inside the same executable), so its cutover is a single step. It is
  prepared so that the cutover day is a formality:
  1. build the core **headless** first (no UI), driven by the REST API and running against the Python servers and their
     simulators;
  2. make the API pass golden tests recorded from the real supervisor traffic;
  3. validate each algorithm offline on recorded images against LabVIEW's results;
  4. add the operator UI;
  5. run it in parallel with LabVIEW on a test machine (same recipes, same samples), then cut over one machine, then the
     others.
- **Detailed specification per phase.** The map is an overview. Before each phase is implemented, its part gets the same
  detailed specification the camera server got (chapters 01–09): every frame of the relevant diagrams, defects, questions.
  The HTML export already contains everything needed for this.

## 3. Target architecture

### 3.1 Principles

1. **Keep the external contracts**, byte for byte where clients depend on them (TCP framing, REST routes, parameters, JSON keys,
   status codes, websocket events), and keep the file formats on disk.
2. **Replace implicit LabVIEW mechanisms by explicit ones:** functional globals become service objects with their own lock;
   VI Server tricks (opening a state machine by name, writing its controls) become method calls; the 3-item blocking process
   queue becomes a command bus that never blocks the caller.
3. **One owner per hardware resource** (a serial port, a TCP client), with a lock where LabVIEW had none (the XY wafer-limit
   loop).
4. **Everything runs without hardware:** each hardware component has a simulator (the camera server already has SimCamera,
   SimStage and a fake PI controller), so the whole application can run in CI.
5. **Configuration in one place:** one typed reader for the NI INI format (type suffixes, decimal comma; already written for
   the camera server), one table of defaults, equal to the values in effect today.
6. **Fix the decided defects, reproduce the rest**, and document each choice (section 6).

### 3.2 Processes

During the migration the four processes stay as they are (main application plus three hardware servers on localhost TCP),
because that is what makes stage 1 possible. Once LabVIEW is gone, merging the three servers into one hardware service (or
into the core) becomes possible; that is the owner's decision 2 in [map §3](map.md#3-decisions-for-the-owner) and can wait
until stage 2 is finished.

### 3.3 Code layout (proposed)

One repository (this one), several Python packages:

| Package | Content | Origin |
|---|---|---|
| `aloxhw` | Shared server framework: LabVIEW flattening (`lv`), TCP framing (`wire`), server loop, notifier-name replies, heartbeat, NI INI reader, event log | Extracted from `aloxcam` (phase 5) |
| `aloxcam` | Camera server | Existing |
| `aloxxy` | XY server: PI GCS daisy chain, 29 commands, wafer-limit supervision | New (phase 6) |
| `aloxwl` | WL server: monochromator serial protocol, 23 commands | New (phase 7) |
| `aloxvision` | Algorithms: focus metrics, geometry and diameter fits, bowing/tilt/recentring formulas, wavelength/exposure interpolation, matching and edge tools behind interfaces | New (phase 10); focus metrics moved from `aloxcam` |
| `aloxcore` | Main application: settings, folders, users, recipes, status IDs and translations, server process management, hardware clients, command bus, measurement functions, REST and websocket API, thermal tool | New (phases 8–13) |
| `aloxui` | Operator and engineering UI (Qt) | New (phase 12) |

### 3.4 Main application (`aloxcore`)

```
 FastAPI (uvicorn) :8002 ----+                         +---- websocket server :6123 (event publisher)
                             v                         ^
 Qt UI (aloxui) ------> Command bus (22 commands) ---> Event bus (17 event types, lossless) ---> UI, log, websocket
                             |
          +------------------+------------------------------+------------------------+
          v                  v                              v                        v
   Function runner     Services (settings, recipes,   Hardware clients          Background tasks
   (one at a time;     users, folders, status IDs,    (camera/XY/WL TCP;        (heartbeat, modules heartbeat
   13 functions as     job history, images)           server process manager)   with restart, function status)
   state-machine
   classes)
```

- **Concurrency:** an asyncio event loop for I/O (REST, websocket, TCP clients, timers); image processing in a worker thread
  pool (NumPy and OpenCV release the GIL); the Qt UI in the main thread, talking to the core through the same command and
  event buses. **(Choice to confirm in phase 8; the camera server uses threads and queues, which also works.)**
- **Measurement functions:** one class per function with `start()`, `stop()`, `abort()`, `run_once(timeout)` and
  `status()`; the state names come from the LabVIEW state enums (`typedefs.md`); the high-level functions call the low-level
  ones as LabVIEW does with Run Once. The rule "one function at a time" becomes an explicit lock in the runner.
- **Events:** the payloads stay identical to LabVIEW's; delivery becomes lossless (one queue per subscriber instead of 8
  shared notifiers).

### 3.5 Operator UI

The LabVIEW front panels are the operator and engineering UI: 43 panels (main window, 25 function settings/run panels,
recipes, properties, login, logs, calibration, vignetting, XYZ stages, WL exposure tables, about, splash). Two options:

| Option | For | Against |
|---|---|---|
| **Qt desktop UI (PySide6)** in the core process **(recommended)** | Closest to the current panels; fast display of 2448×2048 images with overlays; works offline on the machine | Desktop only |
| Web UI served by the core | Remote access from any PC | Image display, overlays and about 25 settings panels in JavaScript: more work, and a second technology to maintain |

The UI is built in two steps: first the production UI (main window, image display, recipe selection, login, start/stop,
results, logs: what REMOTE and daily LOCAL operation need), then the engineering panels (function settings, calibration,
vignetting, exposure tables). Recipes stay in the same zip format, so during the gap engineers can still prepare recipes with
the LabVIEW application on the same machine (not at the same time).

## 4. Library mapping (LabVIEW → Python)

| LabVIEW / NI | Used for | Replacement | Risk |
|---|---|---|---|
| NI Vision: image basics, extract, resample, histogram, morphology, masks, FFT | Everywhere | NumPy, OpenCV, SciPy | Low |
| NI Vision: overlays, PNG custom data, calibration info | Display, saved images | Overlay objects drawn by the UI and burned into saved images; NI PNG custom chunks written for compatibility (reader/writer to specify) | Medium |
| NI Vision: **geometric pattern matching** (Learn/Setup/Match Geometric Pattern) | Search object, tilt, detect circle | No direct open-source equivalent. Put it behind a `Matcher` interface; first implementation via the NI Vision C API (`nivision.dll`, ctypes) for identical results while the NI Vision run-time licence is present; an OpenCV implementation (edge/contour or feature based) validated against it on recorded data before NI is dropped | **High** |
| NI Vision: shape detection (Detect Circles/Ellipses), edge tools (Find Edge, Rake, Spoke, Find Circular Edge, Fit Circle), particle analysis | Detect circle, mesa diameters, tilt edge | OpenCV/scikit-image plus small own implementations with documented parameters; validated on recorded images | Medium |
| NI Analysis / gmath: polynomial fit, statistics, Delaunay, convex hull, constrained nonlinear optimisation, sphere fit | Diameters (inscribed, circumscribed, minimum zone), bowing | NumPy, `scipy.spatial`, `scipy.optimize` (formulas in map Part 4) | Low |
| IMAQdx | Cameras (camera server) | GenICam / vendor SDK (camera server plan, phase 3) | Medium |
| PI GCS LabVIEW driver | Z (camera server), XY (two daisy-chained controllers) | `pipython` or the existing pyserial GCS backend, extended with daisy-chain addressing | Low |
| NI-VISA serial | Monochromator, Optris pyrometer | pyserial | Low (protocols to confirm) |
| NI Modbus | BandiT probe | pymodbus | Low |
| NI Web Services + JKI JSON | REST API | FastAPI; a JSON writer that reproduces LabVIEW's key names, order and number-as-string formatting | Medium (exactness) |
| MediaMongrels websockets | Websocket server | `websockets` / FastAPI websocket on its own port 6123 | Low |
| Config File VIs | INI files | NI INI reader/writer from the camera server (`aloxcam.config`) | Low |
| Zip VIs | Recipes | `zipfile` | Low |
| Registry, `cmd START`, `tasklist`, `taskkill`, System Exec | Folders, process control | `winreg`, `subprocess`, `psutil` | Low |
| WireFlow dongle SDK | Licence | WireFlow DLL via ctypes, or a new licence scheme (owner's decision 4) | Medium |
| LabVIEW front panels, event structure | UI | Qt (PySide6) | **High** (size) |

## 5. Phased plan

Relative sizes: S < M < L < XL. Each phase ends with a release that can run at the fab, except where noted.

| Phase | Content | Size | Exit criterion | Needs from the owner |
|---|---|---|---|---|
| 0 | Analysis: camera server specification, Vision Core map | — | **Done** | — |
| 1–2 | Camera server: protocol skeleton, real Z stage | — | **Done** (Z hardware checks at the fab pending) | `z_stage_check.py` reports |
| 3 | Camera server: real cameras, NI image serialisation | L | Images identical to LabVIEW's on the wire | Second capture (exposure, Z moves, focus); camera model |
| 4 | Camera server: autofocus/refocus jobs | M | Same Z results as LabVIEW on recorded runs | — |
| **M1** | **Python camera server in production under the LabVIEW core** | | One machine, then all | Test slot at the fab |
| 5 | Extract the shared server framework (`aloxhw`) from `aloxcam` | S | Camera server unchanged, tests green | — |
| 6 | XY server: detailed spec, PI daisy chain, 29 commands, wafer-limit loop with lock, logged re-homing, simulator, check tool | M | Golden tests from a capture; fab check passed | Capture on port 504 during a job; controller/stage models |
| 7 | WL server: detailed spec, monochromator protocol, 23 commands, simulator, check tool | M | Same | Capture on port 505; monochromator model and manual |
| **M2** | **All hardware servers in Python under the LabVIEW core** | | | |
| 8 | Core foundations, headless: settings, folders, users, recipes, status IDs and translations, event log, server process management, heartbeats with restart, hardware clients, command and event buses | L | Boots, starts the servers, loads a recipe, runs a "Get Image" from the REST API against simulators | Copy of a real `Documents\alox` and `D:\alox` tree (no secrets) |
| 9 | REST (8002) and websocket (6123) API, with simulated functions (the role of AloxSim) | M | Golden tests recorded from the real supervisor pass; supervisor integration test on a bench | Capture of supervisor traffic on 8002/6123 during mesa, bowing and tilt jobs |
| 10 | `aloxvision`: formulas, focus, geometry and diameters, edge and shape tools, `Matcher` (NI C API first) | L | Each algorithm matches LabVIEW's results on a recorded dataset within agreed tolerances | Dataset: images plus LabVIEW results for each function |
| 11 | Measurement functions: low-level (get image, autofocus, refocus, auto exposure, search wavelength, detect object, detect circle, tracking, measure mesa; refocus oxyd if still needed), then high-level (mesa, bowing, tilt); job history files | XL | Full jobs on a test machine give the same results and files as LabVIEW | Which functions are used in production |
| 12 | Qt UI: production panels first, then engineering panels | XL | Operators and engineers accept it on a test machine | UI review sessions |
| 13 | Thermal measurement, viewer, licence, packaging (installer), simulator mode | M | Installer deploys and runs on a clean PC | Licence decision; which tools are still needed |
| **M3** | **Parallel run, then cutover machine by machine** | | Same results over an agreed period; rollback tested | Test machine, production schedule |

Phases 5 to 7 can run while phase 3/4 waits for fab access. Phases 8 to 10 can start in parallel with stage 1, since they
only need simulators and recorded data.

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
| `%LOCALAPPDATA%\Temp` emptied at exit | Not reproduced (LabVIEW workaround) | Owner |
| "RANSAC" line fit | Fix (all orientations, real inlier count) | Owner |
| XY silent re-homing | Keep, but log and report it | Owner |
| XY wafer-limit loop without lock | Fix (owner of the serial link) | Map Part 2 |
| Errors such as "outside wafer" never sent (client times out) | Send the error reply; check the client handles it | Map Part 2 |
| Ports and folders | Values in effect today: REST 8002, websocket 6123, WL 505, folders from `alox_setup.ini` | Owner |
| File names with one-second resolution (same-second overwrite) | Decide: keep names (other tools may read them) or add milliseconds | Map Part 4 |
| Passwords stored Base64 in `alox.ini` | Owner's decision 5 (recommendation: hashed, with a migration on first login) | Map Part 1 |
| Watchdog | Dropped | Owner |
| Camera server defects | As decided in `docs/10` §6 and `docs/answers/` | Camera server spec |

## 7. Validation strategy

1. **Wire golden tests** for each server, recorded at the fab with RawCap as for the camera server (`tools/extract_capture.py`
   already handles any port), replayed in CI.
2. **API golden tests**: supervisor ↔ core traffic recorded on ports 8002 and 6123 during real jobs; requests replayed against
   the Python core, responses and event sequences compared (with known differences listed in section 6).
3. **Algorithm dataset**: for each function, recorded images with LabVIEW's results (job history files, saved images,
   reports). The Python algorithm must match within tolerances agreed with the owner (for example ±0.1 µm on diameters).
4. **Simulation**: every hardware component has a simulator, so complete jobs run in CI (as the camera server's
   `--simulate` mode does today).
5. **Hardware checks at the fab**: one script per device, read-only first, then guarded moves (as `tools/z_stage_check.py`).
6. **Parallel run** before each cutover: same recipes and samples on the LabVIEW and Python versions, reports compared.

## 8. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Geometric pattern matching has no exact open-source equivalent | Search object, tilt, detect circle results differ | `Matcher` interface; NI Vision C API first; OpenCV version only after validation on recorded data |
| Operator UI size (43 panels) | Long phase 12; acceptance by operators | Production UI first; engineers keep LabVIEW for recipe editing meanwhile (same files) |
| Behaviours not yet read in detail | Surprises during implementation | Detailed specification per phase from the HTML export before coding; golden tests |
| Supervisor depends on undocumented quirks | Production stop after cutover | Recorded supervisor traffic as tests; bench integration with the real supervisor before cutover |
| Unknown monochromator model / protocol details | WL server delay | Capture plus manual; read-only check tool first |
| Data compatibility (NI PNG custom data, `.foc` binary files, INI quirks) | Old data unreadable or new data unreadable by other tools | Readers/writers specified and tested on real files |
| Licensing (WireFlow dongle) | Cannot start on customer machines | Decide early (phase 13 input); keep the dongle check via its DLL if needed |
| Two versions in production during the transition | Support effort | Stage 1 keeps one main application; stage 2 cutover machine by machine with tested rollback |

## 9. Inputs needed from the owner

1. **Decisions:** the remaining ones from [map §3](map.md#3-decisions-for-the-owner) (API kept exactly or new version, process
   layout after the migration, licensing, passwords, scope of the tools, file formats), plus the UI technology (section 3.5)
   and the pattern-matching strategy (section 4).
2. **Captures** (RawCap, as for the camera server): supervisor ↔ core on 8002 and 6123 during a mesa, a bowing and a tilt
   job; core ↔ XY server (504) and core ↔ WL server (505) during a job; the second camera server capture already requested.
3. **Algorithm dataset:** for each function used in production, a few jobs with their images, the recipe used and LabVIEW's
   results (job history folder).
4. **Hardware facts:** XY controller and stage models, monochromator model and manual, whether the thermal measurement (Optris
   pyrometer, BandiT) is used, camera models.
5. **A copy of real data trees** without secrets: `Documents\alox` (settings, current recipe) and `D:\alox` (structure and a
   sample of each folder).
6. **Scope:** functions really used in production (for example `refocus_oxyd`), and whether the simulator, viewer, LED tool,
   LYNRED build and licence generators must be migrated.
7. **Supervisor side:** who maintains it, and whether it can accept the bowing key fix.

## 10. Next steps

1. Finish the camera server (phases 3 and 4) as soon as the second capture and the Z-stage checks are available.
2. Meanwhile, start phase 5 (shared framework) and the detailed XY server specification (phase 6), and prepare the capture
   procedure for ports 504, 505, 8002 and 6123 so that one visit to the fab records everything.
3. Start collecting the algorithm dataset, since phases 10 and 11 depend on it most.
