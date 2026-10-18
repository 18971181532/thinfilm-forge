"""Inverse design: target specifications, merit function and a hybrid
differential-evolution / pattern-search optimiser written from scratch.

The optimiser never touches the material library twice: for a fixed material
sequence, angle and polarisation the Fresnel coefficients of every interface
are independent of the layer thicknesses, and the phase thickness is linear in
``d``.  :class:`StackModel` therefore pre-computes everything that does not
depend on the unknowns, which makes a full spectral evaluation roughly an
order of magnitude cheaper than a naive re-simulation.  That is what lets a
pure-Python optimiser converge in seconds.
"""

from __future__ import annotations

import cmath
import math
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import materials, metrics, tmm
from .stack import Layer, Stack

__all__ = [
    "BandTarget",
    "DesignSpec",
    "StackModel",
    "DesignResult",
    "build_grid",
    "optimize",
    "design",
    "passes_spec",
    "parse_band",
]


# ---------------------------------------------------------------------------
# Specification
# ---------------------------------------------------------------------------

@dataclass
class BandTarget:
    """One spectral requirement.

    ``kind``
        ``max``    R <= value inside the band (antireflection, blocking)
        ``min``    R >= value inside the band (high reflector, pass band)
        ``target`` R ~= value inside the band (beam splitter, neutral density)
    """

    lo: float
    hi: float
    kind: str = "max"
    value: float = 0.01
    weight: float = 1.0

    def __post_init__(self) -> None:
        if self.hi < self.lo:
            self.lo, self.hi = self.hi, self.lo
        if self.kind not in ("max", "min", "target"):
            raise ValueError("band kind must be max/min/target")
        if self.weight <= 0:
            raise ValueError("band weight must be positive")

    @classmethod
    def from_dict(cls, payload: dict) -> "BandTarget":
        return cls(float(payload["lo"]), float(payload["hi"]),
                   payload.get("kind", "max"), float(payload["value"]),
                   float(payload.get("weight", 1.0)))

    def to_dict(self) -> dict:
        return {"lo": self.lo, "hi": self.hi, "kind": self.kind,
                "value": self.value, "weight": self.weight}

    def label(self) -> str:
        symbol = {"max": "<=", "min": ">=", "target": "~="}[self.kind]
        return "%g-%g nm: R %s %.3f" % (self.lo, self.hi, symbol, self.value)

    def penalty(self, reflectance: float) -> float:
        if self.kind == "max":
            d = max(0.0, reflectance - self.value)
        elif self.kind == "min":
            d = max(0.0, self.value - reflectance)
        else:
            d = reflectance - self.value
        return d * d

    def satisfied(self, reflectance: float, slack: float = 0.0) -> bool:
        if self.kind == "max":
            return reflectance <= self.value + slack
        if self.kind == "min":
            return reflectance >= self.value - slack
        return abs(reflectance - self.value) <= slack


def parse_band(text: str) -> BandTarget:
    """Parse ``"420:680:max:0.005"`` or ``"420-680:min:0.99:2"``."""
    parts = text.replace("-", ":").split(":")
    if len(parts) < 3:
        raise ValueError("band must look like lo:hi:kind:value[:weight]")
    lo, hi = float(parts[0]), float(parts[1])
    kind = parts[2]
    value = float(parts[3]) if len(parts) > 3 else 0.01
    weight = float(parts[4]) if len(parts) > 4 else 1.0
    return BandTarget(lo, hi, kind, value, weight)


@dataclass
class DesignSpec:
    bands: List[BandTarget]
    theta: float = 0.0
    pol: str = "s"
    angles: Optional[List[float]] = None   # multi-angle average, e.g. [0, 30, 45]
    regularization: float = 0.0            # total-thickness penalty weight
    grid_points: int = 45                  # samples per band during optimisation

    def __post_init__(self) -> None:
        self.bands = [b if isinstance(b, BandTarget) else BandTarget.from_dict(b)
                      for b in self.bands]
        if not self.bands:
            raise ValueError("at least one band target is required")
        if self.angles:
            self.angles = [float(a) for a in self.angles]
        if self.pol not in ("s", "p", "avg"):
            raise ValueError("pol must be s, p or avg")

    def to_dict(self) -> dict:
        return {"bands": [b.to_dict() for b in self.bands], "theta": self.theta,
                "pol": self.pol, "angles": self.angles,
                "regularization": self.regularization}


