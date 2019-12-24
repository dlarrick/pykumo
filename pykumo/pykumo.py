""" Module to interact with Mitsubishi KumoCloud devices via their local API.
"""

import hashlib
import base64
import time
import requests

CACHE_INTERVAL_SECONDS = 5
W_PARAM = bytearray.fromhex('44c73283b498d432ff25f5c8e06a016aef931e68f0a00ea710e36e6338fb22db')
S_PARAM = 0

class PyKumo:
    """ Talk to and control one indoor unit.
    """
    # pylint: disable=R0904

    def __init__(self, name, addr, cfg_json):
        """ Constructor
        """
        self._address = addr
        self._name = name
        self._security = {
            'password': base64.b64decode(cfg_json["password"]),
            'crypto_serial': bytearray.fromhex(cfg_json["crypto_serial"])}
        self._status = {}
        self._profile = {}
        self._sensors = []
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS
        self._update_status()

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
        url = "http://" + self._address + "/api"
        token = self._token(post_data)
        headers = {'Accept': 'application/json, text/plain, */*',
                   'Content-Type': 'application/json'}
        token_param = {'m': token}
        try:
            response = requests.put(url, headers=headers, data=post_data, params=token_param)
            return response.json()
        except Exception as ex:
            print("Error issuing request {url}: {ex}".format(url=url,
                                                             ex=str(ex)))
        return {}

    def _update_status(self):
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
                print("Error retrieving status")

            query = '{"c":{"sensors":{}}}'.encode('utf-8')
            response = self._request(query)
            sensors = response
            try:
                self._sensors = []
                for sensor in sensors['r']['sensors'].values():
                    if isinstance(sensor, dict) and sensor['uuid']:
                        self._sensors.append(sensor)
            except KeyError:
                print("Error retrieving sensors")

            query = '{"c":{"indoorUnit":{"profile":{}}}}'.encode('utf-8')
            response = self._request(query)
            try:
                self._profile = response['r']['indoorUnit']['profile']
            except KeyError:
                print("Error retrieving profile")

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
            except KeyError:
                print("Error retrieving adapter profile")

    def get_name(self):
        """ Unit's name """
        return self._name

    def get_status(self):
        """ Last retrieved status dictionary from unit """
        return self._status

    def get_mode(self):
        """ Last retrieved operating mode from unit """
        self._update_status()
        try:
            val = self._status['mode']
        except KeyError:
            val = None
        return val

    def get_standby(self):
        """ Return if the unit is in standby """
        self._update_status()
        try:
            val = self._status['standby']
        except KeyError:
            val = None
        return val

    def get_heat_setpoint(self):
        """ Last retrieved heat setpoint from unit """
        self._update_status()
        try:
            val = self._status['spHeat']
        except KeyError:
            val = None
        return val

    def get_cool_setpoint(self):
        """ Last retrieved cooling setpoint from unit """
        self._update_status()
        try:
            val = self._status['spCool']
        except KeyError:
            val = None
        return val

    def get_current_temperature(self):
        """ Last retrieved current temperature from unit """
        self._update_status()
        try:
            val = self._status['roomTemp']
        except KeyError:
            val = None
        return val

    def get_fan_speeds(self):
        """ List of valid fan speeds for unit """
        try:
            if self._profile['numberOfFanSpeeds'] != 5:
                print(
                    "Unit reports a different number of fan speeds than "
                    "supported, {num} != 5. Please report which ones work!"
                    .format(num=self._profile['numberOfFanSpeeds']))
        except KeyError:
            pass

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
            print("Unit does not support vane direction")
            return []

        valid_directions = ["horizontal", "midhorizontal", "midpoint",
                            "midvertical", "auto"]
        try:
            if self._profile['hasVaneSwing']:
                valid_directions.append('swing')
        except KeyError:
            pass
        return valid_directions

    def get_fan_speed(self):
        """ Last retrieved fan speed mode from unit """
        self._update_status()
        try:
            val = self._status['fanSpeed']
        except KeyError:
            val = None
        return val

    def get_vane_direction(self):
        """ Last retrieved vane direction mode from unit """
        self._update_status()
        try:
            val = self._status['vaneDir']
        except KeyError:
            val = None
        return val

    def get_current_humidity(self):
        """ Last retrieved humidity from sensor, if any """
        self._update_status()
        val = None
        try:
            for sensor in self._sensors:
                if sensor['humidity'] is not None:
                    return sensor['humidity']
        except KeyError:
            val = None
        return val

    def get_sensor_battery(self):
        """ Last retrieved battery percentage from sensor, if any """
        self._update_status()
        val = None
        try:
            for sensor in self._sensors:
                if sensor['battery'] is not None:
                    return sensor['battery']
        except KeyError:
            val = None
        return val

    def has_dry_mode(self):
        """ True if unit has dry (dehumidify) mode """
        self._update_status()
        val = None
        try:
            val = self._profile['hasModeDry']
        except KeyError:
            val = False
        return val

    def has_heat_mode(self):
        """ True if unit has heat mode """
        self._update_status()
        val = None
        try:
            val = self._profile['hasModeHeat']
        except KeyError:
            val = False
        return val

    def has_vent_mode(self):
        """ True if unit has vent (fan) mode """
        self._update_status()
        val = None
        try:
            val = self._profile['hasModeVent']
        except KeyError:
            val = False
        return val

    def has_auto_mode(self):
        """ True if unit has auto (heat/cool) mode """
        self._update_status()
        val = None
        try:
            val = self._profile['hasModeAuto']
        except KeyError:
            val = False
        return val

    def has_vane_direction(self):
        """ True if unit supports changing its vane direction (aka swing) """
        self._update_status()
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
            print("Attempting to set invalid mode %s" % mode)
            return {}

        command = ('{"c":{"indoorUnit":{"status":{"mode":"%s"}}}}' %
                   mode).encode('utf-8')
        response = self._request(command)
        self._status['mode'] = mode
        return response

    def set_heat_setpoint(self, setpoint):
        """ Change setpoint for heat (in degrees C) """
        # TODO: honor min/max from profile
        setpoint = round(float(setpoint), 1)
        command = ('{"c": { "indoorUnit": { "status": { "spHeat": %f } } } }' %
                   setpoint).encode('utf-8')
        response = self._request(command)
        self._status['spHeat'] = setpoint
        return response

    def set_cool_setpoint(self, setpoint):
        """ Change setpoint for cooling (in degrees C) """
        # TODO: honor min/max from profile
        setpoint = round(float(setpoint), 2)
        command = ('{"c": { "indoorUnit": { "status": { "spCool": %f } } } }' %
                   setpoint).encode('utf-8')
        response = self._request(command)
        self._status['spCool'] = setpoint
        return response

    def set_fan_speed(self, speed):
        """ Change fan speed. Valid speeds: superQuiet, quiet, low, powerful,
            superPowerful, sometimes auto
        """
        valid_speeds = self.get_fan_speeds()
        if speed not in valid_speeds:
            print("Attempting to set invalid fan speed %s" % speed)
            return {}
        command = ('{"c": { "indoorUnit": { "status": { "fanSpeed": "%s" } } } }'
                   % speed).encode('utf-8')
        response = self._request(command)
        self._status['fanSpeed'] = speed
        return response

    def set_vane_direction(self, direction):
        """ Change vane direction. Valid directions: horizontal, midhorizontal,
            midpoint, midvertical, auto, and sometimes swing
        """
        valid_directions = get_vane_directions()
        if direction not in valid_directions:
            print("Attempting to set an invalid vane direction %s" % direction)
            return {}
        command = ('{"c": { "indoorUnit": { "status": { "vaneDir": "%s" } } } }'
                   % direction).encode('utf-8')
        response = self._request(command)
        self._status['vaneDir'] = direction
        return response

