import collections.abc
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

    def to_json_dict(self) -> dict:
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

    def _formatted_days(self) -> str:
        """ Format the days in appropriate Kumo JSON. """
        days = sorted(set(self.scheduled_days))
        return "".join(ALL_DAY_ABBRS[index] for index in days)

    def _formatted_time(self) -> str:
        """ Format the setpoint time in appropriate Kumo JSON. """
        return self.scheduled_time.strftime("%H%M")

    def to_json_dict(self) -> dict:
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


class UnitSchedule(collections.abc.MutableMapping):
    """ Programmed schedule for a single sensor.

    This contains a collection of ScheduleEvent objects. Each ScheduleEvent is
    keyed by a "slot" where slot names are stringified integers. These can be
    accessed as if the UnitSchedule is a dictionary. fetch() must be called
    first in order to (re)populate this structure. push() must be called to
    update the schedule on the PyKumo device.

    Note that only limited validation is performed at this time.

    Examples
    --------
    
        # Assume you already have a PyKumo object called pykumo
        unit_schedule = UnitSchedule(pykumo)
        # Populate slot entries from the PyKumo unit -- without this,
        # everything else will fail.
        unit_schedule.fetch()
        # Now we can view and manipulate those entries.
        for slot, event_schedule in unit_schedule.items():
            print(slot, event_schedule)
        (...)
        # For example, this will activate the ScheduleEvent in the first slot.
        unit_schedule["1"].active = True
        # Push the schedule back to the PyKumo unit so it will take effect.
        unit_schedule.push()
    """
    def __init__(self, pykumo: "PyKumo"):
        self.pykumo = pykumo

        # All schedule events for this unit, indexed by slot name
        self.events_by_slot: dict[str, ScheduleEvent] = {}

    #
    # Methods to make UnitSchedule dict-like.
    #
    def __getitem__(self, slot: str) -> ScheduleEvent:
        """ Get a ScheduleEvent for a specific slot. """
        if not isinstance(slot, str):
            raise TypeError(f"Expected slot to be a slot name string, got {slot!r}")
        return self.events_by_slot[slot]

    def __setitem__(self, slot: str, value: ScheduleEvent):
        """ Set a ScheduleEvent in a specific slot. """
        if not isinstance(slot, str):
            raise TypeError(f"Expected slot to be a slot name string, got {slot!r}")
        if not isinstance(value, ScheduleEvent):
            raise TypeError(f"Expected value to be a ScheduleEvent, got {value!r}")
        if slot not in self.events_by_slot:
            raise ValueError(f"Invalid slot name {slot!r}, must be one of the existing slot names.")
        self.events_by_slot[slot] = value

    def __delitem__(self, slot: str):
        """ Deactivates the ScheduleEvent in a slot. 
        
        Note that slots cannot actually be deleted.
        """
        if not isinstance(slot, str):
            raise TypeError(f"Expected slot to be a slot name string, got {slot!r}")
        if slot not in self.events_by_slot:
            raise ValueError(f"Invalid slot name {slot!r}, must be one of the existing slot names.")
        
        self.events_by_slot[slot].active = False
        self.events_by_slot[slot].in_use = False

    def __len__(self) -> int:
        """ Returns the number of slots in this UnitSchedule. """
        return len(self.events_by_slot)

    def __iter__(self):
        """ Iterate over all slots this schedule. """
        # Make the order consistent.
        yield from sorted(self.events_by_slot.keys())

    #
    # Methods for synchronizing with PyKumo units.
    #
    def to_json_dict(self, slots: set[str] | None = None) -> dict:
        """ Render this ScheduleEvent in a JSON-encodable dict. 
        
        If slots is specified, only include those slots. Otherwise, include all
        slots in this schedule.
        """
        return {
            "events":
                {
                    slot: schedule_event.to_json_dict()
                    for (slot, schedule_event) in self.events_by_slot.items()
                    if slots is None or slot in slots
                }
        }

    def fetch(self) -> None:
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