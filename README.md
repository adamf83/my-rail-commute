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
- **Custom Lovelace Card**: Beautiful, dedicated dashboard card for displaying train information

## Sensors

The integration creates three sensors for each configured commute:

### 1. Commute Summary Sensor
- **Entity ID**: `sensor.{commute_name}_commute_summary`
- **State**: Summary of overall commute status (e.g., "3 trains on time", "2 trains delayed")
- **Attributes**: Origin/destination names, service counts, on-time/delayed/cancelled counts

### 2. Next Train Sensor
- **Entity ID**: `sensor.{commute_name}_next_train`
- **State**: Departure time or status (e.g., "14:45", "Delayed 10 mins", "Cancelled")
- **Attributes**: Platform, operator, calling points, delay reasons, arrival times, and more

### 3. Severe Disruption Binary Sensor
- **Entity ID**: `binary_sensor.{commute_name}_severe_disruption`
- **State**: ON when disruption detected, OFF when services are normal
- **Device Class**: `problem`
- **Attributes**: Disruption type, affected services, delay information, reasons

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
- **Enable Night-Time Updates**: Keep polling during night hours (23:00-05:00)

### Modifying Settings

1. Go to **Settings** → **Devices & Services**
2. Find your My Rail Commute integration
3. Click **Configure**
4. Adjust your settings (time window, number of services, night updates)

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

### Disruption Notification

Get a mobile notification when severe disruption is detected:

```yaml
automation:
  - alias: "Alert on Commute Disruption"
    trigger:
      - platform: state
        entity_id: binary_sensor.morning_commute_severe_disruption
        to: "on"
    action:
      - service: notify.mobile_app
        data:
          title: "Commute Disruption!"
          message: >
            {{ state_attr('binary_sensor.morning_commute_severe_disruption', 'disruption_reasons')[0] }}
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
          {% set dep = state_attr('sensor.morning_commute_next_train', 'expected_departure') %}
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
            Your train departs in 10 minutes from platform
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
          {% set dep = state_attr('sensor.morning_commute_next_train', 'expected_departure') %}
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
              Your train is delayed by {{ delay }} minutes. Adjust your departure time.
            {% else %}
              Leave now to catch your train!
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
    entity: sensor.morning_commute_commute_summary
    name: Morning Commute
    icon: mdi:train

  - type: conditional
    conditions:
      - entity: binary_sensor.morning_commute_severe_disruption
        state: "on"
    card:
      type: markdown
      content: >
        **⚠️ Disruption Detected**

        {{ state_attr('binary_sensor.morning_commute_severe_disruption', 'disruption_reasons') | join(', ') }}

  - type: entities
    title: Next Train
    entities:
      - entity: sensor.morning_commute_next_train
        name: Departure
        secondary_info: last-changed
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

**Version**: 1.0.0
**Minimum Home Assistant Version**: 2024.1.0
