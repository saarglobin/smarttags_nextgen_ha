"""Constants for the SmartThings Find NextGen integration."""

DOMAIN = "smarttags_nextgen"

# Config entry keys
CONF_JSESSION_ID = "jsession_id"
CONF_REGION = "region"
CONF_AUTH_METHOD = "auth_method"

# How the entry authenticates: a copied JSESSIONID (entries without auth_method), or a
# Samsung account sign-in that can create new sessions by itself
AUTH_METHOD_COOKIE = "cookie"
AUTH_METHOD_ACCOUNT = "account"

# Default for the scan interval option
DEFAULT_SCAN_INTERVAL_MINUTES = 5

# Available operational regions as documented in Samsung backend servers
REGION_US_GENERAL = "prd-us"
REGION_EUROPE = "prd-eu"
REGION_ASIA = "prd-ap"
REGION_ASIA_2 = "prd-ap2"
