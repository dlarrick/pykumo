"""Tests for PyKumoBase.has_profile()."""

import unittest
from unittest.mock import MagicMock, patch

from pykumo.py_kumo_base import PyKumoBase


# Minimal cfg_json accepted by PyKumoBase.__init__
_CFG = {
    "password": "dGVzdA==",  # base64("test")
    "crypto_serial": "0123456789ABCDEF01234567",
}


class TestHasProfile(unittest.TestCase):
    """PyKumoBase.has_profile() behaviour."""

    def _make_unit(self):
        return PyKumoBase("Test Unit", "192.168.1.1", _CFG)

    def test_false_before_any_poll(self):
        """has_profile() is False immediately after construction."""
        unit = self._make_unit()
        self.assertFalse(unit.has_profile())

    def test_true_after_profile_populated(self):
        """has_profile() is True once _profile contains real data."""
        unit = self._make_unit()
        unit._profile = {"hasModeAuto": True, "numberOfFanSpeeds": 5}
        self.assertTrue(unit.has_profile())

    def test_false_when_profile_reset_to_empty(self):
        """has_profile() returns False again if profile is cleared."""
        unit = self._make_unit()
        unit._profile = {"hasModeAuto": True}
        self.assertTrue(unit.has_profile())
        unit._profile = {}
        self.assertFalse(unit.has_profile())


if __name__ == "__main__":
    unittest.main()
