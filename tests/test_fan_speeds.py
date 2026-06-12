"""Tests for get_fan_speeds() across all numberOfFanSpeeds values."""

import unittest
from unittest.mock import patch
from pykumo.py_kumo import PyKumo


def make_unit(num_speeds, has_auto=False):
    """Return a minimally-mocked PyKumo with the given profile."""
    with patch.object(PyKumo, "__init__", lambda self, *a, **kw: None):
        unit = PyKumo.__new__(PyKumo)
    profile = {"numberOfFanSpeeds": num_speeds}
    if has_auto:
        profile["hasFanSpeedAuto"] = True
    unit._profile = profile
    unit._status = {}
    return unit


class TestGetFanSpeeds(unittest.TestCase):
    def test_3speed_includes_superquiet(self):
        """Units reporting numberOfFanSpeeds=3 must expose superQuiet."""
        unit = make_unit(3)
        speeds = unit.get_fan_speeds()
        self.assertIn("superQuiet", speeds, "3-speed units should include superQuiet")
        self.assertIn("quiet", speeds)
        self.assertIn("low", speeds)
        self.assertIn("powerful", speeds)

    def test_3speed_superquiet_is_first(self):
        """superQuiet should be the first option for 3-speed units."""
        unit = make_unit(3)
        speeds = unit.get_fan_speeds()
        self.assertEqual(speeds[0], "superQuiet")

    def test_3speed_with_auto(self):
        """3-speed + hasFanSpeedAuto appends auto after superQuiet."""
        unit = make_unit(3, has_auto=True)
        speeds = unit.get_fan_speeds()
        self.assertIn("superQuiet", speeds)
        self.assertIn("auto", speeds)

    def test_4speed_unchanged(self):
        """4-speed behaviour is unchanged."""
        unit = make_unit(4)
        speeds = unit.get_fan_speeds()
        self.assertEqual(speeds, ["quiet", "Low", "powerful", "superPowerful"])

    def test_5speed_unchanged(self):
        """5-speed (default) behaviour is unchanged."""
        unit = make_unit(5)
        speeds = unit.get_fan_speeds()
        self.assertEqual(
            speeds, ["superQuiet", "quiet", "low", "powerful", "superPowerful"]
        )

    def test_set_fan_speed_accepts_superquiet_on_3speed(self):
        """set_fan_speed must accept superQuiet for 3-speed units."""
        with patch.object(PyKumo, "__init__", lambda self, *a, **kw: None):
            unit = PyKumo.__new__(PyKumo)
        unit._profile = {"numberOfFanSpeeds": 3}
        unit._status = {}
        # Verify get_fan_speeds includes superQuiet so set_fan_speed won't reject it
        valid = unit.get_fan_speeds()
        self.assertIn("superQuiet", valid)


if __name__ == "__main__":
    unittest.main()
