""" Module to interact with Mitsubishi KumoCloud devices via their local API.
"""

import hashlib
import base64
import time
import json
import requests

CACHE_INTERVAL_SECONDS = 5

class PyKymo:
    """ Object representing one indoor unit
    """
    def __init__(self, addr, name, cfg_json):
        """ Constructor
        """
        self._address = addr
        self._name = name
        self._security = {
            'w_param': bytearray.fromhex(cfg_json["W"]),
            's_param': cfg_json["S"],
            'password': base64.b64decode(cfg_json["password"]),
            'crypto_serial': bytearray.fromhex(cfg_json["cryptoSerial"])}
        self._status = {}
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS
        self._update_status()

    def _url(self, post_data):
        """ Compute URL including security token for a given command
        """
        data_hash = hashlib.sha256(self._security['password'] +
                                   post_data).digest()

        intermediate = bytearray(88)
        intermediate[0:32] = self._security['w_param'][0:32]
        intermediate[32:64] = data_hash[0:32]
        intermediate[64:66] = bytearray.fromhex("0840")
        intermediate[66] = self._security['s_param']
        intermediate[79] = self._security['crypto_serial'][8]
        intermediate[80:84] = self._security['crypto_serial'][4:8]
        intermediate[84:88] = self._security['crypto_serial'][0:4]

        token = hashlib.sha256(intermediate).hexdigest()

        url = "http://" + self._address + "/api?m=" + token

        return url

    def _request(self, post_data):
        """ Send request to configured unit and return response
        """
        url = self._url(post_data)
        headers = {'Accept': 'application/json, text/plain, */*',
                   'Content-Type': 'application/json'}
        try:
            response = requests.put(url, headers=headers, data=post_data)
            return response
        except Exception as ex:
            print("Error issuing request {url}: {ex}".format(url=url,
                                                             ex=str(ex)))
        return ""

    def _update_status(self):
        """ Retrieve and cache current status dictionary if enough time
            has passed
        """
        now = time.monotonic()
        if (now - self._last_status_update > CACHE_INTERVAL_SECONDS or
                'mode' not in self._status):
            query = '{"c":{"indoorUnit":{"status":{}}}}'.encode('utf-8')
            response = self._request(query)
            raw_status = json.loads(response)
            try:
                self._status = raw_status['r']['indoorUnit']['status']
                self._last_status_update = now
            except KeyError:
                print("Error retrieving status")

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
            val = "error"
        return val

    def get_heat_setpoint(self):
        """ Last retrieved heat setpoint from unit """
        self._update_status()
        try:
            val = self._status['spHeat']
        except KeyError:
            val = 0
        return val

    def get_cool_setpoint(self):
        """ Last retrieved cooling setpoint from unit """
        self._update_status()
        try:
            val = self._status['spCool']
        except KeyError:
            val = 0
        return val

    def get_current_temperature(self):
        """ Last retrieved current temperature from unit """
        self._update_status()
        try:
            val = self._status['roomTemp']
        except KeyError:
            val = 0
        return val

    def get_fan_speed(self):
        """ Last retrieved fan speed mode from unit """
        self._update_status()
        try:
            val = self._status['fanSpeed']
        except KeyError:
            val = "error"
        return val

    def get_vane_direction(self):
        """ Last retrieved vane direction mode from unit """
        self._update_status()
        try:
            val = self._status['vaneDir']
        except KeyError:
            val = "error"
        return val

    def set_mode(self, mode):
        """ Change operation mode. Valid modes: off, heat, cool, dry, vent, auto
        """
        if mode not in ["off", "heat", "cool", "dry", "vent", "auto"]:
            print("Attempting to set invalid mode %s" % mode)
            return ""
        command = ('"c": { "indoorUnit": { "status": { "mode": "%s" } } } }' %
                   mode).encode('utf-8')
        response = self._request(command)
        self._status['mode'] = mode
        return response

    def set_heat_setpoint(self, setpoint):
        """ Change setpoint for heat (in degrees C) """
        command = ('"c": { "indoorUnit": { "status": { "spHeat": %f } } } }' %
                   setpoint).encode('utf-8')
        response = self._request(command)
        self._status['spHeat'] = setpoint
        return response

    def set_cool_setpoint(self, setpoint):
        """ Change setpoint for cooling (in degrees C) """
        command = ('"c": { "indoorUnit": { "status": { "spCool": %f } } } }' %
                   setpoint).encode('utf-8')
        response = self._request(command)
        self._status['spCool'] = setpoint
        return response

    def set_fan_speed(self, speed):
        """ Change fan speed. Valid speeds: quiet, low, powerful,
            superPowerful, auto
        """
        if speed not in ["quiet", "low", "powerful", "superPowerful", "auto"]:
            print("Attempting to set invalid fan speed %s" % speed)
            return ""
        command = ('"c": { "indoorUnit": { "status": { "fanSpeed": "%s" } } } }'
                   % speed).encode('utf-8')
        response = self._request(command)
        self._status['fanSpeed'] = speed
        return response

    def set_vane_direction(self, direction):
        """ Change vane direction. Valid directions: horizontal, midhorizontal,
            midpoint, midvertical, swing, auto
        """
        if direction not in ["horizontal", "midhorizontal", "midpoint",
                             "midvertical", "swing", "auto"]:
            print("Attempting to set an invalid vane direction %s" % direction)
            return ""
        command = ('"c": { "indoorUnit": { "status": { "vaneDir": "%s" } } } }'
                   % direction).encode('utf-8')
        response = self._request(command)
        self._status['vaneDir'] = direction
        return response
