"""Constants for Homely Orion."""

from datetime import timedelta

DOMAIN = "homely_orion"

CONF_LOCATION_ID = "location_id"

UPDATE_INTERVAL = timedelta(minutes=5)
MAX_UPDATE_INTERVAL = timedelta(minutes=30)
