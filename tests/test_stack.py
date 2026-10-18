"""Stack / preset / material-library tests."""

import json
import os
import tempfile
import unittest

import thinfilmforge as tff
from thinfilmforge import materials
from thinfilmforge.stack import Layer, Stack


class TestStack(unittest.TestCase):
    def test_roundtrip(self):
        stack = Stack([Layer("TiO2", 12.5), Layer("SiO2", 88.0)],
                      "BK7", "Air", "demo", "round trip")
        restored = Stack.from_dict(json.loads(json.dumps(stack.to_dict())))
        self.assertEqual(restored.to_dict(), stack.to_dict())

    def test_material_aliases(self):
        self.assertEqual(Stack([], "bk7", "air").substrate, "BK7")
        self.assertEqual(materials.get("tio2").key, "TiO2")
        self.assertEqual(materials.get("TiO₂ 二氧化钛").key, "TiO2")

    def test_quarter_wave(self):
        n = tff.material("TiO2").n_at(550.0)
        self.assertAlmostEqual(tff.quarter_wave_thickness("TiO2", 550.0),
                               550.0 / (4 * n), places=9)

    def test_thickness_validation(self):
        with self.assertRaises(ValueError):
            Layer("SiO2", -5.0)

    def test_total_and_optical_thickness(self):
        stack = Stack([Layer("TiO2", 100.0)], "BK7", "Air")
        self.assertAlmostEqual(stack.total_thickness, 100.0)
        self.assertAlmostEqual(stack.optical_thickness(550.0),
                               100.0 * tff.material("TiO2").n_at(550.0))


class TestPresets(unittest.TestCase):
    def test_all_presets_load(self):
        self.assertGreaterEqual(len(tff.preset_ids()), 6)
        for pid in tff.preset_ids():
            item = tff.preset(pid)
            stack = Stack.from_dict(item["stack"])
            self.assertTrue(item["name"])
            self.assertTrue(item["description"])
            self.assertGreater(len(stack.layers), 0, pid)
            tff.simulate(stack, [450.0, 550.0, 650.0])

    def test_ar_preset_really_antireflects(self):
        stack = Stack.from_dict(tff.preset("ar_broadband")["stack"])
        grid = [420.0 + i for i in range(261)]
        R = tff.simulate(stack, grid).R
        self.assertLess(sum(R) / len(R), 0.004)
        self.assertLess(max(R), 0.01)

    def test_hr_preset_reflects(self):
        stack = Stack.from_dict(tff.preset("hr_mirror")["stack"])
        self.assertGreater(tff.simulate(stack, [550.0]).R[0], 0.99)

    def test_metal_preset(self):
        stack = Stack.from_dict(tff.preset("metal_mirror")["stack"])
        res = tff.simulate(stack, [550.0])
        self.assertGreater(res.R[0], 0.85)
        self.assertLess(res.T[0], 0.01)


class TestMaterials(unittest.TestCase):
    def test_dispersion_is_positive(self):
        for key in materials.names():
            for lam in (400.0, 550.0, 700.0):
                n = materials.get(key).nk(lam)
                self.assertGreater(n.real, 0.0, key)
                self.assertGreaterEqual(n.imag, 0.0, key)

    def test_normal_dispersion_of_glass(self):
        self.assertGreater(materials.get("BK7").n_at(450.0),
                           materials.get("BK7").n_at(650.0))

    def test_user_library(self):
        payload = {"materials": [{"key": "TEST_FILM", "label": "Test film",
                                  "model": "cauchy", "params": [1.9, 0.01, 0.0]}]}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as fh:
            json.dump(payload, fh)
            path = fh.name
        try:
            added = materials.load_user_library(path)
            self.assertIn("TEST_FILM", added)
            self.assertAlmostEqual(materials.get("TEST_FILM").n_at(550.0), 1.93306,
                                   places=3)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
