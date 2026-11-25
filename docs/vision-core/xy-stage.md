# XY stage (two PI C-663 controllers, RS-232 daisy chain)

**Summary.** The XY stage is driven by a separate LabVIEW executable, AloxXYServer (`alox_xy_server.vi`, TCP port 504),
which owns two PI controllers chained on one RS-232 port: X is daisy-chain device 1 (master), Y is device 2. The server
uses the same PI GCS LabVIEW driver as the camera server's Z stage. A consumer loop executes the 29 commands of
`typedef_ext_xy_command` one at a time. Before every move the server checks that both axes are referenced (and silently
re-homes both if not) and that the destination lies within a circle around the wafer origin (error 9404). A third loop,
WAFER XY LIMITS, polls both positions every 500 ms and stops both axes when the stage is outside the wafer; it uses the
serial link without any lock. In the main application, `alox_manager_xy` (32 methods) wraps the client, and
`alox_manager_xy_coordinate_system` converts points between stage, wafer and chuck coordinates.

This chapter specifies what differs from the Z stage. The controller link, the GCS frames, waits, error translation and
joystick mechanics are those of [`../08-z-stage.md`](../08-z-stage.md) (cited "08 §x"); the TCP framing and the reply VIs
are in [`../02-tcp-protocol.md`](../02-tcp-protocol.md) (cited "02 §x"); the overview is
[map.md](map.md) Part 2 §2.8–§2.9.

**Sources and citations.** `<vi> dN` cites block-diagram image N of the VI's HTML export (`d0` = visible diagram).
Short names: `srv` = `alox_xy_server.vi`, `chkref` = `alox_xy_check_axis_references.vi`, `chkwafer` =
`alox_xy_check_wafer position.vi`, `mgr` = `alox_manager_xy.vi`, `cs` = `alox_manager_xy_coordinate_system.vi`, `map` =
`alox_manager_xy_map.vi`, `xyzui` = `alox_xyz_stages_ui.vi`, `hbs` = `alox_modules_heartbeat_service.vi`. Typedefs are
in [typedefs.md](typedefs.md); dependency lists in `vc_deps`. **(inferred)** marks a deduction; everything else was read
on the diagrams.

---

## 1. Hardware and link

### 1.1 Controllers and addressing

| Item | Value | Evidence |
|---|---|---|
| Controllers | Two PI Mercury-family controllers, GCS 2.0, one axis each. Model C-663 (owner answer Q9 for the Z controller; the XY model is not stated anywhere and is assumed identical **(inferred)**) | 08 §1.1, `docs/open-questions.md` Q9 |
| Driver | PI "Mercury GCS LabVIEW" driver, `C:\Users\Public\PI\Mercury\GCS_LabVIEW\…` (same as Z) | `vc_deps` of `srv` |
| Link | One RS-232 port, daisy chain. Comments: "Should be 1 (XY are daisy chained and X controller is the master)", "Should be 2 (… Y controller is the slave)" | `srv d13`, `srv d26` (INI frames) |
| X | System no. = `xaxis_I32` (default 1) and daisy-chain device number = the same value | `srv d49` |
| Y | System no. = `yaxis_I32` (default 2) and device number = the same value | `srv d49` |
| Port | `COM<comport_I32>`, one key read twice (X and Y), default 2; `≤ 0` → COM1, then `n − 1` into the driver's COM ring (as Z, 08 §3.1) | `srv d7`, `srv d20`, `srv d49` |
| Baud rate | `baudrate_I32` index: 0 → 9600, 1 → 19200, 2 → 38400, 3 → 115200 (default 3); same U16 ring as Z (08 §2.1) | `srv d8`, `srv d21` |
| I/O timeout | 5000 ms (constant, both devices) | `srv d49`, `chkref d0` |
| GCS axis id | Each controller has one axis, presumably `"1"` (taken from `SAI?` by the set-up VI into `Session.Axes`) **(inferred)** | 08 §2.2 |

"System no." is the 1-based index of the PI driver's internal tables (08 §1.3); in the XY server it doubles as the
daisy-chain device number. The two sessions (`X Session`, `Y Session`) use `alox_typedef_ext_zstage_session`
{System, Axes, Identity, Serial Number}.

### 1.2 Opening the daisy chain (`Mercury_GCS_Configuration_Setup.vi`)

`Setup` and the re-initialisation of §4 call PI's password-protected set-up VI once per controller, X first. The X call's
error wire feeds the Y call, so Y is skipped when X fails (`srv d49`, `chkref d2`).

| Input | X call | Y call | Differs from Z (08 §4.1) |
|---|---|---|---|
| `System no.` | `xaxis_I32` (1) | `yaxis_I32` (2) | Z: `zaxis_I32` |
| `Interface` | `RS232 DaisyChain` | `RS232 DaisyChain` | Z: `RS232` |
| `RS232 settings` | {COM<n>, baud} | same port and baud | — |
| `DaisyChain settings` (3 fields: Boolean, Boolean, I32 device id) | {**TRUE**, FALSE, 1} | {**FALSE**, FALSE, 2} | Z: {F, F, 1} |
| `Timeout` | 5000 ms | 5000 ms | same |
| two Boolean inputs (`All axes?`, `Switch servo on?`, as Z) | TRUE, TRUE | TRUE, TRUE | same |
| `Reference Move` | **unwired** in `Setup` (label "Default speed/range settings"); `Reference (FRF)` in the re-initialisation | same | Z: `Negative (FNL)` |

The cluster constants are overwritten by a `Bundle` whose two Boolean inputs come from separate constants (TRUE/FALSE for X,
FALSE/FALSE for Y) and whose I32 comes from the axis key (`srv d49`, zoomed). The PI field names are not visible. The
first Boolean is TRUE only for the master, so it most probably means "open the daisy chain (port)" while FALSE means
"connect to a device of the chain opened by the master" **(inferred)**.

Outputs: `Identification`, `SN out`, `Conn. Axes` → session {System = axis key, Identity, Serial Number, Axes}; `X Inited`
= no error after the X call, `Y Inited` = no error after the Y call (which includes the X error) (`srv d49`).

### 1.3 PI driver VIs used by the XY server

From the dependency list of `srv` and the diagrams. Every call passes `System no.` = `Session.System`; axis-based calls
pass `Session.Axes`.

