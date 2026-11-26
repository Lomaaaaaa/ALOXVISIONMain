#!/usr/bin/env python3
r"""Measure the latency of the Fast autofocus on the machine (owner's decision B8).

Why: the Fast autofocus dates each image by the time it was grabbed, but the image was exposed earlier; the difference
(the latency) shifts every Z of the scan and the best Z. This tool measures it by comparing, on the same sample and
range, the peak of a Slow autofocus (no bias: the stage stands still during each exposure) with the peak of a Fast
autofocus run without compensation (``alox.functions.af_latency``). The result is the value of ``alox.ini``
``[hardware.z] fastaflatency_DBL`` (seconds).

Procedure (stop Alox Vision first: the tool opens the Z stage and the camera itself):

1. Put a sharp, flat sample under the camera (a mesa or any pattern with fine detail) and focus it roughly by hand.
2. Choose a range that contains the focus with margin, e.g. focus ± 0.2 mm, and a Slow step of 5 µm (0.005 mm).
3. Run, for the High Mag camera::

       py -3 tools\af_latency_check.py --alox-ini D:\alox\settings\alox.ini --camera highmag --z-min 4.8 --z-max 5.2

   The tool prints the plan and moves nothing until you type ``yes``. Each run is a Slow then a Fast autofocus
   (``--runs 3`` by default; ``--duration 4`` seconds for the Fast scan, as the production recipes use).
4. Copy the printed line ``fastaflatency_DBL = …`` into ``alox.ini`` under ``[hardware.z]``. If the spread is larger
   than about 10 % of the latency, repeat with a sharper sample or a longer duration. Repeat for ``--camera lowmag`` if
   the Low Mag camera is used for the Fast autofocus: the latency depends on the camera's exposure and transfer time;
   if the two values differ, use the High Mag one (the production autofocus camera) and report both.

``--simulate`` rehearses the procedure without hardware (the simulated camera does not depend on Z, so the numbers are
meaningless). A JSON report is written next to this tool (or in ``--out``).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from alox.app import AppConfig, Application
from alox.core.functions import FunctionRunner
from alox.functions.af_latency import (
    LATENCY_SETTING,
    LatencyEstimate,
    measure_fast_latency,
)
from alox.functions.focus import (
    Autofocus,
    AutofocusMethod,
    AutofocusParams,
    CameraType,
    FocusOptions,
)

CAMERAS = {"highmag": CameraType.HIGH_MAG, "lowmag": CameraType.LOW_MAG}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Measure the latency of the Fast autofocus (owner B8).")
    p.add_argument("--alox-ini", type=Path, help="alox.ini of the machine (hardware settings)")
    p.add_argument("--simulate", action="store_true", help="simulated Z stage and camera (rehearsal)")
    p.add_argument("--camera", choices=sorted(CAMERAS), default="highmag")
    p.add_argument("--z-min", type=float, required=True, help="scan start (mm)")
    p.add_argument("--z-max", type=float, required=True, help="scan end (mm)")
    p.add_argument("--step", type=float, default=0.005, help="Slow autofocus step (mm, default 0.005)")
    p.add_argument("--duration", type=int, default=4, help="Fast autofocus duration (whole seconds, default 4)")
    p.add_argument("--method", default="Sobel", help="focus metric (default Sobel)")
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--yes", action="store_true", help="do not ask before moving (rehearsals only)")
    p.add_argument("--out", type=Path, default=Path(__file__).resolve().parent, help="folder of the report")
    return p.parse_args(argv)


def run(args: argparse.Namespace, ask: Any = input, out: Any = print) -> LatencyEstimate | None:
    if not args.z_max > args.z_min:
        raise SystemExit("--z-max must be greater than --z-min")
    config = AppConfig(alox_ini=args.alox_ini, simulate=args.simulate, devices=("z", args.camera),
                       always_reference=False)
    with Application(config) as app:
        states = app.start()
        if any(state != "ready" for state in states.values()):
            out(f"devices not ready: {states}")
            return None
        z, camera = app.devices["z"], app.devices[args.camera]
        out(f"Z now {z.position():.4f} mm; scans {args.z_min:g} -> {args.z_max:g} mm with the {args.camera} camera:")
        out(f"  {args.runs} x (Slow, step {args.step:g} mm; then Fast, {args.duration} s)")
        if not args.yes and ask("The Z stage will move in that range. Type yes to start: ").strip().lower() != "yes":
            out("nothing moved")
            return None
        runner = FunctionRunner()
        runner.register(Autofocus(z, {CAMERAS[args.camera]: camera},
                                  options=FocusOptions(fast_latency_s=0.0, af_move_to_best=False)))
        params = AutofocusParams(camera_type=CAMERAS[args.camera], method=AutofocusMethod.coerce(args.method, "method"),
                                 z_min=args.z_min, z_max=args.z_max, subsampling=0.25)
        timeout = max(120.0, 4 * args.duration + (args.z_max - args.z_min) / args.step)
        estimate = measure_fast_latency(lambda name, p: runner.run_once(name, p, timeout=timeout), params,
                                        step=args.step, duration_s=args.duration, runs=args.runs)
    for i, r in enumerate(estimate.runs, 1):
        out(f"run {i}: slow peak {r.z_slow:.4f} mm, fast peak {r.z_fast:.4f} mm -> latency {r.latency_s * 1000:.1f} ms")
    out(f"latency {estimate.latency_s * 1000:.1f} ms (spread {estimate.spread_s * 1000:.1f} ms) at "
        f"{estimate.speed_mm_s:.4f} mm/s")
    out(f"add under [hardware.z] in alox.ini:  {estimate.ini_line()}")
    args.out.mkdir(parents=True, exist_ok=True)
    report = args.out / f"af_latency_report_{time.strftime('%Y%m%d-%H%M%S')}.json"
    report.write_text(json.dumps({"setting": LATENCY_SETTING, "camera": args.camera, **asdict(estimate)}, indent=2),
                      encoding="utf-8")
    out(f"report: {report}")
    return estimate


def main(argv: list[str] | None = None) -> int:
    return 0 if run(parse_args(argv)) is not None else 1


if __name__ == "__main__":
    sys.exit(main())
