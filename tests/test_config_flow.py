import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.heating_curve_optimizer.const import (
    DOMAIN,
    CONF_AREA_M2,
    CONF_SOURCE_TYPE,
    CONF_SOURCES,
    CONF_ENERGY_LABEL,
    CONF_CONSUMPTION_PRICE_SENSOR,
    CONF_PRODUCTION_PRICE_SENSOR,
    CONF_THERMAL_MASS_CLASS,
    CONF_EMITTER_TYPE,
    SOURCE_TYPE_CONSUMPTION,
)
from custom_components.heating_curve_optimizer.companion_integrations import (
    BATTERY_CONTROLLER_DOMAIN,
    FIELD_SOURCES_CONSUMPTION,
    DetectedSensorList,
)
from custom_components.heating_curve_optimizer.config_flow import (
    STEP_BASIC,
    STEP_HEATING_CURVE_SETTINGS,
    STEP_PRICE_SETTINGS,
    STEP_SELECT_SOURCES,
    HeatingCurveOptimizerConfigFlow,
    _test_api_connection,
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
    going straight to the normal menu."""
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
async def test_detected_integrations_step_prefills_selected_field(
    hass: HomeAssistant,
):
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
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_CONSUMPTION_PRICE_SENSOR: True}
        )
        # Falls through to the normal menu after the prefill step.
        assert result2["type"] == "form"
        assert result2["step_id"] == "user"

        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], {CONF_SOURCE_TYPE: STEP_PRICE_SETTINGS}
        )
    assert result3["step_id"] == STEP_PRICE_SETTINGS
    price_schema = result3["data_schema"].schema
    default_fn = next(
        key.default for key in price_schema if key == CONF_CONSUMPTION_PRICE_SENSOR
    )
    assert default_fn() == "sensor.raw_price"


@pytest.mark.asyncio
async def test_detected_integrations_step_accepts_source_list_field(
    hass: HomeAssistant,
):
    """Accepting a detected `sources_consumption` list (Part A of the
    config-flow modernization: companion_integrations.detect_source_sensors)
    must populate self.configs directly, the same shape
    _update_source_config's step-based flow produces - not just the
    single-entity_id fields detect_main_flow_sensors already covered."""
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
    that source_type (matching _update_source_config's own
    replace-not-duplicate behaviour), not append a second one."""
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
async def test_finish_step_shows_cannot_connect_error_on_api_failure(
    hass: HomeAssistant,
):
    """The "finish" branch of async_step_user must reject a config whose
    weather API can't be reached, rather than creating a dead entry."""
    flow = HeatingCurveOptimizerConfigFlow()
    flow.hass = hass
    flow.area_m2 = 150.0
    flow.configs = [{"source_type": "consumption", "entities": ["sensor.power"]}]

    with patch(
        "custom_components.heating_curve_optimizer.config_flow._test_api_connection",
        new=AsyncMock(return_value="cannot_connect"),
    ):
        result = await flow.async_step_user({CONF_SOURCE_TYPE: "finish"})

    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_finish_step_creates_entry_when_api_reachable(hass: HomeAssistant):
    """The "finish" branch proceeds to async_create_entry once the API
    check passes, with the merged config still intact."""
    flow = HeatingCurveOptimizerConfigFlow()
    flow.hass = hass
    flow.area_m2 = 150.0
    flow.energy_label = "C"
    flow.configs = [{"source_type": "consumption", "entities": ["sensor.power"]}]

    with patch(
        "custom_components.heating_curve_optimizer.config_flow._test_api_connection",
        new=AsyncMock(return_value=None),
    ):
        result = await flow.async_step_user({CONF_SOURCE_TYPE: "finish"})

    assert result["type"] == "create_entry"
    assert result["data"][CONF_AREA_M2] == 150.0


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
async def test_options_flow_shows_user_form(hass: HomeAssistant):
    """quality_scale's config-flow-test-coverage rule: the options flow
    (Settings > Devices & Services > Configure) is a second, largely
    parallel entry point into the same schema/validation code the main
    flow uses - it needs its own coverage, not just the initial setup
    flow's."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150, "energy_label": "C"},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    ctx1, ctx2 = _mock_hass_integration()
    with ctx1, ctx2:
        result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] == "form"
    assert result["step_id"] == "user"


@pytest.mark.asyncio
async def test_options_flow_basic_step(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150, "energy_label": "C"},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    ctx1, ctx2 = _mock_hass_integration()
    with ctx1, ctx2:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SOURCE_TYPE: STEP_BASIC}
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == STEP_BASIC


@pytest.mark.asyncio
async def test_options_flow_basic_step_prefills_existing_values(hass: HomeAssistant):
    """The basic step's schema must be built `with_defaults=True` so
    editing an existing entry shows its current area/energy label rather
    than the setup wizard's blank defaults."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 175, "energy_label": "B"},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    ctx1, ctx2 = _mock_hass_integration()
    with ctx1, ctx2:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SOURCE_TYPE: STEP_BASIC}
        )

    schema = result2["data_schema"].schema
    area_field = next(k for k in schema if str(k) == CONF_AREA_M2)
    assert area_field.default() == 175