| PI VI | Arguments (as wired) | Used by |
|---|---|---|
| `Mercury_GCS_Configuration_Setup` | §1.2 | Setup, re-initialisation (§4) |
| `Close connection if open` | System (the X call also gets a TRUE constant, purpose unknown) | Cleanup, re-initialisation |
| `FRF?` | System, Axes → Boolean array, element 0 | Get Reference, `chkref` |
| `FRF` | System, Axes | Do Reference |
| `POS?` | System, Axes → element 0 | Get Position, Get Position XY, moves (other axis), `chkwafer`, limits loop |
| `MOV` / `MVR` | System, Axes, [value] | Move Absolute (X/Y/XY) / Move Relative |
| `VEL` / `VEL?` | System, Axes, [speed] / → element 0 | Set Speed / Get Speed |
| `STP` | System, Axes (`STP 1`, as Z) | Stop, limits loop |
| `ERR?`, `GCSTranslateError` | System | Get Status, Get Error, Set Speed, Stop, Do Reference, limits loop |
| `General wait for movement to stop` | System, Axes, Add. wait, Polling cycle, Add. wait only | Wait End Move (client values), Do Reference (polling 10 ms) |
| `JAX`, `JON`, `JON?`, `JBS?` | System, Joystick ID, Joystick Axis / [ID], [mode] / [ID], F / [ID], [axis] | joystick commands |
| LabVIEW `Clear Errors` | — | Stop, `chkref`, limits loop |

Frames on the wire are those of 08 §3.4 (axis `"1"`) with daisy-chain addressing added by the driver. In GCS 2.0 a
daisy-chained command is prefixed with the target address (`2 MOV 1 12.5000`) and answers carry the addresses
**(inferred: GCS 2.0 addressing rule, not visible in ALOX code; how the single-byte commands `#5`/`#7` are addressed in a
daisy chain is unknown)**. Capture the real exchange before writing the transport (Q2).

---

## 2. Server structure (AloxXYServer)

| Step | Content | Evidence |
|---|---|---|
| Frame 0–1 | transparent panel; reset `error out`, `Exit`, `Connected`, `X Inited`, `Y Inited`, `XY Log Data` | map §2.8 |
| Frame 2 | read `<Documents>\alox\settings\alox.ini` (comment "Load Default settings from Alox configuration file"), §8 | `srv d3–d31` |
| Frame 3 | three parallel loops (below) | `srv d0` |
| Frame 4 | quit LabVIEW when run-time | map §2.8 |

- **TCP SERVER PRODUCER LOOP**: `TCP Listen` on `localhost:TCP Port`, 2000 ms; `Connected` = no Listen error (`srv d34`);
  timeout 56 → Read again (`srv d36`); a `Command` frame of any value `"Show UI".."Do Reference"` (all 29) is enqueued to
  `XY Stages Mgmt Queue` (blocking) and appended to a 500-entry history `XY Commands` (`srv d39`). Rest as 02 / map §2.2.
- **XY STAGE CONSUMER LOOP**: queue `XY Stages Mgmt Queue Name`, 5 elements, `Dequeue` timeout −1; shift registers
  `X Session`, `Y Session` initialised from the panel controls; a case per command (§3). A dequeue error runs an empty
  case **(inferred: `srv d131`)**.
- **WAFER XY LIMITS LOOP**: §6.
- After the loops: both queues are released; the PI links are **not** closed (`srv d0`).

Shared state (front-panel objects read/written through local variables): `X/Y Stage Settings`
(`alox_typedef_ext_xystage_properties` {Com, Baudrate, Axis, Speed, Slow Speed, Wafer Origin (mm), Chuck Origin (mm)};
the X cluster holds the X components of the origins, the Y cluster the Y components), `Wafer Size (mm)`, `Wafer Limits`,
`Frequency (ms)`, `Connected`, `X Inited`, `Y Inited`, `Exit`, `X/Y Session`, display-only `X/Y Pos. (mm)`,
`Distance (mm)`, `Inside Wafer?`, `String`, `error out`.

---

## 3. Command reference

### 3.1 Common pattern

Same as Z (08 §5.1): the case starts with no error; `Variant To Data` decodes `Command Data` (its error is chained unless
noted); per-axis commands select `"X", Default` / `"Y"` and use that axis' session; the reply is sent with an
`Enqueue Send … Response` VI; `Log Server Error` (Server Type `XY Stages`) writes a line **only when the error is set**,
tag `ERR (XY STAGE)-`, file `<vision>\logs\<yymmdd>_xy_events.log`, the "Log" column being the `Extra String`
([`../09-configuration-logging-errors.md`](../09-configuration-logging-errors.md) §7). **ACK** replies use `Enqueue Send
Error Cluster Reponse`, which most probably sends nothing when the error is set (02 §8.4): every failed ACK command,
including a refused move (9404), leaves the client waiting for its own timeout **(inferred, strong)**.

### 3.2 The 29 commands

`S`/`A` = session System/Axes of the addressed axis; payload defaults in brackets.

