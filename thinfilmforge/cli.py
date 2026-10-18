"""Command line interface for ThinFilmForge.

Examples
--------
    python -m thinfilmforge materials
    python -m thinfilmforge simulate --preset ar_broadband --range 400 700
    python -m thinfilmforge design --preset ar_broadband \\
        --band 420:680:max:0.005 --iterations 160 --out ar.json
    python -m thinfilmforge tolerance --stack ar.json --band 420:680:max:0.01 \\
        --runs 300 --sigma 1.0
    python -m thinfilmforge serve --port 8777
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import List, Optional, Sequence

from . import __version__, materials, metrics, tmm, tolerance
from .design import BandTarget, DesignSpec, design, parse_band, passes_spec
from .stack import Layer, Stack, preset, preset_ids

__all__ = ["main", "build_parser"]


def _fmt_pct(value: float) -> str:
    return "%.3f%%" % (100.0 * value)


def _resolve_stack(args) -> Stack:
    if getattr(args, "stack", None):
        return Stack.load(args.stack)
    if getattr(args, "preset", None):
        return Stack.from_dict(preset(args.preset)["stack"])
    raise SystemExit("error: provide --stack FILE or --preset ID")


def _band_args(args) -> List[BandTarget]:
    bands = [parse_band(text) for text in (args.band or [])]
    if not bands:
        raise SystemExit("error: at least one --band lo:hi:kind:value[:weight] is required")
    return bands


def _write_csv(path: str, wavelengths: Sequence[float], R: Sequence[float],
               T: Sequence[float], A: Sequence[float]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("wavelength_nm,R,T,A\n")
        for row in zip(wavelengths, R, T, A):
            fh.write("%.3f,%.8f,%.8f,%.8f\n" % row)


def cmd_materials(args) -> int:
    rows = materials.describe()
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False))
        return 0
    print("%-8s %-22s %-11s %8s %8s  %s" % ("KEY", "LABEL", "CATEGORY", "n@550", "k@550", "MODEL"))
    print("-" * 86)
    for row in rows:
        print("%-8s %-22s %-11s %8.4f %8.4f  %s"
              % (row["key"], row["label"], row["category"], row["n550"],
                 row["k550"], row["model"]))
    print("\n%d materials. Extend with a JSON table via materials.load_user_library()."
          % len(rows))
    return 0


def cmd_simulate(args) -> int:
    stack = _resolve_stack(args)
    lo, hi = args.range
    points = max(2, int(args.points))
    grid = [lo + (hi - lo) * i / (points - 1) for i in range(points)]
    result = tmm.simulate(stack, grid, args.theta, args.pol)
    report = metrics.spectral_summary(grid, result.R, result.T, result.A,
                                      visible=(lo, hi))
    colour = metrics.color.reflectance_color(grid, result.R)
    gd = metrics.group_delay(stack, grid, args.theta,
                             "s" if args.pol == "avg" else args.pol)

    if args.csv:
        _write_csv(args.csv, grid, result.R, result.T, result.A)

    if args.json:
        payload = {
            "stack": stack.to_dict(),
            "spectra": result.to_dict(),
            "metrics": report,
            "color": colour,
            "gdd": gd,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print("Stack: %s  |  %d layers  |  %.1f nm total"
          % (stack.name, len(stack), stack.total_thickness))
    print("Ambient %s -> %s, theta = %.1f deg, pol = %s"
          % (stack.incident, stack.substrate, args.theta, args.pol))
    print("-" * 62)
    for i, layer in enumerate(stack.layers, 1):
        print("  L%-2d %-8s %8.2f nm" % (i, layer.material, layer.thickness))
    print("-" * 62)
    print("R: avg %s  min %s  max %s  (%.0f-%.0f nm)"
          % (_fmt_pct(report["R"]["avg"]), _fmt_pct(report["R"]["min"]),
             _fmt_pct(report["R"]["max"]), lo, hi))
    print("T: avg %s  max %s" % (_fmt_pct(report["T"]["avg"]),
                                 _fmt_pct(report["T"]["max"])))
    print("Appearance under D65: %s   CIE xy = (%.4f, %.4f)   L* = %.1f"
          % (colour["hex"], colour["xy"][0], colour["xy"][1], colour["lab"][0]))
    print("Group delay %.3f fs at band centre -> penetration depth %.1f nm"
          % (gd["gd0"], gd["penetrationDepth"]))
    for warning in stack.validate():
        print("warning: %s" % warning)
    if args.csv:
        print("CSV written to %s" % args.csv)
    return 0


def _stack_from_materials(args) -> Stack:
    mats = [materials.get(m).key for m in args.materials]
    count = max(1, args.layers)
    layer_mats = [mats[i % len(mats)] for i in range(count)]
    q = 550.0
    return Stack([Layer(m, q / (4 * materials.get(m).n_at(q))) for m in layer_mats],
                 args.substrate, "Air", "optimised")


def cmd_design(args) -> int:
    stack = _resolve_stack(args) if (args.stack or args.preset) else _stack_from_materials(args)
    bands = _band_args(args)
    spec = DesignSpec(bands, theta=args.theta, pol=args.pol,
                      angles=args.angles, regularization=args.regularization)
    bounds = None
    if args.min_nm is not None or args.max_nm is not None:
        lo = args.min_nm if args.min_nm is not None else 4.0
        hi = args.max_nm if args.max_nm is not None else 400.0
        bounds = [(lo, hi)] * len(stack.layers)

    print("Optimising %d layers of %s" % (len(stack), "/".join(stack.materials)))
    for band in bands:
        print("  target %s" % band.label())
    started = time.time()
    last = {"t": 0.0}

    def progress(info):
        if time.time() - last["t"] < 0.4:
            return
        last["t"] = time.time()
        print("  [%s] gen %d/%d  merit %.3e  %.1fs"
              % (info["phase"], info["gen"], info["maxiter"], info["best"],
                 info["elapsed"]))

    result = design(stack, spec, popsize=args.popsize, maxiter=args.iterations,
                    seed=args.seed, time_budget=args.time, bounds=bounds,
                    progress=progress)
    elapsed = time.time() - started
    payload = result.to_dict()

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print("-" * 62)
    print("merit %.3e -> %.3e   (%d evaluations, %.1fs)"
          % (result.initial_merit, result.merit, result.evaluations, elapsed))
    for i, (mat, d) in enumerate(zip(result.materials, result.thicknesses), 1):
        print("  L%-2d %-8s %8.2f nm" % (i, mat, d))
    print("total physical thickness %.1f nm" % sum(result.thicknesses))
    m = result.metrics
    print("R: avg %s  max %s   |   T: avg %s"
          % (_fmt_pct(m["R"]["avg"]), _fmt_pct(m["R"]["max"]),
             _fmt_pct(m["T"]["avg"])))
    print("colour %s" % (result.color["hex"] if result.color else "-"))
    for item in result.breakdown:
        print("  %-28s mean %.4f  range [%.4f, %.4f]"
              % (item["label"], item["avg"], item["min"], item["max"]))
    ok, per_band = passes_spec(result.spectra["R"], result.spectra["wavelengths"], bands)
    if not ok:
        failed = [band.label() for band, flag in zip(bands, per_band) if not flag]
        print("warning: %d band(s) not met: %s"
              % (len(failed), "; ".join(failed)))
        print("         more layers or a wider thickness range may be required.")
    if args.out:
        print("design written to %s" % args.out)
    return 0


def cmd_tolerance(args) -> int:
    stack = _resolve_stack(args)
    bands = _band_args(args)
    report = tolerance.tolerance_report(
        stack, bands, runs=args.runs, thickness_sigma=args.sigma,
        thickness_mode=args.mode, index_sigma=args.index_sigma,
        angle_sigma=args.angle_sigma, theta=args.theta, pol=args.pol,
        seed=args.seed)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print("Monte-Carlo tolerance: %d runs, sigma = %.2f %s"
          % (report["runs"], args.sigma, "%" if args.mode == "percent" else "nm"))
    print("-" * 62)
    print("yield            %6.1f%%  (%d failures)" % (report["yield"], report["failures"]))
    print("merit  p50 %.3e   p95 %.3e   max %.3e"
          % (report["merit"]["p50"], report["merit"]["p95"], report["merit"]["max"]))
    for band in report["bands"]:
        print("  %-30s pass %5.1f%%" % (band["label"], band["passRate"]))
    print("-" * 62)
    print("layer sensitivity (mean |dR| for +2 nm):")
    for row in report["sensitivity"][:6]:
        print("  L%-2d %-8s %7.2f nm   dR %.3f%%   dMerit %+.2e"
              % (row["layer"], row["material"], row["thickness"],
                 100.0 * row["deltaR"], row["deltaMerit"]))
    return 0


def cmd_serve(args) -> int:
    from .server import serve
    serve(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_presets(args) -> int:
    items = []
    for pid in preset_ids():
        item = preset(pid)
        stack = Stack.from_dict(item["stack"])
        items.append({"id": pid, "name": item["name"],
                      "description": item["description"],
                      "layers": len(stack),
                      "totalThickness": round(stack.total_thickness, 1)})
    if args.json:
        print(json.dumps(items, indent=2, ensure_ascii=False))
        return 0
    for item in items:
        print("%-14s %-24s %2d layers  %7.1f nm" %
              (item["id"], item["name"], item["layers"], item["totalThickness"]))
        print("               %s" % item["description"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="thinfilm",
        description="ThinFilmForge - optical thin-film design and analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__)
    parser.add_argument("--version", action="version", version="%(prog)s " + __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("materials", help="list the built-in material library")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_materials)

    p = sub.add_parser("presets", help="list the ready-made demo designs")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_presets)

    p = sub.add_parser("simulate", help="compute R/T/A and colour of a stack")
    p.add_argument("--stack", help="stack JSON file")
    p.add_argument("--preset", choices=preset_ids(), help="use a built-in design")
    p.add_argument("--range", nargs=2, type=float, default=[380.0, 780.0],
                   metavar=("LO", "HI"), help="wavelength range in nm")
    p.add_argument("--points", type=int, default=201)
    p.add_argument("--theta", type=float, default=0.0, help="incidence angle (deg)")
    p.add_argument("--pol", choices=["s", "p", "avg"], default="s")
    p.add_argument("--csv", help="write the spectrum to a CSV file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("design", help="inverse-design layer thicknesses")
    p.add_argument("--stack", help="starting stack JSON")
    p.add_argument("--preset", choices=preset_ids())
    p.add_argument("--materials", nargs="+", default=["TiO2", "SiO2"],
                   help="material pool used when no stack is given")
    p.add_argument("--layers", type=int, default=6)
    p.add_argument("--substrate", default="BK7")
    p.add_argument("--band", action="append", metavar="LO:HI:KIND:VALUE[:WEIGHT]",
                   help="spectral target, repeatable")
    p.add_argument("--theta", type=float, default=0.0)
    p.add_argument("--pol", choices=["s", "p", "avg"], default="s")
    p.add_argument("--angles", nargs="+", type=float, default=None,
                   help="average the merit over several angles")
    p.add_argument("--iterations", type=int, default=160)
    p.add_argument("--popsize", type=int, default=26)
    p.add_argument("--time", type=float, default=30.0, help="time budget in seconds")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--min-nm", type=float, default=None)
    p.add_argument("--max-nm", type=float, default=None)
    p.add_argument("--regularization", type=float, default=0.0)
    p.add_argument("--out", help="write the design to a JSON file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_design)

    p = sub.add_parser("tolerance", help="Monte-Carlo manufacturing yield")
    p.add_argument("--stack", help="stack JSON file")
    p.add_argument("--preset", choices=preset_ids())
    p.add_argument("--band", action="append", metavar="LO:HI:KIND:VALUE[:WEIGHT]")
    p.add_argument("--runs", type=int, default=300)
    p.add_argument("--sigma", type=float, default=1.0,
                   help="1-sigma thickness error (%% or nm, see --mode)")
    p.add_argument("--mode", choices=["percent", "nm"], default="percent")
    p.add_argument("--index-sigma", type=float, default=0.0,
                   help="1-sigma refractive-index error (%%)")
    p.add_argument("--angle-sigma", type=float, default=0.0,
                   help="1-sigma incidence-angle error (deg)")
    p.add_argument("--theta", type=float, default=0.0)
    p.add_argument("--pol", choices=["s", "p", "avg"], default="s")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_tolerance)

    p = sub.add_parser("serve", help="start the local web workbench")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8777)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_serve)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except (ValueError, FileNotFoundError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
