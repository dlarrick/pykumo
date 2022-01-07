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
from getpass import getpass
from .const import CACHE_INTERVAL_SECONDS
from .py_kumo_base import PyKumoBase

_LOGGER = logging.getLogger(__name__)

class PyKumo(PyKumoBase):
    """ Talk to and control one indoor unit.
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
            query = '{"c":{"indoorUnit":{"status":{}}}}'.encode('utf-8')
            response = self._request(query)
            raw_status = response
            try:
                self._status = raw_status['r']['indoorUnit']['status']
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

            query = '{"c":{"indoorUnit":{"profile":{}}}}'.encode('utf-8')
            response = self._request(query)
            try:
                self._profile = response['r']['indoorUnit']['profile']
            except KeyError:
                _LOGGER.warning("Error retrieving profile")
                return False

            # Edit profile with settings from adapter
            query = '{"c":{"adapter":{"status":{}}}}'.encode('utf-8')
            response = self._request(query)
            try:
                status = response['r']['adapter']['status']
                self._profile['hasModeAuto'] = not status.get(
                    'autoModePrevention', False)
                if not status.get('userHasModeDry', False):
                    self._profile['hasModeDry'] = False
                if not status.get('userHasModeHeat', False):
                    self._profile['hasModeHeat'] = False
                try:
                    self._profile['wifiRSSI'] = (
                        status['localNetwork']['stationMode']['RSSI'])
                except KeyError:
                    self._profile['wifiRSSI'] = None
                self._profile['runState'] = status.get('runState', "unknown")
            except KeyError:
                _LOGGER.warning("Error retrieving adapter profile")
                return False

            # Edit profile with data from MHK2 if present
            query = '{"c":{"mhk2":{"status":{}}}}'.encode('utf-8')
            response = self._request(query)
            try:
                self._mhk2 = response['r']['mhk2']
                if isinstance(self._mhk2, dict):
                    mhk2_humidity = self._mhk2['status']['indoorHumid']

                    if mhk2_humidity is not None:
                        # Add a sensor entry for the MHK2 unit.
                        mhk2_sensor_value = {
                            'battery': None,
                            'humidity': mhk2_humidity,
                            'rssi': None,
                            'temperature': None,
                            'txPower': None,
                            'uuid': None
                        }
                        self._sensors.append(mhk2_sensor_value)
            except (KeyError, TypeError) as e:
                # We don't bailout here since the MHK2 component is optional.
                _LOGGER.info(f"Error retrieving MHK2 status: {e}")
                pass
        return True


    def get_mode(self):
        """ Last retrieved operating mode from unit """
        try:
            val = self._status['mode']
        except KeyError:
            val = None
        return val

    def get_standby(self):
        """ Return if the unit is in standby """
        try:
            val = self._status['standby']
        except KeyError:
            val = None
        return val

    def get_heat_setpoint(self):
        """ Last retrieved heat setpoint from unit """
        try:
            val = self._status['spHeat']
        except KeyError:
            val = None
        return val

    def get_cool_setpoint(self):
        """ Last retrieved cooling setpoint from unit """
        try:
            val = self._status['spCool']
        except KeyError:
            val = None
        return val

    def get_current_temperature(self):
        """ Last retrieved current temperature from unit """
        try:
            val = self._status['roomTemp']
        except KeyError:
            val = None
        return val

    def get_fan_speeds(self):
        """ List of valid fan speeds for unit """
        try:
            speeds = self._profile['numberOfFanSpeeds']
        except KeyError:
            speeds = 5
        if speeds not in (5, 3):
            _LOGGER.info(
                "Unit reports a different number of fan speeds than "
                "supported, %d != [5|3]. Please report which ones work!",
                self._profile['numberOfFanSpeeds'])

        if speeds == 3:
            valid_speeds = ["quiet", "low", "powerful"]
        else:
            valid_speeds = ["superQuiet", "quiet", "low", "powerful", "superPowerful"]
        try:
            if self._profile['hasFanSpeedAuto']:
                valid_speeds.append('auto')
        except KeyError:
            pass
        return valid_speeds

    def get_vane_directions(self):
        """ List of valid vane directions for unit """
        if not self.has_vane_direction():
            _LOGGER.info("Unit does not support vane direction")
            return []

        valid_directions = ["horizontal", "midhorizontal", "midpoint",
                            "midvertical", "vertical", "auto"]
        try:
            if self._profile['hasVaneSwing']:
                valid_directions.append('swing')
        except KeyError:
            pass
        return valid_directions

    def get_fan_speed(self):
        """ Last retrieved fan speed mode from unit """
        try:
            val = self._status['fanSpeed']
        except KeyError:
            val = None
        return val

    def get_vane_direction(self):
        """ Last retrieved vane direction mode from unit """
        try:
            val = self._status['vaneDir']
        except KeyError:
            val = None
        return val

    def get_current_humidity(self):
        """ Last retrieved humidity from sensor or MHK2, if any """
        val = None
        try:
            for sensor in self._sensors:
                if sensor['humidity'] is not None:
                    return sensor['humidity']
        except KeyError:
            val = None
        return val

    def get_current_sensor_temperature(self):
        """ Last retrieved temperature from sensor, if any """
        val = None
        try:
            for sensor in self._sensors:
                if sensor['temperature'] is not None:
                    return sensor['temperature']
        except KeyError:
            val = None
        return val

    def get_sensor_battery(self):
        """ Last retrieved battery percentage from sensor, if any """
        val = None
        try:
            for sensor in self._sensors:
                if sensor['battery'] is not None:
                    return sensor['battery']
        except KeyError:
            val = None
        return val

    def get_runstate(self):
        """ Last retrieved runState, if any """
        """ True if unit has heat mode """
        val = None
        try:
            val = self._profile['runState']
        except KeyError:
            val = None
        return val

    def get_filter_dirty(self):
        """ Last retrieved filter status from unit """
        try:
            val = self._status['filterDirty']
        except KeyError:
            val = None
        return val

    def get_defrost(self):
        """ Last retrieved filter status from unit """
        try:
            val = self._status['defrost']
        except KeyError:
            val = None
        return val

    def has_dry_mode(self):
        """ True if unit has dry (dehumidify) mode """
        val = None
        try:
            val = self._profile['hasModeDry']
        except KeyError:
            val = False
        return val

    def has_heat_mode(self):
        """ True if unit has heat mode """
        val = None
        try:
            val = self._profile['hasModeHeat']
        except KeyError:
            val = False
        return val

    def has_vent_mode(self):
        """ True if unit has vent (fan) mode """
        val = None
        try:
            val = self._profile['hasModeVent']
        except KeyError:
            val = False
        return val

    def has_auto_mode(self):
        """ True if unit has auto (heat/cool) mode """
        val = None
        try:
            val = self._profile['hasModeAuto']
        except KeyError:
            val = False
        return val

    def has_vane_direction(self):
        """ True if unit supports changing its vane direction (aka swing) """
        val = None
        try:
            val = self._profile['hasVaneDir']
        except KeyError:
            val = False
        return val

    def set_mode(self, mode):
        """ Change operation mode. Valid modes: off, cool sometimes also heat,
        dry, vent, auto
        """
        modes = ["off", "cool"]
        if self.has_dry_mode():
            modes.append("dry")
        if self.has_heat_mode():
            modes.append("heat")
        if self.has_vent_mode():
            modes.append("vent")
        if self.has_auto_mode():
            modes.append("auto")
        if mode not in modes:
            _LOGGER.warning("Attempting to set invalid mode %s", mode)
            return {}

        command = ('{"c":{"indoorUnit":{"status":{"mode":"%s"}}}}' %
                   mode).encode('utf-8')
        response = self._request(command)
        self._status['mode'] = mode
        self._last_status_update = time.monotonic()
        return response

    def set_heat_setpoint(self, setpoint):
        """ Change setpoint for heat (in degrees C) """
        # TODO: honor min/max from profile
        setpoint = round(float(setpoint), 1)
        command = ('{"c": { "indoorUnit": { "status": { "spHeat": %f } } } }' %
                   setpoint).encode('utf-8')
        response = self._request(command)
        self._status['spHeat'] = setpoint
        self._last_status_update = time.monotonic()
        return response

    def set_cool_setpoint(self, setpoint):
        """ Change setpoint for cooling (in degrees C) """
        # TODO: honor min/max from profile
        setpoint = round(float(setpoint), 2)
        command = ('{"c": { "indoorUnit": { "status": { "spCool": %f } } } }' %
                   setpoint).encode('utf-8')
        response = self._request(command)
        self._status['spCool'] = setpoint
        self._last_status_update = time.monotonic()
        return response

    def set_fan_speed(self, speed):
        """ Change fan speed. Valid speeds: superQuiet, quiet, low, powerful,
            superPowerful, sometimes auto
        """
        valid_speeds = self.get_fan_speeds()
        if speed not in valid_speeds:
            _LOGGER.warning("Attempting to set invalid fan speed %s", speed)
            return {}
        command = ('{"c": { "indoorUnit": { "status": { "fanSpeed": "%s" } } } }'
                   % speed).encode('utf-8')
        response = self._request(command)
        self._status['fanSpeed'] = speed
        self._last_status_update = time.monotonic()
        return response

    def set_vane_direction(self, direction):
        """ Change vane direction. Valid directions: horizontal, midhorizontal,
            midpoint, midvertical, vertical, auto, and sometimes swing
        """
        valid_directions = self.get_vane_directions()
        if direction not in valid_directions:
            _LOGGER.warning("Attempting to set an invalid vane direction %s", direction)
            return {}
        command = ('{"c": { "indoorUnit": { "status": { "vaneDir": "%s" } } } }'
                   % direction).encode('utf-8')
        response = self._request(command)
        self._status['vaneDir'] = direction
        self._last_status_update = time.monotonic()
        return response

