"""Test the entity module."""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.heating_curve_optimizer.entity import BaseUtilitySensor


@pytest.fixture
def device_info():
    """Create mock device info."""
    return DeviceInfo(identifiers={("test", "1")})


@pytest.mark.asyncio
async def test_base_utility_sensor_initialization(hass: HomeAssistant, device_info):
    """Test BaseUtilitySensor initialization."""
    sensor = BaseUtilitySensor(
        name="Test Sensor",
        unique_id="test_sensor",
        unit="kW",
        device_class="power",
        icon="mdi:test",
        visible=True,
        device=device_info,
        translation_key="test_sensor",
    )

    assert sensor.name == "Test Sensor"
    assert sensor.unique_id == "test_sensor"
    assert sensor.native_unit_of_measurement == "kW"
    assert sensor.device_class == "power"
    assert sensor.icon == "mdi:test"
    assert sensor.entity_registry_enabled_default is True


@pytest.mark.asyncio
async def test_base_utility_sensor_restore_state(hass: HomeAssistant, device_info):
    """Test BaseUtilitySensor restores previous state."""
    sensor = BaseUtilitySensor(
        name="Test Sensor",
        unique_id="test_restore_sensor",
        unit="kW",
        device_class=None,
        icon="mdi:test",
        visible=True,
        device=device_info,
    )

    # Mock restored data - use simple mock with state attribute
    mock_state = MagicMock()
    mock_state.state = "2.5"

    with patch(
        "custom_components.heating_curve_optimizer.entity.RestoreEntity.async_get_last_state",
        new=AsyncMock(return_value=mock_state),
    ):
        sensor.hass = hass
        sensor.entity_id = "sensor.test_restore_sensor"
        await sensor.async_added_to_hass()

        # Sensor should restore the value
        assert sensor._attr_native_value == 2.5


@pytest.mark.asyncio
async def test_base_utility_sensor_no_restore_data(hass: HomeAssistant, device_info):
    """Test BaseUtilitySensor when no restore data available."""
    sensor = BaseUtilitySensor(
        name="Test Sensor",
        unique_id="test_no_restore",
        unit="kW",
        device_class=None,
        icon="mdi:test",
        visible=True,
        device=device_info,
    )

    with patch(
        "custom_components.heating_curve_optimizer.entity.RestoreEntity.async_get_last_state",
        new=AsyncMock(return_value=None),
    ):
        sensor.hass = hass
        sensor.entity_id = "sensor.test_no_restore"
        await sensor.async_added_to_hass()

        # Should not crash, value stays at default 0.0
        assert sensor._attr_native_value == 0.0


@pytest.mark.asyncio
async def test_base_utility_sensor_invalid_restore_value(hass: HomeAssistant, device_info):
    """Test BaseUtilitySensor handles invalid restore value."""
    sensor = BaseUtilitySensor(
        name="Test Sensor",
        unique_id="test_invalid_restore",
        unit="kW",
        device_class=None,
        icon="mdi:test",
        visible=True,
        device=device_info,
    )

    # Mock restored data with invalid value
    mock_state = MagicMock()
    mock_state.state = "invalid"

    with patch(
        "custom_components.heating_curve_optimizer.entity.RestoreEntity.async_get_last_state",
        new=AsyncMock(return_value=mock_state),
    ):
        sensor.hass = hass
        sensor.entity_id = "sensor.test_invalid_restore"
        await sensor.async_added_to_hass()

        # Should handle gracefully, fallback to 0.0
        assert sensor._attr_native_value == 0.0


@pytest.mark.asyncio
async def test_base_utility_sensor_device_info(hass: HomeAssistant, device_info):
    """Test BaseUtilitySensor returns device info."""
    sensor = BaseUtilitySensor(
        name="Test Sensor",
        unique_id="test_device",
        unit="kW",
        device_class=None,
        icon="mdi:test",
        visible=True,
        device=device_info,
    )

    assert sensor.device_info == device_info


