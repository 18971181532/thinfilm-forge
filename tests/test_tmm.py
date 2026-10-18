"""Physics regression tests: Fresnel, energy conservation and solver agreement."""

import math
import random
import unittest

import thinfilmforge as tff
from thinfilmforge import tmm
from thinfilmforge.stack import Layer, Stack


class TestFresnel(unittest.TestCase):
    def test_bare_interface_matches_analytic(self):
        n_s = tff.material("BK7").n_at(550.0)
        expected = ((1.0 - n_s) / (1.0 + n_s)) ** 2
        res = tmm.simulate(Stack([], "BK7", "Air"), [550.0])
        self.assertAlmostEqual(res.R[0], expected, places=4)
        self.assertAlmostEqual(res.T[0], 1.0 - expected, places=6)

    def test_polarisation_split_at_brewster(self):
        # Air (1.0) -> BK7-like substrate: p-polarised reflection vanishes at
        # the Brewster angle arctan(n_s / n_0).
        stack = Stack([], "Glass", "Air")
        theta_b = math.degrees(math.atan(1.52))
        p = tmm.simulate(stack, [550.0], theta_b, "p")
        s = tmm.simulate(stack, [550.0], theta_b, "s")
        self.assertLess(p.R[0], 1e-6)
        self.assertGreater(s.R[0], 0.10)

    def test_energy_conservation_lossless(self):
        stack = tff.quarter_wave_stack("TiO2", "SiO2", 550.0, 4)
        for theta in (0.0, 15.0, 45.0, 70.0):
            res = tmm.simulate(stack, [430.0, 550.0, 660.0], theta, "s")
            for r, t in zip(res.R, res.T):
                self.assertAlmostEqual(r + t, 1.0, places=9)

    def test_total_internal_reflection(self):
        # Glass -> air beyond the critical angle: no transmitted flux.
        stack = Stack([], "Air", "Glass")
        res = tmm.simulate(stack, [550.0], 50.0, "s")
        self.assertAlmostEqual(res.T[0], 0.0, places=9)
        self.assertAlmostEqual(res.R[0], 1.0, places=9)


class TestSolvers(unittest.TestCase):
    def test_airy_matches_characteristic_matrix(self):
        rng = random.Random(2024)
        mats = ["TiO2", "SiO2", "Ta2O5", "MgF2", "Al2O3"]
        for _ in range(25):
            layers = [Layer(rng.choice(mats), rng.uniform(5, 350))
                      for _ in range(rng.randint(0, 9))]
            stack = Stack(layers, rng.choice(["BK7", "SiO2", "Sapphire"]), "Air")
            for lam in (420.0, 550.0, 690.0):
                for theta, pol in ((0.0, "s"), (37.0, "p")):
                    airy = tmm.simulate(stack, [lam], theta, pol)
                    r_mat, t_mat = tmm.reflectance_matrix(stack, lam, theta, pol)
                    self.assertAlmostEqual(airy.R[0], r_mat, places=9)
                    self.assertAlmostEqual(airy.T[0], t_mat, places=9)

    def test_quarter_wave_mirror_formula(self):
        # R = ((1 - (nH/nL)^(2N) * nH^2/ns) / (1 + ...))^2 for (HL)^N H
        lam0 = 550.0
        stack = tff.quarter_wave_stack("TiO2", "SiO2", lam0, pairs=6)
        n_h = tff.material("TiO2").n_at(lam0)
        n_l = tff.material("SiO2").n_at(lam0)
        n_s = tff.material("SiO2").n_at(lam0)
        y = (n_h / n_l) ** 12 * n_h ** 2 / n_s
        expected = ((1 - y) / (1 + y)) ** 2
        got = tmm.simulate(stack, [lam0]).R[0]
        self.assertAlmostEqual(got, expected, places=4)

    def test_absorbing_metal(self):
        stack = Stack([Layer("Al", 100.0)], "Glass", "Air")
        res = tmm.simulate(stack, [550.0])
        self.assertLess(res.R[0], 1.0)
        self.assertGreater(res.A[0], 0.0)
        self.assertAlmostEqual(res.R[0] + res.T[0] + res.A[0], 1.0, places=9)

    def test_thick_absorbing_layer_is_stable(self):
        stack = Stack([Layer("Si", 2_000_000.0)], "SiO2", "Air")
        res = tmm.simulate(stack, [800.0])
        self.assertTrue(0.0 <= res.R[0] <= 1.0)
        self.assertLess(res.T[0], 1e-6)

    def test_angle_scan_stays_bounded(self):
        st = Stack.from_dict(tff.preset("hr_mirror")["stack"])
        values = [tmm.simulate(st, [550.0], a, "avg").R[0] for a in range(0, 86, 5)]
        for v in values:
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)
        self.assertGreater(values[0], 0.99)   # normal incidence stop-band
        self.assertGreater(min(values), 0.5)  # still reflective at 85 degrees


if __name__ == "__main__":
    unittest.main()
