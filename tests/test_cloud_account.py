import unittest
from unittest.mock import patch
from pykumo.py_kumo_cloud_account import KumoCloudAccount


class TestKumoCloudAccount(unittest.TestCase):
    def setUp(self):
        self.username = "testuser"
        self.password = "testpass"
        self.cached_dict = [
            {},
            {},
            {
                "children": [
                    {
                        "zoneTable": {
                            "SERIAL1": {
                                "serial": "SERIAL1",
                                "label": "Unit 1",
                                "address": "192.168.1.10",
                                "password": "pw1",
                                "cryptoSerial": "cs1",
                                "mac": "mac1",
                                "unitType": "ductless",
                            }
                        }
                    }
                ]
            },
        ]

    @patch("pykumo.py_kumo_cloud_account.KumoCloudV3")
    @patch("pykumo.py_kumo_cloud_account.probe_ip")
    def test_scenario_1_cached_info_fine(self, mock_probe_ip, mock_v3):
        """Scenario 1: Cached info is fine. The indoor units in the cache are reachable."""
        mock_probe_ip.return_value = True

        # Mock V3 to return the same unit
        v3_instance = mock_v3.return_value
        v3_instance.get_all_device_credentials.return_value = {
            "SERIAL1": {
                "serial": "SERIAL1",
                "label": "Unit 1",
                "password": "pw1",
                "cryptoSerial": "cs1",
                "mac": "mac1",
                "unitType": "ductless",
            }
        }

        account = KumoCloudAccount(
            self.username, self.password, kumo_dict=self.cached_dict
        )
        success = account.try_setup()

        self.assertTrue(success)
        self.assertIn("SERIAL1", account._units)
        self.assertEqual(account.get_address("SERIAL1"), "192.168.1.10")
        mock_probe_ip.assert_called_with(
            "192.168.1.10", {"password": "pw1", "cryptoSerial": "cs1"}, timeout=2.0
        )

    @patch("pykumo.py_kumo_cloud_account.KumoCloudV3")
    @patch("pykumo.py_kumo_cloud_account.probe_ip")
    def test_scenario_2_cached_info_unreachable(self, mock_probe_ip, mock_v3):
        """Scenario 2: Cached info is fine, but one or more indoor units is not reachable."""

        # Unit 1 reachable, Unit 2 unreachable
        def probe_side_effect(ip, creds, timeout):
            return ip == "192.168.1.10"

        mock_probe_ip.side_effect = probe_side_effect

        cached_dict = [
            {},
            {},
            {
                "children": [
                    {
                        "zoneTable": {
                            "SERIAL1": {
                                "serial": "SERIAL1",
                                "label": "U1",
                                "address": "192.168.1.10",
                                "password": "pw1",
                                "cryptoSerial": "cs1",
                                "mac": "mac1",
                                "unitType": "ductless",
                            },
                            "SERIAL2": {
                                "serial": "SERIAL2",
                                "label": "U2",
                                "address": "192.168.1.20",
                                "password": "pw2",
                                "cryptoSerial": "cs2",
                                "mac": "mac2",
                                "unitType": "ductless",
                            },
                        }
                    }
                ]
            },
        ]

        v3_instance = mock_v3.return_value
        v3_instance.get_all_device_credentials.return_value = {
            "SERIAL1": {
                "serial": "SERIAL1",
                "label": "U1",
                "password": "pw1",
                "cryptoSerial": "cs1",
                "mac": "mac1",
                "unitType": "ductless",
            },
            "SERIAL2": {
                "serial": "SERIAL2",
                "label": "U2",
                "password": "pw2",
                "cryptoSerial": "cs2",
                "mac": "mac2",
                "unitType": "ductless",
            },
        }

        account = KumoCloudAccount(self.username, self.password, kumo_dict=cached_dict)
        success = account.try_setup()

        self.assertTrue(success)
        self.assertIn("SERIAL1", account._units)
        self.assertIn("SERIAL2", account._units)
        self.assertEqual(account.get_address("SERIAL1"), "192.168.1.10")
        # SERIAL2 remains in _units with its last known address despite being unreachable
        self.assertEqual(account.get_address("SERIAL2"), "192.168.1.20")

    @patch("pykumo.py_kumo_cloud_account.KumoCloudV3")
    @patch("pykumo.py_kumo_cloud_account.probe_ip")
    @patch("pykumo.py_kumo_cloud_account.probe_candidate_ips")
    def test_scenario_3_stale_ip_new_discovery(
        self, mock_probe_candidate, mock_probe_ip, mock_v3
    ):
        """Scenario 3: Stale IP in cache, but discovery has a new address."""
        # Cached IP 1.10 is unreachable
        mock_probe_ip.return_value = False
        # Discovery finds it at 1.15
        mock_probe_candidate.return_value = {"SERIAL1": "192.168.1.15"}

        v3_instance = mock_v3.return_value
        v3_instance.get_all_device_credentials.return_value = {
            "SERIAL1": {
                "serial": "SERIAL1",
                "label": "U1",
                "password": "pw1",
                "cryptoSerial": "cs1",
                "mac": "mac1",
                "unitType": "ductless",
            }
        }

        candidate_ips = {"mac1": "192.168.1.15"}
        account = KumoCloudAccount(
            self.username, self.password, kumo_dict=self.cached_dict
        )
        success = account.try_setup(candidate_ips=candidate_ips)

        self.assertTrue(success)
        self.assertEqual(account.get_address("SERIAL1"), "192.168.1.15")

    @patch("pykumo.py_kumo_cloud_account.KumoCloudV3")
    @patch("pykumo.py_kumo_cloud_account.probe_ip")
    @patch("pykumo.py_kumo_cloud_account.probe_candidate_ips")
    def test_scenario_4_new_unit_in_discovery(
        self, mock_probe_candidate, mock_probe_ip, mock_v3
    ):
        """Scenario 4: A new unit is contained within the discovery information."""
        # SERIAL1 in cache, SERIAL2 only in Cloud and Discovery
        mock_probe_ip.side_effect = lambda ip, creds, timeout: ip == "192.168.1.10"
        mock_probe_candidate.return_value = {"SERIAL2": "192.168.1.20"}

        v3_instance = mock_v3.return_value
        v3_instance.get_all_device_credentials.return_value = {
            "SERIAL1": {
                "serial": "SERIAL1",
                "label": "U1",
                "password": "pw1",
                "cryptoSerial": "cs1",
                "mac": "mac1",
                "unitType": "ductless",
            },
            "SERIAL2": {
                "serial": "SERIAL2",
                "label": "U2",
                "password": "pw2",
                "cryptoSerial": "cs2",
                "mac": "mac2",
                "unitType": "ductless",
            },
        }

        candidate_ips = {"mac2": "192.168.1.20"}
        account = KumoCloudAccount(
            self.username, self.password, kumo_dict=self.cached_dict
        )
        success = account.try_setup(candidate_ips=candidate_ips)

        self.assertTrue(success)
        self.assertIn("SERIAL1", account._units)
        self.assertIn("SERIAL2", account._units)
        self.assertEqual(account.get_address("SERIAL1"), "192.168.1.10")
        self.assertEqual(account.get_address("SERIAL2"), "192.168.1.20")

    @patch("pykumo.py_kumo_cloud_account.KumoCloudV3")
    @patch("pykumo.py_kumo_cloud_account.probe_candidate_ips")
    def test_scenario_5_new_installation_all_discovered(
        self, mock_probe_candidate, mock_v3
    ):
        """Scenario 5: New installation with no cache, all units in discovery."""
        mock_probe_candidate.return_value = {
            "SERIAL1": "192.168.1.10",
            "SERIAL2": "192.168.1.20",
        }

        v3_instance = mock_v3.return_value
        v3_instance.get_all_device_credentials.return_value = {
            "SERIAL1": {
                "serial": "SERIAL1",
                "label": "U1",
                "password": "pw1",
                "cryptoSerial": "cs1",
                "mac": "mac1",
                "unitType": "ductless",
            },
            "SERIAL2": {
                "serial": "SERIAL2",
                "label": "U2",
                "password": "pw2",
                "cryptoSerial": "cs2",
                "mac": "mac2",
                "unitType": "ductless",
            },
        }

        candidate_ips = {"mac1": "192.168.1.10", "mac2": "192.168.1.20"}
        account = KumoCloudAccount(self.username, self.password, kumo_dict=None)
        success = account.try_setup(candidate_ips=candidate_ips)

        self.assertTrue(success)
        self.assertEqual(len(account._units), 2)
        self.assertEqual(account.get_address("SERIAL1"), "192.168.1.10")
        self.assertEqual(account.get_address("SERIAL2"), "192.168.1.20")

    @patch("pykumo.py_kumo_cloud_account.KumoCloudV3")
    @patch("pykumo.py_kumo_cloud_account.probe_candidate_ips")
    def test_scenario_6_new_installation_missing_discovery(
        self, mock_probe_candidate, mock_v3
    ):
        """Scenario 6: New installation, one unit missing discovery information."""
        # Only SERIAL1 discovered
        mock_probe_candidate.return_value = {"SERIAL1": "192.168.1.10"}

        v3_instance = mock_v3.return_value
        v3_instance.get_all_device_credentials.return_value = {
            "SERIAL1": {
                "serial": "SERIAL1",
                "label": "U1",
                "password": "pw1",
                "cryptoSerial": "cs1",
                "mac": "mac1",
                "unitType": "ductless",
            },
            "SERIAL2": {
                "serial": "SERIAL2",
                "label": "U2",
                "password": "pw2",
                "cryptoSerial": "cs2",
                "mac": "mac2",
                "unitType": "ductless",
            },
        }

        candidate_ips = {"mac1": "192.168.1.10"}
        account = KumoCloudAccount(self.username, self.password, kumo_dict=None)
        success = account.try_setup(candidate_ips=candidate_ips)

        self.assertTrue(success)
        self.assertIn("SERIAL1", account._units)
        self.assertIn(
            "SERIAL2", account._units
        )  # Should be preserved in _units despite no address
        self.assertEqual(account.get_address("SERIAL1"), "192.168.1.10")
        self.assertEqual(account.get_address("SERIAL2"), "")

        # Verify kumo_dict has SERIAL2 preserved for future discovery
        raw_json = account.get_raw_json()
        zone_table = raw_json[2]["children"][0]["zoneTable"]
        self.assertIn("SERIAL2", zone_table)
        self.assertEqual(zone_table["SERIAL2"]["password"], "pw2")

    @patch("pykumo.py_kumo_cloud_account.KumoCloudV3")
    @patch("pykumo.py_kumo_cloud_account.probe_ip")
    def test_cloud_failure_cache_preservation(self, mock_probe_ip, mock_v3):
        """Test that cache is used when Cloud fetch fails."""
        mock_probe_ip.return_value = True
        mock_v3.return_value.get_all_device_credentials.side_effect = Exception(
            "Cloud Down"
        )

        account = KumoCloudAccount(
            self.username, self.password, kumo_dict=self.cached_dict
        )
        success = account.try_setup()

        self.assertTrue(success)
        self.assertIn("SERIAL1", account._units)
        self.assertEqual(account.get_address("SERIAL1"), "192.168.1.10")

    @patch("pykumo.py_kumo_cloud_account.KumoCloudV3")
    @patch("pykumo.py_kumo_cloud_account.probe_ip")
    def test_prefer_cache_skips_cloud(self, mock_probe_ip, mock_v3):
        """Test that prefer_cache=True skips the V3 cloud call when cache is present."""
        mock_probe_ip.return_value = True

        account = KumoCloudAccount(
            self.username, self.password, kumo_dict=self.cached_dict
        )
        success = account.try_setup(prefer_cache=True)

        self.assertTrue(success)
        mock_v3.assert_not_called()
        self.assertEqual(account.get_address("SERIAL1"), "192.168.1.10")

    def test_factory_standalone(self):
        """Test that Factory() correctly creates an account object."""
        # Test with credentials
        account = KumoCloudAccount.Factory(username="user", password="pass")
        self.assertEqual(account._username, "user")
        self.assertEqual(account._password, "pass")
        self.assertIsNone(account._kumo_dict)

        # Test with kumo_dict
        account = KumoCloudAccount.Factory(kumo_dict=self.cached_dict)
        self.assertEqual(account._kumo_dict, self.cached_dict)
        self.assertFalse(account._need_fetch)

    @patch("pykumo.py_kumo_cloud_account.KumoCloudV3")
    @patch("pykumo.py_kumo_cloud_account.probe_ip")
    @patch("pykumo.py_kumo_cloud_account.probe_candidate_ips")
    def test_false_positive_discovery_probed_but_ignored(
        self, mock_probe_candidate, mock_probe_ip, mock_v3
    ):
        """Test that an IP in candidate_ips that fails probe is not used."""
        # SERIAL1 unreachable at its cached IP 1.10
        mock_probe_ip.return_value = False
        # Discovery finds NO matches even though an IP was provided
        mock_probe_candidate.return_value = {}

        v3_instance = mock_v3.return_value
        v3_instance.get_all_device_credentials.return_value = {
            "SERIAL1": {
                "serial": "SERIAL1",
                "label": "U1",
                "password": "pw1",
                "cryptoSerial": "cs1",
                "mac": "mac1",
                "unitType": "ductless",
            }
        }

        # IP 1.50 is provided in candidates (e.g. matched OUI but is some other device)
        candidate_ips = {"mac-unknown": "192.168.1.50"}
        account = KumoCloudAccount(
            self.username, self.password, kumo_dict=self.cached_dict
        )
        success = account.try_setup(candidate_ips=candidate_ips)

        self.assertTrue(success)
        # SERIAL1 was not updated with the false positive IP (it kept its preserved 1.10)
        self.assertEqual(account.get_address("SERIAL1"), "192.168.1.10")

        # Verify probe_candidate_ips was called with the false positive IP
        mock_probe_candidate.assert_called()
        args, _ = mock_probe_candidate.call_args
        # args[1] is candidate_ips.values() list
        self.assertIn("192.168.1.50", args[1])


if __name__ == "__main__":
    unittest.main()
