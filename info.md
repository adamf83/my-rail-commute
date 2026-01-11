# My Rail Commute

A custom Home Assistant integration that tracks regular commutes using National Rail real-time data from the Darwin API. Monitor train services, get disruption alerts, and automate your commuting routine.

## Features

- **Real-time Train Tracking**: Monitor upcoming train services between any two UK rail stations
- **Smart Update Intervals**: Automatically adjusts polling frequency based on time of day (peak/off-peak/night)
- **Disruption Detection**: Binary sensor that alerts on cancellations or significant delays
- **Rich Sensor Data**: Comprehensive attributes including platforms, delays, calling points, and more
- **Multi-Route Support**: Configure multiple commutes (e.g., morning and evening journeys)
- **UI Configuration**: Easy setup through Home Assistant's config flow interface

## Sensors

The integration creates three sensors for each configured commute:

1. **Commute Summary Sensor** - Overview of all tracked services
2. **Next Train Sensor** - Detailed information about the next departure
3. **Severe Disruption Binary Sensor** - Alerts when disruption is detected

## Prerequisites

### National Rail API Key

You'll need a free API key from the Rail Data Marketplace:

1. Visit [Rail Data Marketplace](https://raildata.org.uk/)
2. Create a free account
3. Navigate to the [Live Departure Boards API](https://raildata.org.uk/dataProduct/P-d81d6eaf-8060-4467-a339-1c833e50cbbe/overview)
4. Subscribe to the API (it's free)
5. Copy your API key

### Station CRS Codes

You'll need the 3-letter CRS (Computer Reservation System) codes for your stations. Find your station codes at [National Rail Enquiries](https://www.nationalrail.co.uk/stations/).

Examples:
- **PAD** = London Paddington
- **RDG** = Reading
- **MAN** = Manchester Piccadilly
- **BHM** = Birmingham New Street

## Configuration

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for "My Rail Commute"
4. Follow the configuration steps:
   - Enter your Rail Data Marketplace API key
   - Enter origin and destination station CRS codes
   - Configure commute settings (name, time window, number of services)

## Update Intervals

The integration automatically adjusts update frequency:

- **Peak Hours** (06:00-10:00, 16:00-20:00): Every 2 minutes
- **Off-Peak Hours**: Every 5 minutes
- **Night Time** (23:00-05:00): Every 15 minutes (or disabled if night-time updates are off)

## Support

For issues, questions, or feature requests, please visit the [GitHub repository](https://github.com/adamf83/my-rail-commute).