@pytest.mark.asyncio
async def test_base_utility_sensor_no_device_class(hass: HomeAssistant, device_info):
    """Test BaseUtilitySensor without device class."""
    sensor = BaseUtilitySensor(
        name="Test Sensor",
        unique_id="test_no_class",
        unit="kW",
        device_class=None,
        icon="mdi:test",
        visible=True,
        device=device_info,
    )

    assert sensor.device_class is None


@pytest.mark.asyncio
async def test_base_utility_sensor_invisible(hass: HomeAssistant, device_info):
    """Test BaseUtilitySensor with visible=False."""
    sensor = BaseUtilitySensor(
        name="Test Sensor",
        unique_id="test_invisible",
        unit="kW",
        device_class=None,
        icon="mdi:test",
        visible=False,
        device=device_info,
    )

    assert sensor.entity_registry_enabled_default is False


@pytest.mark.asyncio
async def test_base_utility_sensor_translation_key(hass: HomeAssistant, device_info):
    """Test BaseUtilitySensor with translation key."""
    sensor = BaseUtilitySensor(
        name="Test Sensor",
        unique_id="test_translation",
        unit="kW",
        device_class=None,
        icon="mdi:test",
        visible=True,
        device=device_info,
        translation_key="test_sensor_key",
    )

    assert sensor.translation_key == "test_sensor_key"


@pytest.mark.asyncio
async def test_base_utility_sensor_reset(hass: HomeAssistant, device_info):
    """Test BaseUtilitySensor reset method."""
    sensor = BaseUtilitySensor(
        name="Test Sensor",
        unique_id="test_reset",
        unit="kW",
        device_class=None,
        icon="mdi:test",
        visible=True,
        device=device_info,
    )
    sensor.hass = hass
    sensor.entity_id = "sensor.test_reset"

    # Set a value
    sensor.set_value(5.0)
    assert sensor._attr_native_value == 5.0

    # Reset should set to 0.0
    sensor.reset()
    assert sensor._attr_native_value == 0.0


@pytest.mark.asyncio
async def test_base_utility_sensor_set_value(hass: HomeAssistant, device_info):
    """Test BaseUtilitySensor set_value method."""
    sensor = BaseUtilitySensor(
        name="Test Sensor",
        unique_id="test_set_value",
        unit="kW",
        device_class=None,
        icon="mdi:test",
        visible=True,
        device=device_info,
    )
    sensor.hass = hass
    sensor.entity_id = "sensor.test_set_value"

    # Set a value with rounding
    sensor.set_value(3.123456789)
    assert sensor._attr_native_value == 3.12345679  # Rounded to 8 decimals


@pytest.mark.asyncio
async def test_base_utility_sensor_unavailable_marking(
    hass: HomeAssistant, device_info
):
    """Test BaseUtilitySensor _set_unavailable and _mark_available methods."""
    sensor = BaseUtilitySensor(
        name="Test Sensor",
        unique_id="test_unavailable",
        unit="kW",
        device_class=None,
        icon="mdi:test",
        visible=True,
        device=device_info,
    )

    # Initially available
    assert sensor._attr_available is True

    # Set unavailable
    sensor._set_unavailable("Test reason")
    assert sensor._attr_available is False
    assert sensor._last_unavailable_reason == "Test reason"

    # Set unavailable again with same reason (should not log again)
    sensor._set_unavailable("Test reason")
    assert sensor._attr_available is False

    # Mark available
    sensor._mark_available()
    assert sensor._attr_available is True
    assert sensor._last_unavailable_reason is None


@pytest.mark.asyncio
async def test_base_utility_sensor_async_reset(hass: HomeAssistant, device_info):
    """Test BaseUtilitySensor async_reset method."""
    sensor = BaseUtilitySensor(
        name="Test Sensor",
        unique_id="test_async_reset",
        unit="kW",
        device_class=None,
        icon="mdi:test",
        visible=True,
        device=device_info,
    )
    sensor.hass = hass
    sensor.entity_id = "sensor.test_async_reset"

    # Set a value
    sensor.set_value(7.0)
    assert sensor._attr_native_value == 7.0

    # Async reset should call reset
    await sensor.async_reset()
    assert sensor._attr_native_value == 0.0
