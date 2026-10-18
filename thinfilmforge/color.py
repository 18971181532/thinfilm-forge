"""Colour science helpers: CIE 1931 observer, D65 illuminant, sRGB and CIELAB.

The colour matching functions use the analytic multi-lobe Gaussian fit of
Wyman, Sloan & Shirley (JCGT 2013), which is accurate to a few 1e-3 over the
visible range and keeps the package dependency-free.  The D65 spectral power
distribution is tabulated at 10 nm (CIE 15:2004) and linearly interpolated.
"""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple

__all__ = [
    "cie_xyz",
    "cmf",
    "d65",
    "reflectance_color",
    "xyz_to_srgb",
    "xyz_to_lab",
    "delta_e76",
    "spectrum_to_hex",
]

# CIE D65 relative SPD, 380..780 nm at 10 nm
_D65_10NM = [
    49.98, 54.65, 82.75, 91.49, 93.43, 86.68, 104.86, 117.01, 117.81,
    114.86, 115.92, 108.81, 109.35, 107.80, 104.79, 107.69, 104.41, 104.05,
    100.00, 96.33, 95.79, 88.69, 90.01, 89.60, 87.70, 83.29, 83.70, 80.03,
    80.21, 82.28, 78.28, 69.72, 71.61, 74.35, 61.60, 69.89, 75.09, 63.59,
    46.42, 66.81, 63.38,
]

_WHITE_D65 = (0.95047, 1.00000, 1.08883)


def _lobe(x: float, mu: float, s1: float, s2: float) -> float:
    s = s1 if x < mu else s2
    return math.exp(-0.5 * ((x - mu) / s) ** 2)


def cmf(lam_nm: float) -> Tuple[float, float, float]:
    """CIE 1931 2-degree colour matching functions (x_bar, y_bar, z_bar)."""
    x = (1.056 * _lobe(lam_nm, 599.8, 37.9, 31.0)
         + 0.362 * _lobe(lam_nm, 442.0, 16.0, 26.7)
         - 0.065 * _lobe(lam_nm, 501.1, 20.4, 26.2))
    y = (0.821 * _lobe(lam_nm, 568.8, 46.9, 40.5)
         + 0.286 * _lobe(lam_nm, 530.9, 16.3, 31.1))
    z = (1.217 * _lobe(lam_nm, 437.0, 11.8, 36.0)
         + 0.681 * _lobe(lam_nm, 459.0, 26.0, 13.8))
    return max(0.0, x), max(0.0, y), max(0.0, z)


def d65(lam_nm: float) -> float:
    """Relative spectral power of CIE standard illuminant D65."""
    lo = 380.0
    if lam_nm <= lo:
        return _D65_10NM[0]
    if lam_nm >= 780.0:
        return _D65_10NM[-1]
    pos = (lam_nm - lo) / 10.0
    i = int(pos)
    t = pos - i
    return _D65_10NM[i] * (1 - t) + _D65_10NM[i + 1] * t


def _resample(wavelengths: Sequence[float], spectrum: Sequence[float],
              lo: float = 380.0, hi: float = 780.0,
              step: float = 1.0) -> Tuple[List[float], List[float]]:
    """Resample onto a uniform grid, holding the edge values constant.

    Colour integration needs a dense, uniform grid; spectra coming from an
    optimiser can be as coarse as 40 samples.  Edge values are extended so a
    partial spectral range still yields a sensible appearance.
    """
    pts = list(zip(wavelengths, spectrum))
    pts = [(float(l), float(v)) for l, v in pts if v == v]  # drop NaN
    pts.sort()
    if not pts:
        return [], []
    out_l: List[float] = []
    out_v: List[float] = []
    n = max(1, int(round((hi - lo) / step)))
    k = 0
    for i in range(n + 1):
        lam = lo + i * step
        while k < len(pts) - 1 and pts[k + 1][0] < lam:
            k += 1
        if lam <= pts[0][0]:
            out_l.append(lam)
            out_v.append(pts[0][1])
        elif lam >= pts[-1][0]:
            out_l.append(lam)
            out_v.append(pts[-1][1])
        else:
            x0, y0 = pts[k]
            x1, y1 = pts[k + 1]
            t = (lam - x0) / (x1 - x0) if x1 > x0 else 0.0
            out_l.append(lam)
            out_v.append(y0 + t * (y1 - y0))
    return out_l, out_v


