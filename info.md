# My Rail Commute

A custom Home Assistant integration that tracks regular commutes using National Rail real-time data from the Darwin API. Monitor train services, get disruption alerts, and automate your commuting routine.

## Features

- **Real-time Train Tracking**: Monitor upcoming train services between any two UK rail stations
- **Smart Update Intervals**: Automatically adjusts polling frequency based on time of day (peak/off-peak/night)
- **Disruption Detection**: Binary sensor that alerts on cancellations or significant delays
- **Rich Sensor Data**: Comprehensive attributes including platforms, delays, calling points, and more
- **Multi-Route Support**: Configure multiple commutes (e.g., morning and evening journeys)
- **UI Configuration**: Easy setup through Home Assistant's config flow interface
- **Custom Lovelace Card**: Dedicated dashboard card available at [lovelace-my-rail-commute-card](https://github.com/adamf83/lovelace-my-rail-commute-card)

## Sensors

The integration creates three sensors for each configured commute:

1. **Commute Summary Sensor** - Overview of all tracked services
2. **Next Train Sensor** - Detailed information about the next departure
3. **Severe Disruption Binary Sensor** - Alerts when disruption is detected

## Prerequisites

### Rail Data Marketplace API Key

You'll need a free **Rail Data Marketplace API key** for the **Live Departure Board** product. On Rail Data Marketplace this key is shown as the **Consumer Key**.

1. Visit [Rail Data Marketplace](https://raildata.org.uk/)
2. Create a free account
3. Navigate to the [Live Departure Boards API](https://raildata.org.uk/dataProduct/P-d81d6eaf-8060-4467-a339-1c833e50cbbe/overview)
4. Subscribe to the product (it's free). Two versions are offered; v1.1 is the one this integration is developed and tested against, so choose that
5. Open the product's **Specification** tab, which shows a **Consumer Key** and a **Consumer Secret**
6. Copy the **Consumer Key** (this is your API key) and paste it into the **API Key** field when adding the integration. You don't need the Consumer Secret

> **Wrong key?** A rejected key shows "Authentication failed. Please check your API key." in the setup dialog. Make sure you copied the Consumer Key (not the Consumer Secret) from the **Live Departure Board** product, not from another product.
>
> **"Invalid flow specified"** is a Home Assistant message, not one from this integration. It means the setup dialog was no longer valid when you submitted it (for example it sat open for a long time or Home Assistant restarted). Close the dialog and start **Add Integration** again.

### Delay Repay keys (optional)

To confirm actual arrival times for Delay Repay claims you also need free keys for the **Live Arrival Board** and **Service Details** products on Rail Data Marketplace. They are separate subscriptions from the departure board and are asked for once, when you enable Delay Repay.

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

## Automation Blueprints

Importable Home Assistant blueprints (status change, pre-departure reminder, time to leave, disruption, platform change, connection and Delay Repay alerts) are included, so common automations need no YAML. See the [README](https://github.com/adamf83/my-rail-commute#automation-blueprints) for one-click import links.

## Support

For issues, questions, or feature requests, please visit the [GitHub repository](https://github.com/adamf83/my-rail-commute).
