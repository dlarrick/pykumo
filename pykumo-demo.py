#!/usr/bin/python3
""" Module to interact with Mitsubishi KumoCloud devices via their local API.
"""

import json
import hashlib
import base64

PARAMS = {"username": "doug@parkercat.org"}
W_PARAM = bytearray.fromhex('44c73283b498d432ff25f5c8e06a016aef931e68f0a00ea710e36e6338fb22db')
S_PARAM = 0

def url_token(unit_cfg, post_data):
    """ Compute security token for a given unit and command
    """
    password = base64.b64decode(unit_cfg["password"])
    crypto_serial = bytearray.fromhex(unit_cfg["cryptoSerial"])

    data_hash = hashlib.sha256(password + post_data).digest()

    token = bytearray(88)
    token[0:32] = W_PARAM[0:32]
    token[32:64] = data_hash[0:32]
    token[64:66] = bytearray.fromhex("0840")
    token[66] = S_PARAM
    token[79] = crypto_serial[8]
    token[80:84] = crypto_serial[4:8]
    token[84:88] = crypto_serial[0:4]

    result = hashlib.sha256(token).hexdigest()

    return result

def main():
    """ Entry point
    """
    with open("kumo.cfg") as cfg_file:
        cfg = json.load(cfg_file)
    cfg = cfg[PARAMS["username"]]
    id_map = {}
    for unit_id in cfg:
        id_map[cfg[unit_id]["label"]] = unit_id

    # Demo of security token generation
    unit = "Loft"
    example = '{"c":{"indoorUnit":{"status":{}}}}'.encode('utf-8')
    unit_cfg = cfg[id_map[unit]]
    url = "http://" + unit_cfg["address"] + "/api?m=" + url_token(unit_cfg, example)

    print(url)

if __name__ == '__main__':
    main()
