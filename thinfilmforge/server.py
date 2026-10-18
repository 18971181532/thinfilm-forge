"""Local HTTP server and JSON API for the ThinFilmForge workbench.

The server is intentionally dependency-free: ``http.server.ThreadingHTTPServer``
plus a tiny router.  Optimisation runs in a background thread and is polled by
the browser, so the UI stays responsive during a 20-second design run.

API
---
``GET  /api/health``            service banner
``GET  /api/materials``         material library with dispersion samples
``GET  /api/presets``           ready-made demo stacks
``POST /api/simulate``          spectral response, colour, GDD, angle map
``POST /api/design``            start an inverse-design job -> ``{jobId}``
``GET  /api/design/<jobId>``    poll job state / result
``POST /api/tolerance``         Monte-Carlo yield analysis (synchronous)
"""

from __future__ import annotations

import json
import mimetypes
import os
import posixpath
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

from . import __version__, color, materials, metrics, tmm, tolerance
from .design import BandTarget, DesignSpec, design
from .stack import Stack, preset, preset_ids

__all__ = ["serve", "make_server", "JobManager"]

WEB_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_STARTED = time.time()
_MAX_BODY = 2 << 20


class JobManager:
    """Thread-safe registry of background design jobs."""

    def __init__(self, keep: int = 24):
        self._jobs: Dict[str, dict] = {}
        self._order: List[str] = []
        self._lock = threading.Lock()
        self._keep = keep
        self._counter = 0

    def submit(self, fn) -> str:
        with self._lock:
            self._counter += 1
            job_id = "job-%d-%d" % (int(time.time()), self._counter)
            job = {"id": job_id, "state": "queued", "progress": 0.0,
                   "gen": 0, "maxiter": 0, "best": None, "elapsed": 0.0,
                   "result": None, "error": None, "created": time.time()}
            self._jobs[job_id] = job
            self._order.append(job_id)
            while len(self._order) > self._keep:
                self._jobs.pop(self._order.pop(0), None)
        thread = threading.Thread(target=self._run, args=(job, fn), daemon=True)
        thread.start()
        return job_id

    def _run(self, job: dict, fn) -> None:
        job["state"] = "running"

        def progress(info: dict) -> None:
            job["gen"] = info.get("gen", 0)
            job["maxiter"] = info.get("maxiter", 0)
            job["best"] = info.get("best")
            job["elapsed"] = info.get("elapsed", 0.0)
            if job["maxiter"]:
                job["progress"] = min(0.99, job["gen"] / float(job["maxiter"]))

        try:
            result = fn(progress)
            job["result"] = result
            job["state"] = "done"
            job["progress"] = 1.0
        except Exception as exc:  # surfaced to the UI, never crashes the server
            job["state"] = "error"
            job["error"] = "%s: %s" % (type(exc).__name__, exc)
            traceback.print_exc()

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def snapshot(self) -> dict:
        with self._lock:
            return {"jobs": len(self._jobs)}


_JOBS = JobManager()


# ---------------------------------------------------------------------------
# Request handlers
# ---------------------------------------------------------------------------

def _parse_stack(payload: dict) -> Stack:
    if not isinstance(payload, dict) or "layers" not in payload:
        raise ValueError("payload must contain a stack with a 'layers' array")
    return Stack.from_dict(payload)


def _parse_bands(payload: dict) -> List[BandTarget]:
    raw = payload.get("bands") or []
    bands = [BandTarget.from_dict(b) if isinstance(b, dict) else b for b in raw]
    if not bands:
        raise ValueError("at least one band target is required")
    return bands


def _grid(range_, points: int) -> List[float]:
    lo, hi = float(range_[0]), float(range_[1])
    if hi <= lo:
        raise ValueError("range must be [lo, hi] with hi > lo")
    points = max(21, min(601, int(points)))
    return [lo + (hi - lo) * i / (points - 1) for i in range(points)]


def api_simulate(payload: dict) -> dict:
    stack = _parse_stack(payload.get("stack", {}))
    lam = _grid(payload.get("range", [380, 780]), payload.get("points", 201))
    theta = float(payload.get("theta", 0.0))
    pol = payload.get("pol", "s")
    if pol not in ("s", "p", "avg"):
        raise ValueError("pol must be s, p or avg")

    result = tmm.simulate(stack, lam, theta, pol)
    summary = metrics.spectral_summary(lam, result.R, result.T, result.A)
    bands = [BandTarget.from_dict(b) for b in payload.get("bands", []) if b]
    band_reports = []
    for band in bands:
        band_reports.append({
            "label": band.label(),
            "R": metrics.band_stats(lam, result.R, band.lo, band.hi),
            "T": metrics.band_stats(lam, result.T, band.lo, band.hi),
            "target": band.to_dict(),
        })

    response: Dict[str, Any] = {
        "ok": True,
        "stack": stack.to_dict(),
        "spectra": result.to_dict(),
        "metrics": summary,
        "bands": band_reports,
        "color": color.reflectance_color(lam, result.R),
        "gdd": metrics.group_delay(stack, lam, theta,
                                   "s" if pol == "avg" else pol),
        "warnings": stack.validate(),
        "config": {"theta": theta, "pol": pol, "range": [lam[0], lam[-1]],
                   "points": len(lam)},
    }

    if payload.get("angleMap"):
        angles = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0,
                  50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 80.0]
        map_lam = [380.0 + 5.0 * i for i in range(81)]
        rows = []
        for angle in angles:
            rows.append([round(v, 5) for v in
                         tmm.simulate(stack, map_lam, angle, "avg").R])
        response["angleMap"] = {"theta": angles, "lambda": map_lam, "R": rows}

    if payload.get("admittance", True):
        response["admittance"] = metrics.admittance_locus(
            stack, float(payload.get("lam0", 550.0)), theta,
            "s" if pol == "avg" else pol)

    response["peaks"] = {
        "reflection": metrics.find_peaks(lam, result.R, "max", 0.3, 5),
        "transmission": metrics.find_peaks(lam, result.T, "max", 0.3, 5),
        "transmissionDips": metrics.find_peaks(lam, result.T, "min", 0.7, 5),
    }
    return response


