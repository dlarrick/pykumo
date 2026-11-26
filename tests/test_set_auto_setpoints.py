"""Tests for set_auto_setpoints(): the combined heat/cool setpoint command."""

import json
import unittest
from unittest.mock import patch

from pykumo.py_kumo import PyKumo


def make_unit():
    """Return a minimally-mocked PyKumo with an empty status cache."""
    with patch.object(PyKumo, "__init__", lambda self, *a, **kw: None):
        unit = PyKumo.__new__(PyKumo)
    unit._status = {}
    unit._last_status_update = 0
    return unit


class TestSetAutoSetpoints(unittest.TestCase):
    def test_single_command_carries_both_setpoints(self):
        """One request is issued, containing both spCool and spHeat."""
        unit = make_unit()
        with patch.object(unit, "_request", return_value={"r": {}}) as mock_req:
            response = unit.set_auto_setpoints(26.67, 15.6)
        self.assertEqual(mock_req.call_count, 1)
        payload = json.loads(mock_req.call_args[0][0].decode("utf-8"))
        status = payload["c"]["indoorUnit"]["status"]
        self.assertEqual(status["spCool"], 26.67)
        self.assertEqual(status["spHeat"], 15.6)
        self.assertEqual(response, {"r": {}})

    def test_rounding_matches_individual_setters(self):
        """Cool rounds to 2 decimals, heat to 1, like the single setters."""
        unit = make_unit()
        with patch.object(unit, "_request", return_value={}) as mock_req:
            unit.set_auto_setpoints(26.666, 15.55)
        payload = json.loads(mock_req.call_args[0][0].decode("utf-8"))
        status = payload["c"]["indoorUnit"]["status"]
        self.assertEqual(status["spCool"], 26.67)
        self.assertEqual(status["spHeat"], round(15.55, 1))

    def test_status_cache_updated_for_both(self):
        """The optimistic cache update covers both setpoints."""
        unit = make_unit()
        with patch.object(unit, "_request", return_value={}):
            unit.set_auto_setpoints(26.67, 15.6)
        self.assertEqual(unit._status["spCool"], 26.67)
        self.assertEqual(unit._status["spHeat"], 15.6)


if __name__ == "__main__":
    unittest.main()
