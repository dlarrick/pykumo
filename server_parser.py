#!/usr/bin/python3
""" Quick & dirty utility to check parsing of various server JSON files
"""

import sys
import json
from pykumo import KumoCloudAccount, PyKumoStation

def main():
    """ Entry point
    """
    filename = sys.argv[1]
    file = open(filename)
    kumo_json = json.load(file)
    account = KumoCloudAccount(None, None, kumo_dict=kumo_json)
    units = account.get_all_units()
    print("Units: %s" % str(units))
    for unit in units:
        print("Unit %s: address: %s credentials: %s" %
              (account.get_name(unit), account.get_address(unit),
               account.get_credentials(unit)))
    # Optional script to test fetching data from the units
    # kumos = account.make_pykumos()
    # for kumoName in kumos:
    #     if isinstance(kumos[kumoName], PyKumoStation):
    #         print("Kumo Station Outdoor Temp: %s" % (kumos[kumoName].get_outdoor_temperature()))
    #     else:
    #         print("Indoor Unit %s Current Temp: %s" % (kumoName, kumos[kumoName].get_current_temperature()))

if __name__ == '__main__':
    main()
