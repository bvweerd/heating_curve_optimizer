"""Shared pytest fixtures for Heating Curve Optimizer tests."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.heating_curve_optimizer.const import DOMAIN


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    return enable_custom_integrations


@pytest.fixture
def basic_config() -> dict:
    """Minimal valid main-entry config (no zone subentry required)."""
    return {
        "consumption_price_sensor": "sensor.price_consumption",
        "k_factor": 0.025,
        "base_cop": 3.5,
        "cop_compensation_factor": 1.0,
        "outdoor_temp_coefficient": 0.025,
    }


@pytest.fixture
def mock_config_entry(basic_config) -> MockConfigEntry:
    """A MockConfigEntry for the integration with no subentries."""
    return MockConfigEntry(
        domain=DOMAIN,
        data=basic_config,
        entry_id="test_entry_id",
        title="Heating Curve Optimizer",
    )
