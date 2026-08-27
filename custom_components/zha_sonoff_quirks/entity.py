"""Device attachment shared by every entity this integration owns.

The entities live on the valve's ZHA device, but they must NOT claim it
through ``DeviceInfo`` with the ZHA device's identifiers: on current HA that
no longer merges — each config entry claiming a foreign identifier gets its
own duplicate "shadow" device (observed live on 2026-08-10). The association
HA actually reads is the entity registry's ``device_id``, so the entities
register with no device at all and move themselves onto the right one as soon
as they are added.

The trick is subtle enough that having it written twice would be an invitation
to divergence, hence this mixin. It carries no platform of its own: mix it in
FIRST, before the platform's entity class.
"""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import Entity


class SwvAttachedEntity(Entity):
    """An entity that hooks itself onto the ZHA device of a channel switch."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, device: dr.DeviceEntry, switch_entity: str, channel: str
    ) -> None:
        """Remember the target ZHA device and the switch/channel pair."""
        self._switch_entity = switch_entity
        self._channel = channel
        # Deliberately NO _attr_device_info (see module docstring).
        self._target_device_id = device.id

    async def async_added_to_hass(self) -> None:
        """Move the entity onto the valve's ZHA device."""
        await super().async_added_to_hass()
        ent_reg = er.async_get(self.hass)
        reg_entry = ent_reg.async_get(self.entity_id)
        if reg_entry is not None and reg_entry.device_id != self._target_device_id:
            ent_reg.async_update_entity(
                self.entity_id, device_id=self._target_device_id
            )
