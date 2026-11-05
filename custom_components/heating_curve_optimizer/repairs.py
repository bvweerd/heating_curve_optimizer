"""Repair flows for the Heating Curve Optimizer integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .calibration import MODE_APPLY
from .const import CONF_CALIBRATION_MODE

if TYPE_CHECKING:
    # Home Assistant 2026.10+ validates with probatio and aliases voluptuous to it
    # at runtime, so Core's signatures expect probatio types. Older releases still
    # ship voluptuous, which is why the runtime import stays as it is.
    import probatio as vol
else:
    import voluptuous as vol

# The generic base of every release's repairs result type: 2026.10 added
# RepairsFlowResult (a subtype of this), older releases return FlowResult.
_RepairsResult = FlowResult[Any, str]


class ApplyCalibrationRepairFlow(RepairsFlow):
    """Confirm switching a zone from observing to applying its calibration."""

    def __init__(self, entry_id: str, subentry_id: str) -> None:
        """Initialize the flow."""
        self._entry_id = entry_id
        self._subentry_id = subentry_id

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> _RepairsResult:
        """Start the flow."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> _RepairsResult:
        """Apply the learned model after confirmation."""
        if user_input is not None:
            entry = self.hass.config_entries.async_get_entry(self._entry_id)
            if entry is not None and self._subentry_id in entry.subentries:
                subentry = entry.subentries[self._subentry_id]
                self.hass.config_entries.async_update_subentry(
                    entry,
                    subentry,
                    data={**subentry.data, CONF_CALIBRATION_MODE: MODE_APPLY},
                )
                self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_create_entry(data={})
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the fix flow for a fixable issue."""
    data = data or {}
    return ApplyCalibrationRepairFlow(
        str(data.get("entry_id")), str(data.get("subentry_id"))
    )
