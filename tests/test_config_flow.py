"""Tests for the Essex & Suffolk Water config flow."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from custom_components.essex_suffolk_water.const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    DOMAIN,
)
from eswater import InvalidAuth, ServiceUnavailable
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

USER_INPUT = {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"}


@contextmanager
def patch_auth(side_effect: Exception | None = None) -> Iterator[AsyncMock]:
    """Patch the client used by the config flow's credential check."""
    with patch(
        "custom_components.essex_suffolk_water.config_flow.ESWaterClient"
    ) as cls:
        authenticate = AsyncMock(side_effect=side_effect)
        cls.return_value.authenticate = authenticate
        yield authenticate


async def test_user_flow_success(hass: HomeAssistant) -> None:
    """A valid login creates the config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with patch_auth(), patch(
        "custom_components.essex_suffolk_water.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "user@example.com"
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == "user@example.com"


async def test_user_flow_invalid_auth_then_recovers(hass: HomeAssistant) -> None:
    """Invalid credentials show an error, then a retry succeeds."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )

    with patch_auth(side_effect=InvalidAuth("bad")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    with patch_auth(), patch(
        "custom_components.essex_suffolk_water.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_cannot_connect(hass: HomeAssistant) -> None:
    """A ServiceUnavailable maps to the cannot_connect error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch_auth(side_effect=ServiceUnavailable("down")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_unknown_error(hass: HomeAssistant) -> None:
    """An unexpected exception maps to the unknown error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch_auth(side_effect=RuntimeError("???")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}


async def test_user_flow_already_configured(hass: HomeAssistant) -> None:
    """The same account cannot be set up twice."""
    MockConfigEntry(
        domain=DOMAIN, unique_id="user@example.com", data=USER_INPUT
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch_auth():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_flow_success(hass: HomeAssistant) -> None:
    """Reauth updates the stored password and reloads the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={CONF_EMAIL: "user@example.com", CONF_PASSWORD: "old"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch_auth(), patch(
        "custom_components.essex_suffolk_water.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new-password"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"
