"""Climate entities for Home Connect AC."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    ENUM,
    KEY_FAN_SPEED,
    KEY_FAN_SPEED_MODE,
    KEY_FAN_SPEED_PCT,
    KEY_POWER,
    KEY_SETPOINT_TEMP,
    FAN_MODE_AUTO,
    FAN_MODE_MANUAL,
    POWER_ON,
    POWER_STANDBY,
    PROGRAM_AUTO,
    PROGRAM_COOL,
    PROGRAM_DRY,
    PROGRAM_FAN,
    PROGRAM_HEAT,
)
from .coordinator import HomeConnectACCoordinator

_LOGGER = logging.getLogger(__name__)

PROGRAM_TO_HVAC: dict[str, HVACMode] = {
    PROGRAM_COOL: HVACMode.COOL,
    PROGRAM_HEAT: HVACMode.HEAT,
    PROGRAM_AUTO: HVACMode.AUTO,
    PROGRAM_DRY: HVACMode.DRY,
    PROGRAM_FAN: HVACMode.FAN_ONLY,
}

HVAC_TO_PROGRAM: dict[HVACMode, str] = {v: k for k, v in PROGRAM_TO_HVAC.items()}

# Pitsos discrete fan levels
FAN_LEVEL_MAP = {
    "Auto": "Auto",
    "Level1": "Level1",
    "Level2": "Level2",
    "Level3": "Level3",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entities from a config entry."""
    coordinator: HomeConnectACCoordinator = entry.runtime_data
    entities = [
        HomeConnectACClimate(coordinator, ha_id, info)
        for ha_id, info in coordinator.appliances.items()
    ]
    async_add_entities(entities)


class HomeConnectACClimate(
    CoordinatorEntity[HomeConnectACCoordinator], ClimateEntity
):
    """Climate entity for a Home Connect air conditioner."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.AUTO,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
    ]
    _enable_turn_on_off_backwards_compat = False

    def __init__(
        self,
        coordinator: HomeConnectACCoordinator,
        ha_id: str,
        appliance_info: dict[str, Any],
    ) -> None:
        super().__init__(coordinator)
        self._ha_id = ha_id
        self._appliance_info = appliance_info

        self._attr_unique_id = f"homeconnect_ac_{ha_id}"
        self._attr_name = None  # Use device name only (no duplicate)

        name = appliance_info.get("name", ha_id)
        brand = appliance_info.get("brand", "Home Connect")
        model = appliance_info.get("vib", "AC")
        self._attr_device_info = {
            "identifiers": {(DOMAIN, ha_id)},
            "name": name,
            "manufacturer": brand,
            "model": model,
            "suggested_area": name,
        }

    @property
    def _device_data(self) -> dict[str, Any]:
        """Get this device's data from the coordinator."""
        if self.coordinator.data and self._ha_id in self.coordinator.data:
            return self.coordinator.data[self._ha_id]
        return {}

    @property
    def _is_percentage_fan(self) -> bool:
        """True if device uses percentage fan model (Bosch)."""
        return self._device_data.get("fan_speed_percentage") is not None

    @property
    def _is_level_fan(self) -> bool:
        """True if device uses discrete level fan model (Pitsos)."""
        return self._device_data.get("fan_mode") is not None

    # ── HVAC mode ──

    @property
    def hvac_mode(self) -> HVACMode:
        data = self._device_data
        if not data.get("power_on"):
            return HVACMode.OFF
        program = data.get("program")
        if program:
            return PROGRAM_TO_HVAC.get(program, HVACMode.OFF)
        return HVACMode.OFF

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        client = self.coordinator.client
        if hvac_mode == HVACMode.OFF:
            await client.set_setting(self._ha_id, KEY_POWER, POWER_STANDBY)
        else:
            # Power on first if needed
            if not self._device_data.get("power_on"):
                await client.set_setting(self._ha_id, KEY_POWER, POWER_ON)
            program_key = HVAC_TO_PROGRAM.get(hvac_mode)
            if program_key:
                await client.select_program(self._ha_id, program_key)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self) -> None:
        await self.coordinator.client.set_setting(
            self._ha_id, KEY_POWER, POWER_ON
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self) -> None:
        await self.coordinator.client.set_setting(
            self._ha_id, KEY_POWER, POWER_STANDBY
        )
        await self.coordinator.async_request_refresh()

    async def _ensure_power_on(self) -> None:
        """Power on the device if it's in standby."""
        if not self._device_data.get("power_on"):
            await self.coordinator.client.set_setting(
                self._ha_id, KEY_POWER, POWER_ON
            )

    # ── Temperature ──

    @property
    def target_temperature(self) -> float | None:
        return self._device_data.get("target_temperature")

    @property
    def min_temp(self) -> float:
        return 16.0

    @property
    def max_temp(self) -> float:
        return 30.0

    @property
    def target_temperature_step(self) -> float:
        return 1.0

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        await self._ensure_power_on()
        await self.coordinator.client.set_selected_option(
            self._ha_id, KEY_SETPOINT_TEMP, temp, unit="\u00b0C"
        )
        await self.coordinator.async_request_refresh()

    # ── Fan mode ──

    @property
    def fan_modes(self) -> list[str]:
        if self._is_percentage_fan:
            return ["Auto", "Manual"]
        if self._is_level_fan:
            return ["Auto", "Level1", "Level2", "Level3"]
        return []

    @property
    def fan_mode(self) -> str | None:
        data = self._device_data
        # Percentage model (Bosch)
        if data.get("fan_speed_mode") is not None:
            mode = data["fan_speed_mode"]
            if mode == "Automatic":
                return "Auto"
            pct = data.get("fan_speed_percentage")
            if pct is not None:
                return f"Manual {int(pct)}%"
            return "Manual"
        # Discrete level model (Pitsos)
        if data.get("fan_mode") is not None:
            return data["fan_mode"]
        return None

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self._ensure_power_on()
        client = self.coordinator.client
        if self._is_percentage_fan:
            if fan_mode == "Auto":
                await client.set_selected_option(
                    self._ha_id, KEY_FAN_SPEED_MODE, FAN_MODE_AUTO
                )
            else:
                # "Manual" — set to manual mode (keeps current percentage)
                await client.set_selected_option(
                    self._ha_id, KEY_FAN_SPEED_MODE, FAN_MODE_MANUAL
                )
        elif self._is_level_fan:
            level = FAN_LEVEL_MAP.get(fan_mode)
            if level:
                await client.set_selected_option(
                    self._ha_id,
                    KEY_FAN_SPEED,
                    f"{ENUM}FanSpeedLevel.{level}",
                )
        await self.coordinator.async_request_refresh()

    # ── Extra state attributes ──

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self._device_data
        attrs: dict[str, Any] = {}
        for key in (
            "connected", "boost", "horizontal_swing", "vertical_swing",
            "vertical_fan_direction", "breeze_away", "gear", "display_light",
            "fan_speed_percentage",
        ):
            val = data.get(key)
            if val is not None:
                attrs[key] = val
        return attrs
