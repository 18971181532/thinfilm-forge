"""Tests for spectral post-processing: colour, group delay, admittance loci."""

import math
import unittest

import thinfilmforge as tff
from thinfilmforge import color, metrics, tmm
from thinfilmforge.stack import Layer, Stack


class TestColor(unittest.TestCase):
    def grid(self):
        return [380.0 + i for i in range(401)]

    def test_white_is_d65_white(self):
        report = color.reflectance_color(self.grid(), [1.0] * 401)
        self.assertAlmostEqual(report["xy"][0], 0.3127, places=3)
        self.assertAlmostEqual(report["xy"][1], 0.3290, places=3)
        self.assertAlmostEqual(report["lab"][0], 100.0, places=1)
        self.assertEqual(report["hex"], "#ffffff")

    def test_black(self):
        report = color.reflectance_color(self.grid(), [0.0] * 401)
        self.assertEqual(report["hex"], "#000000")
        self.assertAlmostEqual(report["luminance"], 0.0, places=6)

    def test_coarse_grid_is_resampled(self):
        coarse = color.reflectance_color([400.0, 700.0], [1.0, 1.0])
        self.assertAlmostEqual(coarse["lab"][0], 100.0, places=0)

    def test_delta_e(self):
        self.assertAlmostEqual(color.delta_e76([50, 0, 0], [50, 0, 0]), 0.0)
        self.assertAlmostEqual(color.delta_e76([50, 1, 0], [50, 1, 3]), 3.0)


class TestGroupDelay(unittest.TestCase):
    def test_slab_delay_matches_group_index(self):
        # 1 um of SiO2: GD = n_g * L / c, with n_g ~ 1.47 in the visible.
        slab = Stack([Layer("SiO2", 1000.0)], "SiO2", "Air")
        lams = [540.0 + 0.5 * i for i in range(41)]
        gd = metrics.group_delay(slab, lams, quantity="transmission")
        expected = 1000.0 * 1.474 / tmm.C_NM_PER_FS
        self.assertAlmostEqual(gd["gd0"], expected, delta=0.1)
        self.assertLess(abs(gd["gdd0"]), 1.0)

    def test_mirror_penetration_depth(self):
        stack = tff.quarter_wave_stack("TiO2", "SiO2", 550.0, 6)
        gd = metrics.group_delay(stack, [540.0 + 0.5 * i for i in range(41)])
        self.assertGreater(gd["gd0"], 0.0)
        self.assertGreater(gd["penetrationDepth"], 50.0)

    def test_chirped_mirror_has_large_gdd(self):
        stack = tff.chirped_stack("TiO2", "SiO2", 800.0, 12, 0.30)
        gd = metrics.group_delay(stack, [720.0 + i for i in range(161)])
        self.assertGreater(max(abs(v) for v in gd["gdd"]), 20.0)


class TestAdmittance(unittest.TestCase):
    def test_locus_endpoint_matches_reflectance(self):
        stack = Stack.from_dict(tff.preset("ar_broadband")["stack"])
        loc = metrics.admittance_locus(stack, 550.0)
        y = complex(loc["re"][-1], loc["im"][-1])
        eta0 = complex(*loc["eta0"])
        expected = abs((eta0 - y) / (eta0 + y)) ** 2
        self.assertAlmostEqual(expected, tmm.simulate(stack, [550.0]).R[0], places=6)
        self.assertEqual(len(loc["re"]), len(stack.layers) + 1)
        self.assertAlmostEqual(loc["reflectance"][-1], expected, places=6)

    def test_quarter_wave_rotation(self):
        # One QW layer rotates the admittance by 90 degrees on a circle.
        stack = Stack([Layer("TiO2", 550.0 / (4 * 2.38))], "SiO2", "Air")
        loc = metrics.admittance_locus(stack, 550.0)
        self.assertEqual(len(loc["re"]), 2)


class TestPeaks(unittest.TestCase):
    def test_fabry_perot_peak(self):
        stack = Stack.from_dict(tff.preset("fp_filter")["stack"])
        lams = [520.0 + 0.02 * i for i in range(2001)]
        res = tmm.simulate(stack, lams)
        peaks = metrics.find_peaks(lams, res.T, "max", 0.2, 3)
        self.assertTrue(peaks)
        self.assertAlmostEqual(peaks[0]["lambda"], 550.0, delta=2.0)
        self.assertGreater(peaks[0]["value"], 0.9)
        self.assertLess(peaks[0]["fwhm"], 12.0)

    def test_band_stats(self):
        lam = [400.0, 500.0, 600.0, 700.0]
        vals = [0.0, 0.02, 0.04, 0.06]
        stats = metrics.band_stats(lam, vals, 500.0, 600.0)
        self.assertAlmostEqual(stats["avg"], 0.03)
        self.assertEqual(stats["n"], 2)


if __name__ == "__main__":
    unittest.main()