| # | Command | Payload (typedef, default) | GCS calls in order | Reply | Log text / notes | Frames |
|---|---|---|---|---|---|---|
| 0 | Show UI | Boolean [T] | none; "On" → `FP.Transparency` 0, "Off" → hidden **(inferred)** | ACK | `Show UI` | `srv d118–d121` |
| 1 | Get Heartbeat | — | none; `Tick Count (ms)` | U32, no error | none; queued behind slow commands | `srv d117` |
| 2 | Setup | not read | §1.2 X then Y; no `VEL` | ACK | `Setup`; `X/Y Inited` ← no error | `srv d49` |
| 3 | Cleanup | not read | `Close connection if open` (two calls, one with TRUE), merged errors | ACK | `Cleanup`; then sessions ← default, `X/Y Inited` ← F | `srv d115` |
| 4 | Get Serial | `get_serial_data` {Axis X, Type F} | none (session strings) | STR: Type ? Identity : Serial Number | `Get Serial X/Y` | `srv d50–d52` |
| 5 | Get Status | `get_status_data` {Axis} | `ERR?` (reads **and clears** the controller error) | BOOL: no error and code 0 | `Get Status X/Y` | `srv d53–d55` |
| 6 | Get Error | `get_error_data` {Axis} | `ERR?`, `GCSTranslateError` | STR: description or "" | `Get Error X/Y` | `srv d56–d58` |
| 7 | Set Speed | `set_speed_data` {Axis, Speed (mm/s) 0} | `VEL` (+ its own `ERR?`), `ERR?`, translate | ACK | `Set Speed X/Y`; settings not updated | `srv d59–d61` |
| 8 | Get Speed | `get_speed_data` {Axis} | `VEL?` | DBL (0.0 on error) | `Get Speed X/Y` | `srv d62–d64` |
| 9 | Get Position | `get_position_data` {Axis} | `POS?` | DBL; writes `X/Y Pos. (mm)` | `Get Position X/Y` | `srv d68–d70` |
| 10 | Move Absolute | `move_absolute_data` {Axis, Move (mm) 0} | `chkref` (§4); `POS?` other axis; `chkwafer` (absolute); `MOV` | ACK; 9404 `X Move outside wafer` / `Y Move outside wafer` | `Move Absolute X (X=%f)` or `Ref. Warning Move Absolute X (X=%f)`; Y: `Move Absolute Y ( Y=%f)` / `Ref. Warning Move absolute Y ( Y=%f)` | `srv d72–d82` |
| 11 | Move Relative | `move_relative_data` {Axis, Move 0} | `chkref`; `chkwafer` (relative, other component 0); `MVR` | ACK; 9404 as above | `Move Relative X (X=%f)`, `Move Releative Y (Y=%f)`; no ref-warning text | `srv d47`, `d89–d99` |
| 12 | Wait End Move | `move_wait_data` {Axis, Add. Wait 0, Polling 10 ms, Add. Wait Only F} | `General wait for movement to stop` (`#5` polling, 300 s, 08 §8) | ACK | `Wait End Move X/Y` | `srv d100–d102` |
| 13 | Stop | `stop_data` {Axis} | `STP`, `ERR?`, `Clear Errors` | ACK, **always no error** | `Stop X/Y` (never written) | `srv d103–d105` |
| 14 | Enable Joystick | `joystick_data` {Axis, ID 1, Axis 1} | `JAX(S, 1, 1, A)`, `JON(S, [1], [1])` | ACK | `Enable Joystick X/Y` | `srv d106–d108` |
| 15 | Disable Joystick | same | `JAX`, `JON(S, [1], [0])` | ACK | `Disable Joystick X/Y` | `srv d109–d111` |
| 16 | Joystick Enabled ? | same | `JON?(S, [1])` | BOOL: value ≠ 0 | `Joystick Enabled X/Y` | `srv d112–d114` |
| 17 | Exit | — | none | none | `Exit` ← T, loop stops | `srv d116` |
| 18 | Log | `xy_log_data` {Log F, Path, Max Lines 0, Max Count 0} | none; stored in `XY Log Data` ("#TODO: Log every XY actions into files") | ACK | none | `srv d122` |
| 19 | Get Joystick Button | `joystick_data` {Axis, ID 1, Axis 1} | `JBS?(S, [ID], [Joystick Axis])` | BOOL element 0 | `Get Joystick Button` | `srv d123–d125` |
| 20 | Move Absolute XY | `move_absolute_data_2` {Move X, Move Y} | `chkref`; `chkwafer` (absolute); `MOV` X then `MOV` Y (comment "Move X AND Y with single command") | ACK; 9404 `XY Move outside wafer` | `Move absolute XY (X=%f; Y=%f)` / `Ref. Warning, Move absolute XY (…)` | `srv d84–d88` |
| 21 | Get Position XY | — | `POS?` X, `POS?` Y (chained) | DBL 2-D array holding x and y **(shape inferred)** | `Get Position XY`; `String` ← "Get popoposs" | `srv d71` |
| 22 | Set Chuck Origin | NI Vision point {x, y} (SGL) | none; `X/Y Stage Settings.Chuck Origin` | ACK | `Set Wafer Origin` (sic) | `srv d127` |
| 23 | Set Wafer Origin | same | none; `…Wafer Origin` | ACK | `Set Wafer Origin` | `srv d126` |
| 24 | Set Wafer Diameter | DBL [0] | none; `Wafer Size (mm)` | ACK | `Set Wafer Diameter` | `srv d128` |
| 25 | Set Wafer Limit | Boolean | none; `Wafer Limits` | ACK | `Set Wafer Limit` | `srv d129` |
| 26 | Set Wafer Check Freq | I32 [500] | none; `Frequency (ms)` | ACK | `Set Wafer Limit` (sic) | `srv d130` |
| 27 | Get Reference | {Axis} | `FRF?` | BOOL (F on error); `String` ← "GET REF" | `Get Reference X/Y` | `srv d65–d67` |
| 28 | Do Reference | not read | X chain `FRF`, wait (10 ms), `ERR?`, translate ∥ Y chain the same; merge | ACK | `Do Ref. Reference performed XY` | `srv d83` |
| — | Default | — | none | **none** | `String` ← "DEFAULT" | `srv d48` |

Payload typedef names are abbreviated (`typedef_ext_xy_<name>.ctl`). The setters (22–26) are memory-only: no
validation, no INI write, lost at server restart; they apply at once to the limits loop and to the next move.

### 3.3 Details that matter

- **Moves are acknowledged when sent**, never waited (as Z). The client must send `Wait End Move` per axis.
- **Move preparation** (cases 10, 11, 20), in this order (`srv d72`, `d47`, `d84`): `chkref` with both sessions and both
  settings (§4; its outputs write `X Inited`, `Y Inited` and the session registers); for a single-axis absolute move,
  `POS?` on the *other* axis; `chkwafer` (§5) with the destination point; on `In Wafer` the `MOV`/`MVR`, else the 9404
  error. Errors from `chkref`, `POS?` or `chkwafer` skip the move and become the (unsent) reply. `X/Y Pos. (mm)` ← target
  after a successful `MOV` (not after `MVR`).
- **The ref-warning log text is written only on error** (`Log Server Error`), so a successful silent re-homing leaves no
  trace in the server log (§4).
- **Move Absolute XY** sends two independent `MOV`s: both axes move at their own speed, the path is not a straight line
  **(inferred)**.
- **Do Reference** (`srv d83`): the X and Y chains have no data dependency (the Y `FRF` has no error input), so LabVIEW runs
  them in parallel: both `FRF` are sent at about the same time and both axes reference together; the two `General wait`
  calls then poll (serialised if the PI VI is non-reentrant) **(inferred)**. No `FRF?` check, no `SVO`/`RON`, no update of
  `X/Y Inited`. The client waits 60 s (§7). `String` ← "Do reference state" + error source **(inferred)**.
- **Stop** reads `ERR?` (clears controller error 10, 08 §5.3) and then clears every error: the reply is always success.
- **Joystick**: diagram comment "Joystick ID = 1 (First Joystick) / Joystick Axis = 1 (First Axis of System)": each
  controller has its own joystick input; motion commands are refused while `JON` is on (08 §9).
- **Exit** does not close the PI links; `Cleanup` must be sent first (the core does, §7.3).
- **Speeds**: `speed_DBL`/`slowspeed_DBL` are read into the settings but never used by the server; neither `Setup` nor the
  re-initialisation sends `VEL`. The speed is whatever the controller holds; the core sends `Set Speed` X and Y with
  `hardware.xy.speed` after `Setup` (§7.3). Whether the PI set-up VI changes `VEL` is unknown.

---

## 4. Reference handling

### 4.1 `alox_xy_check_axis_references` (before every move)

