import pytest
from homeassistant.helpers.device_registry import DeviceInfo
from unittest.mock import patch

from custom_components.heating_curve_optimizer.number import HeatingCurveOffsetNumber


@pytest.mark.asyncio
async def test_number_initial_value_and_set(hass):
    number = HeatingCurveOffsetNumber(
        unique_id="offset_number",
        device=DeviceInfo(identifiers={("test", "1")}),
    )
    assert number.native_value == 0.0

    number.hass = hass
    with patch.object(number, "async_write_ha_state") as write_state:
        await number.async_set_native_value(2)
        assert number.native_value == 2
        assert write_state.called
