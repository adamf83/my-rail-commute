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
CONF_SEVERE_DELAY_THRESHOLD: Final = "severe_delay_threshold"
CONF_MAJOR_DELAY_THRESHOLD: Final = "major_delay_threshold"
CONF_MINOR_DELAY_THRESHOLD: Final = "minor_delay_threshold"
CONF_DEPARTED_TRAIN_GRACE_PERIOD: Final = "departed_train_grace_period"
CONF_ADD_RETURN_JOURNEY: Final = "add_return_journey"
CONF_ALL_DEPARTURES: Final = "all_departures"
CONF_ADD_ARRIVALS: Final = "add_arrivals"
# Which board a config entry tracks at its origin station: departures from it,
# or arrivals into it (filtered to trains coming from the destination).
CONF_BOARD_TYPE: Final = "board_type"
BOARD_TYPE_DEPARTURES: Final = "departures"
BOARD_TYPE_ARRIVALS: Final = "arrivals"
DEFAULT_BOARD_TYPE: Final = BOARD_TYPE_DEPARTURES

# Legacy config keys (for migration)
CONF_DISRUPTION_SINGLE_DELAY: Final = "disruption_single_delay"
CONF_DISRUPTION_MULTIPLE_DELAY: Final = "disruption_multiple_delay"
CONF_DISRUPTION_MULTIPLE_COUNT: Final = "disruption_multiple_count"

# API Configuration
API_BASE_URL: Final = (
    "https://api1.raildata.org.uk/1010-live-arrival-and-departure-boards-arr-and-dep1_1/LDBWS/api/20220120"
)
API_TIMEOUT: Final = 30

# Default values
DEFAULT_TIME_WINDOW: Final = 60
DEFAULT_NUM_SERVICES: Final = 3
DEFAULT_NIGHT_UPDATES: Final = False
DEFAULT_NAME: Final = "My Rail Commute"
DEFAULT_DEPARTED_TRAIN_GRACE_PERIOD: Final = 5  # minutes

# Limits
MIN_TIME_WINDOW: Final = 15
MAX_TIME_WINDOW: Final = 120
MIN_NUM_SERVICES: Final = 1
MAX_NUM_SERVICES: Final = 10
MIN_GRACE_PERIOD: Final = 0  # minutes
MAX_GRACE_PERIOD: Final = 15  # minutes

# Location-based station lookup radius (miles)
LOCATION_SEARCH_MIN_RADIUS_MILES: Final = 5.0
LOCATION_SEARCH_MAX_RADIUS_MILES: Final = 10.0

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

# User-configurable delay thresholds (default values)
DEFAULT_SEVERE_DELAY_THRESHOLD: Final = 15  # minutes
DEFAULT_MAJOR_DELAY_THRESHOLD: Final = 10  # minutes
DEFAULT_MINOR_DELAY_THRESHOLD: Final = 3  # minutes

# Threshold limits (for validation)
MIN_DELAY_THRESHOLD: Final = 1  # minutes
MAX_DELAY_THRESHOLD: Final = 60  # minutes
# Validation enforces: severe >= major >= minor >= MIN_DELAY_THRESHOLD

# Sensor types
SENSOR_SUMMARY: Final = "summary"
SENSOR_STATUS: Final = "status"
SENSOR_NEXT_TRAIN: Final = "next_train"
SENSOR_DISRUPTION: Final = "disruption"

# Commute status levels (unified hierarchy for all sensors)
# These are checked in priority order from highest to lowest:
# 1. CRITICAL: Any cancellations (highest priority)
# 2. SEVERE_DISRUPTION: Any train ≥ severe_delay_threshold (user-configurable)
# 3. MAJOR_DELAYS: Any train ≥ major_delay_threshold (user-configurable)
# 4. MINOR_DELAYS: Any train ≥ minor_delay_threshold (user-configurable)
# 5. NORMAL: All trains on time
#
# Users configure all three thresholds with validation ensuring: severe >= major >= minor
STATUS_NORMAL: Final = "Normal"
STATUS_MINOR_DELAYS: Final = "Minor Delays"
STATUS_MAJOR_DELAYS: Final = "Major Delays"
STATUS_SEVERE_DISRUPTION: Final = "Severe Disruption"
STATUS_CRITICAL: Final = "Critical"

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
ERROR_INVALID_STATION: Final = (
    "Invalid station code. Please use a valid 3-letter CRS code."
)
ERROR_NO_SERVICES: Final = "No services found for this route."
ERROR_API_UNAVAILABLE: Final = "Rail API is currently unavailable."
ERROR_RATE_LIMIT: Final = "API rate limit exceeded. Retrying later."
ERROR_NETWORK: Final = "Network error occurred while contacting Rail API."

# User Agent
USER_AGENT: Final = f"{DOMAIN}/1.0.0"

# Historical statistics storage
STORAGE_VERSION: Final = 1
STATS_RETENTION_DAYS: Final = 90

# Attribute names for historical sensors
ATTR_ON_TIME_PCT_TODAY: Final = "on_time_pct_today"
ATTR_ON_TIME_PCT_7D: Final = "on_time_pct_7day"
ATTR_ON_TIME_PCT_30D: Final = "on_time_pct_30day"
ATTR_AVG_DELAY_TODAY: Final = "avg_delay_today"
ATTR_AVG_DELAY_7D: Final = "avg_delay_7day"
ATTR_WORST_DAY: Final = "worst_day"
ATTR_BEST_DAY: Final = "best_day"
ATTR_TOTAL_OBSERVATIONS_TODAY: Final = "total_observations_today"
ATTR_ON_TIME_COUNT_TODAY: Final = "on_time_count_today"
ATTR_DELAYED_COUNT_TODAY: Final = "delayed_count_today"
ATTR_CANCELLED_COUNT_TODAY: Final = "cancelled_count_today"
ATTR_DAILY_BREAKDOWN: Final = "daily_breakdown"

# Reverse-route stats attributes (exposed on CommuteSummarySensor for the paired direction)
ATTR_REVERSE_ON_TIME_PCT_TODAY: Final = "reverse_on_time_pct_today"
ATTR_REVERSE_ON_TIME_PCT_7D: Final = "reverse_on_time_pct_7day"
ATTR_REVERSE_ON_TIME_PCT_30D: Final = "reverse_on_time_pct_30day"
ATTR_REVERSE_AVG_DELAY_7D: Final = "reverse_avg_delay_7day"
ATTR_REVERSE_WORST_DAY: Final = "reverse_worst_day"
ATTR_REVERSE_BEST_DAY: Final = "reverse_best_day"
ATTR_REVERSE_DAILY_BREAKDOWN: Final = "reverse_daily_breakdown"
