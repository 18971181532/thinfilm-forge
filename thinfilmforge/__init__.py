"""ThinFilmForge - an inverse-design workbench for optical thin films.

Public API
----------
>>> import thinfilmforge as tff
>>> stack = tff.quarter_wave_stack("TiO2", "SiO2", 550, pairs=6)
>>> spec = tff.DesignSpec([tff.BandTarget(450, 650, "min", 0.98)])
>>> result = tff.design(stack, spec, maxiter=40, time_budget=8)
>>> round(result.thicknesses[0], 1) > 0
True
"""

from __future__ import annotations

__version__ = "1.0.0"
__author__ = "ThinFilmForge contributors"

from .materials import Material, get as material, names as material_names
from .stack import (Layer, Stack, PRESETS, preset, preset_ids,
                    quarter_wave_stack, quarter_wave_thickness,
                    cavity_filter, chirped_stack)
from .tmm import simulate, SimulationResult
from .color import reflectance_color, cie_xyz, delta_e76
from .metrics import (band_stats, spectral_summary, group_delay,
                      admittance_locus, find_peaks, full_report)
from .design import (BandTarget, DesignSpec, StackModel, DesignResult,
                     design, optimize, build_grid, parse_band, passes_spec)
from .tolerance import monte_carlo, layer_sensitivity, tolerance_report

__all__ = [
    "__version__",
    "Material", "material", "material_names",
    "Layer", "Stack", "PRESETS", "preset", "preset_ids",
    "quarter_wave_stack", "quarter_wave_thickness", "cavity_filter",
    "chirped_stack",
    "simulate", "SimulationResult",
    "reflectance_color", "cie_xyz", "delta_e76",
    "band_stats", "spectral_summary", "group_delay", "admittance_locus",
    "find_peaks", "full_report",
    "BandTarget", "DesignSpec", "StackModel", "DesignResult", "design",
    "optimize", "build_grid", "parse_band", "passes_spec",
    "monte_carlo", "layer_sensitivity", "tolerance_report",
]