Inputs: `X/Y Axis Session In`, `X/Y Stage Settings`, `error in`; outputs: `X/Y Axis Session Out`, `X INITED`, `Y INITED`,
`REF WARNING?`, `error out` (`chkref d0–d2`).

```text
rx, e = FRF?(X.System, X.Axes, error in); Clear Errors(e)          # "X Ref OK ?"; incoming error also cleared
ry, e = FRF?(Y.System, Y.Axes, e)                                   # "Y Ref OK ?"
REF LOST = (not rx[0]) or (not ry[0])                                # inverted inputs of a compound OR; empty array -> F
REF WARNING? = REF LOST
if not REF LOST:                                                     # case "XY REFS OK"
    sessions unchanged; X INITED = Y INITED = TRUE; error out = no error
else:                                                                # case "X/Y REF LOST, REINIT ALL AXIS"
    Close connection if open(Y.System)                               # "Close Y"
    Close connection if open(X.System, TRUE)                         # "Close X"
    Clear Errors
    X session, e = Mercury_GCS_Configuration_Setup(X as §1.2, Reference Move = "Reference (FRF)")   # "Reinit X"
    X INITED = not e.status
    Y session, e = Mercury_GCS_Configuration_Setup(Y as §1.2, Reference Move = "Reference (FRF)", e) # "Reinit Y"
    Y INITED = not e.status; error out = e
```

Consequences:

- **Both axes are re-homed** when either reference is lost, and also when `FRF?` merely **fails** (link closed, cable,
  timeout, a previous `Cleanup`, or an error entering the VI: the X `FRF?` is then skipped and returns an empty array).
  A move sent before `Setup` or after `Cleanup` therefore opens the chain and references both axes first.
