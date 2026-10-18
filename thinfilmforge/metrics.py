"""Post-processing of spectral data: band statistics, colour, group delay,
admittance loci and resonance analysis."""

from __future__ import annotations

import cmath
import math
from typing import Dict, List, Optional, Sequence, Tuple

from . import color, materials
from . import tmm
from .stack import Stack

__all__ = [
    "band_stats",
    "spectral_summary",
    "unwrap_phase",
    "group_delay",
    "admittance_locus",
    "find_peaks",
    "full_report",
]


def band_stats(wavelengths: Sequence[float], values: Sequence[float],
               lo: float, hi: float) -> Dict[str, float]:
    sel = [v for l, v in zip(wavelengths, values) if lo <= l <= hi]
    if not sel:
        return {"lo": lo, "hi": hi, "n": 0, "avg": 0.0, "min": 0.0,
                "max": 0.0, "rms": 0.0, "p95": 0.0}
    ordered = sorted(sel)
    p95 = ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]
    return {
        "lo": lo,
        "hi": hi,
        "n": len(sel),
        "avg": sum(sel) / len(sel),
        "min": min(sel),
        "max": max(sel),
        "rms": math.sqrt(sum(v * v for v in sel) / len(sel)),
        "p95": p95,
    }


def spectral_summary(wavelengths: Sequence[float], R: Sequence[float],
                     T: Sequence[float], A: Sequence[float],
                     visible: Tuple[float, float] = (400.0, 700.0)) -> dict:
    r = band_stats(wavelengths, R, *visible)
    t = band_stats(wavelengths, T, *visible)
    a = band_stats(wavelengths, A, *visible)
    return {
        "visible": {"lo": visible[0], "hi": visible[1]},
        "R": r, "T": t, "A": a,
        "Ravg": r["avg"], "Rmax": r["max"], "Rmin": r["min"],
        "Tavg": t["avg"], "Tmax": t["max"], "Tmin": t["min"],
    }


def unwrap_phase(phases: Sequence[float]) -> List[float]:
    """Unwrap a phase sequence so that adjacent samples differ by < pi."""
    if not phases:
        return []
    out = [phases[0]]
    for p in phases[1:]:
        prev = out[-1]
        diff = p - prev
        while diff > math.pi:
            diff -= 2 * math.pi
        while diff < -math.pi:
            diff += 2 * math.pi
        out.append(prev + diff)
    return out


def _gradient(xs: Sequence[float], ys: Sequence[float]) -> List[float]:
    n = len(xs)
    if n < 2:
        return [0.0] * n
    out = [0.0] * n
    out[0] = (ys[1] - ys[0]) / (xs[1] - xs[0])
    out[-1] = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
    for i in range(1, n - 1):
        out[i] = (ys[i + 1] - ys[i - 1]) / (xs[i + 1] - xs[i - 1])
    return out


def group_delay(stack: Stack, wavelengths: Sequence[float], theta_deg: float = 0.0,
                pol: str = "s", quantity: str = "reflection") -> dict:
    """Group delay (fs) and group delay dispersion (fs^2) of a stack.

    ``GD = -dphi/domega`` evaluated from the unwrapped spectral phase;
    ``GDD = dGD/domega``.  For a mirror, ``GD`` also gives the effective
    penetration depth ``L = c*GD/2``, the quantity that matters when a
    chirped mirror is designed for femtosecond pulse compression.
    """
    result = tmm.simulate(stack, wavelengths, theta_deg, pol)
    phases = ([cmath.phase(v) for v in result.r] if quantity == "reflection"
              else [cmath.phase(v) for v in result.t])
    phases = unwrap_phase(phases)
    lams = list(wavelengths)
    dphi = _gradient(lams, phases)  # rad / nm
    gd = [-l * l / (2 * math.pi * tmm.C_NM_PER_FS) * d for l, d in zip(lams, dphi)]
    dgd = _gradient(lams, gd)      # fs / nm
    gdd = [l * l / (2 * math.pi * tmm.C_NM_PER_FS) * d for l, d in zip(lams, dgd)]
    mid = len(lams) // 2
    gd0 = gd[mid]
    return {
        "lambda": lams,
        "gd": [round(v, 5) for v in gd],
        "gdd": [round(v, 5) for v in gdd],
        "gd0": round(gd0, 5),
        "gdd0": round(gdd[mid], 5),
        "penetrationDepth": round(abs(gd0) * tmm.C_NM_PER_FS / 2.0, 2),
        "quantity": quantity,
        "pol": pol,
        "theta": theta_deg,
    }


