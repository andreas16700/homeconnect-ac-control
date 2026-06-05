"""Constants for Home Connect AC Monitor integration."""

DOMAIN = "homeconnect_ac"

# BSH key prefixes
OPT = "HeatingVentilationAirConditioning.AirConditioner.Option."
ENUM = "HeatingVentilationAirConditioning.AirConditioner.EnumType."
PROG = "HeatingVentilationAirConditioning.AirConditioner.Program."
SETTING = "HeatingVentilationAirConditioning.AirConditioner.Setting."

# Settings keys
KEY_POWER = "BSH.Common.Setting.PowerState"
KEY_CONNECTED = "BSH.Common.Status.BackendConnected"
KEY_DISPLAY_LIGHT = f"{SETTING}Light.Display.Power"

# Power values
POWER_ON = "BSH.Common.EnumType.PowerState.On"
POWER_STANDBY = "BSH.Common.EnumType.PowerState.Standby"

# Program option keys
KEY_SETPOINT_TEMP = f"{OPT}SetpointTemperature"
KEY_FAN_SPEED_PCT = f"{OPT}FanSpeedPercentage"
KEY_FAN_SPEED_MODE = f"{OPT}FanSpeedMode"
KEY_FAN_SPEED = f"{OPT}FanSpeed"
KEY_BOOST = f"{OPT}Boost"
KEY_HORIZONTAL_SWING = f"{OPT}HorizontalSwing"
KEY_VERTICAL_SWING = f"{OPT}VerticalSwing"
KEY_VERTICAL_FAN_DIR = f"{OPT}VerticalFanDirection"
KEY_BREEZE_AWAY = f"{OPT}BreezeAway"
KEY_GEAR = f"{OPT}Gear"

# Fan speed mode values
FAN_MODE_AUTO = f"{ENUM}FanSpeedMode.Automatic"
FAN_MODE_MANUAL = f"{ENUM}FanSpeedMode.Manual"

# Program keys
PROGRAM_COOL = f"{PROG}Cool"
PROGRAM_HEAT = f"{PROG}Heat"
PROGRAM_AUTO = f"{PROG}Auto"
PROGRAM_DRY = f"{PROG}Dry"
PROGRAM_FAN = f"{PROG}Fan"

SCAN_INTERVAL_SECONDS = 300  # REST poll is safety fallback only; SSE does real-time
