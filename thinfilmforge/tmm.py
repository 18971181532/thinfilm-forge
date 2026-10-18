"""Thin-film optics core: Fresnel coefficients, phase thickness and the
Airy / scattering recursion used to solve the multilayer problem.

Two independent solvers are provided:

* :func:`simulate` - production solver.  It uses a backward Airy recursion on
  amplitude coefficients.  Every exponential it forms is ``exp(2i*delta)`` with
  ``Im(delta) >= 0`` for absorbing media, so the recursion is unconditionally
  stable even for millimetre-thick or strongly absorbing layers.
* :func:`reflectance_matrix` - the classic 2x2 characteristic-matrix solution.
  It is kept for cross-validation (see ``tests/test_tmm.py``) and for readers
  who want to compare the two textbook formulations.

Conventions
-----------
* Time dependence ``exp(-i w t)``, propagation ``exp(+i k z)``.
* ``n = n' + i k`` with ``k >= 0``; a positive ``k`` absorbs.
* Optical admittance ``eta = n cos(theta)`` for s-polarisation and
  ``eta = n / cos(theta)`` for p-polarisation.
* All wavelengths in nanometres, all thicknesses in nanometres.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import materials
from .stack import Stack

__all__ = [
    "C_NM_PER_FS",
    "SimulationResult",
    "cos_theta",
    "admittance",
    "fresnel",
    "phase_thickness",
    "simulate",
    "reflectance_matrix",
    "interface_reflectance",
]

C_LIGHT = 299792458.0            # m / s
C_NM_PER_FS = C_LIGHT * 1e-6     # 299.792458 nm / fs


@dataclass
class SimulationResult:
    """Spectral response of one stack at one angle / polarisation."""

    wavelengths: List[float]
    R: List[float]
    T: List[float]
    A: List[float]
    r: List[complex]
    t: List[complex]
    theta: float = 0.0
    pol: str = "s"
    incident: str = "Air"
    substrate: str = "SiO2"

    @property
    def phases(self) -> List[float]:
        return [cmath.phase(v) for v in self.r]

    def at(self, lam: float) -> Tuple[float, float, float]:
        """Nearest-sample (R, T, A) triplet."""
        if not self.wavelengths:
            return (0.0, 0.0, 0.0)
        idx = min(range(len(self.wavelengths)),
                  key=lambda i: abs(self.wavelengths[i] - lam))
        return (self.R[idx], self.T[idx], self.A[idx])

    @property
    def R_avg(self) -> float:
        return sum(self.R) / len(self.R) if self.R else 0.0

    def to_dict(self, digits: int = 6) -> dict:
        return {
            "wavelengths": [round(x, 3) for x in self.wavelengths],
            "R": [round(v, digits) for v in self.R],
            "T": [round(v, digits) for v in self.T],
            "A": [round(v, digits) for v in self.A],
            "theta": self.theta,
            "pol": self.pol,
        }


# ---------------------------------------------------------------------------
# Geometry / Fresnel
# ---------------------------------------------------------------------------

def cos_theta(n, n0: complex, theta0_rad: float) -> complex:
    """Complex propagation cosine inside a medium, principal branch.

    ``cmath.sqrt`` returns the branch with a non-negative real part, which is
    exactly the physically admissible one: a decaying (not growing) wave for
    absorbing media, and ``Im > 0`` for evanescent waves beyond the critical
    angle.
    """
    s = (n0 * math.sin(theta0_rad)) / n
    c = cmath.sqrt(1.0 - s * s)
    if c.real < 0:
        c = -c
    return c


def admittance(n: complex, cos_t: complex, pol: str) -> complex:
    if pol == "s":
        return n * cos_t
    if pol == "p":
        return n / cos_t
    raise ValueError("polarisation must be 's' or 'p'")


def fresnel(eta_i: complex, eta_j: complex) -> Tuple[complex, complex]:
    """Amplitude reflection / transmission between two admittances."""
    denom = eta_i + eta_j
    r = (eta_i - eta_j) / denom
    t = 2.0 * eta_i / denom
    return r, t


def phase_thickness(n: complex, d_nm: float, lam_nm: float,
                    cos_t: complex) -> complex:
    """``delta = 2*pi*n*d*cos(theta)/lambda`` (complex for absorbing films)."""
    return (2.0 * math.pi / lam_nm) * n * d_nm * cos_t


def interface_reflectance(n_i: complex, n_j: complex, theta_deg: float = 0.0,
                          pol: str = "s", n0: Optional[complex] = None) -> float:
    """Power reflectance of a single interface (ambient defaults to ``n_i``)."""
    n0 = n_i if n0 is None else n0
    t0 = math.radians(theta_deg)
    ci = cos_theta(n_i, n0, t0)
    cj = cos_theta(n_j, n0, t0)
    r, _ = fresnel(admittance(n_i, ci, pol), admittance(n_j, cj, pol))
    return abs(r) ** 2


# ---------------------------------------------------------------------------
# Production solver: backward Airy recursion
# ---------------------------------------------------------------------------

def _media(stack: Stack, lam: float, overrides: Optional[Dict[str, materials.Material]]):
    resolve = (lambda name: overrides[name]) if overrides else materials.get
    n0 = resolve(stack.incident).nk(lam)
    ns = resolve(stack.substrate).nk(lam)
    n_layers = [resolve(name).nk(lam) for name in stack.materials]
    return n0, n_layers, ns


def _solve(stack: Stack, lam: float, theta_deg: float, pol: str,
           overrides=None):
    n0, n_layers, ns = _media(stack, lam, overrides)
    t0 = math.radians(theta_deg)
    n_all = [n0] + n_layers + [ns]
    cos_all = [cos_theta(n, n0, t0) for n in n_all]
    eta = [admittance(n, c, pol) for n, c in zip(n_all, cos_all)]

    # interface j sits between medium j and medium j+1  (j = 0 .. N)
    r_if: List[complex] = []
    t_if: List[complex] = []
    for j in range(len(n_all) - 1):
        r, t = fresnel(eta[j], eta[j + 1])
        r_if.append(r)
        t_if.append(t)

    n_layers_cnt = len(stack.layers)
    rho = r_if[-1]
    tau = t_if[-1]
    for j in range(n_layers_cnt, 0, -1):
        layer = stack.layers[j - 1]
        delta = phase_thickness(n_all[j], layer.thickness, lam, cos_all[j])
        e2 = cmath.exp(2j * delta)
        denom = 1.0 + r_if[j - 1] * rho * e2
        tau = t_if[j - 1] * tau * cmath.exp(1j * delta) / denom
        rho = (r_if[j - 1] + rho * e2) / denom

    R = abs(rho) ** 2
    T = abs(tau) ** 2 * (eta[-1].real / eta[0].real)
    return rho, tau, R, T


def simulate(stack: Stack, wavelengths: Sequence[float], theta_deg: float = 0.0,
             pol: str = "s", overrides=None) -> SimulationResult:
    """Solve the stack over a wavelength grid.

    ``pol`` accepts ``'s'``, ``'p'`` or ``'avg'`` (unpolarised average).
    """
    lams = [float(x) for x in wavelengths]
    if not lams:
        raise ValueError("wavelength grid is empty")
    if pol not in ("s", "p", "avg"):
        raise ValueError("pol must be 's', 'p' or 'avg'")
    pols = ("s", "p") if pol == "avg" else (pol,)
    acc = {p: {"R": [], "T": [], "r": [], "t": []} for p in pols}
    for lam in lams:
        for p in pols:
            r, t, R, T = _solve(stack, lam, theta_deg, p, overrides)
            acc[p]["R"].append(R)
            acc[p]["T"].append(T)
            acc[p]["r"].append(r)
            acc[p]["t"].append(t)

    n = len(pols)
    R = [sum(acc[p]["R"][i] for p in pols) / n for i in range(len(lams))]
    T = [sum(acc[p]["T"][i] for p in pols) / n for i in range(len(lams))]
    A = [max(0.0, 1.0 - R[i] - T[i]) for i in range(len(lams))]
    r_mix = [sum(acc[p]["r"][i] for p in pols) / n for i in range(len(lams))]
    t_mix = [sum(acc[p]["t"][i] for p in pols) / n for i in range(len(lams))]
    return SimulationResult(lams, R, T, A, r_mix, t_mix, theta_deg, pol,
                            stack.incident, stack.substrate)


# ---------------------------------------------------------------------------
# Reference solver: characteristic matrix (testing / education)
# ---------------------------------------------------------------------------

def reflectance_matrix(stack: Stack, lam: float, theta_deg: float = 0.0,
                       pol: str = "s") -> Tuple[float, float]:
    """Classic 2x2 characteristic-matrix solution.  Returns ``(R, T)``."""
    n0, n_layers, ns = _media(stack, lam, None)
    t0 = math.radians(theta_deg)
    n_all = [n0] + n_layers + [ns]
    eta = [admittance(n, cos_theta(n, n0, t0), pol) for n in n_all]

    m00, m01, m10, m11 = 1 + 0j, 0j, 0j, 1 + 0j
    for idx, layer in enumerate(stack.layers, start=1):
        c = cos_theta(n_all[idx], n0, t0)
        delta = phase_thickness(n_all[idx], layer.thickness, lam, c)
        cd, sd = cmath.cos(delta), cmath.sin(delta)
        a, b = cd, 1j * sd / eta[idx]
        cc, d = 1j * sd * eta[idx], cd
        m00, m01, m10, m11 = (m00 * a + m01 * cc, m00 * b + m01 * d,
                              m10 * a + m11 * cc, m10 * b + m11 * d)

    B = m00 + m01 * eta[-1]
    C = m10 + m11 * eta[-1]
    r = (eta[0] * B - C) / (eta[0] * B + C)
    t = 2.0 * eta[0] / (eta[0] * B + C)
    R = abs(r) ** 2
    T = abs(t) ** 2 * (eta[-1].real / eta[0].real)
    return R, T
