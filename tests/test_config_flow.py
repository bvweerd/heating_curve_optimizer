import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.heating_curve_optimizer.const import (
    DOMAIN,
    CONF_SOURCE_TYPE,
    CONF_ENERGY_LABEL,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_PRODUCTION_PRICE_SENSOR,
)
from custom_components.heating_curve_optimizer.config_flow import (
    STEP_BASIC,
    STEP_PRICE_SETTINGS,
)


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
async def test_price_settings_step_includes_consumption_and_production(hass):
    hass.states.async_set(
        "sensor.price_consumption",
        "0.1",
        {"device_class": "monetary"},
    )
    hass.states.async_set(
        "sensor.price_production",
        "0.2",
        {"unit_of_measurement": "€/kWh"},
    )
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
            result["flow_id"], {CONF_SOURCE_TYPE: STEP_PRICE_SETTINGS}
        )

    assert result2["type"] == "form"
    schema = result2["data_schema"].schema
    assert CONF_CONSUMPTION_PRICE_SENSOR in schema
    assert CONF_PRODUCTION_PRICE_SENSOR in schema
    options = schema[CONF_CONSUMPTION_PRICE_SENSOR].config["options"]
    assert "sensor.price_consumption" in options
    assert "sensor.price_production" in options
