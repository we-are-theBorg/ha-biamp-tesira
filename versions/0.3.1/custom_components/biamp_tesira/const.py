DOMAIN = "biamp_tesira"

CONF_HOSTNAME = "hostname"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_PORT = "port"
CONF_HOST_KEY_CHECK = "host_key_check"
CONF_BLOCK_MAP_PATH = "block_map_path"
CONF_ZONES = "zones"

DEFAULT_PORT = 22
DEFAULT_HOST_KEY_CHECK = False
DEFAULT_DEVICE_REFRESH_INTERVAL = 5

# Parlé beam tracking
PARLE_TYPE_PATTERNS = ("parle", "beamtrack")
PARLE_ACTIVE_THRESHOLD = 0.5
PARLE_AZ_DEBOUNCE_DEG = 5.0
PARLE_SUB_RATE_MS = 300

# Custom event name
EVENT_TALKER_LOCATION = f"{DOMAIN}_talker_location"

# Default 4-quadrant zone map: {name: [start_deg, end_deg]}
# Azimuth is 0-360° CCW from Biamp logo. Ranges wrap around 0 where start > end.
DEFAULT_ZONES: dict[str, list[float]] = {
    "North": [315.0, 45.0],
    "East":  [45.0,  135.0],
    "South": [135.0, 225.0],
    "West":  [225.0, 315.0],
}
