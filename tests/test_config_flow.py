import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from custom_components.heating_curve_optimizer.const import DOMAIN, CONF_SOURCE_TYPE
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