class KumoCloudAccount:
    """ API to talk to KumoCloud servers
    """
    def __init__(self, username, password, kumo_dict=None):
        """ Constructor from URL
        """
        if kumo_dict:
            self._url = None
            self._kumo_dict = kumo_dict
            self._username = None
            self._password = None
            self._last_status_update = time.monotonic()
            self._units = {}
        else:
            self._url = "https://geo-c.kumocloud.com/login"
            self._kumo_dict = None
            self._last_status_update = (time.monotonic() -
                                        2 * CACHE_INTERVAL_SECONDS)
            self._username = username
            self._password = password
            self._units = {}

    @staticmethod
    def _parse_unit(raw_unit):
        """ Parse needed fields from raw json and return dict representing
            a unit
        """
        unit = {}
        fields = {'serial', 'label', 'address', 'password', 'cryptoSerial'}
        try:
            for field in fields:
                unit[field] = raw_unit[field]
        except KeyError:
            pass
        return unit

    def _fetch_if_needed(self):
        """ Fetch configuration from server.
        """
        now = time.monotonic()
        if (now - self._last_status_update > CACHE_INTERVAL_SECONDS or
                not self._kumo_dict):
            headers = {'Accept': 'application/json, text/plain, */*',
                       'Accept-Encoding': 'gzip, deflate, br',
                       'Accept-Language': 'en-US,en',
                       'Content-Type': 'application/json'}
            body = ('{"username":"%s","password":"%s","appVersion":"2.2.0"}' %
                    (self._username, self._password))
            response = requests.post(self._url, headers=headers, data=body)
            if response.ok:
                self._kumo_dict = response.json()
                self._last_status_update = now
            else:
                print("Error response from KumoCloud: {code} {msg}".format(
                    code=response.status_code, msg=response.text))
            if not self._kumo_dict:
                print("No JSON returned from KumoCloud; check credentials?")
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

    def get_indoor_units(self):
        """ Return list of indoor unit serial numbers
        """
        self._fetch_if_needed()

        return self._units.keys()

    def get_name(self, unit):
        """ Return name of given unit
        """
        self._fetch_if_needed()

        try:
            return self._units[unit]['label']

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
