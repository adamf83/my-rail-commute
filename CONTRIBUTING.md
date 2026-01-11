# Contributing to My Rail Commute Integration

Thank you for your interest in contributing to the My Rail Commute integration for Home Assistant! This document provides guidelines and information for contributors.

## Code of Conduct

By participating in this project, you agree to maintain a respectful and collaborative environment. Be kind, considerate, and constructive in all interactions.

## How to Contribute

### Reporting Bugs

Before creating a bug report:
1. Check the [existing issues](https://github.com/adamf83/my-rail-commute/issues) to avoid duplicates
2. Verify you're running the latest version of the integration
3. Review the [README troubleshooting section](README.md#troubleshooting)

When reporting bugs, include:
- Home Assistant version
- Integration version
- Detailed description of the problem
- Steps to reproduce
- Relevant logs (enable debug logging)
- Configuration details (redact sensitive data)

### Suggesting Features

Feature requests are welcome! Please:
1. Check if the feature has already been requested
2. Clearly describe the use case and benefits
3. Explain how it fits within the integration's scope
4. Be open to discussion and alternative approaches

### Submitting Pull Requests

1. Fork the repository
2. Create a new branch for your feature/fix: `git checkout -b feature/my-feature`
3. Make your changes following the code style guidelines
4. Test your changes thoroughly
5. Commit with clear, descriptive messages
6. Push to your fork: `git push origin feature/my-feature`
7. Open a pull request with a detailed description

## Development Setup

### Prerequisites

- Python 3.11 or higher
- Home Assistant development environment
- Git
- A National Rail API key for testing

### Setting Up Development Environment

1. Clone the repository:
   ```bash
   git clone https://github.com/adamf83/my-rail-commute.git
   cd my-rail-commute
   ```

2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. Install Home Assistant:
   ```bash
   pip install homeassistant
   ```

4. Create a test configuration directory:
   ```bash
   mkdir config
   ```

5. Symlink the integration:
   ```bash
   ln -s $(pwd)/custom_components/my_rail_commute config/custom_components/my_rail_commute
   ```

6. Run Home Assistant:
   ```bash
   hass -c config
   ```

### Testing Your Changes

1. Add the integration through the UI
2. Configure a test commute
3. Verify sensors are created and updating
4. Check logs for errors or warnings
5. Test edge cases (no trains, cancellations, delays)
6. Validate config flow and options flow

### Debug Logging

Enable debug logging in `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.my_rail_commute: debug
```

## Code Style Guidelines

### Python Style

- Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/)
- Use [Black](https://github.com/psf/black) for code formatting
- Use [isort](https://pycqa.github.io/isort/) for import sorting
- Use type hints for all function parameters and returns
- Maximum line length: 88 characters (Black default)

### Naming Conventions

- Classes: `PascalCase`
- Functions/methods: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private methods: `_leading_underscore`

### Documentation

- All public functions/classes must have docstrings
- Use Google-style docstrings
- Include type information in docstrings
- Keep comments concise and relevant

Example:
```python
async def get_departure_board(
    self,
    origin_crs: str,
    destination_crs: str,
    time_window: int = 60,
) -> dict[str, Any]:
    """Get departure board for a route.

    Args:
        origin_crs: Origin station CRS code (3 letters)
        destination_crs: Destination station CRS code (3 letters)
        time_window: Time window in minutes

    Returns:
        Departure board data with services

    Raises:
        InvalidStationError: If station codes are invalid
        NationalRailAPIError: For other API errors
    """
```

### Code Organization

- Keep functions focused and single-purpose
- Limit function length to ~50 lines when possible
- Extract complex logic into separate methods
- Use meaningful variable names
- Avoid deep nesting (max 3-4 levels)

## Integration Architecture

### File Structure

- `__init__.py` - Integration setup and entry management
- `config_flow.py` - UI configuration flow
- `const.py` - Constants and configuration keys
- `coordinator.py` - Data update coordinator
- `api.py` - National Rail API client
- `sensor.py` - Sensor entities
- `binary_sensor.py` - Binary sensor entities
- `strings.json` - UI text strings
- `manifest.json` - Integration metadata

### Key Components

#### API Client (`api.py`)
- Handles all HTTP communication with National Rail API
- Implements retry logic with exponential backoff
- Validates station codes and API keys
- Parses API responses into standardized format

#### Data Coordinator (`coordinator.py`)
- Manages data fetching and caching
- Implements dynamic update intervals
- Calculates disruption status
- Handles error states gracefully

#### Config Flow (`config_flow.py`)
- Multi-step configuration wizard
- Validates user input
- Handles API key authentication
- Supports options flow for updates

#### Entities (`sensor.py`, `binary_sensor.py`)
- Inherit from `CoordinatorEntity`
- Implement proper device info
- Provide rich attributes
- Handle unavailable states

## Testing Guidelines

### Manual Testing Checklist

- [ ] Config flow completes successfully
- [ ] Invalid API keys are rejected
- [ ] Invalid station codes are rejected
- [ ] Same origin/destination is rejected
- [ ] Sensors appear with correct entity IDs
- [ ] Sensor states update correctly
- [ ] Attributes contain expected data
- [ ] Disruption sensor triggers appropriately
- [ ] Options flow allows configuration changes
- [ ] Multiple integrations can coexist
- [ ] Integration handles API errors gracefully
- [ ] Night mode respects settings
- [ ] Update intervals change based on time
- [ ] Unloading integration cleans up properly

### Edge Cases to Test

1. **No trains found**: Route with no services
2. **All trains cancelled**: Severe disruption
3. **Mixed statuses**: Some on time, some delayed
4. **API unavailable**: Network errors, timeouts
5. **Rate limiting**: Excessive requests
6. **Station validation**: Invalid codes, missing stations
7. **Time boundaries**: Peak/off-peak/night transitions

## Commit Message Guidelines

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): description

[optional body]

[optional footer]
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Test additions or changes
- `chore`: Maintenance tasks

Examples:
```
feat(sensor): add individual train sensors
fix(api): handle timeout errors correctly
docs(readme): add automation examples
```

## Pull Request Process

1. Update documentation if needed
2. Ensure all changes are tested
3. Update version number if applicable
4. Fill out the PR template completely
5. Wait for review and address feedback
6. Squash commits if requested
7. Ensure CI checks pass (if implemented)

## Version Numbering

This project follows [Semantic Versioning](https://semver.org/):

- **MAJOR**: Breaking changes
- **MINOR**: New features (backwards compatible)
- **PATCH**: Bug fixes (backwards compatible)

## Questions?

If you have questions about contributing:
- Open a [GitHub Discussion](https://github.com/adamf83/my-rail-commute/discussions)
- Check existing issues and PRs
- Review this document and the README

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing to My Rail Commute! Your efforts help make this integration better for everyone.
