"""Device-centric irrigation services for the SONOFF SWV-ZF2 water valve.

The quirk (see ``quirks/sonoff_swv_zf2.py``) exposes the valve's irrigation
configuration as plain HA entities: a mode select, target number entities
(duration in minutes / volume in liters), a fail-safe number, and one On/Off
switch per channel. Starting a volume- or time-limited run therefore means
"write the config entities, then flip the channel switch" — the valve firmware
reads the (global, endpoint-1) config when a channel opens and closes the
channel by itself once the target is reached.

The services registered here bundle that multi-entity sequence into a single
device-centric call so cards and automations don't have to know the entity
layout:

    zha_sonoff_quirks.irrigation_by_liters(device_id, channel, liters,
                                           fail_safe_minutes?)
    zha_sonoff_quirks.irrigation_by_minutes(device_id, channel, minutes,
                                            fail_safe_minutes?)

There is deliberately no server-side timer or monitoring task: the SWV-ZF2
auto-closes on-device, so once the switch is on the job is done. Stopping a
run early is a plain ``switch.turn_off`` on the channel switch.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import CHANNEL_LABELS, CHANNELS, DOMAIN, normalize_channel
from .helpers import resolve_entities

_LOGGER = logging.getLogger(__name__)

SERVICE_IRRIGATION_BY_LITERS = "irrigation_by_liters"
SERVICE_IRRIGATION_BY_MINUTES = "irrigation_by_minutes"

ATTR_DEVICE_ID = "device_id"
ATTR_CHANNEL = "channel"
ATTR_LITERS = "liters"
ATTR_MINUTES = "minutes"
ATTR_FAIL_SAFE_MINUTES = "fail_safe_minutes"

# Exact option strings of the quirk's irrigation-mode select as HA exposes
# them: zha's ZCLEnumSelectEntity turns enum member names into options via
# name.replace("_", " "), so IrrigationMode.duration -> "duration" and
# IrrigationMode.capacity -> "capacity" (lowercase, and the third option is
# "duration with interval" WITH spaces). select.select_option only accepts
# these exact strings.
MODE_OPTION_DURATION = "duration"
MODE_OPTION_CAPACITY = "capacity"

#: Accepted spellings, for the error message: 1, 2, A, B.
_CHANNEL_CHOICES = ", ".join([*CHANNELS, *CHANNEL_LABELS.values()])


def validate_channel(value: Any) -> str:
    """Normalize the channel field, accepting the valve's A/B panel letters.

    The selector sends the canonical "1"/"2", but a hand-written automation
    may well use the letters printed on the device — or the bare number 1,
    which YAML parses as an int. All of them normalize here, so nothing
    downstream ever sees anything but "1" or "2".

    Public because device_action.py validates its saved configs with it too:
    one definition of what a channel may be spelled as, not two.
    """
    channel = normalize_channel(value)
    if channel is None:
        raise vol.Invalid(f"channel must be one of {_CHANNEL_CHOICES} (got {value!r})")
    return channel


# Shared fields of both services.
_COMMON_FIELDS = {
    vol.Required(ATTR_DEVICE_ID): cv.string,
    vol.Required(ATTR_CHANNEL): validate_channel,
    vol.Optional(ATTR_FAIL_SAFE_MINUTES): vol.All(
        vol.Coerce(int), vol.Range(min=0, max=719)
    ),
}

LITERS_SCHEMA = vol.Schema(
    {
        **_COMMON_FIELDS,
        vol.Required(ATTR_LITERS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10000)
        ),
    }
)

MINUTES_SCHEMA = vol.Schema(
    {
        **_COMMON_FIELDS,
        vol.Required(ATTR_MINUTES): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=719)
        ),
    }
)


async def _async_start_irrigation(
    hass: HomeAssistant,
    device_id: str,
    channel: str,
    mode_option: str,
    target_key: str,
    target_label: str,
    target_value: int,
    fail_safe_minutes: int | None,
    context: Context | None,
) -> None:
    """Write the irrigation config to the valve, then open the channel.

    Order matters: the irrigation config attribute is GLOBAL for both
    channels and the firmware snapshots it when a channel opens, so mode,
    target and fail-safe must all be written BEFORE the switch turns on.
    Every call uses blocking=True so a Zigbee write failure surfaces here
    (and aborts the sequence) instead of opening the valve half-configured.

    The originating ServiceCall's context is threaded into every sub-call so
    the logbook attributes the valve opening to the user/automation that
    asked for it, and HA's per-user entity permission checks apply.
    """
    entities = resolve_entities(hass, device_id, channel)
    if not any(entities.values()):
        raise HomeAssistantError(
            f"No SWV-ZF2 entities found for device {device_id} — is it a "
            "SONOFF SWV-ZF2 with the zha_sonoff_quirks quirk applied?"
        )

    missing = []
    if entities["mode"] is None:
        missing.append("irrigation mode select (irrigation_mode)")
    if entities[target_key] is None:
        missing.append(target_label)
    if entities["switch"] is None:
        missing.append(f"channel {channel} switch")
    # fail_safe is optional overall, but if the caller explicitly asked for a
    # fail-safe we must not silently drop a safety setting.
    if fail_safe_minutes is not None and entities["fail_safe"] is None:
        missing.append("fail-safe number (fail_safe)")
    if missing:
        raise HomeAssistantError(
            f"Missing required entities for device {device_id}: "
            f"{', '.join(missing)}. The zha_sonoff_quirks quirk may not be "
            "applied to this device (try reconfiguring it in ZHA)."
        )

    # Refuse to reconfigure a channel mid-run: rewriting the global config
    # while a session is active would silently retarget the running session.
    switch_entity = entities["switch"]
    switch_state = hass.states.get(switch_entity)
    if switch_state is not None and switch_state.state == "on":
        raise HomeAssistantError(
            f"Channel {channel} ({switch_entity}) is already irrigating — "
            "stop it with switch.turn_off before starting a new run."
        )

    _LOGGER.debug(
        "Starting irrigation on %s ch%s: mode=%s target=%s fail_safe=%s",
        device_id,
        channel,
        mode_option,
        target_value,
        fail_safe_minutes,
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": entities["mode"], "option": mode_option},
        blocking=True,
        context=context,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": entities[target_key], "value": target_value},
        blocking=True,
        context=context,
    )
    if fail_safe_minutes is not None:
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": entities["fail_safe"], "value": fail_safe_minutes},
            blocking=True,
            context=context,
        )
    # Hand the run log its attribution BEFORE the switch turns on: the
    # observer reads (and consumes) this on the off→on transition. Cleared on
    # failure so a run the valve never started can't tag the next manual one.
    pending = hass.data.setdefault(DOMAIN, {}).setdefault("pending", {})
    pending[switch_entity] = {
        "source": "integration",
        # The run log drops entries older than its TTL: if the switch never
        # confirms this start, the attribution must not stick to a much later
        # manual run.
        "ts": dt_util.utcnow().timestamp(),
    }
    try:
        # Open the valve last. Nothing else to do afterwards: the SWV-ZF2
        # closes the channel on-device when the target is reached.
        await hass.services.async_call(
            "switch",
            "turn_on",
            {"entity_id": switch_entity},
            blocking=True,
            context=context,
        )
    except Exception:
        pending.pop(switch_entity, None)
        raise


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the two irrigation services.

    Plain sync helper (registration itself is synchronous) called from
    ``async_setup_entry``; the singleton config flow guarantees this runs at
    most once per HA session, so no double-registration guard is needed.
    """

    async def _handle_liters(call: ServiceCall) -> None:
        """Start a volume-limited run ("capacity" mode)."""
        await _async_start_irrigation(
            hass,
            call.data[ATTR_DEVICE_ID],
            call.data[ATTR_CHANNEL],
            MODE_OPTION_CAPACITY,
            "volume",
            "volume number (irrigation_volume)",
            call.data[ATTR_LITERS],
            call.data.get(ATTR_FAIL_SAFE_MINUTES),
            call.context,
        )

    async def _handle_minutes(call: ServiceCall) -> None:
        """Start a time-limited run ("duration" mode)."""
        await _async_start_irrigation(
            hass,
            call.data[ATTR_DEVICE_ID],
            call.data[ATTR_CHANNEL],
            MODE_OPTION_DURATION,
            "duration",
            "duration number (irrigation_duration)",
            call.data[ATTR_MINUTES],
            call.data.get(ATTR_FAIL_SAFE_MINUTES),
            call.context,
        )

    hass.services.async_register(
        DOMAIN, SERVICE_IRRIGATION_BY_LITERS, _handle_liters, schema=LITERS_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_IRRIGATION_BY_MINUTES, _handle_minutes, schema=MINUTES_SCHEMA
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """Remove the irrigation services (called from ``async_unload_entry``)."""
    hass.services.async_remove(DOMAIN, SERVICE_IRRIGATION_BY_LITERS)
    hass.services.async_remove(DOMAIN, SERVICE_IRRIGATION_BY_MINUTES)
