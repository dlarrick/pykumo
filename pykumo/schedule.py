from dataclasses import dataclass
import datetime
import json

ALL_DAY_ABBRS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

@dataclass
class ScheduleSettings:
    """ HVAC settings for a ScheduleEvent.

    These are HVAC settings that take effect at a set time on set days if their
    associated ScheduleEvent is enabled. Note that only limited validation is
    performed at this time.
    """
    mode: str
    set_point_cool: int | None
    set_point_heat: int | None
    vane_dir: str
    fan_speed: str

    def to_json_dict(self):
        """ Render this ScheduleEvent in a JSON-encodable dict. """
        json_dict = {
            "mode": self.mode,
            "vaneDir": self.vane_dir,
            "fanSpeed": self.fan_speed,
        }
        # This should be left empty rather than setting them to 'null' in JSON.
        if self.set_point_cool is not None:
            json_dict["spCool"] = self.set_point_cool
        if self.set_point_heat is not None:
            json_dict["spHeat"] = self.set_point_heat

        return json_dict

    @classmethod
    def from_json(cls, settings_json: dict):
        """ Read a ScheduleSettings from a JSON-encodable dict. """
        return cls(
            mode=settings_json["mode"],
            set_point_cool=settings_json.get("spCool"),
            set_point_heat=settings_json.get("spHeat"),
            vane_dir=settings_json["vaneDir"],
            fan_speed=settings_json["fanSpeed"]
        )


@dataclass
class ScheduleEvent:
    """ A single entry in the HVAC's program.

    This is typically contained in a UnitSchedule, which contains all the
    entries for a single sensor.

    Note that only limited validation is performed at this time.
    """
    active: bool
    in_use: bool
    # days follows date.weekday() conventions -- Monday is 0, Sunday is 6.
    scheduled_days: list[int]
    scheduled_time: datetime.time
    settings: ScheduleSettings

    def _formatted_days(self):
        """ Format the days in appropriate Kumo JSON. """
        days = sorted(set(self.scheduled_days))
        return "".join(ALL_DAY_ABBRS[index] for index in days)

    def _formatted_time(self):
        """ Format the setpoint time in appropriate Kumo JSON. """
        return self.scheduled_time.strftime("%H%M")

    def to_json_dict(self):
        """ Render this ScheduleEvent in a JSON-encodable dict. """
        json_dict = {
            "active": self.active,
            "inUse": self.in_use,
            "day": self._formatted_days(),
            "time": self._formatted_time(),
            "settings": self.settings.to_json_dict(),
        }

        return json_dict

    @classmethod
    def from_json(cls, schedule_json: dict):
        """ Read a ScheduleEvent from a JSON-encodable dict. """
        def _parse_days(days: str):
            """ Parse scheduled day (e.g., "MoTuWe"). """
            return [
                ALL_DAY_ABBRS.index(days[i:i + 2]) for i in range(0, len(days), 2)
            ]

        def _parse_time(scheduled_time: str):
            """ Parse scheduled time (e.g., "0700"). """
            return datetime.time(
                hour=int(scheduled_time[:2]), minute=int(scheduled_time[2:])
            )

        return cls(
            active=schedule_json["active"],
            in_use=schedule_json["inUse"],
            scheduled_days=_parse_days(schedule_json["day"]),
            scheduled_time=_parse_time(schedule_json["time"]),
            settings=ScheduleSettings.from_json(schedule_json["settings"]),
        )


class UnitSchedule:
    """ Programmed schedule for a single sensor.

    This contains a collection of ScheduleEvent objects, accessible via the
    events_by_slot attribute or iteration. Each ScheduleEvent is attached to a
    "slot" where slot names appear to be stringified integers. fetch() must be
    called in order to (re)populate this structure.

    Note that only limited validation is performed at this time.
    """
    def __init__(self, pykumo: "PyKumo"):
        self.pykumo = pykumo

        # All schedule events for this unit, indexed by slot name
        self.events_by_slot: dict[str, ScheduleEvent] = {}

    def __iter__(self):
        """ Iterate over all ScheduleEvent obejcts in this schedule. """
        # Make the order consistent.
        for slot, schedule_event in sorted(self.events_by_slot.items()):
            yield schedule_event

    def to_json_dict(self, slots: set[str]):
        """ Render this ScheduleEvent in a JSON-encodable dict. """
        return {
            "events":
                {
                    slot: schedule_event.to_json_dict()
                    for (slot, schedule_event) in self.events_by_slot.items()
                    if slot in slots
                }
        }

    def fetch(self):
        """Fetch the latest schedule for this sensor.

        Note that changes to any ScheduleEvent and ScheduleSettings from a
        previous fetch() will be disconnected from this object and no longer
        have any effect.
        """
        self.events_by_slot.clear()

        command = '{"c":{"indoorUnit":{"schedule":{}}}}'.encode('utf-8')
        response = self.pykumo._request(command)
        try:
            events = response["r"]["indoorUnit"]["schedule"]["events"]
        except KeyError:
            raise ValueError(f"Schedule information not available: {response!r}")

        self.events_by_slot = {
            slot: ScheduleEvent.from_json(schedule_json)
            for slot, schedule_json in events.items()
        }

    def push(self, batch_size: int = 20):
        """ Set the schedule on the sensor using the events in this object. """
        # If we try to set the full schedule, we may get an error
        # ("{'_api_error': 'device_authentication_error'}"). Instead, we'll
        # batch our updates to at most batch_size per request.
        all_slots = sorted(self.events_by_slot.keys())
        for start_index in range(0, len(all_slots), batch_size):
            batch_slots = set(all_slots[start_index:start_index + batch_size])

            json_dict = {"c": {"indoorUnit": {"schedule": self.to_json_dict(slots=batch_slots)}}}
            command = json.dumps(json_dict).encode('utf-8')
            response = self.pykumo._request(command)
            if '_api_error' in response:
                raise ValueError(f"API error: {response!r}")