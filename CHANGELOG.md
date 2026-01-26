# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-01-26

### Changed
- **Simplified Threshold Configuration**: Completely redesigned the threshold system to be more intuitive
  - Old system used separate thresholds for single/multiple delays plus a train count requirement
  - New system uses three simple time-based thresholds: Severe (15min), Major (10min), Minor (3min)
  - All three thresholds are fully customizable
  - Status hierarchy based simply on maximum delay across all trains
  - Validation ensures thresholds maintain logical order: Severe ≥ Major ≥ Minor ≥ 1 minute
- Automatic migration of existing configurations to new threshold format

### Fixed
- **delayed_count attribute**: Now correctly shows ALL delayed trains, not just those meeting severe disruption thresholds
- **cancelled_count attribute**: Now shows actual cancelled train count consistently across all sensors
- Test imports updated to use new three-tier delay threshold system
- Removed non-existent STATUS_*_DELAY_THRESHOLD constants from sensor imports

### Removed
- `disruption_type` attribute (was causing confusion)
- `affected_services` attribute (was causing confusion)

### Added
- Debug logging for data updates and sensor state calculations

## [1.0.0] - 2026-01-XX

### Added
- Initial public release
- Real-time train tracking using National Rail Darwin API
- Smart update intervals (peak/off-peak/night-time)
- Multiple sensor types:
  - Commute Summary sensor (`sensor.{commute_name}_summary`)
  - Commute Status sensor (`sensor.{commute_name}_status`) for easier automations
    - Hierarchical states: Normal, Minor Delays, Major Delays, Severe Disruption, Critical
    - Dynamic icons based on status
    - Rich attributes including delay counts and max delay minutes
  - Individual train sensors (1-10 configurable)
  - Next Train sensor for convenience (mirrors Train 1)
  - Has Disruption binary sensor with Yes/No display
- Rich sensor data including:
  - Platforms and platform changes
  - Delays and cancellation reasons
  - Calling points
  - Expected vs scheduled times
  - Platform change detection (`platform_changed` and `previous_platform` attributes)
- Disruption detection with configurable thresholds per commute
- Multi-route support for multiple commutes
- UI-based configuration through config flow
- Time window configuration (15-120 minutes)
- Configurable number of services to track (1-10)
- Night-time update toggle
- HACS compatibility
- Custom Lovelace card support via separate repository
- Automatic cleanup of stale train entities when reducing number of services

### Changed
- User-friendly labels for disruption threshold settings in translations
- Reordered configuration fields for improved user experience

### Fixed
- API endpoint structure and base URL corrections
- Config value type handling (ensure integers)
- 500 error handling improvements
- Manual refresh to always fetch fresh data
- Night-time pause using long interval instead of None
- Prevention of stale overnight data in morning updates
- Platform change detection logic improved for reliability
- Test mock data format for expected_departure and estimated_arrival
- HACS validation errors resolved
- Thread naming in Python 3.12/3.13 compatibility
- Options flow initialization in tests
- Automatic API key reuse for multiple commute routes

[1.1.0]: https://github.com/adamf83/my-rail-commute/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/adamf83/my-rail-commute/releases/tag/v1.0.0