def build_grid(bands: Sequence[BandTarget], points_per_band: int = 45,
               hi_res: bool = False) -> List[float]:
    """Union of per-band linear grids, dense enough to resolve fringes."""
    pts: List[float] = []
    for band in bands:
        n = max(9, points_per_band if not hi_res else points_per_band * 3)
        step = 0.5 if hi_res else 1.0
        span = band.hi - band.lo
        n = max(n, int(span / step) + 1)
        for i in range(n):
            pts.append(band.lo + span * i / (n - 1))
    pts = sorted(set(round(p, 4) for p in pts))
    if len(pts) > (900 if hi_res else 220):
        stride = max(1, len(pts) // (900 if hi_res else 220))
        pts = pts[::stride]
    return pts


# ---------------------------------------------------------------------------
# Fast spectral model
# ---------------------------------------------------------------------------

class StackModel:
    """Pre-computed multilayer model for a fixed material sequence."""

    def __init__(self, layer_materials: Sequence[str], incident: str,
                 substrate: str, wavelengths: Sequence[float],
                 theta_deg: float = 0.0, pol: str = "s"):
        if not wavelengths:
            raise ValueError("empty wavelength grid")
        self.materials = [materials.get(m).key for m in layer_materials]
        self.incident = materials.get(incident).key
        self.substrate = materials.get(substrate).key
        self.wavelengths = [float(w) for w in wavelengths]
        self.theta = float(theta_deg)
        self.pol = pol
        self._pols = ("s", "p") if pol == "avg" else (pol,)
        self._n_layers = len(self.materials)
        self._cache: List[dict] = []
        self._build()

    def _build(self) -> None:
        self._cache = []
        for lam in self.wavelengths:
            per_pol = {}
            for p in self._pols:
                per_pol[p] = self._prepare(lam, p)
            self._cache.append(per_pol)

    def _prepare(self, lam: float, pol: str) -> dict:
        n0 = materials.nk(self.incident, lam)
        ns = materials.nk(self.substrate, lam)
        n_layers = [materials.nk(m, lam) for m in self.materials]
        t0 = math.radians(self.theta)
        n_all = [n0] + n_layers + [ns]
        cos_all = [tmm.cos_theta(n, n0, t0) for n in n_all]
        eta = [tmm.admittance(n, c, pol) for n, c in zip(n_all, cos_all)]
        r_if, t_if = [], []
        for j in range(len(n_all) - 1):
            r, t = tmm.fresnel(eta[j], eta[j + 1])
            r_if.append(r)
            t_if.append(t)
        q = [(2.0 * math.pi / lam) * n_all[j + 1] * cos_all[j + 1]
             for j in range(self._n_layers)]
        return {"r": r_if, "t": t_if, "q": q,
                "eta0": eta[0], "etaS": eta[-1]}

    # ------------------------------------------------------------------ solve
    def reflectance(self, thicknesses: Sequence[float]) -> List[float]:
        if len(thicknesses) != self._n_layers:
            raise ValueError("expected %d thicknesses, got %d"
                             % (self._n_layers, len(thicknesses)))
        n_pol = len(self._pols)
        out: List[float] = []
        for wl in self._cache:
            acc = 0.0
            for p in self._pols:
                w = wl[p]
                rho = w["r"][-1]
                for j in range(self._n_layers, 0, -1):
                    d = thicknesses[j - 1]
                    e2 = cmath.exp(2j * (w["q"][j - 1] * d))
                    denom = 1.0 + w["r"][j - 1] * rho * e2
                    rho = (w["r"][j - 1] + rho * e2) / denom
                acc += abs(rho) ** 2
            out.append(acc / n_pol)
        return out

    def sample_fast(self, thicknesses: Sequence[float], lam: float) -> float:
        """Reflectance at the nearest cached wavelength (used for reporting)."""
        idx = min(range(len(self.wavelengths)),
                  key=lambda i: abs(self.wavelengths[i] - lam))
        return self.reflectance(thicknesses)[idx]


class _BandIndices:
    def __init__(self, spec: DesignSpec, wavelengths: Sequence[float]):
        self.bands = spec.bands
        self.idx = [[i for i, l in enumerate(wavelengths) if b.lo <= l <= b.hi]
                    for b in self.bands]

    def merit(self, R: Sequence[float], thicknesses: Sequence[float],
              regularization: float) -> float:
        total = 0.0
        for band, idx in zip(self.bands, self.idx):
            if not idx:
                continue
            acc = 0.0
            for i in idx:
                r = R[i]
                # squared constraint violation ...
                acc += band.penalty(r)
                # ... plus a small margin term.  Without it the objective is
                # flat as soon as a band is satisfied, and the optimiser stops
                # improving a design that only just meets the spec.
                if band.kind == "max":
                    acc += 0.02 * r * r
                elif band.kind == "min":
                    acc += 0.02 * (1.0 - r) ** 2
            total += band.weight * acc / len(idx)
        if regularization:
            total += regularization * sum(thicknesses) / 1000.0
        return total

    def breakdown(self, R: Sequence[float]) -> List[dict]:
        out = []
        for band, idx in zip(self.bands, self.idx):
            if idx:
                vals = [R[i] for i in idx]
                out.append({"band": band.to_dict(), "label": band.label(),
                            "avg": sum(vals) / len(vals),
                            "min": min(vals), "max": max(vals),
                            "penalty": sum(band.penalty(v) for v in vals) / len(vals)})
        return out


# ---------------------------------------------------------------------------
# Optimiser
# ---------------------------------------------------------------------------

@dataclass
class DesignResult:
    thicknesses: List[float]
    materials: List[str]
    merit: float
    initial_merit: float
    history: List[float]
    evaluations: int
    elapsed: float
    bounds: List[Tuple[float, float]]
    breakdown: List[dict] = field(default_factory=list)
    method: str = "de+pattern"
    stack: Optional[dict] = None
    spectra: Optional[dict] = None
    metrics: Optional[dict] = None
    color: Optional[dict] = None
    gdd: Optional[dict] = None
    spec: Optional[dict] = None
    report: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "thicknesses": [round(v, 3) for v in self.thicknesses],
            "materials": self.materials,
            "merit": self.merit,
            "initialMerit": self.initial_merit,
            "history": [round(v, 8) for v in self.history],
            "evaluations": self.evaluations,
            "elapsed": round(self.elapsed, 3),
            "bounds": [[round(a, 2), round(b, 2)] for a, b in self.bounds],
            "breakdown": self.breakdown,
            "method": self.method,
            "stack": self.stack,
            "spectra": self.spectra,
            "metrics": self.metrics,
            "color": self.color,
            "gdd": self.gdd,
            "spec": self.spec,
        }


