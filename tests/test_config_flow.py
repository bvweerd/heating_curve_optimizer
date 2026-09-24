import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.heating_curve_optimizer.const import (
    DOMAIN,
    CONF_SOURCE_TYPE,
    CONF_SOURCES,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_PRODUCTION_PRICE_SENSOR,
    SOURCE_TYPE_CONSUMPTION,
)
from custom_components.heating_curve_optimizer.companion_integrations import (
    BATTERY_CONTROLLER_DOMAIN,
    FIELD_SOURCES_CONSUMPTION,
    DetectedSensorList,
)
from custom_components.heating_curve_optimizer.config_flow import (
    HeatingCurveOptimizerConfigFlow,
    _test_api_connection,
)


def _mock_hass_integration():
    """Context manager pair mocking the integration lookup HA's flow
    manager does on async_init - same pattern test_show_user_form and
    test_abort_if_configured already use."""
    return patch(
        "homeassistant.config_entries._load_integration", return_value=None
    ), patch(
        "homeassistant.loader.async_get_integration",
        AsyncMock(
            return_value=SimpleNamespace(domain=DOMAIN, single_config_entry=False)
        ),
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
    """An entry created by this flow always carries unique_id=DOMAIN (see
    async_step_user's async_set_unique_id call), so a second attempt must
    be caught by _abort_if_unique_id_configured()."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, unique_id=DOMAIN)
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
async def test_shows_detected_integrations_step_when_battery_controller_configured(
    hass: HomeAssistant,
):
    """companion_integrations.py: a battery_controller entry with a raw
    price sensor is enough to trigger the (opt-in) prefill step, instead of
    going straight to the normal form."""
    bc_entry = MockConfigEntry(
        domain=BATTERY_CONTROLLER_DOMAIN,
        data={"price_sensor": "sensor.raw_price"},
    )
    bc_entry.add_to_hass(hass)
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
    assert result["type"] == "form"
    assert result["step_id"] == "detected_integrations"
    assert CONF_CONSUMPTION_PRICE_SENSOR in result["data_schema"].schema


@pytest.mark.asyncio
async def test_detected_integrations_step_accepts_source_list_field(
    hass: HomeAssistant,
):
    """Accepting a detected `sources_consumption` list (Part A of the
    config-flow modernization: companion_integrations.detect_source_sensors)
    must populate self.configs directly, the same shape
    _build_configs_from_sources produces."""
    flow = HeatingCurveOptimizerConfigFlow()
    flow.hass = hass
    flow._detected_sensor_lists = {
        FIELD_SOURCES_CONSUMPTION: DetectedSensorList(
            ["sensor.elec_consumption"], "Battery Controller"
        )
    }

    result = await flow.async_step_detected_integrations(
        {FIELD_SOURCES_CONSUMPTION: True}
    )

    assert result["step_id"] == "user"
    assert flow.configs == [
        {
            CONF_SOURCE_TYPE: SOURCE_TYPE_CONSUMPTION,
            CONF_SOURCES: ["sensor.elec_consumption"],
        }
    ]


@pytest.mark.asyncio
async def test_detected_integrations_step_replaces_existing_block_for_same_source_type(
    hass: HomeAssistant,
):
    """Accepting a detected source list must replace any existing block for
    that source_type (matching replace-not-duplicate behaviour), not append
    a second one."""
    flow = HeatingCurveOptimizerConfigFlow()
    flow.hass = hass
    flow.configs = [
        {CONF_SOURCE_TYPE: SOURCE_TYPE_CONSUMPTION, CONF_SOURCES: ["sensor.old"]}
    ]
    flow._detected_sensor_lists = {
        FIELD_SOURCES_CONSUMPTION: DetectedSensorList(
            ["sensor.new"], "Battery Controller"
        )
    }

    await flow.async_step_detected_integrations({FIELD_SOURCES_CONSUMPTION: True})

    assert flow.configs == [
        {CONF_SOURCE_TYPE: SOURCE_TYPE_CONSUMPTION, CONF_SOURCES: ["sensor.new"]}
    ]


@pytest.mark.asyncio
async def test_test_api_connection_success(hass: HomeAssistant):
    """quality_scale's test-before-configure rule: open-meteo.com
    reachability is checked (ported from battery_controller's
    _test_api_connection) before the entry is created."""
    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.__aenter__.return_value = mock_response
    mock_response.__aexit__.return_value = None

    mock_session = MagicMock()
    mock_session.get.return_value = mock_response

    with patch(
        "custom_components.heating_curve_optimizer.config_flow.async_get_clientsession",
        return_value=mock_session,
    ):
        error = await _test_api_connection(hass)

    assert error is None


@pytest.mark.asyncio
async def test_test_api_connection_bad_status(hass: HomeAssistant):
    mock_response = AsyncMock()
    mock_response.status = 500
    mock_response.__aenter__.return_value = mock_response
    mock_response.__aexit__.return_value = None

    mock_session = MagicMock()
    mock_session.get.return_value = mock_response

    with patch(
        "custom_components.heating_curve_optimizer.config_flow.async_get_clientsession",
        return_value=mock_session,
    ):
        error = await _test_api_connection(hass)

    assert error == "cannot_connect"


@pytest.mark.asyncio
async def test_test_api_connection_client_error(hass: HomeAssistant):
    import aiohttp

    mock_session = MagicMock()
    mock_session.get.side_effect = aiohttp.ClientError("boom")

    with patch(
        "custom_components.heating_curve_optimizer.config_flow.async_get_clientsession",
        return_value=mock_session,
    ):
        error = await _test_api_connection(hass)

    assert error == "cannot_connect"


@pytest.mark.asyncio
async def test_user_step_shows_cannot_connect_error_on_api_failure(
    hass: HomeAssistant,
):
    """Submitting the sectioned form with a price sensor but failing the
    API check must re-show the form with a cannot_connect error.

    The handler is called directly (bypassing HA's schema-validation layer)
    because the sectioned schema's selector validators are not compatible
    with plain string values in the test environment's pinned HA version."""
    flow = HeatingCurveOptimizerConfigFlow()
    flow.hass = hass

    user_input = {
        "building": {
            CONF_CONSUMPTION_PRICE_SENSOR: "sensor.price",
            CONF_PRODUCTION_PRICE_SENSOR: "sensor.price",
        },
        "sensors": {
            FIELD_SOURCES_CONSUMPTION: ["sensor.power"],
        },
        "heat_pump_and_curve": {},
        "advanced": {},
    }

    with patch(
        "custom_components.heating_curve_optimizer.config_flow._test_api_connection",
        new=AsyncMock(return_value="cannot_connect"),
    ):
        result = await flow.async_step_user(user_input)

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_user_step_creates_entry_when_api_reachable(hass: HomeAssistant):
    """Submitting the sectioned form with valid sources and a reachable API
    creates the entry. No zone-specific field (area_m2 etc.) is required or
    present on the main entry - every zone, including the first, is
    configured via a subentry instead.

    The handler is called directly (bypassing HA's schema-validation layer)
    because the sectioned schema's selector validators are not compatible
    with plain string values in the test environment's pinned HA version."""
    flow = HeatingCurveOptimizerConfigFlow()
    flow.hass = hass

    user_input = {
        "building": {
            CONF_CONSUMPTION_PRICE_SENSOR: "sensor.price",
            CONF_PRODUCTION_PRICE_SENSOR: "sensor.price",
        },
        "sensors": {
            FIELD_SOURCES_CONSUMPTION: ["sensor.power"],
        },
        "heat_pump_and_curve": {},
        "advanced": {},
    }

    with patch(
        "custom_components.heating_curve_optimizer.config_flow._test_api_connection",
        new=AsyncMock(return_value=None),
    ):
        result = await flow.async_step_user(user_input)

    assert result["type"] == "create_entry"
    assert "area_m2" not in result["data"]
    assert "energy_label" not in result["data"]


@pytest.mark.asyncio
async def test_user_step_no_sources_shows_no_blocks_error(hass: HomeAssistant):
    """Submitting the form without any consumption/production sources must
    re-show the form with a no_blocks error.

    The handler is called directly (bypassing HA's schema-validation layer)
    because the sectioned schema's selector validators are not compatible
    with plain string values in the test environment's pinned HA version."""
    flow = HeatingCurveOptimizerConfigFlow()
    flow.hass = hass

    user_input = {
        "building": {
            CONF_CONSUMPTION_PRICE_SENSOR: "sensor.price",
            CONF_PRODUCTION_PRICE_SENSOR: "sensor.price",
        },
        "sensors": {},
        "heat_pump_and_curve": {},
        "advanced": {},
    }

    result = await flow.async_step_user(user_input)

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_blocks"}


@pytest.mark.asyncio
async def test_options_flow_shows_init_form(hass: HomeAssistant):
    """quality_scale's config-flow-test-coverage rule: the options flow
    (Settings > Devices & Services > Configure) shows the single-page
    sectioned form with step_id=init."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    ctx1, ctx2 = _mock_hass_integration()
    with ctx1, ctx2:
        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == "form"
    assert result["step_id"] == "init"


@pytest.mark.asyncio
async def test_options_flow_no_sources_shows_no_blocks_error(hass: HomeAssistant):
    """Submitting the options form without any consumption/production sources
    must re-show the form with a no_blocks error.

    The handler is called directly (bypassing HA's schema-validation layer)
    because the sectioned schema's selector validators are not compatible
    with plain string values in the test environment's pinned HA version.
    The handler itself is the unit under test here, not the schema."""
    from custom_components.heating_curve_optimizer.config_flow import (
        HeatingCurveOptimizerOptionsFlowHandler,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    handler = HeatingCurveOptimizerOptionsFlowHandler(entry)
    handler.hass = hass

    result = await handler.async_step_init(
        {
            "building": {
                CONF_CONSUMPTION_PRICE_SENSOR: "sensor.price",
                CONF_PRODUCTION_PRICE_SENSOR: "sensor.price",
            },
            "sensors": {},
            "heat_pump_and_curve": {},
            "advanced": {},
        }
    )

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    assert result["errors"] == {"base": "no_blocks"}


@pytest.mark.asyncio
async def test_options_flow_creates_entry_with_valid_input(hass: HomeAssistant):
    """Submitting the options form with valid sources creates the entry
    without zone-specific fields.

    The handler is called directly (bypassing HA's schema-validation layer)
    because the sectioned schema's selector validators are not compatible
    with plain string values in the test environment's pinned HA version.
    The handler itself is the unit under test here, not the schema."""
    from custom_components.heating_curve_optimizer.config_flow import (
        HeatingCurveOptimizerOptionsFlowHandler,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CONSUMPTION_PRICE_SENSOR: "sensor.price",
            "configurations": [
                {
                    CONF_SOURCE_TYPE: SOURCE_TYPE_CONSUMPTION,
                    CONF_SOURCES: ["sensor.power"],
                }
            ],
        },
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    handler = HeatingCurveOptimizerOptionsFlowHandler(entry)
    handler.hass = hass

    result = await handler.async_step_init(
        {
            "building": {
                CONF_CONSUMPTION_PRICE_SENSOR: "sensor.price",
                CONF_PRODUCTION_PRICE_SENSOR: "sensor.price",
            },
            "sensors": {
                FIELD_SOURCES_CONSUMPTION: ["sensor.power"],
            },
            "heat_pump_and_curve": {},
            "advanced": {},
        }
    )

    assert result["type"] == "create_entry"
    assert "area_m2" not in result["data"]
    assert "energy_label" not in result["data"]


# thermal_mass_class/emitter_type coverage is in test_zone_subentry.py -
# they're collected per zone (HeatingZoneSubentryFlow), not on the main entry.
