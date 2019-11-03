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

if __name__ == '__main__':
    main()
