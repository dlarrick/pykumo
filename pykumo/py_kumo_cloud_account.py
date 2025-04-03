""" Class used to represent the Kumo Cloud service
"""

import json
import logging
import re
import requests
from requests.exceptions import Timeout
from getpass import getpass
from .py_kumo import PyKumo
from .py_kumo_station import PyKumoStation
from .const import KUMO_CONNECT_TIMEOUT_SECONDS, KUMO_RESPONSE_TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)

KUMO_UNIT_TYPE_TO_CLASS = {
    "ductless": PyKumo,
    "mvz": PyKumo,
    "sez": PyKumo,
    "pead": PyKumo,
    "headless": PyKumoStation
}


class KumoCloudAccount:
    """ API to talk to KumoCloud servers
    """
    def __init__(self, username, password, kumo_dict=None):
        """ Constructor from URL
        """
        if kumo_dict:
            self._url = None
            self._kumo_dict = kumo_dict
            self._need_fetch = False
            self._username = None
            self._password = None
            self._units = {}
        else:
            self._url = "https://geo-c.kumocloud.com/login"
            self._kumo_dict = None
            self._need_fetch = True
            self._username = username
            self._password = password
            self._units = {}

    @staticmethod
    def Factory(username=None, password=None):
        """Factory that prompts for username/password if not given
        """
        if username is None:
            username = input('Kumo Cloud username: ')
        if password is None:
            password = getpass()

        return KumoCloudAccount(username, password)

    @staticmethod
    def _parse_unit(raw_unit):
        """ Parse needed fields from raw json and return dict representing
            a unit
        """
        fields = {'serial', 'label', 'address', 'password', 'cryptoSerial', 'mac', 'unitType'}
        # Not all fields are always present; e.g. 'address'
        common_fields = fields & raw_unit.keys()
        return {field: raw_unit[field] for field in common_fields}

    def _fetch_if_needed(self):
        """ Fetch configuration from server.
        """
        if self._url and self._need_fetch:
            headers = {'Accept': 'application/json, text/plain, */*',
                       'Accept-Encoding': 'gzip, deflate, br',
                       'Accept-Language': 'en-US,en',
                       'Content-Type': 'application/json'}

            body = {"username": self._username,
                    "password": self._password,
                    "appVersion": "2.2.0"}
            try:
                response = requests.post(self._url, headers=headers, data=json.dumps(body),
                                         timeout=(KUMO_CONNECT_TIMEOUT_SECONDS,
                                                  KUMO_RESPONSE_TIMEOUT_SECONDS))
            except Timeout as ex:
                response = None
                _LOGGER.warning("Timeout querying KumoCloud: %s", str(ex))
            if response:
                if response.ok:
                    self._kumo_dict = response.json()
                else:
                    _LOGGER.warning("Error response from KumoCloud: %s %s}",
                                    str(response.status_code), response.text)
            # Only try to fetch once
            self._need_fetch = False
            if not self._kumo_dict:
                _LOGGER.warning("No JSON returned from KumoCloud; check credentials?")
                return

        self._units = {}
        try:
            for child in self._kumo_dict[2]['children']:
                for raw_unit in child['zoneTable'].values():
                    unit = self._parse_unit(raw_unit)
                    serial = unit['serial']
                    self._units[serial] = unit
                if 'children' in child:
                    for grandchild in child['children']:
                        for raw_unit in grandchild['zoneTable'].values():
                            unit = self._parse_unit(raw_unit)
                            serial = unit['serial']
                            self._units[serial] = unit
        except KeyError:
            pass

    def try_setup(self):
        """Try to set up and return success/failure"""
        self._fetch_if_needed()

        return len(self._units.keys()) > 0

    def get_raw_json(self):
        """Return raw dict retrieved from KumoCloud"""
        return self._kumo_dict

    def get_all_units(self):
        """ Return list of all unit serial numbers
        """
        self._fetch_if_needed()

        return self._units.keys()

    def get_indoor_units(self):
        """ Return list of indoor unit serial numbers
        """

        return list(filter(
            lambda x: self.is_indoor_unit(x), self.get_all_units()))

    def get_kumo_stations(self):
        """ Return list of kumo station serial numbers
        """

        return list(filter(
            lambda x: self.is_headless_unit(x), self.get_all_units()))

    def get_name(self, unit):
        """ Return name of given unit
        """
        self._fetch_if_needed()

        try:
            return self._units[unit]['label']

        except KeyError:
            pass

        return None

    def is_indoor_unit(self, unit):
        """ Return whether unit is an indoor unit
        """
        self._fetch_if_needed()

        unit_type = self.get_unit_type(unit)
        if unit_type == "headless":
            return False
        return True

    def is_headless_unit(self, unit):
        """ Return whether unit is a headless unit (KumoStation)
        """
        self._fetch_if_needed()

        unit_type = self.get_unit_type(unit)
        if unit_type == "headless":
            return True
        return False

    def get_unit_type(self, unit):
        """ Return unit type of given unit
        """
        self._fetch_if_needed()

        try:
            return self._units[unit]['unitType']

        except KeyError:
            pass

        return None

    def get_address(self, unit):
        """ Return IP address of named unit
        """
        self._fetch_if_needed()

        try:
            return self._units[unit]['address']

        except KeyError:
            pass

        return None

    def get_mac(self, unit):
        """ Return mac address of named unit
        """
        self._fetch_if_needed()

        try:
            return self._units[unit]['mac']

        except KeyError:
            pass

        return None

    def get_credentials(self, unit):
        """ Return dict of credentials required to talk to unit
        """
        self._fetch_if_needed()

        try:
            credentials = {'password': self._units[unit]['password'],
                           'crypto_serial': self._units[unit]['cryptoSerial']}
            return credentials

        except KeyError:
            pass

        return None

    def make_pykumos(self, timeouts=None, init_update_status=True, use_schedule=False):
        """ Return a dict mapping names of all indoor units to newly-created
        `PyKumoBase` objects
        """
        kumos = {}
        for unitSerial in list(self.get_all_units()):
            name = self.get_name(unitSerial)
            if name in kumos:
                # I'm not sure if it's possible to have the same name repeated,
                # but just in case...
                m = re.match(r'(.*) \(([0-9]*)\)', name)
                if m:
                    name = m.group(1) + ' ({})'.format(int(m.group(2)) + 1)
                else:
                    kumos[name + ' (1)'] = kumos.pop(name)
                    name = name + ' (2)'
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
