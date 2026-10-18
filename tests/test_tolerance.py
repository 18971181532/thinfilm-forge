"""Tolerance-analysis tests."""

import unittest

import thinfilmforge as tff
from thinfilmforge.stack import Layer, Stack


class TestTolerance(unittest.TestCase):
    def setUp(self):
        mats = ["TiO2", "SiO2"] * 6
        self.hr = Stack([Layer(m, 550.0 / (4 * tff.material(m).n_at(550.0)))
                         for m in mats], "BK7", "Air")
        self.bands = [tff.BandTarget(520, 580, "min", 0.95)]

    def test_tight_process_has_high_yield(self):
        report = tff.monte_carlo(self.hr, self.bands, runs=40,
                                 thickness_sigma=0.2, seed=3)
        self.assertGreaterEqual(report["yield"], 95.0)
        self.assertEqual(sum(report["histogram"]["counts"]), 40)

    def test_loose_process_lowers_yield(self):
        ar = Stack.from_dict(tff.preset("ar_broadband")["stack"])
        bands = [tff.BandTarget(420, 680, "max", 0.02)]
        tight = tff.monte_carlo(ar, bands, runs=40, thickness_sigma=0.5, seed=3)
        loose = tff.monte_carlo(ar, bands, runs=40, thickness_sigma=8.0, seed=3)
        self.assertEqual(tight["yield"], 100.0)
        self.assertLess(loose["yield"], tight["yield"])
        self.assertGreater(loose["merit"]["max"], tight["merit"]["max"])

    def test_index_and_angle_errors(self):
        report = tff.monte_carlo(self.hr, self.bands, runs=25,
                                 thickness_sigma=0.0, index_sigma=1.0,
                                 angle_sigma=1.0, seed=1)
        self.assertEqual(report["runs"], 25)
        self.assertIn("p95", report["merit"])

    def test_sensitivity_ranking(self):
        rows = tff.layer_sensitivity(self.hr, self.bands, delta_nm=2.0)
        self.assertEqual(len(rows), len(self.hr.layers))
        magnitudes = [abs(r["deltaR"]) for r in rows]
        self.assertEqual(magnitudes, sorted(magnitudes, reverse=True))
        self.assertTrue(all(r["material"] in ("TiO2", "SiO2") for r in rows))

    def test_empty_stack_rejected(self):
        with self.assertRaises(ValueError):
            tff.monte_carlo(Stack([], "BK7", "Air"), self.bands, runs=5)


if __name__ == "__main__":
    unittest.main()
