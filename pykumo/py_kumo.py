"""Class used to represent indoor units"""

import datetime
import logging
import time
from collections.abc import MutableMapping

from .schedule import UnitSchedule

from .const import CACHE_INTERVAL_SECONDS, POSSIBLE_SENSORS, SETTABLE_TEMP_SOURCES
from .cn105 import (
    DEFAULT_INFO_CODES,
    TELEMETRY_KEYS,
    CompressorActivityEstimator,
    decode_info_reply,
)
from .cn105_bus import REPLY_TIMEOUT_SECONDS, Cn105Bus
from .py_kumo_base import PyKumoBase

_LOGGER = logging.getLogger(__name__)
ALL_FAN_SPEEDS = ["superQuiet", "quiet", "low", "Low", "powerful", "superPowerful"]


def merge(d, v):
    """
    Merge two dictionaries.

    Merge dict-like `v` into dict-like `d`. In case keys between them are the same, merge
    their sub-dictionaries where possible. Otherwise, values in `v` overwrite `d`.
    """
    for key in v:
        if (
            key in d
            and isinstance(d[key], MutableMapping)
            and isinstance(v[key], MutableMapping)
        ):
            d[key] = merge(d[key], v[key])
        else:
            d[key] = v[key]
    return d


class PyKumo(PyKumoBase):
    """Talk to and control one indoor unit."""

    # pylint: disable=R0904, R0902

    def __init__(
        self,
        name,
        addr,
        cfg_json,
        timeouts=None,
        serial=None,
        use_schedule: bool = False,
    ):
        """Constructor"""
        self._last_reboot = None
        self._unit_schedule = UnitSchedule(self) if use_schedule else None
        self._cn105 = Cn105Bus(self)
        # What the last successful update_cn105_telemetry() read.
        self._cn105_telemetry = {}
        self._cn105_telemetry_at = None
        self._compressor_activity = CompressorActivityEstimator()
        super().__init__(name, addr, cfg_json, timeouts, serial)

    def _rebootable_response(self, response):
        """
        Check whether response warrants immediate reboot of adapter
        """
        return response.get(
            "_api_error", ""
        ) == "serializer_error" or "__no_memory" in str(response)

    def _retryable_response(self, response):
        """
        Check whether response is retryable
        """
        return (
            response.get("_api_error", "") == "serializer_error"
            or response.get("_api_error", "") == "device_authentication_error"
            or "__no_memory" in str(response)
        )

    def _retrieve_attributes(
        self,
        query_path: list[str],
        needed: list[str],
        do_top_query: bool = True,
        retries=3,
    ) -> dict:
        """Try to retrieve a base query, but in specific error conditions retrieve specific
        needed attributes individually.
        """
        base_query = '{"c":{'
        for item in query_path:
            base_query += '"' + item + '":{'
        base_query += "}" * (len(query_path) + 2)
        query = base_query.encode("utf-8")
        try:
            should_reboot = False
            response = None
            if do_top_query:
                tries = 0
                while tries < retries:
                    response = self._request(query)
                    if self._rebootable_response(response):
                        should_reboot = True
                        break
                    if self._retryable_response(response):
                        _LOGGER.info(f"Retry {tries} main query due to {response}")
                        time.sleep(1.0)
                        tries += 1
                    else:
                        break
            built_response = {"r": {}}
            if not should_reboot and (
                not response or self._retryable_response(response)
            ):
                # Use individual attribute queries
                for attribute in needed:
                    if should_reboot:
                        break
                    attr_query = base_query.replace(
                        "{}", '{"' + attribute + '":{}}'
                    ).encode("utf-8")
                    tries = 0
                    while tries < retries:
                        sub_response = self._request(attr_query)
                        if self._rebootable_response(sub_response):
                            should_reboot = True
                            break
                        if self._retryable_response(sub_response):
                            _LOGGER.info(
                                f"Retry {tries} sub query due to {sub_response}"
                            )
                            time.sleep(1.0)
                            tries += 1
                        else:
                            break

                    if attribute in str(sub_response):
                        built_response = merge(built_response, sub_response)
                    else:
                        _LOGGER.warning(
                            f"{self._name}: Did not get {attribute} from {attr_query}: "
                            f"{sub_response}"
                        )
            if built_response.get("r"):
                # Got at least some good sub-responses
                response = built_response
            now = datetime.datetime.now()
            if should_reboot and (
                not self._last_reboot
                or self._last_reboot < now - datetime.timedelta(minutes=30)
            ):
                # Attempt to reboot the adapter
                _LOGGER.warning(f"{self._name}: Attempting to reboot Kumo adapter")
                self._last_reboot = now
                self.do_reboot()
                time.sleep(5.0)
                return self._retrieve_attributes(
                    query_path, needed, do_top_query, retries
                )
        except Exception as e:
            _LOGGER.warning("Exception fetching %s: %s", base_query, str(e))
        return response

    def _compute_has_mode_auto(self, auto_mode_prevention: bool) -> bool:
        """True if the unit supports auto (heat/cool) mode.

        Honors the adapter's autoModePrevention flag, but falls back to the
        unit profile's auto setpoints, since some installer configurations
        set autoModePrevention=True even though the unit (and the Mitsubishi
        Comfort app) treat auto mode as supported. Checks both
        maximumSetPoints and minimumSetPoints for an 'auto' key.
        """
        if not auto_mode_prevention:
            return True
        max_sp = self._profile.get("maximumSetPoints", {}) or {}
        min_sp = self._profile.get("minimumSetPoints", {}) or {}
        return "auto" in max_sp or "auto" in min_sp

    def update_status(self):
        """Retrieve and cache current status dictionary if enough time
        has passed
        """

        # Use cycle-aware session management to optimize multiple requests during status update,
        # then close session at the end of the cycle to send a clean FIN to the adapter.
        self.begin_cycle()
        try:
            now = time.monotonic()
            if (
                now - self._last_status_update > CACHE_INTERVAL_SECONDS
                or "mode" not in self._status
            ):
                query = ["indoorUnit", "status"]
                needed = [
                    "mode",
                    "standby",
                    "spHeat",
                    "spCool",
                    "roomTemp",
                    "fanSpeed",
                    "vaneDir",
                    "filterDirty",
                    "defrost",
                    "tempSource",
                    "activeThermistor",
                ]
                # Following not currently used:
                # 'hotAdjust', 'runTest'
                response = self._retrieve_attributes(query, needed)
                raw_status = response
                try:
                    self._status = raw_status["r"]["indoorUnit"]["status"]
                    self._last_status_update = now
                except KeyError as ke:
                    _LOGGER.warning(
                        f"{self._name}: Error retrieving status from {response}: "
                        f"{str(ke)}"
                    )
                    return False

                self._sensors = []
                for s in range(POSSIBLE_SENSORS):
                    s_str = f"{s}"
                    query = ["sensors", s_str]
                    needed = [
                        "uuid",
                        "humidity",
                        "temperature",
                        "battery",
                        "rssi",
                        "txPower",
                    ]

                    response = self._retrieve_attributes(query, needed)

                    try:
                        sensor = response["r"]["sensors"][s_str]
                        if isinstance(sensor, dict) and sensor.get("uuid"):
                            self._sensors.append(sensor)
                        else:
                            # No sensor found at this index; skip the rest
                            break
                    except KeyError as ke:
                        _LOGGER.warning(
                            f"{self._name}: Error retrieving sensors from {response}: "
                            f"{str(ke)}"
                        )
                        return False

                query = ["indoorUnit", "profile"]
                needed = [
                    "numberOfFanSpeeds",
                    "hasFanSpeedAuto",
                    "hasVaneSwing",
                    "hasModeDry",
                    "hasModeHeat",
                    "hasModeVent",
                    "hasModeAuto",
                    "hasVaneDir",
                    "maximumSetPoints",
                    "minimumSetPoints",
                ]
                # Following not currently used
                # 'extendedTemps', 'usesSetPointInDryMode', 'hasHotAdjust', 'hasDefrost',
                # 'hasStandby'
                response = self._retrieve_attributes(query, needed)
                try:
                    self._profile = response["r"]["indoorUnit"]["profile"]
                except KeyError as ke:
                    _LOGGER.warning(
                        f"{self._name}: Error retrieving profile from {response}: "
                        f"{str(ke)}"
                    )
                    return False

                # Edit profile with settings from adapter
                query = ["adapter", "status"]
                needed = [
                    "autoModePrevention",
                    "userHasModeDry",
                    "userHasModeHeat",
                    "localNetwork",
                    "runState",
                ]
                # Following not currently used:
                # 'name', 'roomTempOffset', 'userMinCoolSetPoint', 'userMaxHeatSetPoint',
                # 'ledDisabled', 'serverHostname'
                # ['adapter', 'info'] not used:
                # ['macAddress', 'serialNumber', 'isTestMode', 'firmwareVersion']
                response = self._retrieve_attributes(query, needed)
                try:
                    status = response["r"]["adapter"]["status"]
                    self._profile["hasModeAuto"] = self._compute_has_mode_auto(
                        status.get("autoModePrevention", False)
                    )
                    if not status.get("userHasModeDry", False):
                        self._profile["hasModeDry"] = False
                    if not status.get("userHasModeHeat", False):
                        self._profile["hasModeHeat"] = False
                    try:
                        self._profile["wifiRSSI"] = status["localNetwork"][
                            "stationMode"
                        ]["RSSI"]
                    except KeyError:
                        self._profile["wifiRSSI"] = None
                    self._profile["runState"] = status.get("runState", "unknown")
                except KeyError as ke:
                    _LOGGER.warning(
                        f"{self._name}: Error retrieving adapter profile from {response}: "
                        f"{str(ke)}"
                    )
                    return False

                # Edit profile with data from MHK2 if present
                query = '{"c":{"mhk2":{"status":{}}}}'.encode("utf-8")
                response = self._request(query)
                try:
                    self._mhk2 = response["r"]["mhk2"]
                    if isinstance(self._mhk2, dict):
                        mhk2_humidity = self._mhk2["status"]["indoorHumid"]

                        if mhk2_humidity is not None:
                            # Add a sensor entry for the MHK2 unit.
                            mhk2_sensor_value = {
                                "battery": None,
                                "humidity": mhk2_humidity,
                                "rssi": None,
                                "temperature": None,
                                "txPower": None,
                                "uuid": None,
                            }
                            self._sensors.append(mhk2_sensor_value)
                except (KeyError, TypeError) as e:
                    # We don't bailout here since the MHK2 component is optional.
                    _LOGGER.info(
                        f"{self._name}: Error retrieving MHK2 status from {response}: {e}"
                    )
                    pass

            if self._unit_schedule is not None:
                self._unit_schedule.fetch()

            return True
        finally:
            self.end_cycle()

    def get_mode(self):
        """Last retrieved operating mode from unit"""
        try:
            val = self._status["mode"]
        except KeyError:
            val = None
        return val

    def get_standby(self):
        """Return if the unit is in standby"""
        try:
            val = self._status["standby"]
        except KeyError:
            val = None
        return val

    def get_heat_setpoint(self):
        """Last retrieved heat setpoint from unit"""
        try:
            val = self._status["spHeat"]
        except KeyError:
            val = None
        return val

    def get_cool_setpoint(self):
        """Last retrieved cooling setpoint from unit"""
        try:
            val = self._status["spCool"]
        except KeyError:
            val = None
        return val

    def get_current_temperature(self):
        """Last retrieved current temperature from unit"""
        try:
            val = self._status["roomTemp"]
        except KeyError:
            val = None
        return val

    def get_temp_source(self):
        """Last retrieved temperature source from unit"""
        try:
            val = self._status["tempSource"]
        except KeyError:
            val = None
        return val

    def get_active_thermistor(self):
        """Last retrieved active thermistor from unit. Reports 'api' while an
        injected room temperature is live.
        """
        try:
            val = self._status["activeThermistor"]
        except KeyError:
            val = None
        return val

    def get_fan_speeds(self):
        """List of valid fan speeds for unit"""
        try:
            speeds = self._profile["numberOfFanSpeeds"]
        except KeyError:
            speeds = 5
        if speeds not in (5, 4, 3):
            _LOGGER.info(
                "Unit reports a different number of fan speeds than "
                "supported, %d != [5|4|3]. Please report which ones work!",
                self._profile["numberOfFanSpeeds"],
            )

        if speeds == 3:
            # Intentionally return all 5 speeds even though the profile reports
            # numberOfFanSpeeds=3.  These units under-report their capability:
            # real hardware accepts the full superQuiet..superPowerful range,
            # as confirmed on units in the field.
            valid_speeds = ["superQuiet", "quiet", "low", "powerful", "superPowerful"]
        elif speeds == 4:
            valid_speeds = ["quiet", "Low", "powerful", "superPowerful"]
        else:
            valid_speeds = ["superQuiet", "quiet", "low", "powerful", "superPowerful"]
        try:
            if self._profile["hasFanSpeedAuto"]:
                valid_speeds.append("auto")
        except KeyError:
            pass
        return valid_speeds

    def get_vane_directions(self):
        """List of valid vane directions for unit"""
        if not self.has_vane_direction():
            _LOGGER.info("Unit does not support vane direction")
            return []

        valid_directions = [
            "horizontal",
            "midhorizontal",
            "midpoint",
            "midvertical",
            "vertical",
            "auto",
        ]
        try:
            if self._profile["hasVaneSwing"]:
                valid_directions.append("swing")
        except KeyError:
            pass
        return valid_directions

    def get_fan_speed(self):
        """Last retrieved fan speed mode from unit"""
        try:
            val = self._status["fanSpeed"]
        except KeyError:
            val = None
        return val

    def get_vane_direction(self):
        """Last retrieved vane direction mode from unit"""
        try:
            val = self._status["vaneDir"]
        except KeyError:
            val = None
        return val

    def get_current_humidity(self):
        """Last retrieved humidity from sensor or MHK2, if any"""
        val = None
        try:
            for sensor in self._sensors:
                if sensor["humidity"] is not None:
                    return sensor["humidity"]
        except KeyError:
            val = None
        return val

    def get_current_sensor_temperature(self):
        """Last retrieved temperature from sensor, if any"""
        val = None
        try:
            for sensor in self._sensors:
                if sensor["temperature"] is not None:
                    return sensor["temperature"]
        except KeyError:
            val = None
        return val

    def get_unit_schedule(self) -> UnitSchedule | None:
        """Retrieve the program (UnitSchedule) from sensor, if any."""
        return self._unit_schedule

    def get_sensor_battery(self):
        """Last retrieved battery percentage from sensor, if any"""
        val = None
        try:
            for sensor in self._sensors:
                if sensor["battery"] is not None:
                    return sensor["battery"]
        except KeyError:
            val = None
        return val

    def get_runstate(self):
        """Last retrieved runState, if any. True if unit has heat mode."""
        val = None
        try:
            val = self._profile["runState"]
        except KeyError:
            val = None
        return val

    def get_filter_dirty(self):
        """Last retrieved filter status from unit"""
        try:
            val = self._status["filterDirty"]
        except KeyError:
            val = None
        return val

    def get_defrost(self):
        """Last retrieved defrost status from unit"""
        try:
            val = self._status["defrost"]
        except KeyError:
            val = None
        return val

    def get_hold_time(self):
        """Get hold time from MHK2"""
        query = '{"c":{"mhk2":{"hold":{"adapter":{"endTime":{}}}}}}'.encode("utf-8")
        response = self._request(query)
        try:
            end_time = response["r"]["mhk2"]["hold"]["adapter"]["endTime"]
        except KeyError:
            end_time = None
        return end_time

    def get_hold_status(self):
        """Get hold status similar to representation on kumo app and MHK2 display"""
        end_time = self.get_hold_time()
        # mhk returns 3774499593 for "permanent hold"
        if end_time is None:
            _LOGGER.warning("End time not available")
            hold_status = ""
        elif end_time == 3774499593:
            hold_status = "permanent hold"
        elif end_time == 0:
            hold_status = "following schedule"
        elif (end_time - time.time()) > 82800:  # 23 hours
            days = round((end_time - time.time()) / 86400)
            hold_status = f"hold for {days} days"
        else:
            dt = datetime.datetime.fromtimestamp(end_time)
            hold_status = f"hold until {dt.strftime('%H:%M')}"
        return hold_status

    def has_dry_mode(self):
        """True if unit has dry (dehumidify) mode"""
        val = None
        try:
            val = self._profile["hasModeDry"]
        except KeyError:
            val = False
        return val

    def has_heat_mode(self):
        """True if unit has heat mode"""
        val = None
        try:
            val = self._profile["hasModeHeat"]
        except KeyError:
            val = False
        return val

    def has_vent_mode(self):
        """True if unit has vent (fan) mode"""
        val = None
        try:
            val = self._profile["hasModeVent"]
        except KeyError:
            val = False
        return val

    def has_auto_mode(self):
        """True if unit has auto (heat/cool) mode"""
        val = None
        try:
            val = self._profile["hasModeAuto"]
        except KeyError:
            val = False
        return val

    def has_vane_direction(self):
        """True if unit supports changing its vane direction (aka swing)"""
        val = None
        try:
            val = self._profile["hasVaneDir"]
        except KeyError:
            val = False
        return val

    def set_mode(self, mode):
        """Change operation mode. Valid modes: off, cool sometimes also heat,
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

        command = ('{"c":{"indoorUnit":{"status":{"mode":"%s"}}}}' % mode).encode(
            "utf-8"
        )
        response = self._request(command)
        self._status["mode"] = mode
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS
        return response

    def set_heat_setpoint(self, setpoint):
        """Change setpoint for heat (in degrees C)"""
        # TODO: honor min/max from profile
        setpoint = round(float(setpoint), 1)
        command = (
            '{"c": { "indoorUnit": { "status": { "spHeat": %f } } } }' % setpoint
        ).encode("utf-8")
        response = self._request(command)
        self._status["spHeat"] = setpoint
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS
        return response

    def set_cool_setpoint(self, setpoint):
        """Change setpoint for cooling (in degrees C)"""
        # TODO: honor min/max from profile
        setpoint = round(float(setpoint), 2)
        command = (
            '{"c": { "indoorUnit": { "status": { "spCool": %f } } } }' % setpoint
        ).encode("utf-8")
        response = self._request(command)
        self._status["spCool"] = setpoint
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS
        return response

    def set_fan_speed(self, speed):
        """Change fan speed. Valid speeds: superQuiet, quiet, low, powerful,
        superPowerful, sometimes auto
        """
        if speed not in ALL_FAN_SPEEDS + ["auto"]:
            _LOGGER.warning("Attempting to set invalid fan speed %s", speed)
            return {}
        valid_speeds = self.get_fan_speeds()
        if speed not in valid_speeds:
            _LOGGER.warning(
                "Unit does not report fan speed %s as supported. Setting anyway", speed
            )
        command = (
            '{"c": { "indoorUnit": { "status": { "fanSpeed": "%s" } } } }' % speed
        ).encode("utf-8")
        response = self._request(command)
        self._status["fanSpeed"] = speed
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS
        return response

    def set_vane_direction(self, direction):
        """Change vane direction. Valid directions: horizontal, midhorizontal,
        midpoint, midvertical, vertical, auto, and sometimes swing
        """
        valid_directions = self.get_vane_directions()
        if direction not in valid_directions:
            _LOGGER.warning("Attempting to set an invalid vane direction %s", direction)
            return {}
        command = (
            '{"c": { "indoorUnit": { "status": { "vaneDir": "%s" } } } }' % direction
        ).encode("utf-8")
        response = self._request(command)
        self._status["vaneDir"] = direction
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS
        return response

    def set_temp_source(self, source):
        """Change temperature source. Valid sources: sensor0-sensor3,
        returnair, remote, api.
        """
        if source not in SETTABLE_TEMP_SOURCES:
            _LOGGER.warning("Attempting to set invalid temp source %s", source)
            return {}
        command = (
            '{"c": { "indoorUnit": { "status": { "tempSource": "%s" } } } }' % source
        ).encode("utf-8")
        response = self._request(command)
        self._status["tempSource"] = source
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS
        return response

    def set_injected_room_temp(self, temperature):
        """Inject a room temperature for the unit to use.

        Only used if tempSource is set to "api". The caller must re-send the value
        periodically to prevent the system from ignoring it as stale.
        """
        temp_source = self.get_temp_source()
        if temp_source != "api":
            _LOGGER.warning(
                "Ignoring injected room temp; tempSource is %s, not 'api'",
                temp_source,
            )
            return {}
        temperature = round(float(temperature), 1)
        command = (
            '{"c": { "indoorUnit": { "status": { "roomTemp": %.1f } } } }' % temperature
        ).encode("utf-8")
        response = self._request(command)
        self._status["roomTemp"] = temperature
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS
        return response

    def set_hold(self, end_time):
        """Set a hold on the current temperature until end_time.
        Accepts unix timesamp.
        MHK uses 4294967295 to set 'permanent hold'
        """
        command = (
            '{"c":{"mhk2":{"hold":{"adapter":{"endTime": %d}}}}}' % end_time
        ).encode("utf-8")
        response = self._request(command)
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS
        return response

    def get_cn105_bus(self) -> Cn105Bus:
        """Return the raw frame interface, for other info codes and experiments."""
        return self._cn105

    def update_cn105_telemetry(
        self, codes=None, timeout: float = REPLY_TIMEOUT_SECONDS
    ) -> bool:
        """Read fresh CN105 data. True if at least one code answered.

        Asks for one info code at a time and caches what comes back, which is what
        the ``get_*`` methods below return. This never raises. A code that fails
        leaves its own fields None and the others alone.

        ``codes`` defaults to :data:`~pykumo.cn105.DEFAULT_INFO_CODES` (``0x03``
        and ``0x09``). Ask for ``InfoCode.COMPRESSOR`` (``0x06``) only on units you
        know support it. On a unit that does not, it costs about 30 s to give up
        and usually takes the codes after it in the same refresh down with it.

        Waits for each reply, a few seconds per code, so call it from an executor
        rather than an event loop.
        """
        answered = False
        runtime_sample = None
        for code in DEFAULT_INFO_CODES if codes is None else codes:
            if code not in TELEMETRY_KEYS:
                _LOGGER.warning(
                    "%s: no CN105 fields known for info code 0x%02x", self._name, code
                )
                continue
            self._cn105_telemetry.update(dict.fromkeys(TELEMETRY_KEYS[code]))
            try:
                reply = self._cn105.read_info(code, timeout=timeout)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.warning(
                    "%s: CN105 read for info code 0x%02x failed",
                    self._name,
                    code,
                    exc_info=True,
                )
                continue
            if reply is None:
                continue
            fields = decode_info_reply(reply, code)
            self._cn105_telemetry.update(fields)
            if "compressor_runtime_minutes" in fields:
                # Time the counter when it was read, not when the whole refresh
                # ends. A code that times out adds tens of seconds, which would
                # otherwise make a fresh reading look far enough from the last
                # one to be compared against it.
                runtime_sample = (
                    fields["compressor_runtime_minutes"],
                    time.monotonic(),
                )
            answered = True
        now = time.monotonic()
        if answered:
            self._cn105_telemetry_at = now
        if runtime_sample is not None:
            self._compressor_activity.update(*runtime_sample)
        return answered

    def get_cn105_telemetry(self) -> dict:
        """Everything read on the last refresh.

        ``operating`` holds whatever :meth:`is_compressor_running` reports, so it
        is there even when info code ``0x06`` was not asked for. Empty until the
        first refresh.
        """
        if not self._cn105_telemetry:
            return {}
        return {**self._cn105_telemetry, "operating": self.is_compressor_running()}

    def get_cn105_telemetry_age(self) -> float | None:
        """How many seconds ago the data came back, or None if it never has."""
        if self._cn105_telemetry_at is None:
            return None
        return time.monotonic() - self._cn105_telemetry_at

    def get_outdoor_temperature(self) -> float | None:
        """Outdoor temperature in C, as of the last refresh.

        The adapter reports this as null in its own status.
        """
        return self._cn105_telemetry.get("outdoor_temperature")

    def get_raw_room_temperature(self) -> float | None:
        """Room temperature in C as the unit itself measures it.

        Not :meth:`get_current_temperature`, which uses whatever sensor the
        adapter was told to use.
        """
        return self._cn105_telemetry.get("room_temperature")

    def get_compressor_runtime_minutes(self) -> int | None:
        """How many minutes the compressor has run, as of the last refresh.

        This is compressor time, not power-on time.
        """
        return self._cn105_telemetry.get("compressor_runtime_minutes")

    def is_compressor_running(self) -> bool | None:
        """Whether the compressor is running, or None if we cannot tell.

        Uses the ``0x06`` operating flag if you asked for that code. Otherwise it
        infers the answer from the runtime counter, which needs two refreshes at
        least 70 s apart and lags the real state by a minute or two.
        """
        operating = self._cn105_telemetry.get("operating")
        if operating is not None:
            return operating
        if self.get_mode() in ("off", "idle"):
            return False
        return self._compressor_activity.running

    def do_reboot(self):
        """Issue a reboot command to the indoor unit's adapter."""
        command = ('{"c":{"adapter":{"status":{"runState":"reboot"}}}}').encode("utf-8")
        response = self._request(command)
        self._last_status_update = time.monotonic() - 2 * CACHE_INTERVAL_SECONDS
        return response
