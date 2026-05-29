DOMAIN = "biamp_tesira"

CONF_HOSTNAME = "hostname"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_PORT = "port"
CONF_HOST_KEY_CHECK = "host_key_check"
CONF_BLOCK_MAP_PATH = "block_map_path"
CONF_ZONES = "zones"
CONF_ENABLED_BLOCK_TYPES = "enabled_block_types"
CONF_PRESETS = "presets"

DEFAULT_PORT = 22
DEFAULT_HOST_KEY_CHECK = False
DEFAULT_DEVICE_REFRESH_INTERVAL = 5

# Parlé beam tracking
PARLE_ACTIVE_THRESHOLD = 0.5
PARLE_AZ_DEBOUNCE_DEG = 5.0
PARLE_SUB_RATE_MS = 500

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

# All block types the integration has entity support for.
# Used in config/options flow checkboxes and to compute skip_block_types for pytesira.
SUPPORTED_BLOCK_TYPES: dict[str, str] = {
    "AEC":                  "AEC (Acoustic Echo Cancellation)",
    "AECReference":         "AEC Reference",
    "AudioDelay":           "Audio Delay",
    "AudioInput":           "Audio Input",
    "AudioMeter":           "Audio Meter",
    "AudioOutput":          "Audio Output",
    "AVBPOEAmp":            "AVB/POE Amplifier (ParléAmp)",
    "BFMic":                "Parlé Beamforming Microphone",
    "BluetoothControlStatus": "Bluetooth Control Status",
    "BluetoothInput":       "Bluetooth Input",
    "BluetoothOutput":      "Bluetooth Output",
    "Compressor":           "Compressor",
    "DanteInput":           "Dante Input",
    "DanteOutput":          "Dante Output",
    "LevelControl":         "Level Control",
    "LogicGate":            "Logic Gate",
    "MatrixMixer":          "Matrix Mixer",
    "MuteControl":          "Mute Control",
    "PassFilter":           "Pass Filter",
    "PeakLim":              "Peak Limiter",
    "SourceSelector":       "Source Selector",
    "UsbInputEx":           "USB Input (Extended)",
    "UsbOutputEx":          "USB Output (Extended)",
}
