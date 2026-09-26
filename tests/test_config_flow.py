"""Tests for the Matchday config flow."""

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.matchday.const import COMPETITIONS, CONF_COMPETITION, DOMAIN


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """Picking a competition creates one entry titled after it."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COMPETITION: "bl1"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bundesliga"
    assert result["data"] == {CONF_COMPETITION: "bl1"}
    assert result["result"].unique_id == "bl1"


async def test_configured_competition_is_not_offered_again(hass: HomeAssistant) -> None:
    """Once set up, a competition disappears from the selection."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_COMPETITION: "bl1"}
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    selector = result["data_schema"].schema[CONF_COMPETITION]
    offered = {option["value"] for option in selector.config["options"]}
    assert "bl1" not in offered
    assert offered == set(COMPETITIONS) - {"bl1"}


async def test_all_configured_aborts(hass: HomeAssistant) -> None:
    """Once every competition is set up, the flow has nothing left to offer."""
    for key in COMPETITIONS:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_COMPETITION: key}
        )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "all_configured"