@pytest.mark.asyncio
async def test_options_flow_price_settings_step(hass: HomeAssistant):
    hass.states.async_set(
        "sensor.price_consumption", "0.1", {"device_class": "monetary"}
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150, "energy_label": "C"},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    ctx1, ctx2 = _mock_hass_integration()
    with ctx1, ctx2:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SOURCE_TYPE: STEP_PRICE_SETTINGS}
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == STEP_PRICE_SETTINGS


@pytest.mark.asyncio
async def test_options_flow_heating_curve_settings_step(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150, "energy_label": "C"},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    ctx1, ctx2 = _mock_hass_integration()
    with ctx1, ctx2:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SOURCE_TYPE: STEP_HEATING_CURVE_SETTINGS}
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == STEP_HEATING_CURVE_SETTINGS


@pytest.mark.asyncio
async def test_options_flow_select_sources_step(hass: HomeAssistant):
    hass.states.async_set("sensor.energy_total", "100", {"device_class": "energy"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"area_m2": 150, "energy_label": "C"},
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    ctx1, ctx2 = _mock_hass_integration()
    with ctx1, ctx2:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SOURCE_TYPE: "Electricity consumption"}
        )

    assert result2["type"] == "form"
    assert result2["step_id"] == STEP_SELECT_SOURCES


@pytest.mark.asyncio
async def test_options_flow_finish_creates_entry_when_api_reachable(
    hass: HomeAssistant,
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "area_m2": 150,
            "energy_label": "C",
            "configurations": [
                {"source_type": "consumption", "entities": ["sensor.power"]}
            ],
        },
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    ctx1, ctx2 = _mock_hass_integration()
    with ctx1, ctx2, patch(
        "custom_components.heating_curve_optimizer.config_flow._test_api_connection",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SOURCE_TYPE: "finish"}
        )

    assert result2["type"] == "create_entry"
    assert result2["data"][CONF_AREA_M2] == 150.0


@pytest.mark.asyncio
async def test_basic_step_includes_thermal_mass_class_and_emitter_type_selectors(
    hass: HomeAssistant,
):
    """thermal_mass_class/emitter_type must be asked for in the Basic
    Settings step, not just read from config with no UI to set them."""
    ctx1, ctx2 = _mock_hass_integration()
    with ctx1, ctx2:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_SOURCE_TYPE: STEP_BASIC}
        )

    schema = result2["data_schema"].schema
    thermal_mass_field = schema[CONF_THERMAL_MASS_CLASS]
    emitter_field = schema[CONF_EMITTER_TYPE]
    assert set(thermal_mass_field.config["options"]) == {"light", "medium", "heavy"}
    assert set(emitter_field.config["options"]) == {
        "radiator",
        "underfloor",
        "fan_coil",
    }


@pytest.mark.asyncio
async def test_apply_basic_input_sets_thermal_mass_class_and_emitter_type(
    hass: HomeAssistant,
):
    """Submitting the basic step must flow thermal_mass_class/emitter_type
    through to the created entry's data, same as area_m2/energy_label."""
    flow = HeatingCurveOptimizerConfigFlow()
    flow.hass = hass
    flow.configs = [{"source_type": "consumption", "entities": ["sensor.power"]}]

    with patch(
        "custom_components.heating_curve_optimizer.config_flow._test_api_connection",
        new=AsyncMock(return_value=None),
    ):
        await flow.async_step_basic(
            {
                CONF_AREA_M2: 150,
                CONF_ENERGY_LABEL: "C",
                CONF_THERMAL_MASS_CLASS: "heavy",
                CONF_EMITTER_TYPE: "underfloor",
            }
        )
        result = await flow.async_step_user({CONF_SOURCE_TYPE: "finish"})

    assert result["type"] == "create_entry"
    assert result["data"][CONF_THERMAL_MASS_CLASS] == "heavy"
    assert result["data"][CONF_EMITTER_TYPE] == "underfloor"


@pytest.mark.asyncio
async def test_options_flow_basic_step_prefills_thermal_mass_class_and_emitter_type(
    hass: HomeAssistant,
):
    """Editing an existing entry must show its stored thermal_mass_class/
    emitter_type as the field defaults, not the wizard's plain defaults."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "area_m2": 150,
            "energy_label": "C",
            CONF_THERMAL_MASS_CLASS: "light",
            CONF_EMITTER_TYPE: "fan_coil",
        },
        unique_id=DOMAIN,
    )
    entry.add_to_hass(hass)

    ctx1, ctx2 = _mock_hass_integration()
    with ctx1, ctx2:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result2 = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_SOURCE_TYPE: STEP_BASIC}
        )

    schema = result2["data_schema"].schema
    thermal_mass_field = next(k for k in schema if str(k) == CONF_THERMAL_MASS_CLASS)
    emitter_field = next(k for k in schema if str(k) == CONF_EMITTER_TYPE)
    assert thermal_mass_field.default() == "light"
    assert emitter_field.default() == "fan_coil"
