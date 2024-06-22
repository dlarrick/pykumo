""" Class used to represent indoor units
"""

import hashlib
import base64
import time
import logging
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from requests.exceptions import Timeout
from .const import (CACHE_INTERVAL_SECONDS, W_PARAM, S_PARAM, UNIT_CONNECT_TIMEOUT_SECONDS,
                    UNIT_RESPONSE_TIMEOUT_SECONDS)

_LOGGER = logging.getLogger(__name__)


class PyKumoBase:
    """ Talk to and control one indoor unit.
    """
    # pylint: disable=R0904, R0902

    def __init__(self, name, addr, cfg_json, timeouts=None, serial=None):
        """ Constructor
        """
        self._name = name
        self._address = addr
        self._serial = serial
        self._security = {
            'password': base64.b64decode(cfg_json["password"]),
            'crypto_serial': bytearray.fromhex(cfg_json["crypto_serial"])}
        if not timeouts:
            _LOGGER.info("Use default timeouts")
            self._timeouts = (UNIT_CONNECT_TIMEOUT_SECONDS,
                              UNIT_RESPONSE_TIMEOUT_SECONDS)
        else:
            _LOGGER.info("Use timeouts=%s", str(timeouts))
            connect_timeout = timeouts[0] if timeouts[0] else UNIT_CONNECT_TIMEOUT_SECONDS
            response_timeout = timeouts[1] if timeouts[1] else UNIT_RESPONSE_TIMEOUT_SECONDS
            self._timeouts = (connect_timeout, response_timeout)
        self._status = {}
        self._profile = {}
        self._sensors = []
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS

    def _token(self, post_data):
        """ Compute URL including security token for a given command
        """
        data_hash = hashlib.sha256(self._security['password'] +
                                   post_data).digest()

        intermediate = bytearray(88)
        intermediate[0:32] = W_PARAM[0:32]
        intermediate[32:64] = data_hash[0:32]
        intermediate[64:66] = bytearray.fromhex("0840")
        intermediate[66] = S_PARAM
        intermediate[79] = self._security['crypto_serial'][8]
        intermediate[80:84] = self._security['crypto_serial'][4:8]
        intermediate[84:88] = self._security['crypto_serial'][0:4]

        token = hashlib.sha256(intermediate).hexdigest()

        return token

    def _request(self, post_data):
        """ Send request to configured unit and return response dict
        """
        if not self._address:
            _LOGGER.warning("Unit %s address not set", self._name)
            return {}
        url = "http://" + self._address + "/api"
        token = self._token(post_data)
        headers = {'Accept': 'application/json, text/plain, */*',
                   'Content-Type': 'application/json'}
        token_param = {'m': token}
        try:
            with requests.Session() as session:
                retries = Retry(total=3, backoff_factor=0.1)
                session.mount('http://', HTTPAdapter(max_retries=retries))
                _LOGGER.debug("Issue request %s %s", url, post_data)
                response = session.put(
                    url, headers=headers, data=post_data, params=token_param,
                    timeout=self._timeouts)
                return response.json()
        except Timeout as ex:
            _LOGGER.warning("Timeout issuing request %s: %s", url, str(ex))
        except Exception as ex:
            _LOGGER.warning("Error issuing request %s: %s", url, str(ex))
        return {}

    def get_status(self):
        """ Last retrieved status dictionary from unit """
        return self._status

    def update_status(self):
        """ Retrieve and cache current status dictionary if enough time
            has passed
        """
        raise NotImplementedError()

    def get_name(self):
        """ Unit's name """
        return self._name

    def get_serial(self):
        """ Unit's serial number """
        return self._serial

    def get_sensor_rssi(self):
        """ Last retrievd sensor signal strength, if any """
        val = None
        try:
            for sensor in self._sensors:
                if sensor['rssi'] is not None:
                    return sensor['rssi']
        except KeyError:
            val = None
        return val

    def get_wifi_rssi(self):
        """ Last retrieved WiFi signal strengh, if any """
        val = None
        try:
            val = self._profile['wifiRSSI']
        except KeyError:
            val = None
        return val
