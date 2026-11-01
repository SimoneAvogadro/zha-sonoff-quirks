"""Sonoff ZHA custom integration.

This integration ships custom ZHA quirks for SONOFF Zigbee devices: the
SWV-ZF2 valve quirk and an inching override of the built-in ZBMINIR2 relay
quirk (see `quirks/`). Its first job is to make sure the bundled quirk modules
are imported, which registers them into ZHA's quirk registry so ZHA applies
them on device join / interrogation — exactly as if they had been dropped into
the directory configured by `zha.custom_quirks_path`.

The quirks register themselves as a side-effect of being imported (a
`QuirkBuilder(...).add_to_registry()` call). The import below happens at
module load time — but that is NOT guaranteed to precede ZHA's device
enumeration: this integration declares `zha` as a manifest dependency, so on
a cold HA start ZHA sets up (and applies quirks to its devices) BEFORE this
module is imported. The SWV devices then come up without the quirk (all
custom entities `unavailable`; observed live on 2026-08-09), and a ZBMINIR2
comes up on the built-in zhaquirks quirk, without the inching entities. The
fix is `_async_ensure_quirk_applied`: after HA has started, inspect the ZHA
gateway and, if an SWV device lacks a quirk or a ZBMINIR2 is still on ZHA's
built-in quirk instead of the inching override, reload the ZHA config entry
ONCE per HA session — the quirks are registered in-process by then, so the
reload applies them deterministically. The decision is HA-free and unit-tested
(`quirk_check_logic.py`).

On top of the quirks, the integration:

- registers two device-centric irrigation services (see `services.py`) that
  bundle the "configure mode/target/fail-safe, then open the channel" entity
  sequence into a single call;
- records every irrigation run (any origin: services, automations, physical
  button, on-device auto-close) into a persistent per-channel run log
  (`history.py`) surfaced by two sensors per channel (`sensor.py`);
- serves the companion Lovelace card (`www/sonoff-valve-card.js`) via a
  static path and auto-registers it as a dashboard resource, so users don't
  have to add the resource manually.

The config flow exists only so the integration can be enabled from the UI with
a single click.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, callback
from homeassistant.setup import async_when_setup

# Imported first of all for its side-effect: registers the bundled quirks into
# ZHA's quirk registry. On a cold start this still happens AFTER ZHA set up (zha
# is a manifest dependency); _async_ensure_quirk_applied closes that gap and
# reads the ZBMINIR2 override's state from quirks.sonoff_zbminir2.
from . import quirks
from .const import (
    DOMAIN,
    JSMODULES,
    PLATFORMS,
    SWV_MODELS,
    URL_BASE,
    ZBMINIR2_MODEL_INFO,
)
from .history import SwvRunLog
from .quirk_check_logic import (
    decide_zha_reload,
    registry_entry_attr,
    zbminir2_override_state,
)
from .services import (
    async_refresh_service_descriptions,
    async_setup_services,
    async_unload_services,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry.

    Quirk registration happened at import time; here we serve the Lovelace
    card, register the irrigation services, start the run log and its sensor
    platform, and schedule the quirk-applied check (see module docstring).
    """
    data = hass.data.setdefault(DOMAIN, {})
    await _async_register_frontend(hass)
    async_setup_services(hass)
    run_log = SwvRunLog(hass)
    data["run_log"] = run_log
    await run_log.async_setup()
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # No orphaned observer: a failed setup would otherwise leave this
        # run log's subscriptions live while HA's retry builds a second one —
        # two writers racing on the same store.
        data.pop("run_log", None)
        async_unload_services(hass)
        await run_log.async_unload()
        raise
    _async_schedule_quirk_check(hass)
    # Line-name labels in the services' channel radio (see services.py). The
    # first publish happens now; the text platform re-publishes when a channel
    # switch appears later (ZHA reload), and the options listener when a line
    # is renamed. The listener does NOT reload the entry — text.py relies on
    # that when it writes the names.
    await async_refresh_service_descriptions(hass, entry)
    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Config entry options changed (a line was renamed): refresh the labels."""
    await async_refresh_service_descriptions(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    The services are removed and the run log flushed, but quirk registration
    is not undone (devices already resolved would keep their quirk anyway)
    and persists for the lifetime of the HA process. The static path and the
    Lovelace resource likewise stay registered — HA exposes no clean way to
    undo them, and leaving them idle is harmless. A restart is required to
    fully remove the quirks.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        # The entry stays loaded: keep services and run log alive too,
        # otherwise the still-registered sensors would point at a dead one.
        return False
    async_unload_services(hass)
    run_log: SwvRunLog | None = hass.data.get(DOMAIN, {}).pop("run_log", None)
    if run_log is not None:
        await run_log.async_unload()
    return True


@callback
def _async_schedule_quirk_check(hass: HomeAssistant) -> None:
    """Run the quirk-applied check once HA is fully started.

    At startup the check must wait for EVENT_HOMEASSISTANT_STARTED so ZHA has
    finished restoring its devices; when the entry is set up later (user just
    added or reloaded the integration) it can run immediately.
    """

    async def _check(_event=None) -> None:
        await _async_ensure_quirk_applied(hass)

    if hass.is_running:
        hass.async_create_task(_check())
    else:
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _check)


async def _async_ensure_quirk_applied(hass: HomeAssistant) -> None:
    """Reload ZHA once if a device came up before the bundled quirks registered.

    On a cold HA start ZHA applies quirks before this integration's module is
    imported (see module docstring): every custom entity of the valve is
    `unavailable`, and a ZBMINIR2 sits on the built-in quirk without the
    inching entities. By the time this runs the quirks ARE in ZHA's registry,
    so one ZHA reload re-initializes the devices with them applied.

    Which devices count is decided by `decide_zha_reload`, fail-closed on ZHA
    API drift: only an EXPLICIT quirk_applied == False marks an SWV as
    unquirked, and the ZBMINIR2 check runs only while the override is
    registered and still wins in the registry, only if ZHA still exposes the
    registry entry a device was resolved to, and only for a relay on the
    built-in entry itself (by identity; a relay on another quirk, e.g. one
    from custom_quirks_path, would land on it again) — better no auto-heal
    than a spurious full Zigbee restart on every boot forever.

    Guarded to a single reload per HA session: if the quirk still doesn't
    apply after the reload the cause is not ordering, and retrying would loop
    a full Zigbee network restart forever.
    """
    data = hass.data.setdefault(DOMAIN, {})
    try:
        from homeassistant.components.zha.helpers import get_zha_gateway

        gateway = get_zha_gateway(hass)
    except Exception as err:  # noqa: BLE001 - ZHA internals, degrade quietly
        _LOGGER.debug("Cannot inspect the ZHA gateway (%s); skipping check", err)
        return

    # quirks.sonoff_zbminir2 is None when that optional module failed to load.
    zbminir2_builtin_entry, zbminir2_effective = zbminir2_override_state(
        getattr(quirks, "sonoff_zbminir2", None)
    )
    # Likewise quirks.sonoff_swv_zf2: with no quirk registered a reload cannot
    # apply it, so the valves must not count.
    swv_registered = getattr(quirks, "sonoff_swv_zf2", None) is not None
    decision = decide_zha_reload(
        getattr(gateway, "devices", {}).values(),
        already_reloaded=bool(data.get("zha_reload_done")),
        swv_models=SWV_MODELS if swv_registered else (),
        zbminir2_model=ZBMINIR2_MODEL_INFO,
        zbminir2_builtin_entry=zbminir2_builtin_entry,
        zbminir2_effective=zbminir2_effective,
        entry_attr=registry_entry_attr(),
    )
    if not decision.reload:
        return

    zha_entries = [
        e
        for e in hass.config_entries.async_entries("zha")
        if e.state is ConfigEntryState.LOADED
    ]
    if not zha_entries:
        return
    data["zha_reload_done"] = True
    _LOGGER.warning(
        "%s (ZHA loaded before the bundled quirks were registered); reloading "
        "ZHA once to apply them",
        decision.describe(),
    )
    await hass.config_entries.async_reload(zha_entries[0].entry_id)


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



def _bundle_hash(path: Path) -> str:
    """Short content hash of a served card file, or "" if it cannot be read."""
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:8]
    except OSError:
        return ""

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

    www_dir = Path(__file__).parent / "www"
    for module in JSMODULES:
        url = f"{URL_BASE}/{module['filename']}"
        # VERSION only changes at release time, but the card ships with every
        # commit: a short hash of the file content rides after it so any
        # rebuilt card gets a fresh URL and no browser / companion-app cache
        # survives it (same scheme as tuya-cards-for-ha).
        digest = await hass.async_add_executor_job(
            _bundle_hash, www_dir / module["filename"]
        )
        versioned_url = f"{url}?v={module['version']}"
        if digest:
            versioned_url += f"-{digest}"
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