def admittance_locus(stack: Stack, lam: float = 550.0, theta_deg: float = 0.0,
                     pol: str = "s") -> dict:
    """Input admittance after each layer, traced from the substrate outwards.

    Plotting the locus in the complex plane is the classic Macleod design aid:
    a quarter-wave layer rotates the admittance by 90 degrees around a circle,
    and the distance of the final point from the air admittance sets the
    residual reflectance.
    """
    n0 = materials.nk(stack.incident, lam)
    ns = materials.nk(stack.substrate, lam)
    t0 = math.radians(theta_deg)
    n_all = [n0] + [materials.nk(m, lam) for m in stack.materials] + [ns]
    cos_all = [tmm.cos_theta(n, n0, t0) for n in n_all]
    eta = [tmm.admittance(n, c, pol) for n, c in zip(n_all, cos_all)]

    y = eta[-1]
    points = [(y.real, y.imag)]
    labels = ["substrate"]
    refl = [0.0]
    for j in range(len(stack.layers), 0, -1):
        layer = stack.layers[j - 1]
        delta = tmm.phase_thickness(n_all[j], layer.thickness, lam, cos_all[j])
        cd, sd = cmath.cos(delta), cmath.sin(delta)
        y = eta[j] * (y * cd + 1j * eta[j] * sd) / (eta[j] * cd + 1j * y * sd)
        points.append((y.real, y.imag))
        labels.append("L%d" % j)
        refl.append(abs((eta[0] - y) / (eta[0] + y)) ** 2)
    return {
        "lambda": lam,
        "re": [round(p[0], 5) for p in points],
        "im": [round(p[1], 5) for p in points],
        "labels": labels,
        "reflectance": [round(v, 6) for v in refl],
        "eta0": [round(eta[0].real, 5), round(eta[0].imag, 5)],
        "etaSub": [round(eta[-1].real, 5), round(eta[-1].imag, 5)],
    }


def find_peaks(wavelengths: Sequence[float], values: Sequence[float],
               kind: str = "max", min_height: float = 0.05,
               max_peaks: int = 8) -> List[dict]:
    """Local extrema with a linearly interpolated full width at half maximum."""
    peaks: List[dict] = []
    n = len(values)
    if n < 3:
        return peaks
    baseline = min(values) if kind == "max" else max(values)
    for i in range(1, n - 1):
        v = values[i]
        if kind == "max":
            if not (v >= values[i - 1] and v >= values[i + 1] and v >= min_height):
                continue
        else:
            if not (v <= values[i - 1] and v <= values[i + 1] and v <= min_height):
                continue
        half = 0.5 * (v + baseline)
        lo = hi = None
        for j in range(i, 0, -1):
            a, b = values[j - 1], values[j]
            if (a - half) * (1 if kind == "max" else -1) <= 0 and b != a:
                frac = (half - a) / (b - a)
                lo = wavelengths[j - 1] + frac * (wavelengths[j] - wavelengths[j - 1])
                break
        for j in range(i, n - 1):
            a, b = values[j], values[j + 1]
            if (b - half) * (1 if kind == "max" else -1) <= 0 and b != a:
                frac = (half - a) / (b - a)
                hi = wavelengths[j] + frac * (wavelengths[j + 1] - wavelengths[j])
                break
        peaks.append({
            "lambda": round(wavelengths[i], 2),
            "value": round(v, 5),
            "fwhm": round(hi - lo, 3) if (lo is not None and hi is not None) else None,
        })
    peaks.sort(key=lambda p: p["value"], reverse=(kind == "max"))
    return peaks[:max_peaks]


def full_report(stack: Stack, wavelengths: Sequence[float], theta_deg: float = 0.0,
                pol: str = "s") -> dict:
    result = tmm.simulate(stack, wavelengths, theta_deg, pol)
    summary = spectral_summary(wavelengths, result.R, result.T, result.A)
    colour = color.reflectance_color(wavelengths, result.R)
    gd = group_delay(stack, wavelengths, theta_deg, "s" if pol == "avg" else pol)
    return {
        "stack": stack.to_dict(),
        "spectra": result.to_dict(),
        "metrics": summary,
        "color": colour,
        "gdd": gd,
        "warnings": stack.validate(),
    }