def _clip(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)


def optimize(model: StackModel, spec: DesignSpec, bounds: Sequence[Tuple[float, float]],
             x0: Optional[Sequence[float]] = None, popsize: int = 26,
             maxiter: int = 160, F: float = 0.7, CR: float = 0.9, seed: int = 1,
             time_budget: float = 25.0, polish: bool = True,
             progress: Optional[Callable[[dict], None]] = None) -> DesignResult:
    """Hybrid differential evolution followed by a Hooke-Jeeves polish."""
    t_start = time.time()
    bounds = [(float(lo), float(hi)) for lo, hi in bounds]
    if len(bounds) != len(model.materials):
        raise ValueError("bounds length must match the layer count")
    n = len(bounds)
    rng = random.Random(seed)
    wl = model.wavelengths
    bands = _BandIndices(spec, wl)
    evals = 0

    def merit(x: Sequence[float]) -> float:
        nonlocal evals
        evals += 1
        return bands.merit(model.reflectance(x), x, spec.regularization)

    init = list(x0) if x0 is not None else [0.5 * (lo + hi) for lo, hi in bounds]
    init = [_clip(init[i], bounds[i][0], bounds[i][1]) for i in range(n)]
    best = list(init)
    best_cost = merit(best)
    initial_cost = best_cost
    history = [best_cost]

    # --- population ---------------------------------------------------------
    # A purely uniform initialisation over the full thickness range is a poor
    # prior: most random designs are thick and behave like mirrors.  Bias the
    # population towards thin films and log-normal perturbations of the seed.
    population = [best[:]]
    for _ in range(max(3, popsize) - 1):
        individual = []
        for i, (lo, hi) in enumerate(bounds):
            if hi - lo < 1e-9:
                individual.append(lo)
                continue
            if x0 is not None and rng.random() < 0.45:
                v = init[i] * math.exp(rng.gauss(0.0, 0.6))
            else:
                v = lo + (hi - lo) * rng.random() ** 2
            individual.append(_clip(v, lo, hi))
        population.append(individual)
    costs = [merit(ind) for ind in population]
    for i, c in enumerate(costs):
        if c < best_cost:
            best, best_cost = population[i][:], c
    history.append(best_cost)

    stall = 0
    for gen in range(1, maxiter + 1):
        for i in range(len(population)):
            candidates = list(range(len(population)))
            candidates.remove(i)
            a, b, c = rng.sample(candidates, 3)
            mutant = [population[a][k] + F * (population[b][k] - population[c][k])
                      for k in range(n)]
            trial = []
            jrand = rng.randrange(n)
            for k in range(n):
                if rng.random() < CR or k == jrand:
                    trial.append(_clip(mutant[k], bounds[k][0], bounds[k][1]))
                else:
                    trial.append(population[i][k])
            cost = merit(trial)
            if cost <= costs[i]:
                population[i], costs[i] = trial, cost
                if cost < best_cost:
                    best, best_cost = trial[:], cost
                    stall = 0
        stall += 1
        history.append(best_cost)
        if progress is not None:
            progress({"phase": "de", "gen": gen, "maxiter": maxiter,
                      "best": best_cost, "elapsed": time.time() - t_start})
        if time.time() - t_start > time_budget:
            break
        if stall > 30 and best_cost > 1e-12:
            break

    # --- local polish -------------------------------------------------------
    if polish:
        step = [max(0.5, 0.08 * (hi - lo)) for lo, hi in bounds]
        stall = 0
        while time.time() - t_start < time_budget and evals < 60000:
            improved = False
            for i in range(n):
                if bounds[i][1] - bounds[i][0] < 1e-9:
                    continue
                for sign in (1.0, -1.0):
                    trial = best[:]
                    trial[i] = _clip(best[i] + sign * step[i], *bounds[i])
                    if abs(trial[i] - best[i]) < 1e-9:
                        continue
                    cost = merit(trial)
                    if cost < best_cost - 1e-14:
                        best, best_cost = trial, cost
                        improved = True
                        stall = 0
                        break
            if improved:
                history.append(best_cost)
                if progress is not None:
                    progress({"phase": "polish", "gen": len(history),
                              "maxiter": len(history), "best": best_cost,
                              "elapsed": time.time() - t_start})
            else:
                stall += 1
                for i in range(n):
                    step[i] *= 0.5
                if max(step) < 0.05 or stall > 12:
                    break

    elapsed = time.time() - t_start
    return DesignResult(
        thicknesses=[round(v, 4) for v in best],
        materials=list(model.materials),
        merit=best_cost,
        initial_merit=initial_cost,
        history=history,
        evaluations=evals,
        elapsed=elapsed,
        bounds=bounds,
        breakdown=bands.breakdown(model.reflectance(best)),
        method="de+pattern" if polish else "de",
        spec=spec.to_dict(),
    )


