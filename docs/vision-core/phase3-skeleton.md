# Phase 3: application skeleton and device framework

Plan: [migration-plan.md](migration-plan.md) §5, phase 3. Goal: the skeleton of the integrated application, the device
framework every hardware module will use, typed access to the LabVIEW INI files, and the Z stage as the first device.

## 1. What was built

| Module | Content |
|---|---|
| `alox.events` | `Event` (dotted topic, source, data, wall-clock time) and `EventBus`: thread-safe publish/subscribe, one lossless queue per subscriber (optionally bounded: oldest dropped and counted), topic-prefix filters, callbacks |
| `alox.devices.base` | `Device`: one worker thread per device, priority queue (urgent items overtake waiting ones), `open`/`close`/`shutdown`, `call`/`submit` with timeouts, `run_direct` for emergency operations, states and reconnection; `DeviceError`, `DeviceTimeout`, `DeviceNotReady` |
| `alox.devices.zstage` | `ZStage`: the PI C-663 Z stage as a device, built on the camera server's tested `ZController` and backends (`PiSerialStage`, `SimStage`) |
| `alox.config` | `TypedIni` (reading `alox.ini` keys by name, whatever their type suffix) and `IniDocument` (editing an INI file while preserving everything else) |
| `alox.app` | `Application` container (event bus, settings, devices) and the `alox` command line |

The camera server package `aloxcam` is unchanged and keeps its own tests; `alox` imports from it.

## 2. Device framework

```
 caller thread(s)                         device worker thread
 ----------------                         --------------------
 z.move_absolute(5)  --submit-->  [priority queue]  --> ensure ready (reconnect if fault) --> fn() --> backend
        |                                                                                      |
        +<------------- result / DeviceError ------------- Future <----------------------------+
        |
        +-- no result within the timeout --> DeviceTimeout (item cancelled if not started; else _interrupt())
 z.stop() ---------------------------------- run_direct: executed at once in the caller's thread
```

- **States:** `closed` → `opening` → `ready`; a link failure (decided by the device: for the Z stage, codes −4 not
  connected, −6 port, −7 timeout) gives `fault`; `shutdown` is final. Every change is published as
  `device.<name>.state`.
- **Reconnection:** with `auto_reconnect` (default), the next operation on a device in `fault` first closes and re-opens it
  (up to `max_reconnect_attempts`, events `reconnecting` / `reconnected`); otherwise `DeviceNotReady`.
- **Timeouts:** every call has one (default per device). A call that times out while still queued never runs; one that
  times out while running keeps running in the worker (a thread cannot be killed) and its result is discarded; the device
  stays usable.
- **Events of a device:** `device.<name>.state`, `.timeout`, `.reconnecting`, `.reconnected`, plus device-specific ones.

## 3. Configuration

- `TypedIni.get("hardware.z.speed", 10.0)` finds `speed_DBL` (or `_I32`, `_BOOL`, `_STR`, `_U32`, `_PATH`) in
  `[hardware.z]`, converts it by its suffix, and falls back to the default. Reading uses the camera server's
  LabVIEW-compatible `NIConfig` (decimal comma, first occurrence wins, quoted strings).
- `IniDocument` changes single values and keeps comments, order, duplicates and the line endings of the file; values are
  written in LabVIEW's format (`TRUE`/`FALSE`, 6 decimals with a decimal comma, quoted strings, common paths `/D/dir`).
- Phase 3 still reads the Z settings through the camera server's `load_alox_ini` (section `[hardware.z]`).

## 4. Z stage device: what differs from the camera server

| Behaviour | Camera server (LabVIEW-compatible) | `ZStage` device | Reason |
|---|---|---|---|
| Interface | TCP commands and reply clusters | Python methods returning values or raising `DeviceError` | Integrated architecture |
| `ERR?` after `MOV`/`MVR` | Not sent (03 C13): a refused move surfaces at a later `ERR?` | Sent after the move command and after the wait: a refused or stopped move is reported by the move itself | Plan §6: hardware errors reported immediately |
| Wait | Separate `Z Wait End Move` command | `move_absolute(z, wait=True)` (default) or `wait_end_move()` | Simpler for the functions |
| Implicit re-initialisation when the axis is not referenced before a move | Kept, logged | Kept, and published as `device.z.rehomed` | Owner's decision (map §3.1) |
| Stop | Queued behind the running command | `stop()` is sent at once (`run_direct`) and the running wait sees the axis stopped; the move then reports error 10 | Emergency stop must not wait |
| Link lost | Error reply, next command fails | Device enters `fault`, the next call reconnects (set-up again) | Device framework |
| Session, set-up, reference check, macros, error texts | — | Unchanged (same `ZController`) | Reuse of tested code |

## 5. Running

```bash
alox --simulate --check
```

```bash
alox --alox-ini "D:\alox\settings\alox.ini" --check
```

`--check` opens every device, prints their status as JSON and exits with 0 when all are ready; without it the application
runs until Ctrl+C. `--port-map COM2=socket://127.0.0.1:50000` connects the Z stage to the fake controller
(`python -m aloxcam.zstage.fake_c663`) for a rehearsal without hardware. `--log-dir` adds daily log files.

## 6. Next

Phase 4 (XY device) adds the second device on the same framework, with daisy-chain GCS addressing, the wafer-limit
supervision as a locked background task, and a check tool. The core foundations (phase 7) will use `TypedIni`/`IniDocument`
for the full Vision Core `alox.ini`.
