# pykumo
Python library to interact with Mitsubishi Kumo Cloud devices via their local API.

The hard work of generating the security token was done by https://github.com/sushilks/kumojs, and that part of pykumo is based heavily on sushilks's code, translated to Python by me. pykumo has no dependency on kumojs.

## Kumo System Components

### Outdoor Unit
The outdoor unit houses the compressor and the outdoor coil. Each indoor unit connects to its outdoor unit via a pair of refrigerant lines and an electric power supply and communication cable. (Collectively this connection is called a lineset.) Kumo Cloud does not appear to be able to communicate directly with an outdoor unit.

### Indoor Unit
Indoor units, also called air handlers, are in the conditioned space, and contain the indoor fan and heat exchanger coil, providing either cooling or heating. An indoor unit can be connected to some or all of the following:

* WiFi adapter (e.g. PAC-USWHS002-WF-2) (via CN105 connector)
* Wall-mounted controller MHK2 (via CN105 connector)
* Wireless remote sensor (via Bluetooth to the WiFi adapter)

An indoor unit with a WiFi adapter has its own IP address on your local network.

Pykumo communicates with an indoor unit via its WiFi adapter, using an API exposed by the adapter.

### MHK2
The MHK2 wall-mounted controller, if present, acts like a traditional thermostat for a single indoor unit, while also providing other controls. It also contains its own temperature and humidity sensors.

### Kumo Station
Kumo Station allows controlling traditional HVAC equipment via the Kumo Cloud app, managing switchover between Kumo and external equipment. It also provides an outdoor temperature sensor. A Kumo Station has its own IP address.

## Troubleshooting
### WiFi
The most common cause of flaky behavior is weak WiFi signal at the indoor unit. Try measuring WiFi strength (2.4 GHz only) with a phone app. Also try repositioning the Mitsubishi WiFi adapter within the unit, positioning it close to the plastic exterior rather than metal interior components. Users have also reported that isolating the indoor units to their own subnet and WiFi SSID can improve behavior.

### API errors
In early 2023 Mitsubishi appears to have made some change that makes the WiFi adapter less reliable. My educated guess is that it has a memory leak. See [Issue 105](https://github.com/dlarrick/hass-kumo/issues/105) in the hass-kumo repository for discussion.

As of mid 2024, pykumo will issue a `reboot` command to the indoor unit's WiFi adapter (at most once every 30 minutes) if `serializer_error` or `__no_memory` errors occur when performing operations.

## Interactive Use
It's possible to use pykumo in an interactive shell to do debugging, investigation of possible new features, and so on.

I recommend doing this work in a `virtualenv`. If you're not familiar with these, search the web for "python virtualenv" for details specific to your preferred operating system.

### Start an Interactive Python Shell
```
pip3 install pykumo
python3
```
### Set Up KumoCloudAccount
Inside the Python shell:
```
import pykumo
account = pykumo.KumoCloudAccount.Factory()
```
This will prompt for your KumoCloud username and password.

```
account.get_indoor_units()
```
If successful, `get_indoor_units` prints a list of your indoor units' serial numbers.

### Get Indoor Units
```
kumos = account.make_pykumos()
unit = kumos['<indoor unit name>']
unit.update_status()
```
The indoor unit name is the human-readable name. If successful, `update_status` prints `True`. You can then make various API calls on the `unit` object.

### Get Raw Information from Indoor Unit
You can print the internal state of the indoor unit object, which includes the JSON information fetched from the unit itself. This is a good place to look when requesting support for additional pykumo features.
```
import pprint
pp = pprint.PrettyPrinter()
pp.pprint(unit.__dict__)
```

### Reboot a unit's WiFi adapter
If an indoor unit is reachable but returns error responses to legitimate commands, a reboot may help.

```
unit.do_reboot()
```

### Query or Command an Indoor Unit Directly
Indoor units speak a simple protocol of nested JSON. I'm not documenting the protocol here (though documentation patches would be welcome!), but if you look through the `pykumo.py` code for calls to `self._request` you can see the queries and commands that are already enabled. For example:
```
query = '{"c":{"indoorUnit":{"status":{}}}}'.encode('utf-8')
unit._request(query)
```
This prints the primary record, the `status` object. Most of the valid queries and commands were discovered by snooping the traffic between the Kumo Cloud app and the indoor unit. A few were determined by experimentation. It's possible that additional values and controls are available beyond those already discovered, especially on indoor units newer than those owned by this author. I welcome details on these via pull requests or issues on this repo.
