DOMAIN = "hitepro"

CONF_URL = "url"
CONF_API_KEY = "api_key"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_LIGHT_DEVICES = "light_devices"

DEFAULT_URL = "http://hitepro.local/mqtt/"
DEFAULT_API_KEY = "xXxxXXxXXxXXxx"
DEFAULT_SCAN_INTERVAL = 300

WB_DEVICE = "hite-pro"
WB_CTRL_TOPIC = f"/devices/{WB_DEVICE}/controls"

SERVICE_REFRESH = "refresh_devices"

# Relay-LED3S channels read their state from a proxy topic that carries only
# genuine module answers (see state_filter.py). The prefix is outside the
# gateway tree /devices/hite-pro/#, so the gateway never sees it.
LED3S_PREFIX = "Relay-LED3S_"
PROXY_STATE_PREFIX = "hitepro/state"
