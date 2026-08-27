"""Device actions: the irrigation services, offered per device.

The two services are device-centric already, but their ``channel`` field is
described statically in ``services.yaml``, so its two options read "Line A" and
"Line B" for everyone — the frontend renders that description without knowing
which valve you picked. Device actions are the one surface where HA asks the
integration, at runtime and for a specific device, which fields it wants: that
is what lets the picker here read "A — Prato davanti" instead.

Nothing about irrigation is reimplemented — every action ends in a call to the
matching service, which keeps the "refuse to reconfigure a channel mid-run"
guard and the rest of the sequencing in one place.

The actions show up on the valve's ZHA device because our own entities are
attached to it (see entity.py): HA collects candidate domains for a device from
its config entries AND from the platforms of its entities.
"""

from __future__ import annotations

import logging

from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_TYPE
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.typing import ConfigType, TemplateVarsType
import voluptuous as vol

from .const import (
    ACTION_IRRIGATE_LITERS,
    ACTION_IRRIGATE_MINUTES,
    ACTION_TYPES,
    CHANNEL_LABELS,
    CHANNELS,
    DOMAIN,
    OPTIONS_LINE_NAMES,
    line_option_label,
)
from .helpers import find_swv_switches
from .services import (
    ATTR_CHANNEL,
    ATTR_DEVICE_ID,
    ATTR_FAIL_SAFE_MINUTES,
    ATTR_LITERS,
    ATTR_MINUTES,
    SERVICE_IRRIGATION_BY_LITERS,
    SERVICE_IRRIGATION_BY_MINUTES,
    validate_channel,
)

_LOGGER = logging.getLogger(__name__)

LITERS_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=1, max=10000, step=1, unit_of_measurement="L",
        mode=selector.NumberSelectorMode.BOX,
    )
)
MINUTES_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=1, max=719, step=1, unit_of_measurement="min",
        mode=selector.NumberSelectorMode.BOX,
    )
)
FAIL_SAFE_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0, max=719, step=1, unit_of_measurement="min",
        mode=selector.NumberSelectorMode.BOX,
    )
)

#: Saved configs are validated here. The channel goes through the service's own
#: validator, so an automation written by hand with `channel: "A"` is accepted
#: in a device action exactly as it is in a service call.
ACTION_SCHEMA = cv.DEVICE_ACTION_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(ACTION_TYPES),
        vol.Required(ATTR_CHANNEL): validate_channel,
        vol.Optional(ATTR_LITERS): LITERS_SELECTOR,
        vol.Optional(ATTR_MINUTES): MINUTES_SELECTOR,
        vol.Optional(ATTR_FAIL_SAFE_MINUTES): FAIL_SAFE_SELECTOR,
    }
)


def _channels_for_device(hass: HomeAssistant, device_id: str) -> list[tuple[str, str]]:
    """Return the device's (channel, switch entity_id) pairs, ordered by channel."""
    return sorted(
        (info["channel"], info["switch"])
        for info in find_swv_switches(hass)
        if info["device_id"] == device_id
    )


def _line_names(hass: HomeAssistant) -> dict[str, str]:
    """Return the device-level line names, keyed by switch entity_id."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return {}
    return entries[0].options.get(OPTIONS_LINE_NAMES) or {}


def _channel_selector(hass: HomeAssistant, device_id: str) -> selector.SelectSelector:
    """Build the line picker for THIS valve, labelled with its line names."""
    names = _line_names(hass)
    options = [
        selector.SelectOptionDict(
            value=channel,
            label=line_option_label(
                CHANNEL_LABELS.get(channel, channel), names.get(switch)
            ),
        )
        for channel, switch in _channels_for_device(hass, device_id)
    ]
    if not options:
        # The device vanished between listing the action and opening its form:
        # offer the bare letters rather than an empty, unusable picker.
        options = [
            selector.SelectOptionDict(value=c, label=CHANNEL_LABELS[c])
            for c in CHANNELS
        ]
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options, mode=selector.SelectSelectorMode.LIST
        )
    )


async def async_get_actions(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """Offer the two irrigation actions on an SWV valve (nothing elsewhere)."""
    if not _channels_for_device(hass, device_id):
        return []
    base = {CONF_DEVICE_ID: device_id, CONF_DOMAIN: DOMAIN}
    return [{**base, CONF_TYPE: action_type} for action_type in ACTION_TYPES]


async def async_get_action_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """Return the fields to show for this action, on this specific device.

    Insertion order is the order the form renders in: which line, how much,
    then the safety net.
    """
    action_type = config[CONF_TYPE]
    if action_type == ACTION_IRRIGATE_LITERS:
        amount = {vol.Required(ATTR_LITERS): LITERS_SELECTOR}
    elif action_type == ACTION_IRRIGATE_MINUTES:
        amount = {vol.Required(ATTR_MINUTES): MINUTES_SELECTOR}
    else:
        return {}
    fields = {
        vol.Required(ATTR_CHANNEL): _channel_selector(
            hass, config.get(CONF_DEVICE_ID, "")
        ),
        **amount,
        vol.Optional(ATTR_FAIL_SAFE_MINUTES): FAIL_SAFE_SELECTOR,
    }
    return {"extra_fields": vol.Schema(fields)}


async def async_call_action_from_config(
    hass: HomeAssistant,
    config: ConfigType,
    variables: TemplateVarsType,
    context: Context | None,
) -> None:
    """Run the action by calling the matching irrigation service."""
    action_type = config[CONF_TYPE]
    if action_type == ACTION_IRRIGATE_LITERS:
        service, amount_key = SERVICE_IRRIGATION_BY_LITERS, ATTR_LITERS
    elif action_type == ACTION_IRRIGATE_MINUTES:
        service, amount_key = SERVICE_IRRIGATION_BY_MINUTES, ATTR_MINUTES
    else:
        _LOGGER.warning("Unknown device action type %s", action_type)
        return

    data = {
        ATTR_DEVICE_ID: config[CONF_DEVICE_ID],
        ATTR_CHANNEL: config[ATTR_CHANNEL],
        amount_key: int(config[amount_key]),
    }
    if config.get(ATTR_FAIL_SAFE_MINUTES) is not None:
        data[ATTR_FAIL_SAFE_MINUTES] = int(config[ATTR_FAIL_SAFE_MINUTES])
    await hass.services.async_call(
        DOMAIN, service, data, blocking=True, context=context
    )
