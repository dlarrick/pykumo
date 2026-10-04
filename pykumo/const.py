# How many seconds to wait before re-fetching data from a unit
CACHE_INTERVAL_SECONDS = 20

# Magic related to generating the auth tokens
W_PARAM = bytearray.fromhex(
    "44c73283b498d432ff25f5c8e06a016aef931e68f0a00ea710e36e6338fb22db"
)
S_PARAM = 0

# Default timeouts for interacting with the units
UNIT_CONNECT_TIMEOUT_SECONDS = 1.2
UNIT_RESPONSE_TIMEOUT_SECONDS = 8.0

# Minimum idle time to leave an adapter between the end of one request and
# the start of the next, so a poll or a burst of commands doesn't hit it
# back-to-back. Caps traffic to one adapter at roughly 1/interval requests
# per second.
UNIT_MIN_REQUEST_INTERVAL_SECONDS = 0.25

# Default timeouts for interacting with the Kumo Cloud
KUMO_CONNECT_TIMEOUT_SECONDS = 5
KUMO_RESPONSE_TIMEOUT_SECONDS = 60

POSSIBLE_SENSORS = 4

# Valid tempSource values. "unset" is reported by the adapter but rejected on write.
TEMP_SOURCES = [f"sensor{i}" for i in range(POSSIBLE_SENSORS)] + [
    "returnair",
    "remote",
    "api",
    "unset",
]
SETTABLE_TEMP_SOURCES = [s for s in TEMP_SOURCES if s != "unset"]
