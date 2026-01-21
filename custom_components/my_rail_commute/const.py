"""Constants for the My Rail Commute integration."""
from datetime import timedelta
from typing import Final

# Integration domain
DOMAIN: Final = "my_rail_commute"

# Configuration keys
CONF_ORIGIN: Final = "origin"
CONF_DESTINATION: Final = "destination"
CONF_COMMUTE_NAME: Final = "commute_name"
CONF_TIME_WINDOW: Final = "time_window"
CONF_NUM_SERVICES: Final = "num_services"
CONF_NIGHT_UPDATES: Final = "night_updates"
CONF_DISRUPTION_SINGLE_DELAY: Final = "disruption_single_delay"
CONF_DISRUPTION_MULTIPLE_DELAY: Final = "disruption_multiple_delay"
CONF_DISRUPTION_MULTIPLE_COUNT: Final = "disruption_multiple_count"

# API Configuration
API_BASE_URL: Final = "https://api1.raildata.org.uk/1010-live-departure-board-dep1_2/LDBWS/api/20220120"
API_TIMEOUT: Final = 30

# Default values
DEFAULT_TIME_WINDOW: Final = 60
DEFAULT_NUM_SERVICES: Final = 3
DEFAULT_NIGHT_UPDATES: Final = False
DEFAULT_NAME: Final = "My Rail Commute"

# Limits
MIN_TIME_WINDOW: Final = 15
MAX_TIME_WINDOW: Final = 120
MIN_NUM_SERVICES: Final = 1
MAX_NUM_SERVICES: Final = 10

# Update intervals (in minutes)
UPDATE_INTERVAL_PEAK: Final = timedelta(minutes=2)
UPDATE_INTERVAL_OFF_PEAK: Final = timedelta(minutes=5)
UPDATE_INTERVAL_NIGHT: Final = timedelta(minutes=15)

# Time windows for update intervals (hours)
PEAK_HOURS: Final = [(6, 10), (16, 20)]  # Morning and evening peaks
NIGHT_HOURS: Final = (23, 5)  # Night time

# Service status
STATUS_ON_TIME: Final = "on_time"
STATUS_DELAYED: Final = "delayed"
STATUS_CANCELLED: Final = "cancelled"

# Disruption thresholds (default values)
DISRUPTION_DELAY_THRESHOLD_SINGLE: Final = 15  # minutes
DISRUPTION_DELAY_THRESHOLD_MULTIPLE: Final = 10  # minutes
DISRUPTION_MULTIPLE_SERVICES: Final = 2

# Disruption threshold limits
MIN_DISRUPTION_DELAY: Final = 5  # minutes
MAX_DISRUPTION_DELAY: Final = 60  # minutes
MIN_DISRUPTION_COUNT: Final = 2
MAX_DISRUPTION_COUNT: Final = 10

# Sensor types
SENSOR_SUMMARY: Final = "summary"
SENSOR_STATUS: Final = "status"
SENSOR_NEXT_TRAIN: Final = "next_train"
SENSOR_DISRUPTION: Final = "disruption"

# Commute status levels (for overall commute status sensor)
STATUS_NORMAL: Final = "Normal"
STATUS_MINOR_DELAYS: Final = "Minor Delays"
STATUS_MAJOR_DELAYS: Final = "Major Delays"
STATUS_CANCELLATIONS: Final = "Cancellations"

# Delay thresholds for commute status classification
STATUS_MINOR_DELAY_THRESHOLD: Final = 1  # minutes
STATUS_MAJOR_DELAY_THRESHOLD: Final = 10  # minutes

# Attributes
ATTR_ORIGIN: Final = "origin"
ATTR_ORIGIN_NAME: Final = "origin_name"
ATTR_DESTINATION: Final = "destination"
ATTR_DESTINATION_NAME: Final = "destination_name"
ATTR_TIME_WINDOW: Final = "time_window"
ATTR_SERVICES_TRACKED: Final = "services_tracked"
ATTR_TOTAL_SERVICES: Final = "total_services_found"
ATTR_ON_TIME_COUNT: Final = "on_time_count"
ATTR_DELAYED_COUNT: Final = "delayed_count"
ATTR_CANCELLED_COUNT: Final = "cancelled_count"
ATTR_SCHEDULED_DEPARTURE: Final = "scheduled_departure"
ATTR_EXPECTED_DEPARTURE: Final = "expected_departure"
ATTR_PLATFORM: Final = "platform"
ATTR_OPERATOR: Final = "operator"
ATTR_SERVICE_ID: Final = "service_id"
ATTR_CALLING_POINTS: Final = "calling_points"
ATTR_DELAY_MINUTES: Final = "delay_minutes"
ATTR_STATUS: Final = "status"
ATTR_IS_CANCELLED: Final = "is_cancelled"
ATTR_CANCELLATION_REASON: Final = "cancellation_reason"
ATTR_DELAY_REASON: Final = "delay_reason"
ATTR_ESTIMATED_ARRIVAL: Final = "estimated_arrival"
ATTR_SCHEDULED_ARRIVAL: Final = "scheduled_arrival"
ATTR_DISRUPTION_TYPE: Final = "disruption_type"
ATTR_AFFECTED_SERVICES: Final = "affected_services"
ATTR_MAX_DELAY: Final = "max_delay_minutes"
ATTR_DISRUPTION_REASONS: Final = "disruption_reasons"

# API Error Messages
ERROR_AUTH: Final = "Authentication failed. Please check your API key."
ERROR_INVALID_STATION: Final = "Invalid station code. Please use a valid 3-letter CRS code."
ERROR_NO_SERVICES: Final = "No services found for this route."
ERROR_API_UNAVAILABLE: Final = "Rail API is currently unavailable."
ERROR_RATE_LIMIT: Final = "API rate limit exceeded. Retrying later."
ERROR_NETWORK: Final = "Network error occurred while contacting Rail API."

# User Agent
USER_AGENT: Final = f"{DOMAIN}/1.0.0"
