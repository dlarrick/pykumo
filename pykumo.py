#!/usr/bin/python3

import json
import hashlib
import base64
import binascii

params = {"username": "doug@parkercat.org"}

def UrlToken(unitCfg, postData):
    W = bytearray.fromhex(unitCfg["W"])
    password = base64.b64decode(unitCfg["password"])
    S = unitCfg["S"]
    cryptoSerial = bytearray.fromhex(unitCfg["cryptoSerial"])

    dataHash = hashlib.sha256(password + postData).digest()


    token = bytearray(88)
    token[0:32] = W[0:32]
    token[32:64] = dataHash[0:32]
    token[64:66] = bytearray.fromhex("0840")
    token[66] = S
    token[79] = cryptoSerial[8]
    token[80:84] = cryptoSerial[4:8]
    token[84:88] = cryptoSerial[0:4]

    result = hashlib.sha256(token).hexdigest()

    return result

def main():
    """ Entry point
    """
    with open("kumo.cfg") as cfg_file:
        cfg = json.load(cfg_file)
    cfg = cfg[params["username"]]
    id_map = {}
    for id in cfg:
        id_map[cfg[id]["label"]] = id

    unit = "Loft"
    example = '{"c":{"indoorUnit":{"status":{}}}}'.encode('utf-8')
    #exampleCmd = json.dumps(example).encode('utf-8')
    unitCfg = cfg[id_map[unit]]
    url = "http://" + unitCfg["address"] + "/api?m=" + \
        UrlToken(unitCfg, example)

    print(url)
    
if __name__ == '__main__':
    main()
