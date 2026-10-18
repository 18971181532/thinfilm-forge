"""Layer stacks, analytic seeds and ready-to-run demo designs."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

from . import materials

__all__ = [
    "Layer",
    "Stack",
    "quarter_wave_thickness",
    "quarter_wave_stack",
    "cavity_filter",
    "chirped_stack",
    "PRESETS",
    "preset",
    "preset_ids",
]


@dataclass
class Layer:
    material: str
    thickness: float  # nanometres

    def __post_init__(self) -> None:
        # normalise the material key so JSON round-trips are stable
        self.material = materials.get(self.material).key
        self.thickness = float(self.thickness)
        if self.thickness < 0:
            raise ValueError("layer thickness must be >= 0 nm")

    def to_dict(self) -> dict:
        return {"material": self.material, "thickness": round(self.thickness, 4)}


@dataclass
class Stack:
    layers: List[Layer] = field(default_factory=list)
    substrate: str = "SiO2"
    incident: str = "Air"
    name: str = "custom"
    description: str = ""

    def __post_init__(self) -> None:
        self.substrate = materials.get(self.substrate).key
        self.incident = materials.get(self.incident).key
        self.layers = [l if isinstance(l, Layer) else Layer(**l) for l in self.layers]

    # ------------------------------------------------------------------ dunder
    def __len__(self) -> int:
        return len(self.layers)

    def __iter__(self):
        return iter(self.layers)

    # ------------------------------------------------------------------ helpers
    @property
    def materials(self) -> List[str]:
        return [l.material for l in self.layers]

    @property
    def thicknesses(self) -> List[float]:
        return [l.thickness for l in self.layers]

    @property
    def total_thickness(self) -> float:
        return sum(self.thicknesses)

    def copy(self) -> "Stack":
        return Stack([Layer(l.material, l.thickness) for l in self.layers],
                     self.substrate, self.incident, self.name, self.description)

    def with_thicknesses(self, values: Sequence[float], name: Optional[str] = None) -> "Stack":
        if len(values) != len(self.layers):
            raise ValueError("expected %d thicknesses, got %d"
                             % (len(self.layers), len(values)))
        return Stack([Layer(l.material, max(0.0, float(v))) for l, v in zip(self.layers, values)],
                     self.substrate, self.incident, name or self.name, self.description)

    def qwot(self, lam0: float = 550.0) -> List[float]:
        """Quarter-wave optical thickness of each layer at ``lam0``."""
        return [lam0 / (4.0 * materials.get(l.material).n_at(lam0)) for l in self.layers]

    def optical_thickness(self, lam: float = 550.0) -> float:
        return sum(l.thickness * materials.get(l.material).n_at(lam) for l in self.layers)

    def validate(self) -> List[str]:
        warn: List[str] = []
        if len(self.layers) == 0:
            warn.append("stack has no layers - only the bare substrate is simulated")
        if materials.is_metal(self.substrate):
            warn.append("metallic substrate: transmission is not meaningful")
        for i, layer in enumerate(self.layers):
            if materials.is_metal(layer.material) and layer.thickness > 5:
                warn.append("layer %d is metallic (%.0f nm): opaque metal film assumptions apply"
                            % (i + 1, layer.thickness))
            if layer.thickness == 0:
                warn.append("layer %d has zero thickness" % (i + 1))
        return warn

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "incident": self.incident,
            "substrate": self.substrate,
            "layers": [l.to_dict() for l in self.layers],
            "totalThickness": round(self.total_thickness, 2),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Stack":
        if "stack" in payload and isinstance(payload["stack"], dict):
            payload = payload["stack"]
        return cls(
            layers=[Layer(item["material"], item["thickness"])
                    for item in payload.get("layers", [])],
            substrate=payload.get("substrate", "SiO2"),
            incident=payload.get("incident", "Air"),
            name=payload.get("name", "custom"),
            description=payload.get("description", ""),
        )

    @classmethod
    def load(cls, path: str) -> "Stack":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Analytic seeds
# ---------------------------------------------------------------------------

def quarter_wave_thickness(material: str, lam0: float = 550.0) -> float:
    """Physical thickness giving a quarter-wave optical path at ``lam0``."""
    return lam0 / (4.0 * materials.get(material).n_at(lam0))


def quarter_wave_stack(high: str, low: str, lam0: float = 550.0,
                       pairs: int = 6, start: str = "H") -> Stack:
    """(HL)^pairs H quarter-wave mirror - the canonical Bragg reflector."""
    seq = [start.upper()] + [("L" if start.upper() == "H" else "H"),
                             ("H" if start.upper() == "H" else "L")] * pairs
    layers = [Layer(high if s == "H" else low,
                    quarter_wave_thickness(high if s == "H" else low, lam0))
              for s in seq]
    if seq[-1] == "L":  # terminate on the high-index layer for maximum contrast
        layers.pop()
    return Stack(layers, substrate="SiO2", incident="Air",
                 name="qw_mirror",
                 description="Quarter-wave Bragg mirror, centre wavelength %.0f nm" % lam0)


def cavity_filter(high: str, low: str, lam0: float = 550.0, pairs: int = 4,
                  order: int = 2) -> Stack:
    """Fabry-Perot narrow-band filter (HL)^m | kH | (LH)^m."""
    layers: List[Layer] = []
    for _ in range(pairs):
        layers += [Layer(high, quarter_wave_thickness(high, lam0)),
                   Layer(low, quarter_wave_thickness(low, lam0))]
    layers.append(Layer(high, order * quarter_wave_thickness(high, lam0)))
    for _ in range(pairs):
        layers += [Layer(low, quarter_wave_thickness(low, lam0)),
                   Layer(high, quarter_wave_thickness(high, lam0))]
    return Stack(layers, substrate="SiO2", incident="Air", name="fp_filter",
                 description="Fabry-Perot band-pass, cavity order %d at %.0f nm"
                             % (order, lam0))


def chirped_stack(high: str, low: str, lam0: float = 800.0, pairs: int = 12,
                  chirp: float = 0.35) -> Stack:
    """Chirped (apodised) mirror: QW thickness grows linearly across the stack.

    A chirp makes different wavelengths penetrate to different depths, which
    produces the negative group-delay dispersion used to compress ultrashort
    pulses.
    """
    layers: List[Layer] = []
    for i in range(pairs):
        scale = 1.0 + chirp * (i / max(1, pairs - 1) - 0.5) * 2.0
        for mat in (high, low):
            layers.append(Layer(mat, quarter_wave_thickness(mat, lam0) * scale))
    return Stack(layers, substrate="SiO2", incident="Air", name="chirped_mirror",
                 description="Chirped mirror, %.0f%% thickness chirp around %.0f nm"
                             % (chirp * 100, lam0))


# ---------------------------------------------------------------------------
# Presets shown in the UI / CLI
# ---------------------------------------------------------------------------

def _p_ar_single() -> dict:
    lam = 550.0
    d = quarter_wave_thickness("MgF2", lam)
    return {
        "id": "ar_single",
        "name": "单层 MgF₂ 减反膜",
        "description": "550 nm 四分之一波长 MgF₂ 单层，把空气/玻璃界面反射从 4.0% 压到约 1.3%。",
        "stack": Stack([Layer("MgF2", d)], "BK7", "Air", "ar_single",
                       "Single-layer quarter-wave antireflection coating").to_dict(),
    }


def _p_ar_broadband() -> dict:
    stack = Stack([Layer("TiO2", 4.0), Layer("SiO2", 72.5), Layer("TiO2", 114.2),
                   Layer("SiO2", 37.6), Layer("TiO2", 13.2), Layer("SiO2", 38.5)],
                  "BK7", "Air", "ar_broadband",
                  "Six-layer broadband antireflection coating")
    return {
        "id": "ar_broadband",
        "name": "六层宽带减反膜",
        "description": "TiO₂/SiO₂ 六层结构，420–680 nm 平均反射 0.18%、最大 0.45%，"
                       "由本项目的 DE 优化器从 60 nm 均匀初始设计收敛而来。",
        "stack": stack.to_dict(),
    }


def _p_hr() -> dict:
    stack = quarter_wave_stack("TiO2", "SiO2", 550.0, 6)
    stack.name = "hr_mirror"
    stack.description = "13-layer quarter-wave high reflector"
    return {
        "id": "hr_mirror",
        "name": "13 层高反镜",
        "description": "550 nm 处 (HL)⁶H 四分之一波长堆，反射率 > 99.9%，阻带宽度约 180 nm。",
        "stack": stack.to_dict(),
    }


def _p_metal() -> dict:
    stack = Stack([Layer("Al", 120.0), Layer("SiO2", 90.0)], "Glass", "Air",
                  "metal_mirror", "Aluminium mirror with SiO2 protective overcoat")
    return {
        "id": "metal_mirror",
        "name": "铝反射镜 + 保护层",
        "description": "120 nm 铝膜加 90 nm SiO₂ 保护层，可见光宽带反射率 > 90%，接近金属极限。",
        "stack": stack.to_dict(),
    }


def _p_fp() -> dict:
    stack = cavity_filter("TiO2", "SiO2", 550.0, 4, 2)
    return {
        "id": "fp_filter",
        "name": "法布里-珀罗窄带滤光片",
        "description": "两列 (HL)⁴ 反射镜夹一个 2H 腔层，550 nm 处形成亚纳米级透射峰。",
        "stack": stack.to_dict(),
    }


def _p_chirped() -> dict:
    stack = chirped_stack("TiO2", "SiO2", 800.0, 12, 0.30)
    return {
        "id": "chirped_mirror",
        "name": "啁啾镜（色散补偿）",
        "description": "层厚沿生长方向渐变，不同波长穿透深度不同，产生负群延迟色散，用于飞秒脉冲压缩。",
        "stack": stack.to_dict(),
    }


def _p_edge() -> dict:
    values = [82.0, 80.0, 72.0, 151.0, 86.0, 99.0, 63.0, 101.0, 65.0, 95.0,
              64.0, 108.0, 60.0, 103.0, 62.0, 103.0, 61.0, 102.0, 75.0, 4.0]
    layers = [Layer("TiO2" if i % 2 == 0 else "SiO2", d)
              for i, d in enumerate(values)]
    stack = Stack(layers, "BK7", "Air", "edge_filter",
                  "Long-wave pass edge filter")
    return {
        "id": "edge_filter",
        "name": "长波通截止滤光片",
        "description": "20 层 TiO₂/SiO₂ 长波通：400–470 nm 平均 R=1.2%，520–700 nm 平均 R=99.3%，"
                       "可作为「设计」页继续优化的起点。",
        "stack": stack.to_dict(),
    }


_PRESET_FUNCS = [_p_ar_single, _p_ar_broadband, _p_hr, _p_metal, _p_fp,
                 _p_chirped, _p_edge]

PRESETS: Dict[str, dict] = {}


def _build_presets() -> None:
    PRESETS.clear()
    for fn in _PRESET_FUNCS:
        item = fn()
        PRESETS[item["id"]] = item


_build_presets()


def preset(pid: str) -> dict:
    if pid not in PRESETS:
        raise KeyError("unknown preset %r" % (pid,))
    return copy.deepcopy(PRESETS[pid])


def preset_ids() -> List[str]:
    return list(PRESETS.keys())
