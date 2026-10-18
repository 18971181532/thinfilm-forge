"""Tests for the inverse-design engine."""

import unittest

import thinfilmforge as tff
from thinfilmforge.design import (StackModel, build_grid, parse_band,
                                  passes_spec)
from thinfilmforge.stack import Layer, Stack


class TestStackModel(unittest.TestCase):
    def test_matches_full_solver(self):
        stack = Stack([Layer("TiO2", 23.0), Layer("SiO2", 91.0),
                       Layer("Ta2O5", 44.0)], "BK7", "Air")
        grid = [400.0 + 7.0 * i for i in range(50)]
        for pol in ("s", "p", "avg"):
            model = StackModel(stack.materials, stack.incident, stack.substrate,
                                  grid, 23.0, pol)
            fast = model.reflectance(stack.thicknesses)
            slow = tff.simulate(stack, grid, 23.0, pol).R
            for a, b in zip(fast, slow):
                self.assertAlmostEqual(a, b, places=10)

    def test_zero_thickness_layer_collapses(self):
        a = Stack([Layer("TiO2", 0.0), Layer("SiO2", 90.0)], "BK7", "Air")
        b = Stack([Layer("SiO2", 90.0)], "BK7", "Air")
        ra = tff.simulate(a, [550.0]).R[0]
        rb = tff.simulate(b, [550.0]).R[0]
        self.assertAlmostEqual(ra, rb, places=5)


class TestOptimizer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mats = ["TiO2", "SiO2"] * 3
        cls.stack = Stack([Layer(m, 60.0) for m in mats], "BK7", "Air")
        cls.spec = tff.DesignSpec([tff.BandTarget(420, 680, "max", 0.01)])
        cls.result = tff.design(cls.stack, cls.spec, popsize=18, maxiter=60,
                                seed=3, time_budget=20.0)

    def test_merit_improves(self):
        self.assertLess(self.result.merit, self.result.initial_merit)

    def test_spec_is_met(self):
        spectrum = self.result.spectra
        in_band = [r for l, r in zip(spectrum["wavelengths"], spectrum["R"])
                   if 420 <= l <= 680]
        self.assertLess(max(in_band), 0.015)
        self.assertLess(sum(in_band) / len(in_band), 0.006)

    def test_result_payload(self):
        payload = self.result.to_dict()
        for key in ("thicknesses", "materials", "history", "metrics", "spectra"):
            self.assertIn(key, payload)
        self.assertEqual(len(payload["thicknesses"]), 6)
        self.assertGreater(payload["evaluations"], 100)

    def test_bounds_are_respected(self):
        for value, (lo, hi) in zip(self.result.thicknesses, self.result.bounds):
            self.assertGreaterEqual(value, lo - 1e-9)
            self.assertLessEqual(value, hi + 1e-9)

    def test_fixed_layer(self):
        bounds = [(20.0, 20.0)] + [(4.0, 200.0)] * 5
        res = tff.design(self.stack, self.spec, bounds=bounds, popsize=12,
                         maxiter=20, seed=1, time_budget=8.0)
        self.assertAlmostEqual(res.thicknesses[0], 20.0, places=6)

    def test_deterministic_with_seed(self):
        a = tff.design(self.stack, self.spec, popsize=10, maxiter=12, seed=42,
                       time_budget=10.0, polish=False)
        b = tff.design(self.stack, self.spec, popsize=10, maxiter=12, seed=42,
                       time_budget=10.0, polish=False)
        self.assertEqual(a.thicknesses, b.thicknesses)


class TestSpec(unittest.TestCase):
    def test_band_parsing(self):
        band = parse_band("420:680:max:0.005:2")
        self.assertEqual((band.lo, band.hi, band.kind, band.value, band.weight),
                         (420.0, 680.0, "max", 0.005, 2.0))
        with self.assertRaises(ValueError):
            parse_band("bad")

    def test_pass_fail(self):
        bands = [tff.BandTarget(500, 600, "min", 0.9)]
        ok, per_band = passes_spec([0.95] * 5, [500, 525, 550, 575, 600], bands)
        self.assertTrue(ok)
        self.assertEqual(per_band, [True])
        ok, _ = passes_spec([0.5] * 5, [500, 525, 550, 575, 600], bands)
        self.assertFalse(ok)

    def test_multiband_grid_covers_edges(self):
        bands = [tff.BandTarget(400, 500), tff.BandTarget(600, 700)]
        grid = build_grid(bands, 12)
        self.assertIn(400.0, grid)
        self.assertIn(700.0, grid)
        self.assertTrue(any(599 < g < 601 for g in grid))


if __name__ == "__main__":
    unittest.main()
