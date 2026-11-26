#!/usr/bin/env python3
r"""Check the thermal-map probes, read-only: the Optris pyrometer (RS-232) and the kSA BandiT reading (Modbus TCP).

Spec: docs/vision-core/map.md Part 6 §2.1 and the module docstrings of ``alox.thermal.optris`` and
``alox.thermal.bandit``. The tool uses the application's own protocol code, so it sends exactly what the new
application will send. It writes a report (``thermal_report_<date>-<time>.json`` with every value and every byte
exchanged, plus a ``.txt`` summary) next to this tool, or in ``--out``. That report is what to send back.

Run it on the furnace PC, from the repository folder, **with Alox Vision stopped** (LabVIEW keeps the COM port open)::

    py -3 tools\thermal_check.py --alox-ini D:\alox\settings\alox.ini
    py -3 tools\thermal_check.py --probe pyro --port COM7
    py -3 tools\thermal_check.py --probe bandit --host 192.168.1.2

``--fake`` runs everything against an emulated pyrometer and a fake Modbus server inside this program (a rehearsal).

What is sent (nothing changes a setting: no Set Emissivity, Set Transmissivity or laser command is ever sent)
-------------------------------------------------------------------------------------------------------------
Pyrometer (before each request the bytes waiting in the input buffer are discarded, which sends nothing):
``01 01`` (object temperature, ``--samples`` times, 50 ms apart), ``02 02`` (head), ``03 03`` (box), ``81 81``
(actual), ``04`` (emissivity), ``05`` (transmissivity), ``25`` (laser state), ``0E`` (serial), ``0F`` (firmware),
``45`` (sensor information), then ``01`` alone (object temperature without the checksum byte LabVIEW appends: does
the pyrometer need it?). A request without an answer is recorded and the next one is sent; after 3 in a row the rest
is not sent.

BandiT: one Modbus TCP connection to ``--host`` port 502; function 3 (read holding registers, what LabVIEW reads) of
1 register at ``startregister + 12288``, ``--samples`` times; then, for information, function 4 (input registers)
at the same address and function 3 with the unit ids 0, 1 and 255 (``--unit`` is the one used for the samples).

Exit code: 0 the tool ran to the end (findings are in the report), 1 a probe could not be reached, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

try:                                    # a source checkout without ``pip install -e .``: use <repo>/src
    import alox  # noqa: F401
except ImportError:                     # pragma: no cover - depends on how the tool is started
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from alox import __version__ as ALOX_VERSION
from alox.config import TypedIni
from alox.devices.cm11x import LinkFactory, pyserial_version
from alox.devices.cm11x_sim import sim_link_factory
from alox.thermal.bandit import (
    BanditSettings,
    load_bandit_settings,
    voltage_to_temperature,
)
from alox.thermal.errors import TIMEOUT, ProbeError
from alox.thermal.modbus import ModbusTcpClient
from alox.thermal.modbus_sim import FakeModbusServer
from alox.thermal.optris import (
    OptrisCt,
    PyroSettings,
    decode_temperature,
    load_pyro_settings,
)
from alox.thermal.optris_sim import OptrisEmulator
from alox.thermal.settings import load_thermal_settings

__all__ = ["PYRO_REQUESTS", "Environment", "main"]

TOOL_NAME = "thermal_check"
TOOL_VERSION = "1.0"
TOOL_DIR = Path(__file__).resolve().parent
REPORT_PREFIX = "thermal_report_"
EXIT_OK, EXIT_PROBLEM, EXIT_USAGE = 0, 1, 2
MAX_TIMEOUTS_IN_A_ROW = 3
SAMPLE_PERIOD_S = 0.05
#: The pyrometer reads after the samples: (label, request, answer length).
PYRO_REQUESTS: tuple[tuple[str, bytes, int], ...] = (
    ("head temperature", b"\x02\x02", 2), ("box temperature", b"\x03\x03", 2),
    ("actual temperature", b"\x81\x81", 2), ("emissivity", b"\x04", 2), ("transmissivity", b"\x05", 2),
    ("laser state", b"\x25", 1), ("serial", b"\x0e", 3), ("firmware", b"\x0f", 2), ("sensor info", b"\x45", 6),
    ("object temperature without checksum", b"\x01", 2))
UNIT_IDS = (0, 1, 255)


class UsageError(Exception):
    pass


@dataclass
class Environment:
    """What the tool needs from the outside world; the tests replace parts of it."""

    output: TextIO | None = None
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    pyro_link_factory: LinkFactory | None = None        # None: pyserial
    emulator: OptrisEmulator | None = None              # --fake: the emulated pyrometer (set by the tool)
    modbus_server: FakeModbusServer | None = None       # --fake: use this one (not closed by the tool)


def decode(label: str, data: bytes) -> Any:
    if "temperature" in label:
        return decode_temperature(data)
    if label in ("emissivity", "transmissivity"):
        return (data[0] * 256 + data[1]) / 1000.0
    if label == "laser state":
        return bool(data[0])
    if label == "serial":
        return (data[0] << 16) | (data[1] << 8) | data[2]
    if label == "firmware":
        return data[0] * 256 + data[1]
    if label == "sensor info":
        return {"model": data[0:2].decode("latin-1"), "low": data[2] * 256 + data[3], "high": data[4] * 256 + data[5]}
    return data.hex(" ")                                                    # pragma: no cover


class Check:
    def __init__(self, args: argparse.Namespace, env: Environment, out: TextIO) -> None:
        self.args = args
        self.env = env
        self.out = out
        self.report: dict[str, Any] = {
            "report": f"{TOOL_NAME} report",
            "versions": {"tool": TOOL_VERSION, "alox": ALOX_VERSION, "python": platform.python_version(),
                         "pyserial": pyserial_version(), "platform": platform.platform()},
            "started": datetime.now().astimezone().isoformat(timespec="seconds"),
            "options": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
            "pyrometer": None, "bandit": None, "findings": [], "problems": []}

    def line(self, text: str = "") -> None:
        self.out.write(text + "\n")
        self.out.flush()

    def problem(self, text: str) -> None:
        self.report["problems"].append(text)
        self.line(f"PROBLEM: {text}")

    # -- pyrometer
    def pyrometer(self, settings: PyroSettings) -> bool:
        self.line(f"Pyrometer on {settings.port_name}, {settings.baudrate} Bd 8N1, timeout {settings.timeout_ms} ms")
        if self.env.pyro_link_factory is not None:
            ct = OptrisCt(self.env.pyro_link_factory, monotonic=self.env.monotonic)
        else:
            ct = OptrisCt(monotonic=self.env.monotonic)
        section: dict[str, Any] = {"port": settings.port_name, "baudrate": settings.baudrate, "samples": [],
                                   "reads": []}
        self.report["pyrometer"] = section
        try:
            ct.open(settings.port_name, settings.baudrate, settings.timeout_ms / 1000.0)
        except ProbeError as exc:
            self.problem(f"pyrometer: {exc.message} (code {exc.code})")
            return False
        ok = True
        misses = 0
        try:
            for _ in range(self.args.samples):
                started = self.env.monotonic()
                try:
                    value = ct.object_temperature()
                    section["samples"].append(value)
                    self.line(f"  object temperature: {value:.1f} °C")
                    misses = 0
                except ProbeError as exc:
                    section["samples"].append(None)
                    self.line(f"  object temperature: no answer ({exc.message})")
                    misses += 1
                    if exc.code != TIMEOUT or misses >= MAX_TIMEOUTS_IN_A_ROW:
                        self.problem(f"pyrometer: {exc.message} (code {exc.code})")
                        return False
                self.env.sleep(max(0.0, SAMPLE_PERIOD_S - (self.env.monotonic() - started)))
            for label, request, length in PYRO_REQUESTS:
                entry: dict[str, Any] = {"read": label, "request": request.hex(" ")}
                try:
                    data = ct.transact(request, length, what=label)
                    entry.update(answer=data.hex(" "), value=decode(label, data))
                    misses = 0
                except ProbeError as exc:
                    entry.update(error=exc.message, code=exc.code, received=exc.received.hex(" "))
                    misses += 1
                section["reads"].append(entry)
                self.line(f"  {label}: {entry.get('value', 'no answer')}")
                if misses >= MAX_TIMEOUTS_IN_A_ROW:
                    self.problem("pyrometer: 3 requests in a row without an answer; the rest was not sent")
                    ok = False
                    break
        finally:
            section["frames"] = [{"t": round(t, 4), "dir": d, "hex": b.hex(" ")} for t, d, b in ct.frames]
            ct.close()
        answered = [r for r in section["reads"] if r["read"] == "object temperature without checksum"]
        if answered:
            needs = "error" in answered[0]
            self.report["findings"].append({"ref": "pyrometer checksum", "answer": "a read needs the checksum byte"
                                            if needs else "a read is answered without the checksum byte"})
        return ok and any(v is not None for v in section["samples"])

    # -- BandiT
    def bandit(self, settings: BanditSettings) -> bool:
        address = settings.register
        self.line(f"BandiT: Modbus TCP {settings.host}:{settings.port}, unit {settings.unit_id}, holding register "
                  f"{address} ({settings.start_register} + {settings.register_offset})")
        section: dict[str, Any] = {"host": settings.host, "port": settings.port, "unit": settings.unit_id,
                                   "register": address, "samples": [], "information": []}
        self.report["bandit"] = section
        client = ModbusTcpClient()
        try:
            client.open(settings.host, settings.port, settings.timeout_ms / 1000.0)
        except ProbeError as exc:
            self.problem(f"BandiT: {exc.message}")
            return False
        try:
            for _ in range(self.args.samples):
                started = self.env.monotonic()
                try:
                    mv = client.read_holding_registers(address, 1, unit_id=settings.unit_id)[0]
                except ProbeError as exc:
                    section["samples"].append({"error": exc.message, "code": exc.code})
                    self.problem(f"BandiT: {exc.message} (code {exc.code})")
                    if exc.link_failure:
                        return False
                    continue
                temp = voltage_to_temperature(mv, settings.voltage_scale, settings.temperature_scale)
                section["samples"].append({"millivolts": mv, "temperature": temp})
                self.line(f"  register {address}: {mv} mV → {temp:.1f} °C")
                self.env.sleep(max(0.0, SAMPLE_PERIOD_S - (self.env.monotonic() - started)))
            probes: list[tuple[str, Callable[[], list[int]]]] = [
                ("function 4 (input register)", lambda: client.read_input_registers(address, 1,
                                                                                     unit_id=settings.unit_id))]
            for unit in UNIT_IDS:
                probes.append((f"function 3, unit {unit}",
                               lambda unit=unit: client.read_holding_registers(address, 1, unit_id=unit)))
            for label, read in probes:
                entry: dict[str, Any] = {"read": label}
                try:
                    entry["value"] = read()[0]
                except ProbeError as exc:
                    entry.update(error=exc.message, code=exc.code)
                    if exc.link_failure:                            # reconnect for the next probe
                        client.open(settings.host, settings.port, settings.timeout_ms / 1000.0)
                section["information"].append(entry)
                self.line(f"  {label}: {entry.get('value', entry.get('error'))}")
        except ProbeError as exc:
            self.problem(f"BandiT: {exc.message}")
            return False
        finally:
            client.close()
        return any("millivolts" in s for s in section["samples"])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="thermal_check.py", description=__doc__.splitlines()[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--alox-ini", type=Path, metavar="PATH", help="alox.ini: [hardware.pyro] and [hardware.bandit]")
    p.add_argument("--probe", choices=("pyro", "bandit", "both", "auto"), default="auto",
                   help="what to check (auto: the pyrometer, and the BandiT when enableBandit is TRUE)")
    p.add_argument("--port", help="pyrometer serial port: COM7 (or 7), /dev/ttyUSB0, socket://host:port")
    p.add_argument("--baud", type=int, help="pyrometer baud rate (default: alox.ini, else 115200)")
    p.add_argument("--host", help="BandiT Modbus TCP address (default: alox.ini ipadress)")
    p.add_argument("--modbus-port", type=int, help="Modbus TCP port (default 502)")
    p.add_argument("--unit", type=int, help="Modbus unit id (default: alox.ini unitid, else 1)")
    p.add_argument("--timeout-ms", type=int, default=1000, metavar="MS", help="answer timeout (default 1000)")
    p.add_argument("--samples", type=int, default=5, help="temperature samples per probe (default 5)")
    p.add_argument("--fake", action="store_true", help="rehearsal: emulated pyrometer and fake Modbus server")
    p.add_argument("--out", type=Path, metavar="PATH", help="report folder or file (default: next to this tool)")
    return p


def _write_report(report: dict[str, Any], out: Path | None, started: datetime) -> tuple[Path, Path]:
    stem = REPORT_PREFIX + started.strftime("%Y%m%d-%H%M%S")
    if out is None:
        base = TOOL_DIR / stem
    elif out.suffix.lower() in (".json", ".txt"):
        base = out.with_suffix("")
    else:
        base = out / stem
    base.parent.mkdir(parents=True, exist_ok=True)
    json_path, txt_path = base.with_suffix(".json"), base.with_suffix(".txt")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"{TOOL_NAME} {TOOL_VERSION} — {report['started']}", f"Result: {report['result']}"]
    for name in ("pyrometer", "bandit"):
        section = report.get(name)
        if section:
            lines.append(f"{name}: samples {section['samples']}")
    lines += [f"finding: {f['ref']}: {f['answer']}" for f in report["findings"]]
    lines += [f"problem: {p}" for p in report["problems"]]
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, txt_path


def main(argv: Sequence[str] | None = None, *, env: Environment | None = None) -> int:
    env = env or Environment()
    argv = list(sys.argv[1:] if argv is None else argv)
    out = env.output or sys.stdout
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else EXIT_USAGE
    try:
        if args.samples < 1 or args.timeout_ms <= 0:
            raise UsageError("--samples and --timeout-ms must be positive")
        ini: Any = TypedIni.load(args.alox_ini) if args.alox_ini else {}
    except (UsageError, OSError, ValueError) as exc:
        out.write(f"error: {exc}\n")
        return EXIT_USAGE
    started = datetime.now().astimezone()
    check = Check(args, env, out)
    pyro = load_pyro_settings(ini, port=args.port)
    pyro = PyroSettings(pyro.com, args.baud or pyro.baudrate, args.timeout_ms, pyro.port)
    bandit = load_bandit_settings(ini)
    bandit = BanditSettings(host=args.host or bandit.host, port=args.modbus_port or bandit.port,
                            unit_id=bandit.unit_id if args.unit is None else args.unit,
                            start_register=bandit.start_register, register_offset=bandit.register_offset,
                            voltage_scale=bandit.voltage_scale, temperature_scale=bandit.temperature_scale,
                            timeout_ms=args.timeout_ms, connect_wait_ms=0)
    probes = {"pyro": ("pyro",), "bandit": ("bandit",), "both": ("pyro", "bandit")}.get(args.probe)
    if probes is None:
        probes = ("pyro", "bandit") if load_thermal_settings(ini).bandit_enabled and args.alox_ini else ("pyro",)
    server: FakeModbusServer | None = None
    code = EXIT_OK
    try:
        if args.fake:
            emulator = OptrisEmulator(clock=env.monotonic)
            env.emulator = emulator
            env.pyro_link_factory = sim_link_factory(emulator, sleep=env.sleep,  # type: ignore[arg-type]
                                                     monotonic=env.monotonic)
            pyro = PyroSettings(port="SIM", baudrate=pyro.baudrate, timeout_ms=pyro.timeout_ms)
            server = env.modbus_server or FakeModbusServer({bandit.register: 2500}).start()
            bandit = BanditSettings(host="127.0.0.1", port=server.port, unit_id=bandit.unit_id,
                                    start_register=bandit.start_register, register_offset=bandit.register_offset,
                                    voltage_scale=bandit.voltage_scale, temperature_scale=bandit.temperature_scale,
                                    timeout_ms=bandit.timeout_ms, connect_wait_ms=0)
        for probe in probes:
            ok = check.pyrometer(pyro) if probe == "pyro" else check.bandit(bandit)
            if not ok:
                code = EXIT_PROBLEM
    except KeyboardInterrupt:
        check.problem("interrupted by Ctrl+C")
        code = EXIT_PROBLEM
    except Exception as exc:                       # noqa: BLE001 - reported in the file, the links are closed
        check.problem(f"unexpected error: {exc}")
        check.report["traceback"] = traceback.format_exc()
        code = EXIT_PROBLEM
    finally:
        if server is not None and env.modbus_server is None:
            server.close()
    check.report["result"] = {"exit_code": code}
    try:
        paths = _write_report(check.report, args.out, started)
    except OSError as exc:
        out.write(f"The report could NOT be written: {exc}\n")
        return EXIT_PROBLEM
    out.write(f"Report written: {paths[0]}\nPLEASE SEND THIS FILE BACK: {paths[0]}\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