# ---------------------------------------------------------------------------
# High-level driver
# ---------------------------------------------------------------------------

def default_bounds(stack: Stack, lam0: float = 550.0,
                   min_nm: float = 4.0, max_nm: float = 400.0) -> List[Tuple[float, float]]:
    """Bounds anchored on the quarter-wave thickness of each material."""
    bounds = []
    for layer in stack.layers:
        q = max(8.0, stack_qwot(layer.material, lam0))
        bounds.append((min_nm, min(max_nm, max(4 * q, 120.0))))
    return bounds


def stack_qwot(material: str, lam0: float) -> float:
    return lam0 / (4.0 * materials.get(material).n_at(lam0))


def design(stack: Stack, spec: DesignSpec, popsize: int = 26, maxiter: int = 160,
           seed: int = 1, time_budget: float = 25.0,
           bounds: Optional[Sequence[Tuple[float, float]]] = None,
           lam0: float = 550.0, polish: bool = True,
           progress: Optional[Callable[[dict], None]] = None) -> DesignResult:
    """Optimise the thicknesses of ``stack`` against ``spec``."""
    if not stack.layers:
        raise ValueError("cannot optimise a stack without layers")
    bounds = list(bounds) if bounds else default_bounds(stack, lam0)
    grid = build_grid(spec.bands, spec.grid_points)
    angles = spec.angles if spec.angles else [spec.theta]
    models = [StackModel(stack.materials, stack.incident, stack.substrate,
                         grid, a, spec.pol) for a in angles]
    if len(models) == 1:
        model = models[0]
    else:
        base = models[0]

        class _MultiModel:
            materials = base.materials
            wavelengths = base.wavelengths

            def reflectance(self, thicknesses):
                acc = [0.0] * len(self.wavelengths)
                for m in models:
                    for i, v in enumerate(m.reflectance(thicknesses)):
                        acc[i] += v
                return [v / len(models) for v in acc]

        model = _MultiModel()

    result = optimize(model, spec, bounds, x0=stack.thicknesses, popsize=popsize,
                      maxiter=maxiter, seed=seed, time_budget=time_budget,
                      polish=polish, progress=progress)
    refined = stack.with_thicknesses(result.thicknesses, name=stack.name)

    # full-resolution report on the optimised design
    fine = build_grid(spec.bands, spec.grid_points, hi_res=True)
    report = metrics.full_report(refined, fine, spec.theta, spec.pol)
    result.stack = refined.to_dict()
    result.spectra = report["spectra"]
    result.metrics = report["metrics"]
    result.color = report["color"]
    result.gdd = report["gdd"]
    result.report = report
    return result


def passes_spec(R: Sequence[float], wavelengths: Sequence[float],
                bands: Sequence[BandTarget], slack: float = 0.0) -> Tuple[bool, List[bool]]:
    """Yield test: every sample inside every band must satisfy its constraint."""
    per_band: List[bool] = []
    for band in bands:
        vals = [v for l, v in zip(wavelengths, R) if band.lo <= l <= band.hi]
        per_band.append(all(band.satisfied(v, slack) for v in vals) if vals else True)
    return all(per_band), per_band
