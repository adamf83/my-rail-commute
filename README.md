# My Rail Commute - Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![GitHub Release](https://img.shields.io/github/release/adamf83/my-rail-commute.svg)](https://github.com/adamf83/my-rail-commute/releases)
[![License](https://img.shields.io/github/license/adamf83/my-rail-commute.svg)](LICENSE)

A custom Home Assistant integration that tracks regular commutes using National Rail real-time data from the Darwin API. Monitor train services, get disruption alerts, and automate your commuting routine.

## Features

- **Real-time Train Tracking**: Monitor upcoming train services between any two UK rail stations
- **Smart Update Intervals**: Automatically adjusts polling frequency based on time of day (peak/off-peak/night)
- **Disruption Detection**: Binary sensor that alerts on cancellations or significant delays
- **Rich Sensor Data**: Comprehensive attributes including platforms, delays, calling points, and more
- **Multi-Route Support**: Configure multiple commutes (e.g., morning and evening journeys)
- **UI Configuration**: Easy setup through Home Assistant's config flow interface
- **HACS Compatible**: Simple installation via Home Assistant Community Store
- **Custom Lovelace Card**: Beautiful, dedicated [dashboard card](https://github.com/adamf83/lovelace-my-rail-commute-card) for displaying train information

## Sensors

The integration creates multiple sensors for each configured commute:

### 1. Commute Summary Sensor
- **Entity ID**: `sensor.{commute_name}_summary`
- **State**: Summary of overall commute status (e.g., "3 trains on time", "2 trains delayed")
- **Attributes**:
  - Origin/destination names and CRS codes
  - Service counts (requested, tracked, total found)
  - On-time/delayed/cancelled counts
  - Time window setting
  - `all_trains`: Complete array of all tracked trains (useful for custom Lovelace cards)
  - Last updated and next update timestamps

### 2. Commute Status Sensor
- **Entity ID**: `sensor.{commute_name}_status`
- **State**: Overall commute status for easy automation triggers (hierarchical - highest severity wins)
  - `Normal` - All trains running on time (or below minor threshold)
  - `Minor Delays` - One or more trains delayed ≥ minor threshold (default 3 min, user-configurable)
  - `Major Delays` - One or more trains delayed ≥ major threshold (default 10 min, user-configurable)
  - `Severe Disruption` - One or more trains delayed ≥ severe threshold (default 15 min, user-configurable)
  - `Critical` - One or more trains cancelled (highest priority)
- **Icon**: Dynamic based on status
  - `mdi:train` - Normal
  - `mdi:train-variant` - Minor Delays
  - `mdi:clock-alert` - Major Delays
  - `mdi:alert-circle` - Severe Disruption
  - `mdi:alert-octagon` - Critical
- **Attributes**:
  - `total_trains`: Total tracked trains
  - `on_time_count`: Number of on-time trains
  - `minor_delays_count`: Number of trains above minor threshold
  - `major_delays_count`: Number of trains above major threshold
  - `cancelled_count`: Number of cancelled trains
  - `max_delay_minutes`: Maximum delay across all trains
  - Origin/destination information
  - Last updated timestamp
- **Use Case**: Simple state-based automation triggers without template conditions

### 3. Next Train Sensor
- **Entity ID**: `sensor.{commute_name}_next_train`
- **State**: Departure status (e.g., "On Time", "Delayed", "Cancelled", "Expected", "No service")
- **Icon**: Dynamic based on train status
  - `mdi:train-car` - Normal service
  - `mdi:train-variant` - Minor delay (1-10 minutes)
  - `mdi:clock-alert` - Significant delay (>10 minutes)
  - `mdi:alert-circle` - Cancelled
- **Attributes**:
  - `train_number`: Always 1 (next train)
  - `total_trains`: Total number of trains being tracked
  - `departure_time`: Display departure time (HH:MM) - expected time if delayed, otherwise scheduled
  - `scheduled_departure`: Original scheduled departure time (HH:MM)
  - `expected_departure`: Expected departure time including delays (HH:MM)
  - `platform`: Platform number or "TBA"
  - `platform_changed`: Boolean indicating if platform has changed from original
  - `previous_platform`: Previous platform number if changed (null otherwise)
  - `operator`: Train operating company
  - `service_id`: Unique service identifier
  - `status`: Internal status code
  - `delay_minutes`: Number of minutes delayed (0 if on time)
  - `is_cancelled`: Boolean indicating cancellation
  - `calling_points`: List of stops the train will make
  - `scheduled_arrival`: Original scheduled arrival time (HH:MM)
  - `estimated_arrival`: Expected arrival time including delays (HH:MM)
  - `cancellation_reason`: Reason if cancelled
  - `delay_reason`: Reason if delayed
  - `last_updated`: Timestamp of last data update

### 4. Individual Train Sensors
- **Entity IDs**: `sensor.{commute_name}_train_1`, `sensor.{commute_name}_train_2`, etc.
- **Count**: Created dynamically based on your "Number of Services" configuration (1-10)
- **State**: Departure status (e.g., "On Time", "Delayed", "Cancelled", "Expected", "No service")
- **Icon**: Dynamic based on train status
  - `mdi:train-car` - Next train (Train 1) in normal service
  - `mdi:train` - Later trains in normal service
  - `mdi:train-variant` - Minor delay (1-10 minutes)
  - `mdi:clock-alert` - Significant delay (>10 minutes)
  - `mdi:alert-circle` - Cancelled
- **Attributes**: Same as Next Train Sensor, with `train_number` indicating position (1 = next train, 2 = second train, etc.)
  - Includes `departure_time` attribute showing the display time (HH:MM)
  - Includes `platform_changed` and `previous_platform` for platform change detection
- **Use Case**: Track multiple upcoming trains individually for more detailed monitoring and automations

**Note**: The Next Train Sensor mirrors Train 1 for convenience. Train sensors automatically filter out departed trains (2-minute grace period after scheduled departure).

### 5. Has Disruption Binary Sensor
- **Entity ID**: `binary_sensor.{commute_name}_has_disruption`
- **State**: "on" when disruption detected, "off" when services are normal
- **Icon**: Dynamic based on disruption status
  - `mdi:alert-circle` - When on (disruption detected)
  - `mdi:check-circle` - When off (normal service)
- **Attributes**:
  - `current_status`: Current overall status (Normal, Minor Delays, Major Delays, Severe Disruption, or Critical)
  - `cancelled_count`: Total count of cancelled trains
  - `delayed_count`: Total count of delayed trains (all delays, not just severe)
  - `max_delay_minutes`: Maximum delay in minutes across all trains
  - `disruption_reasons`: List of reasons for disruptions (cancellations and delays)
  - `last_checked`: Timestamp of last update
- **Trigger Logic**: Binary sensor is "on" when status is anything other than Normal

## Prerequisites

### National Rail API Key

You'll need a free API key from the Rail Data Marketplace:

1. Visit [Rail Data Marketplace](https://raildata.org.uk/)
2. Create a free account
3. Navigate to the [Live Departure Boards API](https://raildata.org.uk/dataProduct/P-d81d6eaf-8060-4467-a339-1c833e50cbbe/overview)
4. Subscribe to the API (it's free)
5. Copy your API key

### Station CRS Codes

You'll need the 3-letter CRS (Computer Reservation System) codes for your stations:
- **PAD** = London Paddington
- **RDG** = Reading
- **MAN** = Manchester Piccadilly
- **BHM** = Birmingham New Street

Find your station codes at [National Rail Enquiries](https://www.nationalrail.co.uk/stations/).

## Installation

### HACS Installation (Recommended)

1. Open HACS in Home Assistant
2. Click on "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add `https://github.com/adamf83/my-rail-commute` as repository
6. Select "Integration" as category
7. Click "Add"
8. Find "My Rail Commute" in the integration list
9. Click "Download"
10. Restart Home Assistant

### Manual Installation

1. Download the latest release from [GitHub](https://github.com/adamf83/my-rail-commute/releases)
2. Extract the `custom_components/my_rail_commute` directory
3. Copy it to your Home Assistant `custom_components` directory
4. Restart Home Assistant

## Configuration

### Initial Setup

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for "My Rail Commute"
4. Follow the configuration steps:

#### Step 1: API Authentication
- Enter your Rail Data Marketplace API key
- The integration will validate your credentials

#### Step 2: Route Configuration
- **Origin Station**: Enter the 3-letter CRS code (e.g., `PAD`)
- **Destination Station**: Enter the 3-letter CRS code (e.g., `RDG`)
- The integration will validate the stations and show their full names

#### Step 3: Commute Settings
- **Commute Name**: Friendly name (default: "Origin to Destination")
- **Time Window**: How many minutes ahead to look (15-120 minutes, default: 60)
- **Number of Services**: How many trains to track (1-10, default: 3)
- **Severe Disruption Threshold**: Minutes of delay to trigger "Severe Disruption" status for any train (1-60 minutes, default: 15)
- **Major Delays Threshold**: Minutes of delay to trigger "Major Delays" status for any train (1-60 minutes, default: 10)
- **Minor Delays Threshold**: Minutes of delay to trigger "Minor Delays" status for any train (1-60 minutes, default: 3)
- **Enable Night-Time Updates**: Keep polling during night hours (23:00-05:00)

**Note on Status Hierarchy**: The Status sensor uses a 5-level hierarchy based on the maximum delay across all trains:
- **"Normal"**: All trains on time (or below minor threshold)
- **"Minor Delays"**: Any train delayed ≥ Minor Delays Threshold (default 3 minutes)
- **"Major Delays"**: Any train delayed ≥ Major Delays Threshold (default 10 minutes)
- **"Severe Disruption"**: Any train delayed ≥ Severe Disruption Threshold (default 15 minutes)
- **"Critical"**: Any train cancelled (highest priority, always overrides other statuses)

All three delay thresholds are fully customizable. Validation ensures the hierarchy is maintained: Severe ≥ Major ≥ Minor ≥ 1 minute.

### Modifying Settings

1. Go to **Settings** → **Devices & Services**
2. Find your My Rail Commute integration
3. Click **Configure**
4. Adjust your settings:
   - Time window
   - Number of services to track
   - Severe Disruption Threshold
   - Major Delays Threshold
   - Minor Delays Threshold
   - Night-time updates

**Note**: When you update your configuration, any old threshold settings will automatically migrate to the new format.

### Multiple Commutes

To track multiple routes:
1. Add the integration multiple times
2. Configure different origin/destination pairs for each
3. Each commute will appear as a separate device with its own sensors

## Update Intervals

The integration automatically adjusts update frequency based on time of day:

- **Peak Hours** (06:00-10:00, 16:00-20:00): Every 2 minutes
- **Off-Peak Hours**: Every 5 minutes
- **Night Time** (23:00-05:00): Every 15 minutes (or disabled if "Enable Night-Time Updates" is off)

This smart polling reduces API usage while ensuring timely updates when you need them most.

## Automation Examples

### Simple Status Change Alert (NEW!)

Get notified when your commute status changes - no templates needed!

```yaml
automation:
  - alias: "Alert on Commute Status Change"
    trigger:
      - platform: state
        entity_id: sensor.morning_commute_status
        to:
          - "Minor Delays"
          - "Major Delays"
          - "Severe Disruption"
          - "Critical"
    action:
      - service: notify.mobile_app
        data:
          title: "Commute Status: {{ states('sensor.morning_commute_status') }}"
          message: >
            Your commute has {{ state_attr('sensor.morning_commute_status', 'minor_delays_count') + state_attr('sensor.morning_commute_status', 'major_delays_count') + state_attr('sensor.morning_commute_status', 'cancelled_count') }} affected trains.
            Max delay: {{ state_attr('sensor.morning_commute_status', 'max_delay_minutes') }} minutes.
```

### Train Status Trigger (NEW!)

Alert when a specific train becomes delayed - simple state-based trigger:

```yaml
automation:
  - alias: "Alert When Next Train Delayed"
    trigger:
      - platform: state
        entity_id: sensor.morning_commute_next_train
        to: "Delayed"
    action:
      - service: notify.mobile_app
        data:
          title: "Next Train Delayed"
          message: >
            Your next train ({{ state_attr('sensor.morning_commute_next_train', 'departure_time') }})
            is delayed by {{ state_attr('sensor.morning_commute_next_train', 'delay_minutes') }} minutes.
```

### Disruption Notification

Get a mobile notification when disruption is detected:

```yaml
automation:
  - alias: "Alert on Commute Disruption"
    trigger:
      - platform: state
        entity_id: binary_sensor.morning_commute_has_disruption
        to: "on"
    action:
      - service: notify.mobile_app
        data:
          title: "Commute Disruption!"
          message: >
            {{ state_attr('binary_sensor.morning_commute_has_disruption', 'disruption_reasons')[0] }}
          data:
            priority: high
```

### Pre-Departure Reminder

Receive a notification 10 minutes before your train departs:

```yaml
automation:
  - alias: "Next Train Departure Soon"
    trigger:
      - platform: template
        value_template: >
          {% set dep = state_attr('sensor.morning_commute_next_train', 'departure_time') %}
          {% if dep %}
            {% set dep_time = today_at(dep) %}
            {{ (dep_time - now()).total_seconds() / 60 < 10 and (dep_time - now()).total_seconds() > 0 }}
          {% else %}
            false
          {% endif %}
    condition:
      - condition: time
        weekday:
          - mon
          - tue
          - wed
          - thu
          - fri
    action:
      - service: notify.mobile_app
        data:
          title: "Train Departing Soon"
          message: >
            Your train ({{ states('sensor.morning_commute_next_train') }}) departs in 10 minutes from platform
            {{ state_attr('sensor.morning_commute_next_train', 'platform') }}
```

### Smart Departure Alert

Get notified if you should leave for the station based on delays:

```yaml
automation:
  - alias: "Time to Leave for Train"
    trigger:
      - platform: template
        value_template: >
          {% set dep = state_attr('sensor.morning_commute_next_train', 'departure_time') %}
          {% set delay = state_attr('sensor.morning_commute_next_train', 'delay_minutes') | int %}
          {% if dep %}
            {% set dep_time = today_at(dep) %}
            {% set travel_time = 20 %}
            {{ (dep_time - now()).total_seconds() / 60 < (travel_time + 5) and (dep_time - now()).total_seconds() > 0 }}
          {% else %}
            false
          {% endif %}
    action:
      - service: notify.mobile_app
        data:
          title: "Time to Leave!"
          message: >
            {% set delay = state_attr('sensor.morning_commute_next_train', 'delay_minutes') | int %}
            {% if delay > 0 %}
              Your train ({{ states('sensor.morning_commute_next_train') }}) is delayed by {{ delay }} minutes. Adjust your departure time.
            {% else %}
              Leave now to catch your train ({{ states('sensor.morning_commute_next_train') }})!
            {% endif %}
```

### Monitor Specific Train

Track a specific train in your commute (e.g., your preferred service):

```yaml
automation:
  - alias: "Alert if Preferred Train is Delayed"
    trigger:
      - platform: state
        entity_id: sensor.morning_commute_train_2
        to: "Delayed"
    action:
      - service: notify.mobile_app
        data:
          title: "Your Preferred Train is Delayed"
          message: >
            Train 2 ({{ state_attr('sensor.morning_commute_train_2', 'departure_time') }}) is delayed by
            {{ state_attr('sensor.morning_commute_train_2', 'delay_minutes') }} minutes.
            {% if state_attr('sensor.morning_commute_train_2', 'delay_reason') %}
            Reason: {{ state_attr('sensor.morning_commute_train_2', 'delay_reason') }}
            {% endif %}
```

## Lovelace Card

For a better user experience, we've created a dedicated custom Lovelace card specifically designed to display your rail commute information beautifully.

### Installation

The custom Lovelace card is available in a separate repository and can be installed via HACS:

1. Open HACS in Home Assistant
2. Go to "Frontend"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add `https://github.com/adamf83/lovelace-my-rail-commute-card` as repository
6. Select "Lovelace" as category
7. Click "Add"
8. Find "My Rail Commute Card" in the list
9. Click "Download"

For more information, documentation, and manual installation instructions, visit the [Lovelace My Rail Commute Card repository](https://github.com/adamf83/lovelace-my-rail-commute-card).

### Dashboard Card Example (Using Standard Cards)

Alternatively, you can create a commute card using standard Home Assistant cards:

```yaml
type: vertical-stack
cards:
  - type: entity
    entity: sensor.morning_commute_status
    name: Morning Commute Status

  - type: entity
    entity: sensor.morning_commute_summary
    name: Summary
    icon: mdi:train

  - type: conditional
    conditions:
      - entity: binary_sensor.morning_commute_has_disruption
        state: "on"
    card:
      type: markdown
      content: >
        **⚠️ Disruption Detected**

        {{ state_attr('binary_sensor.morning_commute_has_disruption', 'disruption_reasons') | join(', ') }}

  - type: entities
    title: Next Train
    entities:
      - entity: sensor.morning_commute_next_train
        name: Status
        secondary_info: last-changed
      - type: attribute
        entity: sensor.morning_commute_next_train
        attribute: departure_time
        name: Departure Time
      - type: attribute
        entity: sensor.morning_commute_next_train
        attribute: platform
        name: Platform
      - type: attribute
        entity: sensor.morning_commute_next_train
        attribute: operator
        name: Operator
      - type: attribute
        entity: sensor.morning_commute_next_train
        attribute: delay_minutes
        name: Delay (minutes)
```

## Troubleshooting

### Integration Not Showing Up

1. Ensure you've restarted Home Assistant after installation
2. Check the logs for any errors: **Settings** → **System** → **Logs**
3. Verify the `custom_components/my_rail_commute` directory exists

### Authentication Errors

- Double-check your API key is correct
- Ensure you're subscribed to the Live Departure Boards API on Rail Data Marketplace
- Check if your API key has expired or needs renewal

### Invalid Station Codes

- Station codes must be exactly 3 letters
- Use CRS codes, not TIPLOC or other station identifiers
- Verify codes at [National Rail Enquiries](https://www.nationalrail.co.uk/stations/)

### No Data Showing

- Check if trains actually run on your route at this time
- Verify your time window is appropriate
- Review API status at [Rail Data Marketplace Status](https://raildata.org.uk/)
- Check Home Assistant logs for API errors

### Sensors Not Updating

- Check your update interval settings
- If during night hours, ensure "Enable Night-Time Updates" is on
- Verify network connectivity to the API
- Look for rate limit errors in logs

### Rate Limiting

The integration includes automatic retry with exponential backoff. If you see rate limit errors:
- Reduce the number of configured commutes
- Increase time windows between updates
- Disable night-time updates if not needed

## API Information

This integration uses the National Rail Darwin Live Departure Boards API:

- **Provider**: Rail Delivery Group
- **API**: Darwin LDBWS (Live Departure Boards Web Service)
- **Format**: REST/JSON
- **Rate Limits**: Fair usage policy (usually no issues with default settings)
- **Terms**: [Rail Data Marketplace Terms](https://raildata.org.uk/terms)

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## Support

- **Issues**: [GitHub Issues](https://github.com/adamf83/my-rail-commute/issues)
- **Discussions**: [GitHub Discussions](https://github.com/adamf83/my-rail-commute/discussions)
- **Home Assistant Community**: [Community Forum Thread](https://community.home-assistant.io/)

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Acknowledgments

- National Rail and Rail Delivery Group for providing the Darwin API
- Rail Data Marketplace for API access
- Home Assistant community for integration development resources

## Disclaimer

This is an unofficial integration and is not affiliated with, endorsed by, or connected to National Rail, Network Rail, Rail Delivery Group, or any train operating company. Use at your own risk.

Train times and information are provided by National Rail's systems. While we strive for accuracy, always verify critical journey information through official channels.

---

**Version**: 1.1.0
**Minimum Home Assistant Version**: 2024.1.0
