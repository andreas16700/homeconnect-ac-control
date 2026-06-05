"""Config flow for Home Connect AC."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult

from .client import HomeConnectClient

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("client_id"): str,
        vol.Optional("client_secret", default=""): str,
        vol.Required("access_token"): str,
        vol.Required("refresh_token"): str,
    }
)


class HomeConnectACConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Home Connect AC."""

    VERSION = 1
    _reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                appliances = await self._validate_credentials(user_input)
            except RuntimeError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = "unknown"
            else:
                ac_count = sum(
                    1 for a in appliances if a.get("type") == "AirConditioner"
                )
                title = f"Home Connect AC ({ac_count} device{'s' if ac_count != 1 else ''})"
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when tokens expire."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm re-auth with new tokens."""
        errors: dict[str, str] = {}

        if user_input is not None:
            assert self._reauth_entry is not None
            # Merge: keep client_id/secret from existing entry, update tokens
            new_data = {
                **self._reauth_entry.data,
                "access_token": user_input["access_token"],
                "refresh_token": user_input["refresh_token"],
            }
            try:
                await self._validate_credentials(new_data)
            except RuntimeError:
                errors["base"] = "invalid_auth"
            except Exception:
                _LOGGER.exception("Unexpected error during re-auth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    self._reauth_entry, data=new_data
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("access_token"): str,
                    vol.Required("refresh_token"): str,
                }
            ),
            errors=errors,
        )

    async def _validate_credentials(self, data: dict[str, Any]) -> list[dict]:
        """Validate credentials by fetching appliances. Returns the list."""
        client = HomeConnectClient(
            client_id=data["client_id"],
            client_secret=data.get("client_secret", ""),
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
        )
        await self.hass.async_add_executor_job(client.sync_open)
        try:
            return await client.get_appliances()
        finally:
            await client.async_close()
