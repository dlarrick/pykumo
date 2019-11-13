#!/usr/bin/python3
""" Quick & dirty utility to check parsing of various server JSON files
"""

import sys
import json
from pykumo import pykumo

def main():
    """ Entry point
    """
    filename = sys.argv[1]
    file = open(filename)
    kumo_json = json.load(file)
    account = pykumo.KumoCloudAccount(None, None, kumo_dict=kumo_json)
    units = account.get_indoor_units()
    print("Units: %s" % str(units))
    for unit in units:
        print("Unit %s: address: %s credentials: %s" %
              (account.get_name(unit), account.get_address(unit),
               account.get_credentials(unit)))

if __name__ == '__main__':
    main()