def api_design(payload: dict) -> dict:
    stack = _parse_stack(payload.get("stack", {}))
    spec = DesignSpec(_parse_bands(payload),
                      theta=float(payload.get("theta", 0.0)),
                      pol=payload.get("pol", "s"),
                      angles=payload.get("angles") or None,
                      regularization=float(payload.get("regularization", 0.0)),
                      grid_points=int(payload.get("gridPoints", 45)))
    bounds = payload.get("bounds")
    if bounds:
        bounds = [(float(b[0]), float(b[1])) for b in bounds]
        if len(bounds) != len(stack.layers):
            raise ValueError("bounds length must match the layer count")
    popsize = max(6, min(80, int(payload.get("popsize", 26))))
    maxiter = max(5, min(600, int(payload.get("maxiter", 160))))
    time_budget = max(1.0, min(120.0, float(payload.get("timeBudget", 25.0))))
    seed = int(payload.get("seed", 1))

    def task(progress):
        result = design(stack, spec, popsize=popsize, maxiter=maxiter,
                        seed=seed, time_budget=time_budget, bounds=bounds,
                        polish=bool(payload.get("polish", True)),
                        progress=progress)
        return result.to_dict()

    job_id = _JOBS.submit(task)
    return {"ok": True, "jobId": job_id}


def api_tolerance(payload: dict) -> dict:
    stack = _parse_stack(payload.get("stack", {}))
    bands = _parse_bands(payload)
    runs = max(5, min(2000, int(payload.get("runs", 200))))
    report = tolerance.tolerance_report(
        stack, bands, runs=runs,
        thickness_sigma=float(payload.get("thicknessSigma", 1.0)),
        thickness_mode=payload.get("thicknessMode", "percent"),
        index_sigma=float(payload.get("indexSigma", 0.0)),
        angle_sigma=float(payload.get("angleSigma", 0.0)),
        theta=float(payload.get("theta", 0.0)),
        pol=payload.get("pol", "s"),
        seed=int(payload.get("seed", 7)),
        slack=float(payload.get("slack", 0.0)))
    report["ok"] = True
    return report


def api_materials() -> dict:
    items = []
    for row in materials.describe():
        mat = materials.get(row["key"])
        samples = [380.0 + 10.0 * i for i in range(41)]
        nk = [mat.nk(l) for l in samples]
        items.append({
            **row,
            "samples": samples,
            "n": [round(v.real, 4) for v in nk],
            "k": [round(v.imag, 4) for v in nk],
        })
    return {"ok": True, "materials": items}


def api_presets() -> dict:
    items = []
    for pid in preset_ids():
        item = preset(pid)
        items.append(item)
    return {"ok": True, "presets": items}


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "ThinFilmForge/" + __version__
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------------- helpers
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, message: str, status: int = 400) -> None:
        self._send_json({"ok": False, "error": message}, status)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > _MAX_BODY:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSON body: %s" % exc)

    def _serve_static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        safe = posixpath.normpath(urllib.parse.unquote(path)).lstrip("/")
        if safe.startswith("..") or os.path.isabs(safe):
            self._error("forbidden", 403)
            return
        full = os.path.join(WEB_ROOT, safe)
        if not os.path.isfile(full):
            self._error("not found: %s" % path, 404)
            return
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------------ verbs
    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/api/health":
                self._send_json({"ok": True, "version": __version__,
                                 "uptime": round(time.time() - _STARTED, 2),
                                 "materials": len(materials.names()),
                                 **_JOBS.snapshot()})
                return
            if path == "/api/materials":
                self._send_json(api_materials())
                return
            if path == "/api/presets":
                self._send_json(api_presets())
                return
            if path.startswith("/api/design/"):
                job = _JOBS.get(path.rsplit("/", 1)[-1])
                if job is None:
                    self._error("unknown job", 404)
                    return
                self._send_json({"ok": True, **job})
                return
            self._serve_static(path)
        except Exception as exc:
            traceback.print_exc()
            self._error("%s: %s" % (type(exc).__name__, exc), 500)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlsplit(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/simulate":
                self._send_json(api_simulate(payload))
            elif path == "/api/design":
                self._send_json(api_design(payload))
            elif path == "/api/tolerance":
                self._send_json(api_tolerance(payload))
            else:
                self._error("no such endpoint", 404)
        except ValueError as exc:
            self._error(str(exc), 400)
        except KeyError as exc:
            self._error("unknown material: %s" % exc, 400)
        except Exception as exc:
            traceback.print_exc()
            self._error("%s: %s" % (type(exc).__name__, exc), 500)

    def log_message(self, fmt: str, *args) -> None:  # quieter console
        if os.environ.get("TFF_VERBOSE"):
            sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def make_server(host: str = "127.0.0.1", port: int = 8777) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd


def serve(host: str = "127.0.0.1", port: int = 8777,
          open_browser: bool = True) -> None:
    httpd = make_server(host, port)
    url = "http://%s:%d/" % (host, port)
    print("ThinFilmForge %s" % __version__)
    print("  serving %s" % WEB_ROOT)
    print("  open %s  (Ctrl+C to stop)" % url)
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
