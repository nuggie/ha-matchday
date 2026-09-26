"""Config flow for Matchday: one config entry per followed competition."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import COMPETITIONS, CONF_COMPETITION, DOMAIN


class MatchdayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Let the user pick a competition to follow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if user_input is not None:
            competition = COMPETITIONS[user_input[CONF_COMPETITION]]
            await self.async_set_unique_id(competition.key)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=competition.name,
                data={CONF_COMPETITION: competition.key},
            )

        configured = {entry.unique_id for entry in self._async_current_entries()}
        options = [
            SelectOptionDict(value=competition.key, label=competition.name)
            for competition in COMPETITIONS.values()
            if competition.key not in configured
        ]
        if not options:
            return self.async_abort(reason="all_configured")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COMPETITION): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.DROPDOWN
                        )
                    )
                }
            ),
        )
