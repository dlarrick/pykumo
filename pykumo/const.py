# How many seconds to wait before re-fetching data from a unit
CACHE_INTERVAL_SECONDS = 20

# Magic related to generating the auth tokens
W_PARAM = bytearray.fromhex('44c73283b498d432ff25f5c8e06a016aef931e68f0a00ea710e36e6338fb22db')
S_PARAM = 0

# Default timeouts for interacting with the units
UNIT_CONNECT_TIMEOUT_SECONDS = 1.2
UNIT_RESPONSE_TIMEOUT_SECONDS = 8.0

# Default timeouts for interacting with the Kumo Cloud
KUMO_CONNECT_TIMEOUT_SECONDS = 5
KUMO_RESPONSE_TIMEOUT_SECONDS = 60

POSSIBLE_SENSORS = 4
