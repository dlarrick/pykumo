"""Class used to represent the Kumo Cloud service"""

import logging
import re
from getpass import getpass
from .py_kumo import PyKumo
from .py_kumo_station import PyKumoStation
from .py_kumo_cloud_account_v3 import KumoCloudV3
from .py_kumo_discovery import probe_candidate_ips, probe_ip

_LOGGER = logging.getLogger(__name__)

KUMO_UNIT_TYPE_TO_CLASS = {
    "ductless": PyKumo,
    "mvz": PyKumo,
    "sez": PyKumo,
    "pead": PyKumo,
    "headless": PyKumoStation,
}


class KumoCloudAccount:
    """API to talk to KumoCloud servers"""

    def __init__(self, username, password, kumo_dict=None):
        """Constructor from URL"""
        self._username = username
        self._password = password
        self._units = {}
        self._kumo_dict = kumo_dict
        self._need_fetch = kumo_dict is None

    @staticmethod
    def Factory(username=None, password=None, kumo_dict=None):
        """Factory that prompts for username/password if not given"""
        if kumo_dict is None:
            if username is None:
                username = input("Kumo Cloud username: ")
            if password is None:
                password = getpass()

        return KumoCloudAccount(username, password, kumo_dict)

    @staticmethod
    def _parse_unit(raw_unit):
        """Parse needed fields from raw json and return dict representing
        a unit
        """
        fields = {
            "serial",
            "label",
            "address",
            "password",
            "cryptoSerial",
            "mac",
            "unitType",
        }
        # Not all fields are always present; e.g. 'address'
        common_fields = fields & raw_unit.keys()
        return {field: raw_unit[field] for field in common_fields}

    def _fetch_if_needed(self):
        """Fetch configuration from server if not already done."""
        if self._need_fetch:
            self.try_setup()

    def try_setup(self, candidate_ips=None, prefer_cache=False):
        """Set up the account, prioritizing V3 API and preserving cache.

        Args:
            candidate_ips: Optional dict of {mac_address: ip_address} from
                          DHCP discovery. Used to discover device IPs.
            prefer_cache: If True, skip Cloud fetch if a valid cache is present.
        """
        # 1. Extract Cache
        cached_units = self._extract_cached_units()

        # 2. Cloud Fetch
        v3_devices = {}
        v3_error = None
        if not (prefer_cache and cached_units) and self._username and self._password:
            try:
                v3 = KumoCloudV3(self._username, self._password)
                v3_devices = v3.get_all_device_credentials()
            except Exception as ex:
                v3_error = str(ex)
                _LOGGER.warning("V3 cloud fetch failed: %s", v3_error)

        # 3. Handle Cloud Failure / Merge
        # If Cloud failed entirely or returned nothing, fall back to Cache
        if not v3_devices and cached_units:
            _LOGGER.info("Using %d cached units as primary source", len(cached_units))
            source_devices = cached_units
        else:
            source_devices = v3_devices
            # Supplement Cloud with Cache if Cloud is incomplete
            for serial, cached_dev in cached_units.items():
                if serial not in source_devices:
                    _LOGGER.info(
                        "Preserving unit %s from cache (missing in Cloud)", serial
                    )
                    source_devices[serial] = cached_dev
                else:
                    cloud_dev = source_devices[serial]
                    for field in ("password", "cryptoSerial", "address", "mac"):
                        if not cloud_dev.get(field) and cached_dev.get(field):
                            cloud_dev[field] = cached_dev[field]

        if not source_devices:
            _LOGGER.warning("No devices found in Cloud or Cache")
            return False

        # 4. Address Resolution & Validation
        final_units = {}
        for serial, dev in source_devices.items():
            unit = {
                "serial": serial,
                "label": dev.get("label", ""),
                "password": dev.get("password", ""),
                "cryptoSerial": dev.get("cryptoSerial", ""),
                "mac": dev.get("mac", ""),
                "unitType": dev.get("unitType", "ductless"),
                "address": dev.get("address", ""),
            }

            # Check reachability if we have an address
            reachable = False
            if unit["address"]:
                creds = {
                    "password": unit["password"],
                    "cryptoSerial": unit["cryptoSerial"],
                }
                if probe_ip(unit["address"], creds, timeout=2.0):
                    reachable = True
                else:
                    _LOGGER.info(
                        "Unit %s unreachable at %s; will attempt discovery",
                        serial,
                        unit["address"],
                    )

            unit["reachable"] = reachable
            final_units[serial] = unit

        # 5. Discovery for unaddressed/unreachable units
        to_discover = {s: u for s, u in final_units.items() if not u["reachable"]}
        if to_discover and candidate_ips:
            _LOGGER.info(
                "Probing %d candidate IPs for %d missing/unreachable units",
                len(candidate_ips),
                len(to_discover),
            )
            ips_to_probe = list(candidate_ips.values())
            matched = probe_candidate_ips(to_discover, ips_to_probe)
            for serial, ip in matched.items():
                final_units[serial]["address"] = ip
                final_units[serial]["reachable"] = True
                _LOGGER.info("Discovered %s -> %s", serial, ip)

        # 6. Finalize & Preservation
        # Build _units map (only those with credentials)
        self._units = {}
        for serial, unit_data in final_units.items():
            if unit_data.get("password") and unit_data.get("cryptoSerial"):
                # We keep the unit in _units even if address is missing,
                # though PyKumo will fail to connect. This preserves HA entities.
                self._units[serial] = self._parse_unit(unit_data)

        # Update kumo_dict for future caching
        # We wrap it in the expected v2-like structure for HA compatibility
        zone_table = {s: u for s, u in final_units.items()}
        self._kumo_dict = [
            {},  # account info placeholder
            {},  # preferences placeholder
            {"children": [{"zoneTable": zone_table}]},
        ]
        self._need_fetch = False
        _LOGGER.info("Setup complete: %d units configured", len(self._units))
        return len(self._units) > 0

    def try_setup_v3_only(self, candidate_ips=None):
        """Deprecated: Use try_setup instead."""
        return self.try_setup(candidate_ips)

    def _extract_cached_units(self) -> dict:
        """Extract fully-formed units from current kumo_dict."""
        units = {}
        if not self._kumo_dict:
            return units
        fields = [
            "serial",
            "label",
            "password",
            "address",
            "cryptoSerial",
            "mac",
            "unitType",
        ]
        try:
            for child in self._kumo_dict[2]["children"]:
                for raw_unit in child["zoneTable"].values():
                    saved_unit = {}
                    for field in fields:
                        if raw_unit.get(field):
                            saved_unit[field] = raw_unit[field]
                    if all(field in saved_unit for field in fields):
                        serial = saved_unit["serial"]
                        units[serial] = saved_unit
                for grandchild in child.get("children", []):
                    for raw_unit in grandchild["zoneTable"].values():
                        saved_unit = {}
                        for field in fields:
                            if raw_unit.get(field):
                                saved_unit[field] = raw_unit[field]
                        if all(field in saved_unit for field in fields):
                            serial = saved_unit["serial"]
                            units[serial] = saved_unit
        except (KeyError, IndexError, TypeError):
            pass
        if units:
            _LOGGER.info("Preserved %d fully cached units", len(units))
        return units

    def get_raw_json(self):
        """Return raw dict retrieved from KumoCloud"""
        return self._kumo_dict

    def get_all_units(self):
        """Return list of all unit serial numbers"""
        self._fetch_if_needed()

        return self._units.keys()

    def get_indoor_units(self):
        """Return list of indoor unit serial numbers"""

        return list(filter(lambda x: self.is_indoor_unit(x), self.get_all_units()))

    def get_kumo_stations(self):
        """Return list of kumo station serial numbers"""

        return list(filter(lambda x: self.is_headless_unit(x), self.get_all_units()))

    def get_name(self, unit):
        """Return name of given unit"""
        self._fetch_if_needed()

        try:
            return self._units[unit]["label"]

        except KeyError:
            pass

        return None

    def is_indoor_unit(self, unit):
        """Return whether unit is an indoor unit"""
        self._fetch_if_needed()

        unit_type = self.get_unit_type(unit)
        if unit_type == "headless":
            return False
        return True

    def is_headless_unit(self, unit):
        """Return whether unit is a headless unit (KumoStation)"""
        self._fetch_if_needed()

        unit_type = self.get_unit_type(unit)
        if unit_type == "headless":
            return True
        return False

    def get_unit_type(self, unit):
        """Return unit type of given unit"""
        self._fetch_if_needed()

        try:
            return self._units[unit]["unitType"]

        except KeyError:
            pass

        return None

    def get_address(self, unit):
        """Return IP address of named unit"""
        self._fetch_if_needed()

        try:
            return self._units[unit]["address"]

        except KeyError:
            pass

        return None

    def get_mac(self, unit):
        """Return mac address of named unit"""
        self._fetch_if_needed()

        try:
            return self._units[unit]["mac"]

        except KeyError:
            pass

        return None

    def get_credentials(self, unit):
        """Return dict of credentials required to talk to unit"""
        self._fetch_if_needed()

        try:
            credentials = {
                "password": self._units[unit]["password"],
                "crypto_serial": self._units[unit]["cryptoSerial"],
            }
            return credentials

        except KeyError:
            pass

        return None

    def make_pykumos(self, timeouts=None, init_update_status=True, use_schedule=False):
        """Return a dict mapping names of all indoor units to newly-created
        `PyKumoBase` objects
        """
        kumos = {}
        for unitSerial in list(self.get_all_units()):
            name = self.get_name(unitSerial)
            if name in kumos:
                # I'm not sure if it's possible to have the same name repeated,
                # but just in case...
                m = re.match(r"(.*) \(([0-9]*)\)", name)
                if m:
                    name = m.group(1) + " ({})".format(int(m.group(2)) + 1)
                else:
                    kumos[name + " (1)"] = kumos.pop(name)
                    name = name + " (2)"
                # results in a name like "A/C unit (2)"
            unitType = self.get_unit_type(unitSerial)

            kumo_class = KUMO_UNIT_TYPE_TO_CLASS.get(unitType, PyKumo)
            kumos[name] = kumo_class(
                name=name,
                addr=self.get_address(unitSerial),
                cfg_json=self.get_credentials(unitSerial),
                timeouts=timeouts,
                serial=unitSerial,
                use_schedule=use_schedule,
            )

        if init_update_status:
            for pk in kumos.values():
                pk.update_status()

        return kumos
