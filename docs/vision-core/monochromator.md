# Monochromator (WL) device: detailed specification

Specification of the ALOX monochromator ("WL", wavelength) behaviour, written for its rewrite as a device module of the Python
application (the AloxWLServer process is not migrated as a server, see [migration-plan.md](migration-plan.md)). It covers the
serial device and its byte protocol, the WL server (`alox_wl_server.vi`), the main-application manager
(`alox_manager_monochromator.vi`) and the wavelength/exposure table (`alox_manager_exposure_wl_lut.vi`).

Conventions:

- `<vi> dN` cites the block-diagram image `NNNN <vi>dN.png` of the HTML export (`d0` = `<vi>d.png`, the first visible case of
  each structure; `dN` = the N-th hidden frame). `p` = front panel.
- **(inferred)** marks a deduction that is not drawn as such in a diagram. "Manual" means the Spectral Products CM110/CM112
  user manual as quoted by web-search excerpts (the manual PDF itself could not be downloaded from this session, see
  [Sources](#sources)).
- Byte values are decimal unless written `0x..`.

## 1. Summary for the implementer

1. The device is a **Spectral Products Digikröm CM110/CM112-class monochromator** (CM11x binary RS-232 protocol). Confidence:
   high for the CM11x family, none for CM110 versus CM112 (same protocol). See §2.1.
2. The link is **9600 baud** (index 0), **8N1, no flow control** (VISA defaults), on **COM1** in the shipped `alox.ini`
   (code default COM2, bench test VI COM4). Every exchange is binary; values are 16-bit, **high byte first**.
3. The main application only uses **Setup, Cleanup, Is Inited/Connected, Get Serial, Get/Set Position, Get/Set Units, Get
   Groove/Blaze/Grating, Get/Set Scan Speed, Set Timeout, Show UI, Get Heartbeat**. Reset, Scan, Calibrate, Select Gratings
   and Log exist in the server but **no built application sends them** (§4.3, §6.1).
4. A GOTO (`16, hi, lo`) blocks until the device returns `status, 24`; the manager then waits `waitposition` ms (500 shipped).
   The camera exposure is taken from the wavelength/exposure table by **linear interpolation of column 2 (ExpoTh)** (§7).
5. Several defects must not be reproduced: the "command not accepted" test is applied to the first byte of **every** read
   (also data bytes), Get Groove/Blaze/Grating overwrite the manager's **last known position** (used by Get Image to pick the
   exposure), Set/Get Position timeouts raise no error, and the VISA port keeps its default **0x0A termination character**
   on a binary protocol (§9).

## 2. Hardware and link

### 2.1 Model identification

| Evidence | Where | Points to |
|---|---|---|
| Driver VIs named `spectral_products_{init,write,read,query,flush,exit}`, descriptions "ComInit - Initializes the selected serial port", "Write11x - Writes the data in 'string to write' to the serial port", "Read11x - Reads the number of characters specified by response size", "Query - Queries the status of the monochromator" | HTML pages of the six VIs | The Spectral Products LabVIEW driver for the **CM11x** (CM110/CM112) series |
| Query enum `typedef_spectral_products_query` (21 items: 0 Position, 1 Type, 2 Groove/mm, 3 Blaze, 4 Current grating, 5 Scan speed, 6 Step size, 7–10 zero offsets m1g1..m2g2, 11–12 calibrations, 13 # of gratings, 14 Current units, 15–18 Gr/mm and Blaze of gratings 1 and 2, 19 Serial number, 20 Novram #14); the enum value is sent as the QUERY sub-code | [typedefs.md](typedefs.md), `spectral_products_query d1` | CM110 QUERY codes (manual excerpt: `<56> <19>` returns the serial number high/low bytes) |
| Two-grating turret ("Set new grating (1 or 2)"), groove codes 0..7 = 3600..75 gr/mm | `alox_wl_server d65`, `spectral_products_query d5–d14` | CM110/CM112 (two gratings) |
| GOTO with a **2-byte** position (`16, hi, lo`) | `alox_wl_server d47` | CM11x; the DK240/480 series uses a 3-byte position (from memory of the DK manual, not re-checked) **(inferred)** |
| Speed table = maximum speed / 2^n, 1000 Å/s base for 1200 gr/mm | `alox_wl_server d42` | Manual excerpt: "scan speed is the grating's maximum speed divided by 2^n", example 1000 Å/s at 1200 g/mm |

Conclusion: CM110 or CM112, ~90 % confidence for the family. Ask the owner for the exact model, firmware and installed gratings
(open question Q1).

### 2.2 Serial parameters

| Parameter | Value | Source |
|---|---|---|
| Port | `COM<n>`, `n` = `[hardware.monochromator] comport_I32` (code default 2, shipped **1**); resource string built with `COM%d` | `alox_wl_server d7`, `d30` |
| Baud rate | `baudrate_I32` is an **index**: 0 → 9600, 1 → 19200, 2 → 38400, 3 → 115200, any other value → 9600 (code default 0, shipped 0 → **9600**) | `alox_wl_server d8–d12` |
| Data bits, parity, stop bits, flow control | Not wired: VISA Configure Serial Port defaults **8, none, 1, none** **(inferred)** | `spectral_products_init d0` (only timeout and baud rate wired) |
| Termination | No terminator is written (raw bytes). On reads, VISA's default **termination character enabled, 0x0A** stays active because the input is not wired **(inferred)**; see defect D2 | `spectral_products_init d0`, `spectral_products_read d3` |
| VISA I/O timeout | `timeout_I32` (code default 2000 ms, shipped **5000 ms**), set once at Setup | `alox_wl_server d13`, `d30` |
| Bench test VI | COM4, 9600, 5000 ms | `alox_test_monochromator d0` |

### 2.3 Timeouts

Two timeouts apply to every read (`spectral_products_read d2`, `d3`):

1. A **poll** loop (1 ms period) waits until `Bytes at Port ≥ Response Size` or `elapsed > Timeout (ms)`; `Timeout = 0` waits
   forever. Its output `timeout` = TRUE when the poll ended on time (FALSE when `Timeout = 0`).
2. Then a **VISA Read** of `Response Size` bytes runs in all cases, bounded by the VISA timeout of §2.2.

So the worst case of one read is poll timeout + VISA timeout; the poll value only matters where the `timeout` flag is used
(Reset ECHO loop, §4.3). Poll timeouts used by the server, with `T` = `WL Settings.Timeout (ms)`:

| Use | Poll timeout | Frame |
|---|---|---|
| Get Position, Get Groove/mm, Get Blaze, Get Grating, Set Position, Set Units, Scan, Calibrate, Select Gratings, Reset (all reads) | `T` | `alox_wl_server d46`, `d47`, `d51–d56`, `d64`, `d65` |
| Get Units, Get Scan Speed | `T / 100` | `d50`, `d45` |
| Set Scan Speed answer | `T / 500` | `d42` |
| Setup and Get Serial (serial-number query) | not visibly wired; query panel default 2000 ms **(inferred)** | `d30`, `d40`, `spectral_products_query p` |

Main-application side: each manager call waits for the server reply up to `hardware.monochromator.timeout` (default 5000,
shipped 5000), Setup 30000 ms; see §6.1.

## 3. Byte-level protocol

### 3.1 Driver primitives (`spectral_products_*`)

All primitives pass `error in` through untouched when it is set (Error case).

| VI | Behaviour | Frames |
|---|---|---|
| `init` | `VISA Configure Serial Port(resource, baud rate, timeout)` → session | `spectral_products_init d0` |
| `write` | `VISA Write(string)`; the caller builds the string from a U8 array (`Byte Array To String`) | `spectral_products_write d0` |
| `read` | Poll (§2.3), then `VISA Read(Response Size)`. **If byte[0] > 127**: error `{status TRUE, code = byte[0], source "Command not accepted"}`, else no error. Outputs `Read buffer`, `timeout` | `spectral_products_read d0`, `d2`, `d3` |
| `flush` | If `Bytes at Port > 0`: `VISA Read(Bytes at Port)` and discard; outputs `Flushed`, `extra byte` (= bytes were pending) | `spectral_products_flush d0`, `d2`, `d3` |
| `query(n)` | frame 0: `status byte = cancel byte = 0`; frame 1: write `[56, n]` (`n` = query enum as U8); frame 2: `read(2)` → `value = byte0·256 + byte1` (U16), then `read(2)` → `status byte = b0`, `cancel byte = b1`; both reads use the `Timeout` input. If `n` is 15 or 17 (Gr/mm of grating 1 or 2), the raw value is a code: 0 → 3600, 1 → 2400, 2 → 1800, 3 → 1200, 4 → 600, 5 → 300, 6 → 150, 7 and above → 75 | `spectral_products_query d1–d15` |
| `exit` | `VISA Close` | `spectral_products_exit d0` |

`spectral_products_query_old.vi` has the same frame layout (16 images) and no caller.

### 3.2 Commands used by the code

Every server handler that talks to the device **flushes the input buffer first**, then writes, then reads (exceptions noted).
"Reply" is what the code reads; "Manual" is the cross-check with manual excerpts.

| Op | Request bytes (as sent) | Reply read by the code | Manual (excerpt) | Match |
|---|---|---|---|---|
| QUERY | `56, n` | `hi, lo` then `status, 24` (4 bytes in two reads) | `<56> <query byte>` → data high/low byte; `19` = serial number | yes |
| GOTO (Set Position) | `16, hi, lo` (position U32 → U16, split) | 2 bytes `status, cancel` | `<16> <High> <Low>`; moves to `256·High + Low` in the current units; status byte returned | yes |
| SPEED (Set Scan Speed) | `13, hi, lo` | 2 bytes | `<13> <High> <Low>`, speed in Å/s, valid values per grating in an appendix | yes (values: §5.4) |
| UNITS (Set Units) | `50, u` (u = 0, 1, 2) | 2 bytes | `<50> <units byte>` | bytes yes; meaning of 0 to check (§5.1) |
| SCAN | `12, start_hi, start_lo, end_hi, end_lo` | 2 bytes | SCAN = 12 with start and end specifiers (layout not fully quoted) | probable |
| CALIBRATE | `18, hi, lo` | 2 bytes | `<18> <High> <Low>`: if a valid position, sets the scale factor; "erases factory settings"; run right after ZERO | yes |
| SELECT (grating) | `26, hi, lo` (U32 → U16, split) | 2 bytes | `<26> <grating byte>` (**one** byte) | **no**: one byte too many (D3) |
| ECHO | `27` | 1 byte | ECHO returns the byte (exempt from the status-byte rule per the DK manual excerpt) | probable |
| RESET | `255, 255, 255` | 2 bytes | RESET is exempt from the status-byte rule (DK excerpt) | reply read may time out (D5) |

Sources of the "as sent" column: QUERY `spectral_products_query d1`; GOTO `alox_wl_server d47`; SPEED `d42`; UNITS `d51`;
SCAN `d52`; CALIBRATE `d64`; SELECT `d65`; ECHO `d60–d62`; RESET `d58`.

### 3.3 Status and end-of-command bytes

- Status byte: manual "if `<Status Byte>` is smaller than 128 the command was accepted"; higher values flag errors bit by bit
  (bit 7 = not accepted; an excerpt mentions a bit for "specifier not acceptable"; the full bit table is in the manual
  appendix, not read here). The code only tests `> 127`, through `read` (§3.1), on the **first byte of each read**.
- End-of-command byte: the second byte of each 2-byte reply (`cancel byte`, expected 24) is read but **never checked**.
- For a QUERY the first read holds data, so a value ≥ 32768 (high byte ≥ 128) is wrongly reported as "Command not accepted"
  with code = high byte (D1). The second read (status) is checked correctly.
- Errors: VISA errors (for example −1073807339 timeout) and the "Command not accepted" error (code 128..255) are the only
  device errors; there is no retry in the server.

## 4. WL server (`alox_wl_server.vi`)

### 4.1 Structure and setup

- Outer sequence `[0..4]`: 0 front panel open, transparency 100 (`d1`); 1 `error out`, `Connected`, `WL Inited` ← FALSE,
  `WL Log Data` reset (`d2`); 2 read `alox.ini` (comment "Load monochromator default settings from Alox configuration file
  in C:/Users/<USER>/alox/settings/alox.ini") (`d3–d14`); 3 the two loops (`d0`, `d15`); 4 quit LabVIEW in run-time (`d70`).
- INI (`d3–d14`): section `hardware.monochromator`: `port_I32` (502) → TCP port; `[general] core_I32` (1) → `Core #`;
  `[log] maxlines_I32` (1000) → `#Lines`; a 3-iteration loop fills `WL Settings {Com U32, Baudrate U16, Timeout (ms) I32}`
  from `comport_I32` (2), `baudrate_I32` index (0, mapped to the rate in the same frame), `timeout_I32` (2000).
- Loops (`d0`): TCP SERVER PRODUCER LOOP (shared TCP protocol, [map.md](map.md) Part 2 §2.2; TCP queue max size 5, comment
  "#Question: Borner TCP Queue Size ?") and a consumer loop (labelled "Z STAGE CONSUMER LOOP", copy-paste) that dequeues
  `WL Queue Data {WL Command, Notifier Name, Command Data, 3 timestamps}` with an infinite timeout; a dequeue error stops it
  (`d69`). One command is processed at a time: the device is never accessed concurrently.
- State: `WL Session {COM Port Out (VISA), Serial Number (string)}`, `WL Settings`, `WL Log Data`, indicators `WL Inited`,
  `WL (nm)`.
- Replies go to the caller's notifier with `Enqueue Send Error Cluster / U32 / STR / Boolean Response`. Every handler logs
  through `Log Server Error` (Server Type `Monochromator`, extra string = command name). That VI writes **only when the
  error is set**: line `dd/mm/YYYY - HH:MM:SS.mmm - ERR (MONO WL)-(code) : source (Command)` to the panel log and to
  `<vision>\logs\<yymmdd>_wavelength_events.log` ([09-configuration-logging-errors.md](../09-configuration-logging-errors.md) §7).

### 4.2 Setup sequence (command `Setup`)

`alox_wl_server d30` (= `d37`):

1. `resource = Format("COM%d", WL Settings.Com)` (the `Command Data` of the request is ignored).
2. `spectral_products_init(resource, baud, WL Settings.Timeout)`.
3. `flush`, then `query(19 Serial number)`; the U16 is converted to a decimal string.
4. `WL Session ← {resource, serial}` **even on error**; `WL Inited ← NOT error.status`; reply = error cluster; log "Setup".

No check that the device answers anything else (no ECHO, no type query); a previous session is not closed first (D10).

### 4.3 Commands (`typedef_ext_wl_command`, 23 items)

Reply type in brackets. "flush" = `spectral_products_flush`. All device handlers log their command name (on error only).

| # | Command | Input (`Command Data`) | Exchange, in order | Output / effects | Frame |
|---|---|---|---|---|---|
| 0 | Show UI | Boolean | none | panel transparency 0 (show) / 100 (hide); non-Boolean → error 9002 [err] | `d32–d35` |
| 1 | Set Timeout | `{Timeout (ms) I32}` | none | `WL Settings.Timeout` ← value (memory only; the VISA timeout keeps the Setup value) [err] | `d68` |
| 2 | Get Heartbeat | — | none | `Tick Count` ms [U32] | `d36` |
| 3 | Setup | ignored | §4.2 | [err] | `d30` |
| 4 | Cleanup | — | `VISA Close` | `WL Session ← {"", ""}`, `WL Inited ← FALSE` [err] | `d38` |
| 5 | Is Connected ? | — | none | `resource name ≠ ""` [BOOL] | `d39` |
| 6 | Get Serial | — | flush; `56,19` → `hi,lo,status,24` | decimal string [STR] | `d40` |
| 7 | Is Inited ? | — | none | `resource name ≠ "" AND serial ≠ ""` [BOOL] | `d41` |
| 8 | Set Scan Speed | `{Speed U8}` (0 fastest .. 10 slowest, comment) | flush; `56,2` (Groove/mm, poll `T`); build table (§5.4); flush; `13, hi, lo`; read 2 (poll `T/500`) | [err] | `d42–d44` |
| 9 | Get Scan Speed | — | flush; `56,5` (poll `T/100`) | raw value [U32] | `d45` |
| 10 | Get Position | — | flush; `56,0` (poll `T`) | raw position [U32]; also `WL (nm)` indicator | `d46` |
| 11 | Set Position | `{Position U32}` | flush; `16, hi, lo` (U32 → U16); read 2 (poll `T`) | on no error `WL (nm)` ← Position [err] | `d47–d49` |
| 12 | Get Units | — | flush; `56,14` (poll `T/100`) | raw units [U32] | `d50` |
| 13 | Set Units | `{WL Units enum U16}` 0 Centimeters, 1 Nanometers, 2 Angstroms | flush; `50, u`; read 2 (poll `T`) | [err] | `d51` |
| 14 | Scan | `{Start U16, End U16}` | flush; `12, sh, sl, eh, el`; read 2 (poll `T`) | [err]; a scan longer than the timeouts fails **(inferred)** | `d52` |
| 15 | Get Groove/mm | — | flush; `56,2` | [U32] (comment "Groove: rainure/strie") | `d53` |
| 16 | Get Blaze (nm) | — | flush; `56,3` | [U32] | `d54` |
| 17 | Get Grating | — | flush; `56,4` | current grating [U32] | `d55` |
| 18 | Reset | — | frame 0 flush; frame 1 ("Reset Monochromator") `255,255,255`, read 2 (poll `T`); frame 2 `t0 = now`, wait `T` ms; frame 3 loop { wait `T` ms; write `27` ("ECHO cmd used to check comm."); read 1 (poll `T`) } until `now − t0 > 30000` **or** the read's `timeout` flag is TRUE; frame 4 flush | error = that of frames 0, 1, 4 (the loop's errors are dropped) [err]; see D5 | `d56–d63` |
| 19 | Calibrate | `{Grating Data U32}` (comment "Allows for recalibration of gratings table, input (value1) = desired wavelength") | flush; `18, hi, lo`; read 2 | [err] | `d64` |
| 20 | Select Gratings | `{Grating Data U32}` ("Set Grating: set new grating (1 or 2)") | flush; `26, hi, lo`; read 2 | [err]; see D3 | `d65` |
| 21 | Exit | — | none | stops the consumer loop, releases the queues; no reply [none] | `d66`, `d0` |
| 22 | Log | `WL Log Data {Log, Path, Max Lines, Max Count}` | none | stored only ("#TODO: Log every WL actions into files ...") [err] | `d67` |
| — | Default | — | none | no reply | `d31` |

Commands that the main application never sends (no caller in any build, `vi_index.json`): Reset, Scan, Calibrate, Select
Gratings, Log (`alox_wl_command_{reset,scan,calibrate,set_gratings,log}` have no caller). Exit is sent by
`alox_manager_server_module` "Unload Server".

## 5. Units, ranges, gratings, calibration, speed

### 5.1 Units

- The position is a raw unsigned 16-bit number in the **current device units** (manual: "`256·High + Low` [units]").
- The main application sets **Nanometers** (value 1) at init and after a module restart, then works in nm only
  (`alox_core_init_cycle` frame 22, [map.md](map.md) Part 1; `alox_modules_heartbeat_service`, map Part 2 §2.5).
- `typedef_ext_wl_units` names value 0 "Centimeters". The CM-series documentation is believed to define 0 = microns (from
  memory, not confirmed by the excerpts): check in the manual (Q2). The rewrite should only expose nm.
- A manual excerpt says that a valid units byte makes the instrument move to the zero-order position: to confirm (Q2); the
  init cycle sends `Set Position 0` right after `Set Units` anyway.

### 5.2 Ranges and checks

- No range check anywhere on the server side: the U32 position is converted to U16 by LabVIEW (high bits dropped
  **(inferred)**), so 65536 nm becomes 0.
- Disabled manager code (`alox_manager_monochromator d30`, inside a Diagram Disable frame) treated a read position
  `> 1500` as invalid and retried up to 5 times with `timeout/5`; it is not active.
- Table editor bounds (`tools_alox_exposure_verif`, map Part 4 §3.9): WL 0..3000 nm, exposure 33..1 999 800 µs, brightness
  1..255. The default exposure table covers 0 and 700..950 nm (§7.3).

### 5.3 Gratings, calibration, reset, scan

- Gratings: two positions (1, 2). `Get Grating` = QUERY 4; `Get Groove/mm` = QUERY 2 (the current grating's density, not
  coded); `Get Blaze (nm)` = QUERY 3. Per-grating values (QUERY 15–18) are only reachable through the driver enum; codes 15/17
  return 0..7 mapped to 3600..75 gr/mm (§3.1).
- "Select Gratings" (server cmd 20): sends `26` + 2 bytes; changes the active grating (manual) — unused by the application.
- "Calibrate" (cmd 19): CALIBRATE `18` + position; manual: the current position is declared to be that value, the scale
  factor is recomputed, factory settings are erased; must follow a ZERO command (not implemented in the code). Unused.
- "Reset" (cmd 18): device reset `255,255,255` (re-homes the grating **(inferred)**), then ECHO polling for up to 30 s.
  Unused.
- "Scan" (cmd 14): SCAN `12` from Start to End at the current speed. Unused; the application's "search wavelength" function
  scans by repeated GOTO instead (§6.3).

### 5.4 Scan speed table (Set Scan Speed)

`alox_wl_server d42–d44`: `G` = Groove/mm from QUERY 2. For `i = 0..9`: `base_i = 1000 · 2^(−i)`;
`speed_i = round(base_i / 3)` if `G = 1800`, else `speed_i = round(base_i · 1200 / G)` (U16).
The request uses `speed[Speed]` (Index Array, out-of-range → 0).

| Level | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1200 gr/mm | 1000 | 500 | 250 | 125 | 63 | 31 | 16 | 8 | 4 | 2 | **0** |
| 600 gr/mm | 2000 | 1000 | 500 | 250 | 125 | 63 | 31 | 16 | 8 | 4 | **0** |
| 1800 gr/mm (code) | 333 | 167 | 83 | 42 | 21 | 10 | 5 | 3 | 1 | 1 | **0** |

The manual excerpt (max speed / 2^n; 1000 Å/s at 1200 g/mm; 62 Å/s at 600 g/mm with n = 5) agrees with the 1200/G rule; the
1800 gr/mm branch (/3 instead of ×2/3) and level 10 are suspect (D4). Units of the speed value: Å/s (manual).

## 6. Main application side

### 6.1 Manager `alox_manager_monochromator` (FGV, 21 methods)

Non-reentrant functional global (`Method`, `Data In`/`Data Out` variants, `Boolean Out`). Shift registers: TCP queue name,
`Inited ?`, `Last Known Position` (`d0`). It runs only when `error in` is clear (Error case: `Boolean Out = F`, `d62`). Each
device method reads `hardware.monochromator.timeout` (default 5000), calls `alox_wl_command_<x>` with `Wait Response = T`,
and on `timed out?` raises **9403** "wavelength module command timeout" (`alox_en.lang`) unless stated.

| # | Method | Command VI / behaviour | Outputs | Frame |
|---|---|---|---|---|
| 0 | Init | `hardware.monochromator.port` (default **502**) → `alox_wl_command_launch_client`, queue `alox.monochromator.queue`; global `Monochromator Queue Name` | `Inited ? ← F`, `Last Known Position ← 0` | `d3` |
| 1 | Exit | `alox_wl_command_close_client`; global name ← "" | `Inited ? ← F`, position ← 0 | `d4` |
| 2 | Setup | Setup with **30000 ms**, then Get Position (timeout property); 9403 on Setup timeout | `Inited ? ← NOT error`, `Last Known Position ← position` | `d5` |
| 3 | Cleanup | Cleanup | `Inited ? ← F` | `d8` |
| 4 | Is Inited? | Is Inited ? | Boolean (also stored in `Inited ?`) | `d11` |
| 5 | Is Connected? | Is Connected ? | Boolean | `d14` |
| 6 | Set Timeout | Set Timeout `{Timeout (ms)}` | timed-out flag | `d20` |
| 7 | Get Serial | Get Serial | string | `d17` |
| 8 | Set Units | Set Units `{WL Units}` | timed-out flag | `d0`, `d23` |
| 9 | Get Units | Get Units | U32 | `d26` |
| 10 | Get Position | Get Position, **no 9403** | U32 position, `Boolean Out` = timed out; `Last Known Position ← value` (even after a timeout) | `d29`, `d31` |
| 11 | Set Position | `{Position}` → Set Position, **no 9403**; if no error: wait `hardware.monochromator.waitposition` ms (default 0, shipped 500; read errors cleared) | `Boolean Out` = timed out; `Last Known Position ← Position` when no error (also after a timeout) | `d48–d52` |
| 12 | Get Groove | Get Groove/mm | U32; **also written to `Last Known Position`** (D6) | `d32` |
| 13 | Get Blaze | Get Blaze (nm) | U32; **also `Last Known Position`** | `d35` |
| 14 | Get Grating | Get Grating | U32; **also `Last Known Position`** | `d38` |
| 15 | Get Speed | Get Scan Speed | U32 | `d41` |
| 16 | Set Speed | Set Scan Speed `{Speed U8}` | — | `d44` |
| 17 | Get Last Position | none (no device access) | `Last Known Position` | `d47` |
| 18 | Show UI | Boolean (default T) → Show UI | — | `d53` |
| 19 | Get Heartbeat | Get Heartbeat (timeout property, default 500) | U32 tick, 0 on timeout | `d58` |
| 20 | Get Client Queue | none | TCP queue name (used by "Unload Server") | `d61` |

The command VIs (`alox_wl_command_*`, e.g. `alox_wl_command_set_position d0`) enqueue `{Send Command, {Command Data,
Notifier Name}}` to the client queue and wait on a synchronous notifier for up to `timeout in ms`; the client
(`alox_wl_client`) frames the command on TCP (map Part 2 §2.2). In the rewrite this whole TCP layer disappears: the manager
calls the device driver directly.

Asynchronous helpers (`alox_manager_monochromator_get_heartbeat_async d0`, `…_get_serial_async d0`): read the global queue
name and call `get_heartbeat` / `get_serial` directly (timeout property, default 500 resp. 5000; 9403 on timeout), so the
heartbeat service is not blocked behind the FGV. Each heartbeat check therefore performs a real serial exchange (flush +
QUERY 19).

### 6.2 Who calls the manager

| Caller | Methods (observed unless marked) |
|---|---|
| `alox_core_init_cycle` (frames 20, 22, 27) | Init, wait 1000 ms, Show UI; Setup, Set Units Nanometers, Set Position 0; profile `wavelength.wlmanual` → Set Position (map Part 1) |
| `alox_core_exit_cycle` | Cleanup, then "Unload Server" (Exit command), Exit |
| `alox_core_ui` | on each display timer tick, if the WL server is up: Get Position → `MONO_IND`; `[68] MONO_MOVE` event → Set Position **(inferred)** |
| `alox_modules_heartbeat_service` | heartbeat/serial async every `heartbeat` ms (60000 shipped); after `maxfails` (10) failures with `watchdog` TRUE: Unload Server, Exit, kill, restart AloxWLServer, Init, Setup, Set Units Nanometers; websocket `hardware_warning`/`hardware_error`/`hardware_reset` (`monochromator_module`) (map Part 2 §2.5) |
| `alox_manager_server_module` | Get Client Queue (Unload Server) |
| `alox_function_ll_get_image_state_machine` | **Get Last Position** → exposure LUT (§7.4) (`… get_image_state_machine d27`) |
| `alox_vision_overlay_hardware_infos` | "WL (nm): %d" overlay line (Get Last Position **(inferred)**) |
| `alox_function_ll_search_wavelength_state_machine` | Move WL: Set Position(WL) + LUT Get Exposure(WL) (`d24`); WL Pos OK ?: Get Serial (`""` or error → error **9041** "Monochro. Error"), Get Position, compare with target; equal → next state, else stay in "WL Pos OK ?" (`d29`) |
| `alox_function_ll_auto_exposure_state_machine` (+ `_ui_config`) | Set/Check Wavelength states, restore the initial WL on error/stop; error 9094 "auto-exposure error (monochromator)" (map Part 3 §3.8) |
| `alox_function_hl_mesa_measurement_state_machine`, `…_hl_bowing_…`, `…_ll_autofocus_…`, `…_ll_detect_object_…`, search-wavelength UI configs, `alox_properties_ui` | set the manual WL / restore the initial WL, display values (map Part 3; methods not all read) |

Functions and UIs that end up moving the monochromator: init, Search Wavelength (SCAN and MANUAL), Auto Exposure (All
Wavelengths), Measure Mesa (Set Wavelength state, `wavelength.wlmanual`), Tilt ("reset wavelength", changelog 1.0.0.7), the
core UI wavelength control, REST routes that run these functions.

### 6.3 Changing the wavelength in a measurement

Search Wavelength, state `Move WL` (`alox_function_ll_search_wavelength_state_machine d24`): monochromator Set Position
(U32 nm), then `alox_manager_exposure_wl_lut` Get Exposure(WL) → `Exposure(WL)`; next state `WL Pos OK ?` (`d29`), then
Refocus / Detect circle / Measure (`d59`, contrast = mean(OX) − mean(NOX)), next WL = WL + Step.

Exposure and wait after a change (Get Image, Exposure Mode "Fixed", `… get_image_state_machine d27`, `d30`):

1. High Mag.: `exposure = LUT.Get Exposure(Get Last Position)`; NaN/invalid → error **9014**; Low Mag.: fixed low-mag exposure.
2. If the new exposure is outside `current ± hardware.camera.<mag>.exposuretolerance`: Set Camera Exposure (log 008001/008101
   "set high-angle camera exposure time to '%f'").
3. If the LED or the exposure changed: wait `100 + 0.11 · max(new, old) / 1000` ms (exposures in µs), as drawn ("Wait for
   valid frame").

The changelog 1.0.0.11 says "the software is now waiting 2*MAX(old exposure time, new exposure time), after each wavelength
change"; the auto-exposure `Wait` state waits 2 × exposure (map Part 3 §3.8). The rewrite should implement one documented
rule: **after a wavelength change, wait `2 · max(old, new exposure)` before using a frame** (Q5).

## 7. Wavelength / exposure table (`alox_manager_exposure_wl_lut`)

### 7.1 Storage and format

- Stored in the **active profile**, property `general.exposures`, as a 2-D DBL array, one row per wavelength, 7 columns
  (diagram note "Exposure Files columns", `alox_manager_exposure_wl_lut d0`):

| Col | Name | Unit | Meaning |
|---|---|---|---|
| 1 | WL | nm | wavelength (sorted ascending) |
| 2 | ExpoTh | µs | theoretical (fixed) exposure, **the one used by Get Exposure(WL)** |
| 3 | ExpoMin | µs | auto-exposure search lower bound |
| 4 | ExpoMax | µs | auto-exposure search upper bound |
| 5 | BMin | grey level | brightness measured at ExpoMin |
| 6 | BMax | grey level | brightness measured at ExpoMax |
| 7 | ExpoAuto | µs | exposure found by Auto Exposure |

- Columns 1 and 2 are mandatory; if 3 and 4 are missing, `ExpoMin/Max = ExpoTh ∓ 20 %`; columns 5–7 are optional (NaN).
- File import/export ("CSV"): **tab-delimited** text (`Read/Write Delimited Spreadsheet`, delimiter = Tab constant), no
  header. Import reads doubles; `#columns ≥ 4` → use columns 3–4 else ±20 % (factor 0.2); `#columns ≥ 7` → use columns 5–7
  else NaN (`d4`, `d5`). On a read error the table is replaced by the default table (frame "Default Tables", `d6`).
  Export writes the table with format `%.0f` (decimals lost, NaN written as text) (`d7`); the file-path wiring was not seen
  (dialog if unwired **(inferred)**).
- Folder key `alox_setup.ini [folders] exposures = "/D/alox/02-Vision/exposures"` is read only by
  `tools_alox_shared_exposures_folder` (caller outside the project).

### 7.2 Methods (`alox_typedef_exposures_wl_lut_method`)

| # | Method | Behaviour | Frame |
|---|---|---|---|
| 0 | Create Default | Default table from globals (§7.3), ExpoMin/Max = ExpoTh ∓ 20 %, BMin/BMax/ExpoAuto = NaN; writes `general.exposures`; outputs Wavelengths | `d3` |
| 1 | Import CSV | §7.1 | `d4–d6` |
| 2 | Export CSV | §7.1 | `d7` |
| 3 | Get Exposure(WL) | `tools_alox_interpolate_data_from_wavelength(col 1, col 2, WL)` → Exposure (µs) | `d8` |
| 4 | Get Fixed Expo | row of the exact WL (`Search 1D Array`); not found → 9093 "AE Error (Invalid wavelength)" | `d9`, `d13` |
| 5 | Set Fixed Expo | replaces ExpoTh of the exact WL; not found → 9093 | `d0`, `d12` |
| 6 | Get Auto Expo | if `#WL > 0` and ExpoMin/ExpoMax columns at least as long: interpolates ExpoTh, ExpoMin, ExpoMax, BMin, BMax, ExpoAuto at WL → `AE Data Out`; else 9092 "AE Error (AE data not available)" | `d15`, `d16` |
| 7 | Set Auto Expo | `tools_alox_extrapolate_data_from_wavelength`: insert or replace the WL row with `AE Data In`, re-sort | `d18` |
| 8 | Reset Auto Expo | columns 5, 6, 7 ← NaN | `d19` |
| 9 | Save calib values | column 2 (ExpoTh) ← last column (ExpoAuto) (diagram comment wrongly says "Resets the automatic exposure data") | `d20` |
| 10 | Has Auto Expo ? | `#WL > 0`, all columns at least as long, BMin, BMax, ExpoAuto without NaN, ExpoAuto without ±Inf | `d21` |
| 11 | Has Fixed Expo ? | not opened | `d22` |
| 12 | Get Wavelengths | column 1 as U32 | `d23` |
| 13 | Set Wavelengths | if the table is not `N × 7`: **reset it to N × 7 NaN**; then column 1 ← wavelengths | `d24` |

Interpolation (`tools_alox_interpolate_data_from_wavelength`, map Part 4 §3.9): invalid if empty or sizes differ; clamped
to the first/last value outside the table; exact match returns that value; otherwise linear between the bracketing rows.
`numpy.interp` reproduces it. Auto-exposure results only change Get Exposure(WL) after "Save calib values" copies ExpoAuto
into ExpoTh (`d8`, `d20`).

### 7.3 Default table

Globals `Alox Def WL Table` / `Alox Def Exp Table (µs)` (`alox_core_global_variables p`), 12 rows:

| WL (nm) | 0 | 700 | 725 | 750 | 775 | 800 | 825 | 850 | 875 | 900 | 925 | 950 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ExpoTh (µs) | 27000 | 18000 | 20000 | 24000 | 36000 | 55000 | 87100 | 132000 | 189000 | 277000 | 414000 | 628000 |

Diagram TODO: "Revoir la table d'expos théoriques par défaut, inadaptée aux caméras du module OPTO" (`… lut d0`). The init
cycle calls Create Default when Get Wavelengths returns an empty table (map Part 1, init frame 26).

### 7.4 Table editor (`alox_wl_auto_exposures_table_ui`)

Events (`d6–d68`): Timeout, New (reset table), CLEAR AE, Open (import file), Save (export file), cell edit checks, Add
above/below, Remove, **OK/APPLY** (`d48`): rejects WL = −1 or non-increasing wavelengths, then `Set Wavelengths` and, per
row, `Set Fixed Expo` (ExpoTh parsed `%d`) and `Set Auto Expo` (ExpoMin/Max `%d`, BMin/BMax/ExpoAuto `%f`); **Save calib**
(`d59`): `Save calib values`; Cancel, Panel Close. Rows are coerced by `tools_alox_exposure_verif` (§5.2). Opened from the
Get Image, Search Wavelength and Auto Exposure settings panels and the properties UI.

## 8. Configuration keys

`alox.ini` (folder from `alox_setup.ini [folders] settings`), typed keys:

| Section / key | Type | Reader | Code default | Shipped (`alox_default.ini`) |
|---|---|---|---|---|
| `[hardware.monochromator] port` | I32 | server (TCP port), manager Init | 502 | **505** |
| `… comport` | I32 | server | 2 | 1 |
| `… baudrate` | I32 index 0..3 | server | 0 (9600) | 0 (9600) |
| `… timeout` | I32 ms | server: VISA timeout and poll base `T`; manager: reply timeout | server 2000, manager 5000 (500 in Get Heartbeat and the heartbeat async VI) | 5000 |
| `… waitposition` | U32 ms | manager Set Position | 0 | 500 |
| `… heartbeat` | I32 ms | modules heartbeat service | 30000 | 60000 |
| `… maxfails` | U32 | modules heartbeat service | 3 | 10 |
| `… watchdog` | BOOL | modules heartbeat service | TRUE | TRUE |
| `[general] core`, `[log] maxlines` | I32 | server | 1, 1000 | 1, 1000 |
| `[log] savewavelengthdata`, `saveautoexpodata`, `saveautoexpoimages` (and `savewavelengthimage`, read by search WL) | BOOL | functions | — | TRUE, TRUE, FALSE |
| `[user.rights.<role>] propertiesmonochromator`, `searchwavelength`, `searchwavelengthsetup`, `autoexposure`, `autoexposuresetup` | BOOL | rights | — | TRUE for all roles |

Profile properties: `general.exposures` (table §7), `wavelength.wlmanual` (manual WL, nm), search-wavelength and auto-exposure
parameters (map Part 3).

## 9. Defects and risks for the rewrite

| # | Defect / risk | Reproduce | Fix in the rewrite |
|---|---|---|---|
| D1 | "Command not accepted" test on byte 0 of every read: QUERY data with high byte ≥ 128 (value ≥ 32768, e.g. a serial number) is reported as an error | QUERY 19 on a unit whose serial ≥ 32768 → error code = high byte (`spectral_products_read d3`, `…_query d4`) | Check bit 7 of the **status byte** only; verify the end byte = 24 |
| D2 | VISA termination char (0x0A) left enabled on a binary link **(inferred)**: a reply byte equal to 10 (e.g. status 10) ends `VISA Read` early; the remaining byte is consumed by the next flush | hard to provoke; positions whose low byte is 10 (266, 522, 778 nm …) are safe only because 0x0A is then the last byte | pyserial, exact-length reads, no terminator |
| D3 | SELECT sends `26, hi, lo`; manual: one grating byte | Select Gratings 2 → bytes `26, 0, 2`; the device gets grating 0 then a stray byte **(inferred)** | Send `26, g`; confirm with Q2 |
| D4 | Speed table: level 10 → speed 0 (10 entries for 11 levels); 1800 gr/mm branch divides by 3 | Set Scan Speed 10 → `13, 0, 0` | Table from the manual appendix, levels 0..9 |
| D5 | Reset: reads 2 bytes after `255,255,255` (may time out); ECHO loop stops when the echo does **not** arrive and otherwise runs the full 30 s; its errors are dropped | Reset with a working unit → ≈ 30 s + `T` per iteration | `reset()`: send, then ECHO until the byte `27` comes back or 30 s, then flush; raise on timeout |
| D6 | Manager Get Groove/Blaze/Grating overwrite `Last Known Position`; Get Image uses it to pick the exposure | Get Groove (1200) then Get Image Fixed → exposure of "1200 nm" (clamped to the 950 nm row) (`alox_manager_monochromator d32`, `… get_image d27`) | Keep the last position in the device object, updated only by goto/position |
| D7 | Set Position / Get Position timeouts raise no error; Set Position stores the target as the last position anyway; Get Position stores the timed-out value | stop the server during a move | Raise a timeout error; never cache an unconfirmed position |
| D8 | Is Connected/Is Inited TRUE after a failed Setup (resource name kept, serial "0") **(inferred)** | wrong COM port → Setup error, then Is Inited ? → TRUE | State from the real open + ECHO/serial result |
| D9 | No range check; U32 → U16 wraps **(inferred)** | Set Position 70000 | Validate 0..max (per grating), reject otherwise |
| D10 | Setup does not close an open session first (port stays busy) **(inferred)** | Setup twice | `open()` closes first |
| D11 | "WL Pos OK ?" loops on Get Position until it equals the target, no limit or delay visible | device stopping at target ± 1 (other units, calibration) → endless loop (`… search_wavelength d29`) | GOTO is synchronous: check once, tolerance, count retries |
| D12 | Set Timeout changes only the poll base, not the VISA timeout; memory only | — | One timeout setting per operation, in the device config |
| D13 | Port 502 (code) vs 505 (INI); one `timeout` key with two meanings (VISA vs TCP) | — | No TCP in the rewrite; separate `serial_timeout` and `move_timeout` |
| D14 | Log Server Error writes at the start of the daily file and only on error | (doc 09 §7.2) | Normal append logging of every device command at debug level |
| D15 | LUT Set Wavelengths with another row count wipes ExpoTh to NaN → Get Exposure(WL) NaN (the 1.0.0.3 "freeze on NaN" class); Export `%.0f`; Import error silently installs the default table | apply a table with one more row and do not fill ExpoTh | Validate the table as a whole; keep decimals; report import errors |
| D16 | Get Image waits `100 + 0.11·max` ms, the changelog says `2·max` | — | One rule (Q5) |
| D17 | Copy-paste labels: consumer loop "Z STAGE CONSUMER LOOP", manager case comments ("Is Inited ?", "Get Position", "Set Position" on unrelated cases), "Save calib values" comment, units enum "Centimeters" | — | — |

Risk for the implementation: moves longer than the reply timeout (5 s shipped) — the CM110 slew time over a few hundred nm is
not known from the code. Use a move timeout computed from the distance and the slew speed, or a generous fixed value, and keep
the serial port exclusive to one device object (the heartbeat's serial query must go through the same lock).

## 10. Open questions for the owner

1. Q1: Exact model (CM110 or CM112), firmware, installed gratings (gr/mm, blaze, grating 1 or 2 in use), COM port on the
   production PC (INI COM1, test VI COM4).
2. Q2: The CM110/CM112 manual, Appendix K (serial commands): units byte meaning (0 = µm?), SELECT byte count, RESET reply,
   status-bit table, speed table for each grating, whether UNITS moves to zero order.
3. Q3: Typical and worst-case GOTO duration (to set the move timeout).
4. Q4: Are Reset, Scan, Calibrate, Select Gratings and Set Scan Speed needed in production? (No built caller uses the first
   four.) Should the rewrite offer them as maintenance tools only?
5. Q5: Wait after a wavelength change: `2·max(old, new exposure)` (changelog) or Get Image's `100 ms + 0.11·max`?
6. Q6: Is `wavelength 0 nm` (zero order) a real operating point (first row of the default table)?
7. Q7: The default exposure table is marked as unsuitable for the OPTO cameras: provide the production table per tool.
8. Q8: A serial capture of one Setup + Set Position + Get Position on the real unit, to freeze the protocol in tests.

## 11. Proposed device interface (Python)

```text
class Monochromator:                      # one instance owns the serial port (lock inside)
    open() / close()                      # pyserial 9600 8N1, no flow control; flush; serial = query(19)
    is_open: bool; serial_number: str
    query(code: int) -> int               # write [56, code]; read 2 → value; read 2 → status, end (24); check status bit 7
    goto(nm: int) -> None                 # validate range; write [16, hi, lo]; read status, 24 within move_timeout
    position() -> int                     # query(0)
    set_units(u: Units) / units()         # [50, u] / query(14); the application always uses nanometres
    grooves(), blaze(), grating(), speed()# query(2), query(3), query(4), query(5)
    set_speed(level), select_grating(g), calibrate(nm), scan(a, b), echo(), reset()   # maintenance only
    last_position: int                    # updated by goto/position only
```

A simulator implements the same byte protocol (status 0, end byte 24, configurable move time) so that the check tool and the
tests can run without the instrument.

## Sources

Code: the VIs cited above (HTML export), [typedefs.md](typedefs.md), [map.md](map.md) Parts 1–4, `alox_default.ini`,
`changelog.txt`, `translations/alox_en.lang`, `vi_index.json`.

Web (the PDFs could not be fetched from this session: `www.spectralproducts.com` and `manuals.online` are outside its network
allow-list; the cross-check relies on search excerpts of these documents):

- Spectral Products, Digikröm CM110/CM112 user manual, 2014 rev. A:
  <https://www.spectralproducts.com/pdf/CM110%20_USER_MANUAL_2014_rev.A.pdf> (GOTO `<16><High><Low>`, QUERY `<56>`, serial
  `<19>`, CALIBRATE `<18>`, UNITS `<50>`, `<26>` + grating byte, status < 128 = accepted).
- Spectral Products, Digikröm CM110/CM112 manual (older): <https://www.spectralproducts.com/pdf/CM%20Manual.pdf> (speed =
  maximum / 2^n, 1000 Å/s at 1200 g/mm, CALIBRATE after ZERO).
- ManualsLib copy: <https://www.manualslib.com/manual/1934265/Spectral-Products-Digikr-M-Cm110.html>.
- Artisan copy of the CM110 manual: <https://www.artisantg.com/info/Spectral_Products_CVI_CM110_Manual_2019215144914.pdf>
  (SPEED `<13><High><Low>`).
- Spectral Products DK series manual: <https://www.spectralproducts.com/pdf/DK_series_UserManual_revJul2016.pdf> (ECHO and
  RESET exempt from the status byte; "slews until 24 received").
- MathWorks Answers, CM110 GOTO example `16, 0, 250`:
  <https://www.mathworks.com/matlabcentral/answers/88625-using-fprintf-and-instrument-control-toolbox-with-serial-connection>.
