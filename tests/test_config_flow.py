import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.heating_curve_optimizer.const import (
    DOMAIN,
    CONF_SOURCE_TYPE,
    CONF_ENERGY_LABEL,
    CONF_EXTERNAL_FORECAST_SENSOR,
)
from custom_components.heating_curve_optimizer.config_flow import STEP_BASIC


@pytest.mark.asyncio
async def test_show_user_form(hass: HomeAssistant):
    with patch("homeassistant.config_entries._load_integration", return_value=None):
        with patch(
            "homeassistant.loader.async_get_integration",
            AsyncMock(
                return_value=SimpleNamespace(domain=DOMAIN, single_config_entry=False)
            ),
        ):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
    assert result["type"] == "form"
    assert result["step_id"] == "user"


@pytest.mark.asyncio
async def test_abort_if_configured(hass: HomeAssistant):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    with patch(
        "homeassistant.config_entries._load_integration", return_value=None
    ), patch(
        "homeassistant.loader.async_get_integration",
        AsyncMock(
            return_value=SimpleNamespace(domain=DOMAIN, single_config_entry=False)
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_basic_options_step(hass: HomeAssistant):
    with patch(
        "homeassistant.config_entries._load_integration", return_value=None
    ), patch(
        "homeassistant.loader.async_get_integration",
        AsyncMock(
            return_value=SimpleNamespace(domain=DOMAIN, single_config_entry=False)
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SOURCE_TYPE: STEP_BASIC}
        )
    assert result2["type"] == "form"
    assert result2["step_id"] == STEP_BASIC


@pytest.mark.asyncio
async def test_energy_labels_available(hass: HomeAssistant):
    with patch(
        "homeassistant.config_entries._load_integration", return_value=None
    ), patch(
        "homeassistant.loader.async_get_integration",
        AsyncMock(
            return_value=SimpleNamespace(domain=DOMAIN, single_config_entry=False)
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SOURCE_TYPE: STEP_BASIC}
        )

    energy_label_field = result2["data_schema"].schema[CONF_ENERGY_LABEL]
    assert "A+++" in energy_label_field.config["options"]


@pytest.mark.asyncio
async def test_basic_step_includes_forecast_sensor(hass: HomeAssistant):
    hass.states.async_set("sensor.has_forecast", "1", {"forecast": [1]})
    with patch(
        "homeassistant.config_entries._load_integration", return_value=None
    ), patch(
        "homeassistant.loader.async_get_integration",
        AsyncMock(
            return_value=SimpleNamespace(domain=DOMAIN, single_config_entry=False)
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SOURCE_TYPE: STEP_BASIC}
        )
    forecast_field = result2["data_schema"].schema[CONF_EXTERNAL_FORECAST_SENSOR]
    assert "sensor.has_forecast" in forecast_field.config["options"]
