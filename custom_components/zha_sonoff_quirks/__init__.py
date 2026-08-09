"""ZHA Sonoff Quirks custom integration.

This integration ships custom ZHA quirks for SONOFF Zigbee devices. Its first
job is to make sure the bundled quirk modules are imported, which registers
them into zigpy's global registry so ZHA applies them on device join /
interrogation — exactly as if they had been dropped into the directory
configured by `zha.custom_quirks_path`.

The quirks register themselves as a side-effect of being imported (a
`QuirkBuilder(...).add_to_registry()` call). The import below must therefore
happen at module load time, before ZHA enumerates devices — which it does,
because Home Assistant imports this module when the integration is set up.

On top of the quirks, the integration:

- registers two device-centric irrigation services (see `services.py`) that
  bundle the "configure mode/target/fail-safe, then open the channel" entity
  sequence into a single call;
- serves the companion Lovelace card (`www/sonoff-valve-card.js`) via a
  static path and auto-registers it as a dashboard resource, so users don't
  have to add the resource manually.

The config flow exists only so the integration can be enabled from the UI with
a single click.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_when_setup

# Import for side-effect: registers bundled ZHA quirks into zigpy's global
# registry. Needs to happen at module load time so ZHA picks them up before
# enumerating devices.
from . import quirks  # noqa: F401
from .const import JSMODULES, URL_BASE
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry.

    The actual quirk registration already happened at import time (see the
    `from . import quirks` above). Here we additionally serve and auto-register
    the bundled Lovelace card and register the irrigation services.
    """
    await _async_register_frontend(hass)
    async_setup_services(hass)
    _LOGGER.debug("ZHA Sonoff Quirks enabled (quirks registered at import time)")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    The services are removed, but quirk registration cannot be cleanly undone
    (the registry has no public de-registration API) and persists for the
    lifetime of the HA process. The static path and the Lovelace resource
    likewise stay registered — HA exposes no clean way to undo them, and
    leaving them idle is harmless. A restart is required to fully remove the
    quirks.
    """
    async_unload_services(hass)
    return True


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the card via a static path and auto-register it as a Lovelace module.

    The static path can be registered during async_setup_entry, but the
    Lovelace resource registration has to wait until the lovelace component
    itself is set up, hence the async_when_setup deferral.
    """
    www_dir = Path(__file__).parent / "www"
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(URL_BASE, str(www_dir), False)]
        )
    except RuntimeError:
        # Re-setup / reload of the entry within the same HA session: the path
        # is already registered and aiohttp refuses duplicates. Harmless.
        _LOGGER.debug("Static path %s already registered", URL_BASE)

    async_when_setup(hass, "lovelace", _async_register_lovelace_resource)


async def _async_register_lovelace_resource(
    hass: HomeAssistant, _component: str
) -> None:
    """Register the card as a Lovelace module resource.

    Invoked after the lovelace component has finished setting up, so
    hass.data["lovelace"] is guaranteed to be the LovelaceData dataclass
    (attributes: resource_mode, resources, dashboards, ...).

    Every resources.* call is wrapped defensively: the Lovelace resource
    storage is not a public API, so HA API drift must degrade to a logged
    warning (the user can always add the resource manually), never break the
    integration setup.
    """
    lovelace = hass.data.get("lovelace")
    if lovelace is None:
        _LOGGER.warning(
            "Lovelace data missing after setup — cannot auto-register %s",
            URL_BASE,
        )
        return

    # Recent HA versions expose `resource_mode`; older ones exposed `mode`.
    mode = getattr(lovelace, "resource_mode", None) or getattr(lovelace, "mode", None)
    resources = getattr(lovelace, "resources", None)
    if mode != "storage" or resources is None:
        # In YAML mode the resource list is user-managed configuration; HA
        # forbids programmatic writes, so all we can do is tell the user.
        _LOGGER.warning(
            "Lovelace is in '%s' mode; add '%s/sonoff-valve-card.js' as a "
            "module resource manually under Settings → Dashboards → Resources",
            mode,
            URL_BASE,
        )
        return

    try:
        if not resources.loaded:
            await resources.async_load()
    except Exception as err:  # pragma: no cover - defensive against HA API drift
        _LOGGER.warning("Could not load Lovelace resources: %s", err)
        return

    for module in JSMODULES:
        url = f"{URL_BASE}/{module['filename']}"
        versioned_url = f"{url}?v={module['version']}"
        found_id: str | None = None
        try:
            items = resources.async_items()
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.warning("Could not read Lovelace resources: %s", err)
            return
        # Match on the URL without its ?v= so a version bump updates the
        # existing resource entry in place instead of accumulating duplicates.
        for item in items:
            item_url = item.get("url", "")
            if item_url.split("?")[0] == url:
                found_id = item.get("id")
                if item_url == versioned_url:
                    _LOGGER.debug("Resource %s already up to date", versioned_url)
                    found_id = "UPTODATE"
                break
        if found_id == "UPTODATE":
            continue
        try:
            if found_id:
                await resources.async_update_item(
                    found_id, {"res_type": "module", "url": versioned_url}
                )
                _LOGGER.warning("Updated Lovelace resource: %s", versioned_url)
            else:
                await resources.async_create_item(
                    {"res_type": "module", "url": versioned_url}
                )
                _LOGGER.warning("Registered Lovelace resource: %s", versioned_url)
        except Exception as err:  # pragma: no cover - defensive
            _LOGGER.warning(
                "Could not register Lovelace resource %s: %s", versioned_url, err
            )
