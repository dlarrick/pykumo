# pykumo
Python library to interact with Mitsubishi KumoCloud devices via their local API.

The hard work of generating the security token was done by https://github.com/sushilks/kumojs and that part is based heavily on his code, translated to Python by me.

The 'examples' directory contains JSON retrieved from the KumoCloud server, with personal information removed. `server_parser.py` is for testing such examples. Invoke as `python3 server_parser.py examples/json-file-of-interest`.
