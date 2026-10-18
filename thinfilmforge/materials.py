"""Dispersion models and the built-in optical thin-film material library.

Every material exposes a complex refractive index ``n + i k`` (k > 0 means
absorption, following the ``exp(-i w t + i k z)`` convention used throughout
this package).  Three dispersion models are supported:

``sellmeier``
    n^2(l) = 1 + sum_i B_i l^2 / (l^2 - C_i),  l in micrometres.
``cauchy``
    n(l) = A + B / l^2 + C / l^4,             l in micrometres.
``table``
    Tabulated (l, n, k) samples with linear interpolation.  Values outside the
    measured range are clamped (a warning is surfaced by the UI).

The thin-film values below are literature / catalogue approximations intended
for *design studies*.  Real deposited films differ by process (density,
stoichiometry, stress); the library is therefore data-driven - you can ship
your own ellipsometry data as a JSON table and register it at runtime.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "Material",
    "get",
    "names",
    "categories",
    "describe",
    "nk",
    "is_metal",
    "load_user_library",
]


@dataclass(frozen=True)
class Material:
    """A dispersive optical material."""

    key: str
    label: str
    category: str
    model: str
    params: Tuple
    note: str = ""

    # ------------------------------------------------------------------ index
    def nk(self, lam_nm):
        """Complex refractive index at ``lam_nm`` (scalar or iterable)."""
        if isinstance(lam_nm, (int, float)):
            return self._nk_scalar(float(lam_nm))
        return [self._nk_scalar(float(x)) for x in lam_nm]

    def _nk_scalar(self, lam_nm: float) -> complex:
        lam_um = max(lam_nm, 1.0) * 1e-3
        if self.model == "scaled":
            base, factor, dn, dk = self.params
            return base._nk_scalar(lam_nm) * factor + complex(dn, dk)
        if self.model == "constant":
            n = float(self.params[0])
            k = float(self.params[1]) if len(self.params) > 1 else 0.0
            return complex(n, k)
        if self.model == "sellmeier":
            l2 = lam_um * lam_um
            n2 = 1.0
            for b, c in self.params:
                denom = l2 - c
                if abs(denom) < 1e-9:
                    denom = 1e-9
                n2 += b * l2 / denom
            return complex(math.sqrt(n2) if n2 > 0 else 0.0, 0.0)
        if self.model == "cauchy":
            a, b, c = self.params[0], self.params[1], self.params[2]
            k0 = self.params[3] if len(self.params) > 3 else 0.0
            n = a + b / (lam_um ** 2) + c / (lam_um ** 4)
            return complex(n, k0)
        if self.model == "table":
            return _interp_table(self.params, lam_nm)
        raise ValueError("unknown dispersion model: %r" % (self.model,))

    # ---------------------------------------------------------------- helpers
    @property
    def is_metal(self) -> bool:
        return self.category == "metal"

    def scaled(self, factor: float = 1.0, dn: float = 0.0,
               dk: float = 0.0) -> "Material":
        """Return a copy with ``n -> factor*n + dn`` and ``k -> factor*k + dk``.

        Used by the tolerance analyser to model a uniform deposition error in
        the refractive index without rebuilding the whole material library.
        """
        return Material(self.key, self.label, self.category, "scaled",
                        (self, float(factor), float(dn), float(dk)), self.note)

    def n_at(self, lam_nm: float = 550.0) -> float:
        return self._nk_scalar(lam_nm).real

    def k_at(self, lam_nm: float = 550.0) -> float:
        return self._nk_scalar(lam_nm).imag

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "model": self.model,
            "note": self.note,
            "n550": round(self.n_at(550.0), 4),
            "k550": round(self.k_at(550.0), 4),
            "range": self.valid_range,
        }

    @property
    def valid_range(self) -> List[float]:
        if self.model == "table":
            rows = self.params
            return [rows[0][0], rows[-1][0]]
        return [300.0, 1100.0]


def _interp_table(rows: Sequence[Sequence[float]], lam_nm: float) -> complex:
    if lam_nm <= rows[0][0]:
        return complex(rows[0][1], rows[0][2])
    if lam_nm >= rows[-1][0]:
        return complex(rows[-1][1], rows[-1][2])
    for i in range(1, len(rows)):
        x1 = rows[i][0]
        if lam_nm <= x1:
            x0, n0, k0 = rows[i - 1]
            n1, k1 = rows[i][1], rows[i][2]
            t = (lam_nm - x0) / (x1 - x0)
            return complex(n0 + t * (n1 - n0), k0 + t * (k1 - k0))
    return complex(rows[-1][1], rows[-1][2])


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

# Malitson 1965, fused silica (300 nm .. 3.5 um)
_SIO2 = ((0.6961663, 0.0684043 ** 2), (0.4079426, 0.1162414 ** 2),
         (0.8974794, 9.896161 ** 2))

# Dodge 1984, MgF2 ordinary ray
_MGF2 = ((0.48755108, 0.04338408 ** 2), (0.39875031, 0.09461442 ** 2),
         (2.3120353, 23.793604 ** 2))

# Malitson 1962, sapphire ordinary ray
_AL2O3 = ((1.4313493, 0.0726631 ** 2), (0.65054713, 0.1193242 ** 2),
          (5.3414021, 18.028251 ** 2))

# Schott N-BK7
_BK7 = ((1.03961212, 0.00600069867), (0.231792344, 0.0200179144),
        (1.01046945, 103.560653))

_BUILTIN: Dict[str, Material] = {}


def _add(mat: Material) -> None:
    _BUILTIN[mat.key] = mat


_add(Material("Air", "Air 空气", "ambient", "constant", (1.0, 0.0),
              "Reference ambient; n = 1.0."))
_add(Material("Glass", "Glass 理想玻璃", "substrate", "constant", (1.52, 0.0),
              "Generic 1.52 substrate for quick experiments."))
_add(Material("SiO2", "SiO₂ 熔融石英", "dielectric", "sellmeier", _SIO2,
              "Malitson 1965 Sellmeier fit. The workhorse low-index film."))
_add(Material("MgF2", "MgF₂ 氟化镁", "dielectric", "sellmeier", _MGF2,
              "Dodge 1984, ordinary ray. Lowest practical visible index."))
_add(Material("Al2O3", "Al₂O₃ 氧化铝", "dielectric", "sellmeier", _AL2O3,
              "Malitson 1962, ordinary ray (sapphire). Hard mid-index film."))
_add(Material("BK7", "N-BK7 硼硅玻璃", "substrate", "sellmeier", _BK7,
              "Schott N-BK7. Standard optical substrate."))
_add(Material("Sapphire", "Sapphire 蓝宝石基底", "substrate", "sellmeier", _AL2O3,
              "Same dispersion as Al2O3, listed separately as a substrate."))
_add(Material("TiO2", "TiO₂ 二氧化钛", "dielectric", "cauchy",
              (2.180, 0.0600, 0.0, 0.0),
              "Cauchy fit to electron-beam evaporated amorphous TiO2."))
_add(Material("Ta2O5", "Ta₂O₅ 五氧化二钽", "dielectric", "cauchy",
              (2.030, 0.0260, 0.0, 0.0),
              "Ion-assisted Ta2O5, low loss in the visible/NIR."))
_add(Material("Nb2O5", "Nb₂O₅ 五氧化二铌", "dielectric", "cauchy",
              (2.170, 0.0450, 0.0, 0.0),
              "High-index alternative to Ta2O5."))
_add(Material("HfO2", "HfO₂ 二氧化铪", "dielectric", "cauchy",
              (1.920, 0.0300, 0.0008, 0.0),
              "Wide band-gap, laser-damage resistant high index."))
_add(Material("Si3N4", "Si₃N₄ 氮化硅", "dielectric", "cauchy",
              (1.940, 0.0180, 0.0, 0.0),
              "LPCVD / PECVD silicon nitride, CMOS compatible."))
_add(Material("ZrO2", "ZrO₂ 二氧化锆", "dielectric", "cauchy",
              (2.030, 0.0350, 0.0, 0.0),
              "Robust mid/high index for harsh environments."))
_add(Material("PMMA", "PMMA 有机玻璃", "polymer", "cauchy",
              (1.480, 0.0045, 0.0, 0.0),
              "Thermoplastic host for roll-to-roll optical films."))

# Metals: tabulated n, k (Palik / Johnson & Christy, lightly smoothed)
_AL = ((380, 0.38, 4.42), (400, 0.42, 4.85), (450, 0.62, 5.20),
       (500, 0.77, 5.58), (550, 0.96, 5.99), (600, 1.30, 6.60),
       (650, 1.57, 7.02), (700, 1.83, 7.44), (800, 2.50, 8.30))
_AG = ((380, 0.16, 1.60), (400, 0.14, 1.89), (450, 0.09, 2.48),
       (500, 0.05, 3.13), (550, 0.06, 3.62), (600, 0.06, 4.05),
       (650, 0.07, 4.55), (700, 0.08, 5.02), (800, 0.08, 5.90))
_AU = ((380, 1.66, 1.96), (400, 1.55, 2.11), (450, 1.05, 2.20),
       (500, 0.57, 2.35), (550, 0.36, 2.90), (600, 0.21, 3.44),
       (650, 0.16, 4.05), (700, 0.14, 4.65), (800, 0.13, 5.60))
_SI = ((400, 5.57, 0.387), (500, 4.29, 0.026), (600, 3.94, 0.020),
       (700, 3.79, 0.015), (800, 3.69, 0.011), (900, 3.63, 0.008),
       (1000, 3.59, 0.006), (1100, 3.55, 0.004))

_add(Material("Al", "Al 铝", "metal", "table", _AL,
              "Tabulated Palik data; native oxide not included."))
_add(Material("Ag", "Ag 银", "metal", "table", _AG,
              "Highest visible reflectance, tarnishes without capping."))
_add(Material("Au", "Au 金", "metal", "table", _AU,
              "NIR reflector; absorbs below ~520 nm (red cut-off)."))
_add(Material("Si", "Si 单晶硅", "substrate", "table", _SI,
              "IR substrate / detector material, strongly absorbing < 1.1 um."))


_ALIASES = {
    "sio2": "SiO2", "fused silica": "SiO2", "quartz": "SiO2",
    "mgf2": "MgF2", "al2o3": "Al2O3", "sapphire": "Sapphire",
    "bk7": "BK7", "n-bk7": "BK7", "tio2": "TiO2", "ta2o5": "Ta2O5",
    "nb2o5": "Nb2O5", "hfo2": "HfO2", "si3n4": "Si3N4", "zro2": "ZrO2",
    "pmma": "PMMA", "al": "Al", "ag": "Ag", "au": "Au", "si": "Si",
    "air": "Air", "glass": "Glass",
}


def get(name: str) -> Material:
    """Look a material up by key, alias or (exact) label."""
    if not name:
        raise KeyError("empty material name")
    if name in _BUILTIN:
        return _BUILTIN[name]
    lowered = name.strip().lower()
    if lowered in _ALIASES:
        return _BUILTIN[_ALIASES[lowered]]
    for mat in _BUILTIN.values():
        if mat.label == name:
            return mat
    raise KeyError("unknown material %r. Known: %s" % (name, ", ".join(names())))


def names() -> List[str]:
    return list(_BUILTIN.keys())


def categories() -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for mat in _BUILTIN.values():
        out.setdefault(mat.category, []).append(mat.key)
    return out


def describe() -> List[dict]:
    return [m.to_dict() for m in _BUILTIN.values()]


def nk(name: str, lam_nm):
    return get(name).nk(lam_nm)


def is_metal(name: str) -> bool:
    return get(name).is_metal


def _register(mat: Material) -> None:
    _BUILTIN[mat.key] = mat


def load_user_library(path: str) -> List[str]:
    """Register extra materials from a JSON file.

    Format::

        {"materials": [
            {"key": "MY_TiO2", "label": "My TiO2", "category": "dielectric",
             "model": "table", "data": [[400, 2.50, 0.0], [550, 2.38, 0.0]]}
        ]}
    """
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    added: List[str] = []
    for item in payload.get("materials", []):
        model = item.get("model", "table")
        if model == "table":
            params = tuple(tuple(row) for row in item["data"])
        elif model == "cauchy":
            params = tuple(item["params"])
        elif model == "sellmeier":
            params = tuple(tuple(pair) for pair in item["params"])
        elif model == "constant":
            params = tuple(item["params"])
        else:
            raise ValueError("unsupported model %r" % (model,))
        mat = Material(item["key"], item.get("label", item["key"]),
                       item.get("category", "dielectric"), model, params,
                       item.get("note", "user library"))
        _register(mat)
        added.append(mat.key)
    return added


def export_library(path: str) -> None:
    """Dump the built-in library (including any user additions) to JSON."""
    payload = {"materials": []}
    for mat in _BUILTIN.values():
        item = {"key": mat.key, "label": mat.label, "category": mat.category,
                "model": mat.model, "note": mat.note}
        if mat.model == "table":
            item["data"] = [list(row) for row in mat.params]
        else:
            item["params"] = [list(p) if isinstance(p, (tuple, list)) else p
                              for p in mat.params]
        payload["materials"].append(item)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