def cie_xyz(wavelengths: Sequence[float], spectrum: Sequence[float],
            illuminant: str = "D65") -> Tuple[float, float, float]:
    """Tristimulus values of a (reflectance or radiance) spectrum."""
    if len(wavelengths) != len(spectrum):
        raise ValueError("wavelength and spectrum arrays differ in length")
    if len(wavelengths) < 2:
        raise ValueError("need at least two wavelength samples")
    lams, vals = _resample(wavelengths, spectrum)
    X = Y = Z = norm = 0.0
    for i in range(len(lams) - 1):
        l0, l1 = lams[i], lams[i + 1]
        dl = l1 - l0
        if dl <= 0:
            continue
        s0 = vals[i] * (d65(l0) if illuminant == "D65" else 1.0)
        s1 = vals[i + 1] * (d65(l1) if illuminant == "D65" else 1.0)
        x0, y0, z0 = cmf(l0)
        x1, y1, z1 = cmf(l1)
        X += 0.5 * dl * (x0 * s0 + x1 * s1)
        Y += 0.5 * dl * (y0 * s0 + y1 * s1)
        Z += 0.5 * dl * (z0 * s0 + z1 * s1)
        w0 = d65(l0) if illuminant == "D65" else 1.0
        w1 = d65(l1) if illuminant == "D65" else 1.0
        norm += 0.5 * dl * (y0 * w0 + y1 * w1)
    if norm <= 0:
        return (0.0, 0.0, 0.0)
    return (X / norm, Y / norm, Z / norm)


def xyz_to_srgb(X: float, Y: float, Z: float) -> Tuple[float, float, float]:
    """Linear sRGB, desaturated into gamut if necessary (0..1 floats)."""
    r = 3.2406 * X - 1.5372 * Y - 0.4986 * Z
    g = -0.9689 * X + 1.8758 * Y + 0.0415 * Z
    b = 0.0557 * X - 0.2040 * Y + 1.0570 * Z
    lo = min(r, g, b)
    if lo < 0:  # out of gamut: pull towards the achromatic axis
        r, g, b = r - lo, g - lo, b - lo
    hi = max(r, g, b, 1e-9)
    if hi > 1.0:
        r, g, b = r / hi, g / hi, b / hi
    return (r, g, b)


def _encode(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def xyz_to_lab(X: float, Y: float, Z: float) -> Tuple[float, float, float]:
    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)

    fx = f(X / _WHITE_D65[0])
    fy = f(Y / _WHITE_D65[1])
    fz = f(Z / _WHITE_D65[2])
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e76(lab1: Sequence[float], lab2: Sequence[float]) -> float:
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(lab1, lab2)))


def reflectance_color(wavelengths: Sequence[float], R: Sequence[float],
                      illuminant: str = "D65") -> dict:
    """Appearance of a reflecting surface under D65 viewed on an sRGB display.

    Returns hex, sRGB triple, CIE xy chromaticity, CIELAB and the luminance
    factor Y (0..1).  ``hex`` is the closest in-gamut sRGB rendering, useful
    for a quick visual check of a coating design (e.g. a "green" AR tint).
    """
    X, Y, Z = cie_xyz(wavelengths, R, illuminant)
    r, g, b = xyz_to_srgb(X, Y, Z)
    hex_str = "#%02x%02x%02x" % tuple(int(round(_encode(c) * 255)) for c in (r, g, b))
    total = X + Y + Z
    xy = (X / total, Y / total) if total > 0 else (0.0, 0.0)
    return {
        "hex": hex_str,
        "srgb": [round(_encode(c), 4) for c in (r, g, b)],
        "linearRgb": [round(c, 5) for c in (r, g, b)],
        "xyz": [round(X, 5), round(Y, 5), round(Z, 5)],
        "xy": [round(xy[0], 5), round(xy[1], 5)],
        "lab": [round(v, 2) for v in xyz_to_lab(X, Y, Z)],
        "luminance": round(Y, 4),
    }


def spectrum_to_hex(wavelengths: Sequence[float], R: Sequence[float]) -> str:
    return reflectance_color(wavelengths, R)["hex"]