- The re-init blocks the consumer loop for the whole reference move (any duration up to the set-up VI's own limits); the
  client usually times out (10 s, §7) while the server continues.
- The hidden options `Check FRF?`, `Move to middle?`, `RON settings` of the set-up VI apply as for Z (08 §4.3): whether it
  references unconditionally and moves to mid-travel afterwards is unknown.
- After the re-init no `VEL` is sent (§3.3) and any pending controller error is discarded by the purge of
  `PI Open Interface` (08 §3.2) **(inferred)**.
- The owner keeps this behaviour but wants it visible ([map.md](map.md) §3.1): the only trace today is the `Ref. Warning`
  log text, written only when the move fails.

### 4.2 Setup vs re-initialisation (difference with 08 §4)

| | `Setup` (`srv d49`) | Re-initialisation (`chkref d2`) | Z `Z Setup` (08 §4.1) |
|---|---|---|---|
| `Reference Move` | unwired (driver default, unknown); label "Default speed/range settings" | `Reference (FRF)` | `Negative (FNL)` |
| Close before open | no | yes, Y then X | no |
| `VEL` after | no | no | yes (`Speed`) |
| Inited flags | `X/Y Inited` ← no error | `X/Y INITED` outputs → server locals | `Z Inited` |

The core never sends `Do Reference` at start-up (disabled code, map Part 1 frame 16), so on a cold machine the first move
re-homes both axes through `chkref` unless `Setup` already referenced them **(inferred)**.

### 4.3 Do Reference and `alox_xy_do_axis_references`

`Do Reference` is §3.3. `alox_xy_do_axis_references.vi` (reconnect with referencing, from its callees) has no caller.

---

## 5. Wafer check before a move (`alox_xy_check_wafer position`)

Inputs: `error in`, `X Session`, `Y Session`, `Dest. Move (mm)` {x, y} (SGL), `Move Mode` (F: relative, T: absolute),
`Wafer Origin (mm)` {Xorg, Yorg} (SGL), `Wafer Diam. (mm)` (DBL). Outputs `In Wafer ?`, `error out` (`chkwafer d0–d6`).

```text
if error in: return In Wafer = FALSE, error in                                   # outer "Error" case (d6)
if Xorg != 0 and Yorg != 0 and Diam != 0:                                       # three "!= 0" into an AND
    cx, ex = POS?(X); cy, ey = POS?(Y)                                          # always both, even for absolute moves
    if ex or ey: return FALSE, merged error                                     # inner "Error" case (d5)
    X = move.x if absolute else cx + move.x
    Y = move.y if absolute else cy + move.y
    In Wafer = sqrt(|Xorg - X|^2 + |Yorg - Y|^2) <= 80.0                         # see constants below
    return In Wafer, no error
else:                                                                            # "Wafer size and/or origin is undefined" (d2)
    return TRUE, no error
```

**The constants (zoomed on `chkwafer d0`, confirmed in `d1`, `d3`):** `Wafer Radius = Wafer Diam. × 0.5` feeds an
Expression Node `x+10` whose **output is not wired**; the constant `105` is **not wired**; the constant `80` is wired to
the tunnel that reaches the `≤` comparison. The effective rule is therefore *destination within 80 mm of the wafer
origin*, whatever the diameter; the diameter only enables the check (≠ 0). Plausible history **(inferred)**: radius + 10 mm
margin (`[general] wafersizeoffset_DBL = 10` exists in the INI, with no reader found), then 105 mm for a 210 mm chuck (the
AloxXYServer build folder is named `v1.8_210mm`), then 80 mm (a 160 mm circle, the size of the wafer map, §9.3). The
deployed executable may have been built with a different constant (Q4).

Callers build the destination as follows (`srv d72`, `d78`, `d90`, `d95`, `d84`):

| Command | `Dest. Move` | Mode |
|---|---|---|
| Move Absolute X | {target, current Y from `POS?` Y} | absolute |
| Move Absolute Y | {current X from `POS?` X, target} | absolute |
| Move Relative X / Y | {d, 0} / {0, d} | relative |
| Move Absolute XY | {x, y} | absolute |

Notes: the check is independent of `Wafer Limits` (it applies whenever origin and diameter are non-zero, i.e. always with
the shipped INI, §8); an origin coordinate of exactly 0 on **either** axis disables it; `POS?` may return the target of a
running move (08 §4.4); all arithmetic is single precision.

---

## 6. WAFER XY LIMITS loop (`srv d0`, `d132–d145`)

```text
loop until Exit:
    Wait (ms)(Frequency (ms))                                 # runs in parallel with the body: period = max(Frequency, body)
    if Wafer Limits and Connected and X Inited and Y Inited and not Exit:        # "Client connected & XY stages inited"
        if Xorg != 0 and Yorg != 0 and Wafer Size != 0:                         # else "Undefined wafer size or origin,
            x, e = POS?(X Session); y, e = POS?(Y Session)                        #  XY stage moving": Inside = F, nothing
            if no error:
                Distance (mm) = sqrt(|Xorg - x|^2 + |Yorg - y|^2); X/Y Pos. (mm) = x, y
                Inside Wafer? = Distance <= Wafer Size * 0.5                      # radius = diameter / 2, no margin
                if not Inside Wafer?:
                    frame 0 "Stop X Stage & Joystick": STP(X.System, X.Axes); ERR?(X.System); Clear Errors;
                                                     [JON(X.System, [1], [0]) inside a Diagram Disable "Disabled" frame]
                    frame 1 "Stop Y Stage & Joystick": the same on Y
            else: Inside Wafer? = F ("Current XY position unknown"), nothing else
    else: Inside Wafer? = F; Wafer Origin = Wafer Size = F ("Client not connected or XY stages not inited")
```

- **Period**: `Frequency (ms)`, INI `wafercheckfreq_I32` (500), changed live by `Set Wafer Check Freq`; 0 is accepted and
  would poll as fast as the link allows. `Wait (ms)` (watch icon) **(inferred from the icon)**.
- **Joystick**: the `JON … 0` calls sit in the "Disabled" frame of a Diagram Disable structure; the enabled frame is
  empty. **The joystick is not switched off**, despite the labels.
- **Nothing is reported**: no log line, no reply, no event; `ERR?` + `Clear Errors` swallow the controller's error 10.
- **Radius mismatch**: the loop uses `Wafer Size / 2` (50 mm with the shipped `wafersize_DBL = 100`), the move check uses
  80 mm (§5). A move to a point 50–80 mm from the origin is accepted, then stopped by the loop when the stage crosses
  50 mm. While the stage rests outside 50 mm, every later move or joystick motion is stopped within one period, including
  a move back towards the centre; the stage cannot be brought back by commands until `Wafer Limits` is set FALSE or the
  diameter/origin is changed **(inferred from the two formulas)**.
- **Gating**: the loop supervises only while a TCP client is connected (`Connected`) and both `Inited` flags are TRUE. It
  does nothing during a server start before `Setup`, between `Cleanup` and the next `Setup`, or when the core is
  disconnected.

### 6.1 Concurrency with the consumer loop (no lock)

The limits loop and the consumer loop call PI driver VIs on the same two system numbers, i.e. the same COM port, with no
semaphore, queue or other lock (`srv d0`). What can happen:

| Situation | Consequence |
|---|---|
| The two loops call the same PI VI (e.g. `POS?`) | serialised by LabVIEW if the PI VI is non-reentrant (unknown) |
| Different PI VIs at the same time (`POS?` from the limits loop while the consumer polls `#5` in `Wait End Move` or `Do Reference`, or runs `MOV`, `VEL`+`ERR?`, `FRF`, the re-init) | the driver's send and receive are separate calls (08 §12), so `POS? 1` and `#5` can interleave on the wire and each loop can read the other's answer **(inferred)**. Effects: parse errors (100 "Assign values…"), wrong values, `−7` timeouts. In the limits loop an error only means "Current XY position unknown" (no stop); in the consumer the command fails (a `Wait End Move` ends early with an error, an ACK the client never receives, §3.1) |
| The limits loop sends `STP` while the consumer waits in `Wait End Move` | the axis stops, `#5` reports "not moving", the wait returns **success**: the client believes the move completed; the controller's error 10 is cleared by the loop's own `ERR?` |
| `STP` during a reference move (`Do Reference`, or `chkref` re-init while `X/Y Inited` are still TRUE) whose path leaves the radius (e.g. towards a reference switch near the end of travel) | the referencing is aborted; PI says referencing must be stopped with `#24` (08 §5.3), so the controller state after `STP` is unknown; the reference stays lost and the next move re-homes again **(inferred)** |
| Interleaving during `Setup`/re-init | the set-up VI opens/closes the port while the loop issues `POS?` on a session that is being replaced **(inferred)** |
| `ERR?` from the limits loop | consumes controller errors that `Get Status`/`Get Error` would otherwise report |

The rewrite must serialise every exchange on the port (one lock per chain, held for a whole command/answer pair and for
pairs such as `VEL`+`ERR?`), and must decide explicitly how supervision interacts with waits and referencing (§10).

---

## 7. Main application side

### 7.1 `alox_manager_xy` (FGV, 32 methods, `alox_typedef_xy_manager_method`)

State: `Client => Server Queue Name`, `XY stages inited (Setup) ?`, `Store Last known position` (`mgr d0`). Each method
reads `hardware.xy.timeout` (code default 5000 ms, shipped 10000) unless stated, calls the `alox_xy_command_*` VI with
`Wait Response` = TRUE, and turns `timed out?` into an `alox_error` error. Every method's `error out` passes through
`Clear Errors` with specific code **−7**: a −7 carried by a value reply (PI receive timeout) is silently dropped
(`mgr d0`) **(inferred purpose)**.

| # | Method | Server command (payload) | Timeout | Error on timeout | Frame |
|---|---|---|---|---|---|
| 0 | Init | launch client: `hardware.xy.port` (504), queue `alox.xy.queue`; global `XY Server Queue Name` | — | — | `mgr d3` |
| 1 | Exit | close client ("Exit XY client"), which sends `Exit`: **the server terminates** | — | — | `mgr d4` |
| 2 | Show UI | Show UI (Boolean) | xy.timeout | 9402 "XY Timeout", source `Show UI` | `mgr d5` |
| 3 | Get Heartbeat | Get Heartbeat → U32 | xy.timeout | not seen (only the `False` frame was read) | `mgr d10` |
| 4 | Get Status | Get Status {Axis} | xy.timeout | 9402 `Get Status` | `mgr d13` |
| 5 | Setup | Setup | **30000** | 9402 `Setup`; inited ← no error and not timed out | `mgr d16` |
| 6 | Cleanup | if inited: Stop X, Stop Y (xy.timeout), errors cleared; then Cleanup | **10000** | 9402 | `mgr d19` |
| 7 | Is Inited? | register only | — | — | `mgr d25` |
| 8 | Is Connected? | Get Serial {X, T}: no error and identity ≠ "" | xy.timeout | — | `mgr d26` |
| 9 | Get Serial Number | Get Serial {Axis, Type} [X, T] | xy.timeout | 9402 | `mgr d29` |
| 10 | Set Speed | Set Speed {Axis, Speed} | xy.timeout | 9402 | `mgr d32` |
| 11 | Get Speed | Get Speed {Axis} | xy.timeout | 9402 | `mgr d50` |
| 12 | Get Position | Get Position {Axis}; on no error stores X or Y in the last known position | xy.timeout | **9099 "XY Error" on any error or timeout** | `mgr d53` (retry loop 5 × 100 ms with a 0–155 mm validity check is disabled, `d58`) |
| 13 | Move Absolute | Move Absolute {Axis, Move} | xy.timeout | **9099** | `mgr d67` |
| 14 | Move Relative | Move Relative {Axis, Move} | xy.timeout | 9402 | `mgr d73` |
| 15 | Move Wait | Wait End Move {Axis, 0, 10, F} | **xy.timeout** | 9402 | `mgr d76` |
| 16 | Stop | Stop {Axis} | xy.timeout | 9402 | `mgr d79` |
| 17/18 | Enable / Disable Joystick | {Axis, 1, 1} | xy.timeout | 9402 | `mgr d82`, `d85` |
| 19 | Joystick Enabled? | {Axis, 1, 1} → Boolean | xy.timeout | 9402 | `mgr d88` |
| 20 | Log | XY Log Data | xy.timeout | — | `mgr d94` |
| 21 | Get Client Queue | register | — | — | `mgr d24` |
| 22 | Get Joystick Button | {Axis, 1, 1} → Boolean | xy.timeout | 9402 | `mgr d91` |
| 23 | Move Absolute XY | {Move X, Move Y} | xy.timeout | 9402 | `mgr d70` |
| 24 | Get Position XY | Get Position XY → point; stores it as last known | xy.timeout | **9099** | `mgr d62` (disabled retry `d63`) |
| 25 | Get Last Position XY | register (not updated by moves) | — | — | `mgr d97` |
| 26–30 | Set Wafer Diameter (DBL), Set Chuck Origin / Set Wafer Origin (point "in XY stages coordinate system"), Set Wafer Limit (Boolean [T]), Set Wafer Check Freq. (I32 [500]) | the matching server setter | xy.timeout | 9402 | `mgr d35–d47` |
| 31 | Do Reference | Do Reference | **60000** | 9402, source `Get Status` (sic) | `mgr d0`, `d98` |

The async wrappers `alox_manager_xy_get_status_async`, `…_get_ref_async`, `…_get_heartbeat_async` read the global queue
name and call the command VI directly with `hardware.xy.timeout`; their timeout error is 9402 with source `Get Status`
(`alox_manager_xy_get_ref_async d0`, `…_get_status_async d0`).

### 7.2 Who calls what

| Caller | XY methods (and coordinate system) | Evidence |
|---|---|---|
| `alox_core_boot_cycle` frame 15 | `cs` Init; `Set Wafer Coord. Sys` with `hardware.xy.waferxorg/waferyorg/stagescoordsys` | map Part 1 §2.3 |
| `alox_core_init_cycle` | server module Kill/Load AloxXYServer (frame 3); `Init`, wait 1000, `Show UI`, `Setup`, `Get Position XY` (frame 16; `Do Reference` disabled); `Get Position XY` → `Convert` to Wafer → X_CTRL/Y_CTRL (27); `Set Speed` X and Y with `hardware.xy.speed` (default 10) (28) | map Part 1 |
| `alox_core_exit_cycle` | `Stop` X and Y, `Cleanup`, server module Unload (`Exit`), `Exit` | map Part 1 |
| `alox_core_ui` | `Joystick Enabled?` (status refresh); arrow keys: `Get Position XY` → `Move Absolute XY` (jog); joystick on/off through `tools_alox_joystick` (`Enable/Disable Joystick` X then Y) | map Part 1, `tools_alox_joystick d0` |
| `alox_modules_heartbeat_service` | every `hardware.xy.heartbeat` ms: `Get Heartbeat`; X and Y `Get Status` (async) AND `Get Reference` (async) → `X/Y STAGE STATUS`; failures count; at `maxfails`: Unload, `Exit`, Kill, 5 s, Load, `Init`, 1 s, `Setup` (no reference), log "Restarting XY Stages module…" / "XY Stages Restarted" | `hbs d36`, `hbs d50` |
| `alox_xyz_stages_ui` (properties, autofocus config) | `Get Speed` X (shows LOW/HIGH), `Set Speed` X and Y on change; `cs` `Get Wafer Coord. Sys`; displays `hardware.xy.waferlimit`/`wafercheckfreq`/axis keys (read-only); OK saves `hardware.xy.slowspeed`/`speed` | `xyzui d3–d8`, `d11`, `d23` |
| Tracking (LL) | `Move Relative` X, `Move Wait`, then Y | map Part 3 §3.7 |
| Detect Object (LL), Tilt (HL) | moves on a search pattern, `Get Position XY`, `Convert` (wafer) through `tools_tracking_move_relative` | map Part 3 §3.6, §3.13 |
| Mesa (HL) | move to the start position relative to the wafer centre (tilt-corrected) | map Part 3 §3.11 |
| Bowing (HL) | absolute moves in wafer coordinates (Wafer → Stages), retried 3 times | map Part 3 §3.12 |
| Thermal (LL) | `Move Absolute XY` after Chuck → Stages; polls `Get Position XY` until \|Δ\| ≤ 0.01 mm, re-issues after 30 s | map Part 3 thermal |
| `alox_vision_overlay_hardware_infos`, `alox_properties_ui`, `alox_bandit`, `alox_manager_server_module` | position overlay / wafer map / `Get Client Queue` (properties UI not reviewed, 1153 images) | `vi_index.json` |

No caller of `Set Wafer Origin`, `Set Chuck Origin`, `Set Wafer Diameter`, `Set Wafer Limit` or `Set Wafer Check Freq.`
was found in the reviewed VIs; `alox_properties_ui` is the only candidate left (Q8). Unless it sends them, the server runs
on its INI values.

### 7.3 Timing consequences

- `Move Wait` uses `hardware.xy.timeout` (10 s shipped). At 10 mm/s a move longer than ~100 mm, or any wait during a
  re-homing, times out with 9402 while the server keeps waiting; later commands queue behind it (queue of 5)
  **(inferred)**.
- A refused or failed move (9404, re-init failure) produces no reply: the core sees a timeout after 10 s, reported as
  9099 (Move Absolute) or 9402 (others) **(inferred, 02 §8.4)**.
- `Get Heartbeat` goes through the consumer queue: during a long wait or referencing it times out, which counts as a
  heartbeat failure (`hbs d36`).
- The heartbeat's `Get Status` sends `ERR?` to both controllers every 60 s (shipped), clearing pending controller errors;
  an unreferenced axis (`FRF?` FALSE) also counts as a failure. After `maxfails` (10) consecutive failures the server is
  killed and restarted; the restart runs `Setup` only, so the next move re-homes through `chkref`.

---

## 8. Configuration (INI)

The server reads `<Documents>\alox\settings\alox.ini` once at start (`srv d3–d31`); the main application reads the same
file through `alox_manager_properties` with dotted names. Values below are the code defaults and the values in the shipped
`alox_default.ini` (numbers use a **decimal comma**). `vc_text/xy/Alox.ini` and `AloxXYServer.ini` are LabVIEW run-time
INIs without XY settings.

| Section / key | Type | Reader | Default in code | Shipped |
|---|---|---|---|---|
| `[hardware.xy] port` | I32 | server (TCP port), manager | 504 | 504 |
| `[hardware.xy] comport` | I32 | server (X and Y) | 2 | 2 |
| `[hardware.xy] baudrate` | I32 index | server (X and Y) | 3 (115200) | 3 |
| `[hardware.xy] xaxis` / `yaxis` | I32 | server (System no. and device id); `xyzui` (display, default 0) | 1 / 2 | `xaxis_I32` **twice: 1 and 10**; 2 |
| `[hardware.xy] speed` / `slowspeed` | DBL | server (unused); core init `speed` (10); `xyzui` `speed` (5), `slowspeed` (10) | server 10 / 1 | 10,0 / 10,0 (plus unused `speed_I32`, `slowspeed_I32` = 10) |
| `[hardware.xy] waferxorg` / `waferyorg` | DBL | server (wafer origin); boot (`cs` wafer origin) | 0 / 0 | 77,375496 / 77,223198 |
| `[hardware.xy] chuckxorg` / `chuckyorg` | DBL | server (stored, never used) | 0 / 0 | 77,628868 / 88,223190 |
| `[hardware.xy] waferlimit` | BOOL | server (`Wafer Limits`); `xyzui` (display, default F) | F | **TRUE** |
| `[hardware.xy] wafercheckfreq` | I32 | server (`Frequency (ms)`); `xyzui` (display) | 500 | 500 |
| `[hardware.xy] stagescoordsys` | U32 | boot → `cs` (value 0–3 = system #1–#4) | `Coord. Sys. 4` in `cs` Init | 3 (= #4) |
| `[hardware.xy] timeout` | I32 | manager, async wrappers | 5000 | 10000 |
| `[hardware.xy] heartbeat` / `maxfails` / `watchdog` | I32 / U32 / BOOL | heartbeat service | 30000 / 3 / T | 60000 / 10 / TRUE |
| `[hardware.xy] xmin/xmax_I32`, `xmin/xmax_DBL`, `ymin/ymax_DBL` | — | no reader found | — | −25/25, 0/150, 0/150 |
| `[general] wafersize` | DBL | server (`Wafer Size (mm)`), bowing | 100 | 100,0 |
| `[general] wafersizeoffset` | DBL | no XY reader found | — | 10,0 |
| `[general] core`, `[log] maxlines` | I32 | server (log) | 1 / 1000 | 1 / 1000 |

A missing or unreadable key takes its default silently (as Z, 08 §2.1). With a duplicate key the value LabVIEW returns is
not known; the machine works, so the first (1) is presumably used **(inferred)**: with 10, the X controller would be
addressed as device 10.

---

## 9. Coordinate systems (main application)

### 9.1 Definitions (`cs d0` comments)

- **Wafer**: cartesian (X left → right, Y bottom → top); wafer (0, 0) = (Xorg, Yorg) in stage coordinates.
- **Chuck**: same rules with the chuck origin.
- **XY stages**: one of four systems, stored as U16 0–3 (`alox_typedef_stages_coordinate_system_control`, ring
  `Coord. Sys. 1`…`4`):

| Value | Name | X | Y | Stage (0, 0) |
|---|---|---|---|---|
| 0 | Coord. Sys. 1 | direct | direct | bottom left |
| 1 | Coord. Sys. 2 | indirect (right → left) | direct | bottom right |
| 2 | Coord. Sys. 3 | direct | indirect (top → bottom) | top left |
| 3 | Coord. Sys. 4 | indirect | indirect | top right |

The shipped INI selects value 3 (#4). Units: mm; points are NI Vision float points {x, y} (SGL).

### 9.2 `alox_manager_xy_coordinate_system` (FGV)

State: `Wafer Origin Defined`, `XY Stages coordinate system` (default `Coord. Sys. 4`), `XY Stages wafer origin (REF)`,
`XY Stages chuck origin (REF)`. Methods: `Init`/`Exit` (reset all, origins (0, 0), system #4, not defined, `cs d3–d4`);
`Set Wafer Coord. Sys` (system ← input, wafer origin ← `Point In`, defined ← T, `cs d5`); `Get Wafer Coord. Sys`
(`cs d6`); `Set/Get Chuck Coord. Sys` (same with the chuck origin; Set also sets the system and defined ← T, `cs d36–d37`);
`Wafer Coord OK ?` (returns `Wafer Origin Defined` **(inferred)**, `cs d35`); `Convert` (`cs d7–d34`).

`Convert(Src, Dst, Point)` uses the **stored** system (the `Stages Coord. system` input is not wired in `Convert`,
`cs d7`). Per axis, with `O` the wafer or chuck origin and `dir` from the table above (observed on `cs d12`, `d14`):

```text
Stages -> Wafer|Chuck:  direct: w = p - O        indirect: w = O - p
Wafer|Chuck -> Stages:  direct: p = w + O        indirect: p = O - w
same -> same:           identity
Wafer -> Chuck:         no case: the Dst "XY Stages, Default" frame runs, i.e. Wafer -> Stages **(inferred from frame order)**
Chuck -> Wafer:         identity (frame "Wafer" under Src "Chuck" passes the point) **(inferred)**
```

Example with system #4 and the shipped wafer origin (77.375, 77.223): stage (80, 70) → wafer (−2.625, 7.223).

### 9.3 Wafer map (`alox_manager_xy_map`)

`Init` creates an RGB IMAQ image "ALOX_XYZ_TARGET"; `Update` (`map d0`), when `Wafer Coord OK ?`: `Convert(Src = caller's
type, Dst = Wafer)`, then pixel = (80 + round(xw), 80 − round(yw)) in a 160 × 160 image (1 px = 1 mm, centre = wafer
origin), cross marker ±7 px, circles for the 150/100/76 mm wafers **(inferred)**. Used by `alox_properties_ui` and
`alox_bandit`.

---

## 10. Defects and risks for the rewrite

R = reproduce, F = fix, A = ask the owner. Recommended choice in bold.

| # | Defect / risk | Evidence | Recommendation |
|---|---|---|---|
| X1 | Error ACKs probably never sent: a refused move (9404), a failed Setup/re-init or a failed wait reaches the core as a 10 s timeout (9099/9402) | 02 §8.4, §3.1 | **F** with the project switch `send_error_replies`; A: capture a 9404 (Q1) |
| X2 | Move-check radius hard-wired to 80 mm; `x+10` and `105` dead; diameter only enables the check | `chkwafer d0` | **A** (Q4); meanwhile R the 80 mm rule behind a setting, and make the radius explicit (`move_limit_radius_mm`) |
| X3 | Supervision radius (diameter/2) ≠ move radius (80): stage stopped between 50 and 80 mm and then stuck outside | §6 | **F**: one radius for both; let the supervisor stop only motion that leaves the zone (or allow moves towards the centre); A which radius |
| X4 | Limits loop uses the port without a lock: interleaved frames, spurious errors, `STP` turning a wait into a false success, `STP` aborting a referencing | §6.1 | **F**: one lock per chain; supervision as a task taking the lock per exchange; while a reference or re-init runs, suspend supervision; a stop by the supervisor makes the pending wait fail with a dedicated error |
| X5 | Supervision is silent (no log/event), clears error 10, and its joystick switch-off is disabled code | `srv d135–d143` | **F**: log + websocket `xy_hardware_warning` event; A whether the joystick must be switched off (Q6) |
| X6 | Silent re-homing of both axes before any move when a reference is lost or `FRF?` merely fails (incl. comm errors, closed link, decode error) | `chkref d0–d2` | R the behaviour (owner) but **F**: log and report it as an event/status; do not treat a communication error as a lost reference (reconnect first, then `FRF?`) |
| X7 | Supervision only while a TCP client is connected and both axes inited | `srv d0` | **F**: supervise whenever the axes are inited and the limit is enabled (the integrated app has no TCP link) |
| X8 | Origin coordinate exactly 0 on either axis disables both checks; diameter 0 too | `chkwafer d0`, `srv d0` | **F**: explicit "wafer defined" flag; R the 0 = undefined meaning for INI compatibility |
| X9 | Wafer/chuck origin, diameter, limit, frequency are memory-only; no sender found in the core | §3.2, §7.2 | A (Q8); **F**: one source of truth in the integrated app (properties), persisted |
| X10 | `Do Reference` references X and Y in parallel, without checking or updating `Inited` | `srv d83` | A whether simultaneous referencing is safe (Q7); **R** simultaneous if yes; set `Inited` from the result |
| X11 | `Move Wait` bounded by `hardware.xy.timeout` (10 s) while a move can last longer | `mgr d76` | **F**: wait timeout from distance/speed (+ margin) or the 300 s driver limit |
| X12 | Heartbeat answered by the busy consumer; `Get Status` (`ERR?`) consumes controller errors | `hbs d36`, `srv d53` | **F**: health from the device object without hardware traffic during motion; read `ERR?` only under the lock and keep the last error for `Get Error` |
| X13 | `Stop` always reports success (`Clear Errors`) | `srv d103–d105` | **R** success to the caller, F: log the cleared errors |
| X14 | Manager drops −7 on every result; Get Position replaces every error by 9099 | `mgr d0`, `d53`, `d62` | **R** the codes seen by functions; log the original error |
| X15 | No `VEL` after Setup or re-homing: speed after a silent re-init is whatever the controller holds | §3.3 | **F**: re-apply the configured speed after every (re)initialisation |
| X16 | `Setup` reference behaviour depends on an unwired input and hidden options of the PI VI | §1.2, 08 §4.3 | A (Q3); F: explicit sequence (`SVO`, `FRF?`, `FRF` only if needed) |
| X17 | Two `MOV`s for an XY move (non-straight path) | `srv d84` | **R** (moves are short); A if a straight path is required |
| X18 | Copy-paste labels: Set Chuck Origin logs "Set Wafer Origin", Check Freq logs "Set Wafer Limit", "Move Releative Y", "Get popoposs", Do Reference timeout labelled "Get Status" | §3.2, `mgr d98` | **F** (not observable by clients except log text) |
| X19 | `Convert` Wafer → Chuck returns stage coordinates; Chuck → Wafer returns the input | `cs d20–d34` | **F** (convert through stages) after checking no caller relies on it |
| X20 | INI: duplicate `xaxis_I32` (1, 10), decimal comma, unused keys | §8 | **F**: clean INI; parser accepts the comma; first duplicate wins, with a warning |
| X21 | Single-precision arithmetic in the wafer check and conversions | `chkwafer d0`, `cs d12` | **F** (float64); differences < 1e-4 mm |
| X22 | `Exit` without `Cleanup` leaves the PI links open | `srv d116` | **F**: close the chain on shutdown |

### 10.1 Minimum contract for the XY device module

1. One `PiDaisyChain` object owns the port (from `comport`/`baudrate`), opens the chain once and addresses device
   `xaxis` (X) and `yaxis` (Y); every exchange runs under its lock.
2. Commands and replies keep the 29-command semantics of §3.2 (payload defaults, reply kinds, 9404 texts) for the
   functions that use them, with error replies sent (X1).
3. Before each move: reference check (X6), wafer check (§5, X2/X8), then `MOV`/`MVR`; never wait implicitly.
4. Supervision task (§6) with the lock, the same radius as the move check (X3), reported stops (X5).
5. Coordinate conversion of §9.2, stored system from `stagescoordsys` (0–3).
6. A simulator speaking addressed GCS (two devices) for tests, as the Z fake controller (phase 2).

---

## 11. Open questions for the owner

| # | Question |
|---|---|
| Q1 | Capture of port 504 (or the serial line) during a refused move: does the core receive a 9404 reply or only its timeout? |
| Q2 | Exact serial frames of the daisy chain (address prefixes, how `#5`/`#7` are addressed): can a capture be taken on the Moxa/COM2 link? Are both XY controllers C-663 with addresses 1 and 2? |
| Q3 | After `Setup` at power-up, are the XY axes referenced (does the first move home them)? Values of `Reference Move`, `Check FRF?`, `Move to middle?` on the PI set-up VI's panel. |
| Q4 | Which wafer-check radius is in the deployed AloxXYServer.exe (80, 105, or diameter/2 + 10)? Test: a move 90 mm from the wafer origin. Which rule do you want? |
| Q5 | With `waferlimit = TRUE` and a 100 mm wafer, has the stage ever been "stuck" outside the 50 mm circle? Should supervision use the same radius as the move check? |
| Q6 | Should the supervisor switch the joystick off when it stops the stage (the code to do it is disabled)? |
| Q7 | Is referencing X and Y simultaneously safe, and where are the reference switches (inside or outside the wafer circle)? |
| Q8 | Who sets the wafer/chuck origin, diameter, limit and check frequency at run time (properties UI?), and should they persist? |
| Q9 | Are the stage directions (coordinate system #4) and the chuck origin (77.63, 88.22) still valid on every delivered tool? |
| Q10 | Which value of the duplicated `xaxis_I32` (1 or 10) is meant? |
| Q11 | Should moves keep the two-`MOV` path, and is a maximum travel (INI 0–150 mm, disabled check 0–155 mm) to be enforced? |
