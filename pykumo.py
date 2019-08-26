#!/usr/bin/python3
""" Module to interact with Mitsubishi KumoCloud devices via their local API.
"""

import json
import hashlib
import base64

PARAMS = {"username": "doug@parkercat.org"}

def url_token(unit_cfg, post_data):
    """ Compute security token for a given unit and command
    """
    w_param = bytearray.fromhex(unit_cfg["W"])
    password = base64.b64decode(unit_cfg["password"])
    s_param = unit_cfg["S"]
    crypto_serial = bytearray.fromhex(unit_cfg["cryptoSerial"])

    data_hash = hashlib.sha256(password + post_data).digest()

    token = bytearray(88)
    token[0:32] = w_param[0:32]
    token[32:64] = data_hash[0:32]
    token[64:66] = bytearray.fromhex("0840")
    token[66] = s_param
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
