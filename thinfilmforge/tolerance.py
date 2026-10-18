"""Manufacturing tolerance analysis: Monte-Carlo yield and layer sensitivity.

Deposition errors are modelled as independent Gaussian perturbations of layer
thickness, refractive index and incidence angle.  Each trial is a full
re-solve of the perturbed stack, so the reported yield is the fraction of
simulated coatings that would actually meet the specification.
"""

from __future__ import annotations

import math
import random
import time
from typing import Dict, List, Optional, Sequence, Tuple

from . import materials, tmm
from .design import BandTarget, build_grid, passes_spec
from .stack import Layer, Stack

__all__ = ["monte_carlo", "layer_sensitivity", "tolerance_report"]


def _merit(R: Sequence[float], wavelengths: Sequence[float],
           bands: Sequence[BandTarget]) -> float:
    total = 0.0
    for band in bands:
        vals = [v for l, v in zip(wavelengths, R) if band.lo <= l <= band.hi]
        if vals:
            total += band.weight * sum(band.penalty(v) for v in vals) / len(vals)
    return total


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(len(sorted_vals) - 1, lo + 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _histogram(vals: Sequence[float], bins: int = 20) -> dict:
    if not vals:
        return {"edges": [], "counts": []}
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-12:
        hi = lo + 1e-9
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        idx = min(bins - 1, int((v - lo) / width))
        counts[idx] += 1
    edges = [lo + i * width for i in range(bins + 1)]
    return {"edges": [round(e, 9) for e in edges], "counts": counts}


def monte_carlo(stack: Stack, bands: Sequence[BandTarget], runs: int = 200,
                thickness_sigma: float = 1.0, thickness_mode: str = "percent",
                index_sigma: float = 0.0, angle_sigma: float = 0.0,
                theta: float = 0.0, pol: str = "s", seed: int = 7,
                points: int = 71, slack: float = 0.0) -> dict:
    """Run a Monte-Carlo tolerance analysis.

    ``thickness_sigma`` is a 1-sigma relative (%) or absolute (nm) error per
    layer, ``index_sigma`` a relative (%) error applied to every film,
    ``angle_sigma`` the 1-sigma angular error in degrees.
    """
    if runs <= 0:
        raise ValueError("runs must be positive")
    if not stack.layers:
        raise ValueError("cannot analyse an empty stack")
    t_start = time.time()
    rng = random.Random(seed)
    grid = build_grid([BandTarget(b.lo, b.hi) for b in bands], points)
    grid = sorted(set(round(v, 3) for v in grid))
    used = sorted({l.material for l in stack.layers})

    nominal = tmm.simulate(stack, grid, theta, pol).R
    envelope_min = [1.0] * len(grid)
    envelope_max = [0.0] * len(grid)
    merits: List[float] = []
    max_r_runs: List[float] = []
    min_r_runs: List[float] = []
    failures = 0
    band_pass = [0] * len(bands)
    worst = {"merit": -1.0, "thicknesses": [], "angle": theta}
    max_r_max = 0.0
    min_r_min = 1.0

    for _ in range(runs):
        thicknesses: List[float] = []
        for layer in stack.layers:
            if thickness_mode == "nm":
                d = layer.thickness + rng.gauss(0.0, thickness_sigma)
            else:
                d = layer.thickness * (1.0 + rng.gauss(0.0, thickness_sigma / 100.0))
            thicknesses.append(max(0.0, d))

        overrides = None
        if index_sigma > 0:
            overrides = {}
            for name in [stack.incident, stack.substrate] + used:
                overrides[name] = materials.get(name)
            for name in used:
                overrides[name] = materials.get(name).scaled(
                    1.0 + rng.gauss(0.0, index_sigma / 100.0))
        trial_theta = theta + (rng.gauss(0.0, angle_sigma) if angle_sigma > 0 else 0.0)

        perturbed = Stack([Layer(l.material, t) for l, t in zip(stack.layers, thicknesses)],
                          stack.substrate, stack.incident)
        result = tmm.simulate(perturbed, grid, trial_theta, pol, overrides=overrides)
        m = _merit(result.R, grid, bands)
        merits.append(m)
        for i, v in enumerate(result.R):
            if v < envelope_min[i]:
                envelope_min[i] = v
            if v > envelope_max[i]:
                envelope_max[i] = v

        band_max = 0.0
        for band in bands:
            vals = [v for l, v in zip(grid, result.R) if band.lo <= l <= band.hi]
            if vals:
                band_max = max(band_max, max(vals))
        max_r_runs.append(band_max)
        band_min = 1.0
        for band in bands:
            vals = [v for l, v in zip(grid, result.R) if band.lo <= l <= band.hi]
            if vals:
                band_min = min(band_min, min(vals))
        min_r_runs.append(band_min)

        ok, per_band = passes_spec(result.R, grid, bands, slack)
        for i, flag in enumerate(per_band):
            if flag:
                band_pass[i] += 1
        if not ok:
            failures += 1
        for band in bands:
            vals = [v for l, v in zip(grid, result.R) if band.lo <= l <= band.hi]
            if vals:
                max_r_max = max(max_r_max, max(vals))
                min_r_min = min(min_r_min, min(vals))
        if m > worst["merit"]:
            worst = {"merit": m, "thicknesses": [round(t, 2) for t in thicknesses],
                     "theta": round(trial_theta, 3)}

    ordered = sorted(merits)
    elapsed = time.time() - t_start
    return {
        "runs": runs,
        "yield": round(100.0 * (runs - failures) / runs, 2),
        "failures": failures,
        "merit": {
            "mean": round(sum(merits) / runs, 8),
            "std": round(math.sqrt(sum((m - sum(merits) / runs) ** 2 for m in merits) / runs), 8),
            "p05": round(_percentile(ordered, 0.05), 8),
            "p50": round(_percentile(ordered, 0.50), 8),
            "p95": round(_percentile(ordered, 0.95), 8),
            "max": round(ordered[-1], 8),
        },
        "histogram": _histogram(merits, 24),
        "histogramMaxR": _histogram(max_r_runs, 24),
        "histogramMinR": _histogram(min_r_runs, 24),
        "minR": {
            "mean": round(sum(min_r_runs) / len(min_r_runs), 6),
            "p05": round(_percentile(sorted(min_r_runs), 0.05), 6),
            "min": round(min(min_r_runs), 6),
        },
        "thresholdMin": max([b.value for b in bands if b.kind == "min"], default=None),
        "maxR": {
            "mean": round(sum(max_r_runs) / len(max_r_runs), 6),
            "p95": round(_percentile(sorted(max_r_runs), 0.95), 6),
            "max": round(max(max_r_runs), 6),
        },
        "threshold": min([b.value for b in bands if b.kind == "max"], default=None),
        "bands": [
            {"label": band.label(), "passRate": round(100.0 * band_pass[i] / runs, 2)}
            for i, band in enumerate(bands)
        ],
        "envelope": {
            "lambda": [round(v, 2) for v in grid],
            "min": [round(v, 6) for v in envelope_min],
            "max": [round(v, 6) for v in envelope_max],
            "nominal": [round(v, 6) for v in nominal],
        },
        "worstCase": worst,
        "range": {"maxR": round(max_r_max, 6), "minR": round(min_r_min, 6)},
        "config": {
            "thicknessSigma": thickness_sigma,
            "thicknessMode": thickness_mode,
            "indexSigma": index_sigma,
            "angleSigma": angle_sigma,
            "theta": theta,
            "pol": pol,
            "seed": seed,
        },
        "elapsed": round(elapsed, 3),
    }


def layer_sensitivity(stack: Stack, bands: Sequence[BandTarget],
                      delta_nm: float = 2.0, theta: float = 0.0,
                      pol: str = "s", points: int = 71) -> List[dict]:
    """One-at-a-time thickness perturbation, ranked by merit degradation."""
    grid = build_grid([BandTarget(b.lo, b.hi) for b in bands], points)
    grid = sorted(set(round(v, 3) for v in grid))
    base = tmm.simulate(stack, grid, theta, pol)
    base_merit = _merit(base.R, grid, bands)
    rows: List[dict] = []
    for i, layer in enumerate(stack.layers):
        disturbed = stack.copy()
        disturbed.layers[i] = Layer(layer.material, max(0.0, layer.thickness + delta_nm))
        res = tmm.simulate(disturbed, grid, theta, pol)
        rows.append({
            "layer": i + 1,
            "material": layer.material,
            "thickness": round(layer.thickness, 2),
            "deltaMerit": round(_merit(res.R, grid, bands) - base_merit, 9),
            "deltaR": round(sum(abs(a - b) for a, b in zip(res.R, base.R)) / len(base.R), 7),
            "deltaRmean": round(sum(res.R) / len(res.R) - sum(base.R) / len(base.R), 6),
        })
    rows.sort(key=lambda r: r["deltaR"], reverse=True)
    return rows


def tolerance_report(stack: Stack, bands: Sequence[BandTarget], **kwargs) -> dict:
    sens = layer_sensitivity(stack, bands,
                            delta_nm=kwargs.get("sensitivity_delta", 2.0),
                            theta=kwargs.get("theta", 0.0),
                            pol=kwargs.get("pol", "s"))
    mc = monte_carlo(stack, bands, **{k: v for k, v in kwargs.items()
                                      if k in ("runs", "thickness_sigma",
                                               "thickness_mode", "index_sigma",
                                               "angle_sigma", "theta", "pol",
                                               "seed", "points", "slack")})
    mc["sensitivity"] = sens
    mc["stack"] = stack.to_dict()
    return mc
