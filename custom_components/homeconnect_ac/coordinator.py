"""DataUpdateCoordinator for Home Connect AC — SSE-driven with REST fallback."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any

import httpx

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .client import HomeConnectClient

from .const import (
    DOMAIN,
    KEY_BOOST,
    KEY_BREEZE_AWAY,
    KEY_CONNECTED,
    KEY_DISPLAY_LIGHT,
    KEY_FAN_SPEED,
    KEY_FAN_SPEED_MODE,
    KEY_FAN_SPEED_PCT,
    KEY_GEAR,
    KEY_HORIZONTAL_SWING,
    KEY_POWER,
    KEY_SETPOINT_TEMP,
    KEY_VERTICAL_FAN_DIR,
    KEY_VERTICAL_SWING,
    POWER_ON,
    SCAN_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

# Map BSH keys to our internal data keys
_KEY_MAP: dict[str, tuple[str, str]] = {
    # key -> (data_field, transform_type: "raw" | "bool" | "enum")
    KEY_POWER: ("power_on", "power"),
    KEY_CONNECTED: ("connected", "bool"),
    KEY_DISPLAY_LIGHT: ("display_light", "bool"),
    KEY_SETPOINT_TEMP: ("target_temperature", "raw"),
    KEY_FAN_SPEED_PCT: ("fan_speed_percentage", "raw"),
    KEY_FAN_SPEED_MODE: ("fan_speed_mode", "enum"),
    KEY_FAN_SPEED: ("fan_mode", "enum"),
    KEY_BOOST: ("boost", "bool"),
    KEY_HORIZONTAL_SWING: ("horizontal_swing", "bool"),
    KEY_VERTICAL_SWING: ("vertical_swing", "bool"),
    KEY_VERTICAL_FAN_DIR: ("vertical_fan_direction", "enum"),
    KEY_BREEZE_AWAY: ("breeze_away", "bool"),
    KEY_GEAR: ("gear", "enum"),
}


def _to_bool(value: Any) -> bool:
    """Convert API value to bool (handles int 0/1 and bool)."""
    if isinstance(value, bool):
        return value
    return bool(value)


def _extract_enum_suffix(value: str) -> str:
    """Extract the last segment of a BSH enum value."""
    if isinstance(value, str) and "." in value:
        return value.rsplit(".", 1)[-1]
    return str(value)


def _transform(value: Any, transform: str) -> Any:
    """Transform an API value to our internal representation."""
    if transform == "bool":
        return _to_bool(value)
    if transform == "enum":
        return _extract_enum_suffix(value)
    if transform == "power":
        return value == POWER_ON
    return value


def _empty_device_data() -> dict[str, Any]:
    return {
        "power_on": False,
        "connected": None,
        "display_light": None,
        "program": None,
        "target_temperature": None,
        "fan_speed_percentage": None,
        "fan_speed_mode": None,
        "fan_mode": None,
        "boost": None,
        "horizontal_swing": None,
        "vertical_swing": None,
        "vertical_fan_direction": None,
        "breeze_away": None,
        "gear": None,
    }


class HomeConnectACCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinator: SSE for real-time updates, REST poll as safety fallback."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: HomeConnectClient,
        appliances: list[dict[str, Any]],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # REST poll is just a safety fallback — SSE does the real work
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.client = client
        self._rate_limit_until: float = 0.0
        self._sse_task: asyncio.Task | None = None
        self.appliances = {
            a["haId"]: a
            for a in appliances
            if a.get("type") == "AirConditioner"
        }

    def start_sse(self) -> None:
        """Start the SSE listener background task."""
        if self._sse_task is None or self._sse_task.done():
            self._sse_task = self.hass.async_create_background_task(
                self._sse_loop(), f"{DOMAIN}_sse"
            )
            _LOGGER.debug("SSE listener started")

    def stop_sse(self) -> None:
        """Stop the SSE listener."""
        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            self._sse_task = None
            _LOGGER.debug("SSE listener stopped")

    async def _sse_loop(self) -> None:
        """Connect to SSE and apply events. Reconnects on failure."""
        backoff = 5
        while True:
            try:
                _LOGGER.debug("Connecting to SSE stream")
                async for event in self.client.stream_events():
                    backoff = 5  # reset on any successful event
                    self._apply_event(event)
                # stream_events returned (e.g. 401 → token refreshed)
                _LOGGER.debug("SSE stream ended, reconnecting")
            except asyncio.CancelledError:
                return
            except httpx.HTTPStatusError as err:
                if err.response.status_code == 429:
                    retry_after = int(err.response.headers.get("Retry-After", "300"))
                    _LOGGER.warning("SSE rate limited, waiting %ds", retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                _LOGGER.warning("SSE error %s, reconnecting in %ds", err.response.status_code, backoff)
            except Exception:
                _LOGGER.debug("SSE connection lost, reconnecting in %ds", backoff, exc_info=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    def _apply_event(self, event: dict) -> None:
        """Apply a single SSE event to coordinator data."""
        ha_id = event.get("ha_id", "")
        key = event.get("key", "")
        value = event.get("value")
        event_type = event.get("event_type", "")

        if not self.data or ha_id not in self.data:
            return

        device = self.data[ha_id]
        updated = False

        # Program selection event
        if event_type == "EVENT" and key.endswith(".Program.Selected"):
            # value is like "...Program.Cool"
            device["program"] = value
            updated = True
        elif key in _KEY_MAP:
            field, transform = _KEY_MAP[key]
            device[field] = _transform(value, transform)
            updated = True

        if updated:
            _LOGGER.debug("SSE %s: %s = %s", ha_id[-6:], key.rsplit(".", 1)[-1], value)
            self.async_set_updated_data(self.data)

    # ── REST poll (fallback) ──

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """REST poll: seeds initial state and acts as safety fallback."""
        if self._rate_limit_until and time.time() < self._rate_limit_until:
            _LOGGER.debug(
                "Skipping poll, rate limited for %ds more",
                int(self._rate_limit_until - time.time()),
            )
            raise UpdateFailed("Rate limited, waiting for cooldown")

        try:
            result: dict[str, dict[str, Any]] = {}
            for ha_id in self.appliances:
                result[ha_id] = await self._poll_device(ha_id)
            self._rate_limit_until = 0.0

            # SSE is running — don't need frequent REST polls
            if self._sse_task and not self._sse_task.done():
                self.update_interval = timedelta(seconds=SCAN_INTERVAL_SECONDS * 5)
            else:
                self.update_interval = timedelta(seconds=SCAN_INTERVAL_SECONDS)

            return result
        except httpx.HTTPStatusError as err:
            if err.response.status_code == 401:
                raise ConfigEntryAuthFailed(str(err)) from err
            if err.response.status_code == 429:
                retry_after = int(err.response.headers.get("Retry-After", "60"))
                self._rate_limit_until = time.time() + retry_after
                _LOGGER.warning(
                    "Rate limited by Home Connect API, backing off %ds", retry_after
                )
            raise UpdateFailed(str(err)) from err
        except RuntimeError as err:
            if "refresh token" in str(err).lower():
                raise ConfigEntryAuthFailed(str(err)) from err
            raise UpdateFailed(str(err)) from err

    async def _poll_device(self, ha_id: str) -> dict[str, Any]:
        """Poll settings, status, and selected program for one device."""
        device_data = _empty_device_data()

        try:
            settings = await self.client.get_settings(ha_id)
            for s in settings:
                key = s.get("key", "")
                value = s.get("value")
                if key in _KEY_MAP:
                    field, transform = _KEY_MAP[key]
                    device_data[field] = _transform(value, transform)
        except httpx.HTTPStatusError:
            _LOGGER.debug("Failed to get settings for %s", ha_id)

        try:
            status = await self.client.get_status(ha_id)
            for s in status:
                key = s.get("key", "")
                value = s.get("value")
                if key in _KEY_MAP:
                    field, transform = _KEY_MAP[key]
                    device_data[field] = _transform(value, transform)
        except httpx.HTTPStatusError:
            _LOGGER.debug("Failed to get status for %s", ha_id)

        try:
            program = await self.client.get_selected_program(ha_id)
            if program:
                device_data["program"] = program.get("key")
                for opt in program.get("options", []):
                    okey = opt.get("key", "")
                    oval = opt.get("value")
                    if okey in _KEY_MAP:
                        field, transform = _KEY_MAP[okey]
                        device_data[field] = _transform(oval, transform)
        except httpx.HTTPStatusError:
            _LOGGER.debug("Failed to get selected program for %s", ha_id)

        return device_data
