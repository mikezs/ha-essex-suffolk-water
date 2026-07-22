"""Config flow for the Essex & Suffolk Water integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from eswater import ApiError, ESWaterClient, InvalidAuth, ServiceUnavailable
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_EMAIL, CONF_PASSWORD, DOMAIN

_LOGGER = logging.getLogger(__name__)

_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)
_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class EswConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Essex & Suffolk Water."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial credentials step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL]
            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()
            errors = await self._try_auth(email, user_input[CONF_PASSWORD])
            if not errors:
                return self.async_create_entry(title=email, data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when the stored credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Prompt for a fresh password and update the entry."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        email = reauth_entry.data[CONF_EMAIL]
        if user_input is not None:
            errors = await self._try_auth(email, user_input[CONF_PASSWORD])
            if not errors:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data={**reauth_entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_REAUTH_SCHEMA,
            description_placeholders={"email": email},
            errors=errors,
        )

    async def _try_auth(self, email: str, password: str) -> dict[str, str]:
        """Validate credentials, mapping library errors to form error keys."""
        client = ESWaterClient(async_get_clientsession(self.hass), email, password)
        try:
            await client.authenticate()
        except InvalidAuth:
            return {"base": "invalid_auth"}
        except (ServiceUnavailable, ApiError):
            return {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error validating ESW credentials")
            return {"base": "unknown"}
        return {}
