"""Config flow for Home Connect AC.

Authentication is performed by the companion macOS app (HomeConnectACAuth.app):
it runs the SingleKey ID login + PKCE token exchange and produces a single
base64 credential blob. The user pastes that blob here.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult

from .client import HomeConnectClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_CREDENTIALS = "credentials"

STEP_SCHEMA = vol.Schema({vol.Required(CONF_CREDENTIALS): str})

_REQUIRED_KEYS = ("client_id", "access_token", "refresh_token")


def _parse_blob(blob: str) -> dict[str, str]:
    """Decode the credential blob from the macOS auth app.

    Accepts the base64 blob (preferred) or raw JSON. Returns a dict with
    client_id, client_secret, access_token, refresh_token. Raises ValueError
    if the blob is malformed or missing required fields.
    """
    text = (blob or "").strip()
    if not text:
        raise ValueError("empty")

    data: Any = None
    # Try base64 first (what the app emits), then fall back to raw JSON.
    try:
        decoded = base64.b64decode(text, validate=True)
        data = json.loads(decoded)
    except (binascii.Error, ValueError):
        try:
            data = json.loads(text)
        except ValueError as err:
            raise ValueError("not_decodable") from err

    if not isinstance(data, dict) or any(not data.get(k) for k in _REQUIRED_KEYS):
        raise ValueError("missing_fields")

    return {
        "client_id": data["client_id"],
        "client_secret": data.get("client_secret", ""),
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
    }


class HomeConnectACConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Home Connect AC."""

    VERSION = 1
    _reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Initial setup: paste the credential blob from the auth app."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                data = _parse_blob(user_input[CONF_CREDENTIALS])
                appliances = await self._validate_credentials(data)
            except ValueError:
                errors["base"] = "invalid_code"
            except RuntimeError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during setup")
                errors["base"] = "unknown"
            else:
                ac_count = sum(
                    1 for a in appliances if a.get("type") == "AirConditioner"
                )
                title = (
                    f"Home Connect AC ({ac_count} device"
                    f"{'s' if ac_count != 1 else ''})"
                )
                return self.async_create_entry(title=title, data=data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when the refresh token dies."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-auth: paste a fresh credential blob from the auth app."""
        errors: dict[str, str] = {}

        if user_input is not None:
            assert self._reauth_entry is not None
            try:
                data = _parse_blob(user_input[CONF_CREDENTIALS])
                await self._validate_credentials(data)
            except ValueError:
                errors["base"] = "invalid_code"
            except RuntimeError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during re-auth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data={**self._reauth_entry.data, **data},
                )

        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_SCHEMA, errors=errors
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
