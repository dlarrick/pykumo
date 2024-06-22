""" Class used to represent Kumo Station units
"""

import time
import logging
from .const import CACHE_INTERVAL_SECONDS
from .py_kumo_base import PyKumoBase

_LOGGER = logging.getLogger(__name__)


class PyKumoStation(PyKumoBase):
    """ Talk to and control one KumoStation
    """
    # pylint: disable=R0904, R0902

    def __init__(self, name, addr, cfg_json, timeouts=None, serial=None):
        """ Constructor
        """
        super().__init__(name, addr, cfg_json, timeouts, serial)

    def update_status(self):
        """ Retrieve and cache current status dictionary if enough time
            has passed
        """
        now = time.monotonic()
        if (now - self._last_status_update > CACHE_INTERVAL_SECONDS or
                'mode' not in self._status):
            query = '{"c":{"eqc":{"oat":{}}}}'.encode('utf-8')
            # This could be expanded to parse the entire payload
            response = self._request(query)
            raw_status = response
            try:
                self._status = {'outdoorTemp': raw_status['r']['eqc']['oat']}
                self._last_status_update = now
            except KeyError:
                _LOGGER.warning("Error retrieving status")
                return False

            query = '{"c":{"sensors":{}}}'.encode('utf-8')
            response = self._request(query)
            sensors = response
            try:
                self._sensors = []
                for sensor in sensors['r']['sensors'].values():
                    if isinstance(sensor, dict) and sensor['uuid']:
                        self._sensors.append(sensor)
            except KeyError:
                _LOGGER.warning("Error retrieving sensors")
                return False

            # Edit profile with settings from adapter
            query = '{"c":{"adapter":{"status":{}}}}'.encode('utf-8')
            response = self._request(query)
            try:
                status = response['r']['adapter']['status']
                try:
                    self._profile['wifiRSSI'] = (
                        status['localNetwork']['stationMode']['RSSI'])
                except KeyError:
                    self._profile['wifiRSSI'] = None
            except KeyError:
                _LOGGER.warning("Error retrieving adapter profile")
                return False
        return True

    def get_outdoor_temperature(self):
        """ Last retrieved operating mode from unit """
        try:
            val = self._status['outdoorTemp']
        except KeyError:
            val = None
        return val
