# Contributing to Heating Curve Optimizer

Thank you for your interest in contributing! This guide will help you get started.

## Getting Started

### Prerequisites

- Python 3.12+
- Home Assistant development environment
- Git
- pytest for testing

### Development Setup

1. **Fork the repository**
   ```bash
   git clone https://github.com/YOUR_USERNAME/heating_curve_optimizer.git
   cd heating_curve_optimizer
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install -e .
   ```

3. **Install pre-commit hooks**
   ```bash
   pre-commit install
   ```

## Project Structure

```
heating_curve_optimizer/
├── custom_components/
│   └── heating_curve_optimizer/
│       ├── __init__.py           # Integration entry
│       ├── sensor.py             # 17 sensor implementations (2986 lines)
│       ├── config_flow.py        # UI configuration (935 lines)
│       ├── number.py             # Manual control entities
│       ├── binary_sensor.py      # Heat demand sensor
│       └── const.py              # Constants and defaults
├── tests/                        # 18 test modules
├── docs/                         # Documentation (MkDocs)
└── .github/workflows/            # CI/CD pipelines
```

## Development Workflow

### 1. Create a Branch

```bash
git checkout -b feature/your-feature-name
```

### 2. Make Changes

- Follow existing code style
- Add tests for new features
- Update documentation

### 3. Run Tests

```bash
# Run all tests
pytest

# Run specific test
pytest tests/test_heating_curve_offset_sensor.py -v

# Run with coverage
pytest --cov=custom_components.heating_curve_optimizer
```

### 4. Pre-commit Checks

```bash
# Manual run (also runs on git commit)
pre-commit run --all-files
```

This runs:
- pyupgrade (Python syntax)
- black (formatting)
- ruff (linting)
- codespell (spell checking)

### 5. Commit Changes

```bash
git add .
git commit -m "feat: add new feature"
```

**Commit message format**:
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation only
- `test:` Adding tests
- `refactor:` Code refactoring
- `chore:` Maintenance tasks

### 6. Push and Create PR

```bash
git push origin feature/your-feature-name
```

Then create a Pull Request on GitHub.

## Code Guidelines

### Python Style

- **Black** for formatting (line length 88)
- **Type hints** required
- **Docstrings** for public methods
- **PEP 8** compliance

Example:
```python
async def async_update(self) -> None:
    """Update the sensor state."""
    try:
        # Implementation
        self._attr_native_value = calculated_value
        self._attr_available = True
    except Exception as err:
        _LOGGER.error("Update failed: %s", err)
        self._attr_available = False
```

### Testing

- **Test coverage**: Aim for >80%
- **Test patterns**: Use pytest fixtures
- **Mocking**: Mock external dependencies (API calls)

Example:
```python
@pytest.mark.asyncio
async def test_heat_loss_sensor(hass):
    """Test heat loss calculation."""
    # Setup
    entry = MockConfigEntry(...)
    sensor = HeatLossSensor(hass, entry)

    # Execute
    await sensor.async_update()

    # Assert
    assert sensor.state > 0
    assert sensor.state < 20
```

### Documentation

- Update relevant `.md` files in `docs/`
- Add docstrings to new functions/classes
- Update CLAUDE.md for significant changes

## Adding Features

### Adding a New Sensor

1. **Create sensor class** in `sensor.py`
2. **Add to setup** in `async_setup_entry()`
3. **Add translations** in `translations/en.json` and `nl.json`
4. **Write tests** in `tests/test_your_sensor.py`
5. **Update documentation** in `docs/reference/sensors.md`

### Adding Configuration Options

1. **Add constant** to `const.py`
2. **Add to config flow** in `config_flow.py`
3. **Add translations** for UI labels
4. **Add validation** if needed
5. **Update docs** in `docs/configuration.md`

### Modifying Optimization Algorithm

The core algorithm is in `sensor.py:1719-1836` (`_optimize_offsets` method).

**Important considerations**:
- Dynamic programming complexity (exponential in state dimensions)
- Test thoroughly with various scenarios
- Add tests for edge cases
- Document algorithm changes

## Pull Request Guidelines

### Before Submitting

- [ ] Tests pass locally
- [ ] Pre-commit hooks pass
- [ ] Documentation updated
- [ ] CHANGELOG.md updated (if applicable)
- [ ] No breaking changes (or clearly documented)

### PR Description Template

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
How were changes tested?

## Checklist
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] Pre-commit hooks pass
```

## Release Process

Maintainers handle releases:

1. Update version in `manifest.json` and `.bumpversion.toml`
2. Update CHANGELOG.md
3. Create GitHub release
4. GitHub Actions automatically builds and publishes

## Community Guidelines

- Be respectful and constructive
- Search existing issues before creating new ones
- Provide details in bug reports (logs, configuration, steps to reproduce)
- Be patient with maintainers (this is volunteer work)

## Questions?

- **GitHub Discussions**: For questions and ideas
- **GitHub Issues**: For bugs and feature requests
- **Pull Requests**: For code contributions

---

Thank you for contributing to making heating smarter! 🚀
