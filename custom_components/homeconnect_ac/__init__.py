"""Home Connect AC integration."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import HomeConnectClient

from .const import DOMAIN
from .coordinator import HomeConnectACCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE]

type HomeConnectACConfigEntry = ConfigEntry[HomeConnectACCoordinator]

# Persist rate-limit expiry to disk so it survives restarts
_RATE_LIMIT_FILE = Path("/config/.homeconnect_ac_rate_limit")


def _get_rate_limit_until() -> float:
    """Read persisted rate-limit expiry timestamp."""
    try:
        return float(_RATE_LIMIT_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0.0


def _set_rate_limit_until(until: float) -> None:
    """Persist rate-limit expiry timestamp to disk."""
    _RATE_LIMIT_FILE.write_text(str(until))
    _LOGGER.debug("Rate limit persisted until %s", time.ctime(until))


def _clear_rate_limit() -> None:
    """Remove persisted rate limit."""
    _RATE_LIMIT_FILE.unlink(missing_ok=True)


async def _persist_tokens(
    hass: HomeAssistant, entry: ConfigEntry, access_token: str, refresh_token: str
) -> None:
    """Persist refreshed tokens back to the config entry."""
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "access_token": access_token, "refresh_token": refresh_token},
    )
    _LOGGER.debug("Persisted refreshed tokens to config entry")


async def async_setup_entry(
    hass: HomeAssistant, entry: HomeConnectACConfigEntry
) -> bool:
    """Set up Home Connect AC from a config entry."""
    # Don't hit the API if we know we're rate-limited (survives restarts)
    remaining = await hass.async_add_executor_job(_get_rate_limit_until) - time.time()
    if remaining > 0:
        _LOGGER.debug("Rate limited, %ds remaining — skipping API call", int(remaining))
        raise ConfigEntryNotReady(
            f"Rate limited, {int(remaining)}s remaining"
        )

    client = HomeConnectClient(
        client_id=entry.data["client_id"],
        client_secret=entry.data.get("client_secret", ""),
        access_token=entry.data["access_token"],
        refresh_token=entry.data["refresh_token"],
        on_token_refresh=lambda at, rt: _persist_tokens(hass, entry, at, rt),
    )
    await hass.async_add_executor_job(client.sync_open)

    try:
        appliances = await client.get_appliances()
    except httpx.HTTPStatusError as err:
        await client.async_close()
        if err.response.status_code == 429:
            retry_after = int(err.response.headers.get("Retry-After", "60"))
            await hass.async_add_executor_job(_set_rate_limit_until, time.time() + retry_after)
            _LOGGER.warning(
                "Rate limited by Home Connect API (Retry-After: %ds / %dm)",
                retry_after,
                retry_after // 60,
            )
        raise ConfigEntryNotReady(f"API error {err.response.status_code}") from err
    except Exception as err:
        await client.async_close()
        raise ConfigEntryNotReady(f"Failed to connect: {err}") from err

    # Clear any stale rate limit
    await hass.async_add_executor_job(_clear_rate_limit)

    coordinator = HomeConnectACCoordinator(hass, entry, client, appliances)
    await coordinator.async_config_entry_first_refresh()

    # Start SSE listener for real-time updates
    coordinator.start_sse()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HomeConnectACConfigEntry
) -> bool:
    """Unload a config entry."""
    coordinator: HomeConnectACCoordinator = entry.runtime_data
    coordinator.stop_sse()
    await coordinator.client.async_close()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
