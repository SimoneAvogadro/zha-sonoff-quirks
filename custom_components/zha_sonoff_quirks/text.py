"""Line-name entities: the name you gave each outlet, stored on the device.

The Lovelace card has always been able to rename its two lines, but that name
lives in the card's own config: a valve shown on three dashboards had to be
named three times. These two entities move the name to where it belongs —
the device — so every card picks it up, and so the name is editable from the
device page instead of a card editor.

One entity per channel switch, ``EntityCategory.CONFIG`` so it lands under
*Configuration* on the device page. The value is stored in the config entry's
options (``entry.options["line_names"]``, keyed by switch entity_id): unlike
``RestoreEntity`` it never expires, and since ``__init__.py`` registers no
update listener, writing it triggers no reload.

The unique_id embeds the channel FIRST (see ``line_name_unique_id``) because
the card resolves these by prefix — the tail is the switch's entity_id, which
the user is free to rename.
"""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CHANNEL_LABELS,
    LINE_NAME_MAX,
    OPTIONS_LINE_NAMES,
    line_name_unique_id,
)
from .entity import SwvAttachedEntity
from .helpers import find_swv_switches


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one line-name entity per channel switch."""
    dev_reg = dr.async_get(hass)
    added_switches: set[str] = set()

    @callback
    def _add_new_channels() -> None:
        new_entities: list[TextEntity] = []
        for info in find_swv_switches(hass):
            switch = info["switch"]
            if switch in added_switches:
                continue
            device = dev_reg.async_get(info["device_id"])
            if device is None:
                continue
            added_switches.add(switch)
            new_entities.append(
                SwvLineNameText(entry, device, switch, info["channel"])
            )
        if new_entities:
            async_add_entities(new_entities)

    _add_new_channels()

    @callback
    def _registry_updated(event: Event) -> None:
        # A valve paired (or re-quirked via ZHA reload) after setup registers
        # its switches here; pick them up without requiring a restart. Cheap
        # pre-filter: the event fires for every entity of every integration,
        # and only switch entities can introduce a new channel.
        data = event.data
        if data.get("action") not in ("create", "update"):
            return
        if not str(data.get("entity_id", "")).startswith("switch."):
            return
        _add_new_channels()

    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _registry_updated)
    )


class SwvLineNameText(SwvAttachedEntity, TextEntity):
    """Free-text name for one outlet, e.g. "33 davanti"."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = LINE_NAME_MAX

    def __init__(
        self,
        entry: ConfigEntry,
        device: dr.DeviceEntry,
        switch_entity: str,
        channel: str,
    ) -> None:
        """Set the channel-prefixed unique_id and the A/B translation key."""
        super().__init__(device, switch_entity, channel)
        self._entry = entry
        letter = CHANNEL_LABELS.get(channel, channel)
        self._attr_translation_key = f"line_name_{letter.lower()}"
        self._attr_unique_id = line_name_unique_id(channel, switch_entity)

    @property
    def native_value(self) -> str:
        """The stored name; empty while the line has never been named."""
        names = self._entry.options.get(OPTIONS_LINE_NAMES) or {}
        return names.get(self._switch_entity, "")

    async def async_set_value(self, value: str) -> None:
        """Persist the name in the config entry options.

        An empty value drops the key rather than storing "": the card treats
        both the same, and not accumulating empty entries keeps the options
        readable for anyone who opens .storage by hand.
        """
        names = dict(self._entry.options.get(OPTIONS_LINE_NAMES) or {})
        cleaned = value.strip()[:LINE_NAME_MAX]
        if cleaned:
            names[self._switch_entity] = cleaned
        else:
            names.pop(self._switch_entity, None)
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, OPTIONS_LINE_NAMES: names},
        )
        self.async_write_ha_state()
