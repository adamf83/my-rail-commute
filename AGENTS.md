# AGENTS.md

This file helps AI agents understand how to work effectively in this repository.

## Project Overview

**My Rail Commute** is a Home Assistant custom integration that monitors UK National Rail commutes in real-time. It fetches live train data from the National Rail Darwin API and exposes sensor entities to Home Assistant.

- Minimum Home Assistant version: 2024.1.0
- Python: 3.13+
- Installable via [HACS](https://hacs.xyz/)
- Integration domain: `my_rail_commute`

## Repository Layout

```
custom_components/my_rail_commute/   # All integration source code
tests/                               # Pytest test suite
tests/fixtures/                      # JSON API response fixtures
.github/workflows/                   # CI (tests, HACS, hassfest, release)
pyproject.toml                       # Python project config + ruff config
requirements_test.txt                # Test dependencies
```

### Key Source Files

| File | Purpose |
|------|---------|
| `const.py` | All constants, config keys, thresholds, status levels — add new constants here |
| `api.py` | National Rail API client (auth, rate limiting, retry, response parsing) |
| `coordinator.py` | `DataUpdateCoordinator` subclass; fetches data, calculates disruption status, manages dynamic update intervals |
| `config_flow.py` | Multi-step UI config wizard and options flow |
| `sensor.py` | All sensor entities (summary, status, next train, per-train, historical) |
| `binary_sensor.py` | Disruption detection binary sensor |
| `statistics.py` | Persistent daily stats via Home Assistant's `Store` interface |
| `__init__.py` | Entry setup/teardown, platform loading, service registration |
| `manifest.json` | Integration metadata (version, dependencies) |
| `station_data.json` | Bundled UK station CRS codes and coordinates |

## Development Setup

```bash
python -m venv venv
source venv/bin/activate
pip install homeassistant
pip install -r requirements_test.txt
```

## Running Tests

```bash
pytest --cov=custom_components.my_rail_commute --cov-report=term-missing
```

The CI enforces a **40% minimum coverage** threshold. Run the full suite before committing. To target a specific file:

```bash
pytest tests/test_api.py
```

Fixtures for mock API responses live in `tests/fixtures/` (JSON files: `on_time`, `delayed`, `cancelled`, `through_service`). The `tests/conftest.py` provides shared fixtures for the coordinator, API session, and mocked data — reuse these rather than creating duplicates.

## Linting & Formatting

```bash
ruff check .        # lint
ruff format .       # format
```

Configuration is in `pyproject.toml`. Key settings:
- Line length: **88 characters**
- Enabled rule sets: `E`, `W`, `F`, `I` (isort), `UP` (pyupgrade), `B` (bugbear)
- `E501` is ignored (line length enforced by formatter, not linter)

Run both `ruff check` and `ruff format` before committing.

## Architecture & Key Patterns

### Data Flow

```
Config Flow (user input)
    → Coordinator (fetches API, calculates status)
    → Statistics Store (persists daily metrics)
    → Sensor / BinarySensor entities (expose state + attributes to HA)
```

### Coordinator Pattern

All entities inherit from `CoordinatorEntity[NationalRailDataUpdateCoordinator]`. The coordinator fetches data once per interval; entities read from `self.coordinator.data`. Do not make API calls directly from entities.

### Dynamic Update Intervals

The coordinator adjusts its poll interval based on time of day:
- Peak hours: 2 minutes
- Off-peak: 5 minutes
- Night: 15 minutes

### Status Hierarchy

Five levels used throughout: `Normal → Minor → Major → Severe → Critical`. Thresholds (in minutes) are configurable per-entry and stored as config options.

### Configuration Keys

All config entry keys are constants defined in `const.py`. Effective config is always merged from `entry.data` and `entry.options` — never read raw dict keys as strings.

### Dynamic Sensor Count

Each commute entry creates 1–10 individual train sensors (configurable). When adding new sensor types, follow the pattern in `sensor.py` where sensors are built by iterating over the coordinator's service list.

### Error Handling

The API client raises typed exceptions (`AuthenticationError`, `InvalidStationError`, `RateLimitError`, `NationalRailAPIError`). Catch specific types; do not swallow `Exception` broadly.

## CI Checks

All four workflows must pass before merging:

| Workflow | What it checks |
|----------|---------------|
| `tests.yaml` | pytest suite + Codecov upload |
| `hacs.yaml` | HACS repository structure validation |
| `hassfest.yaml` | Home Assistant manifest/integration validation |
| `release.yaml` | Release artifact build |

## Commit Convention

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat(sensor): add new sensor type
fix(api): handle timeout on departure board request
test(coordinator): add update interval edge cases
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

## Further Reading

- `CONTRIBUTING.md` — full contribution guide, PR process, manual testing checklist
- `README.md` — user-facing docs, sensor reference, automation examples, troubleshooting
