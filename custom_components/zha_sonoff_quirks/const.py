"""Constants for the Sonoff ZHA integration."""

DOMAIN = "zha_sonoff_quirks"
VERSION = "0.8.0"

# Entity platforms owned by the integration itself (the quirk entities are
# created by ZHA, not by us; these are the run-history sensors).
PLATFORMS = ["sensor"]

# ZHA model strings this integration's quirk applies to.
SWV_MODELS = ("SWV-ZF2", "SWV-ZF2U", "SWV-ZF2E")

# ── Channels (the valve's two outlets) ──
#: Canonical channel value used EVERYWHERE below the presentation layer: the
#: service API, the history sensors' unique_id (..._ch1/_ch2), the run-log
#: keys and the prefixes the card matches on. Never renamed — doing so would
#: break existing automations and orphan the history sensors.
CHANNELS = ("1", "2")

#: Letter silk-screened next to each outlet on the valve. Presentation only:
#: it never enters an id, an entity name or a stored key.
CHANNEL_LABELS = {"1": "A", "2": "B"}

#: Every accepted spelling -> canonical channel. Derived from the two mappings
#: above so a panel letter is, by construction, an alias of its own channel.
_CHANNEL_ALIASES = {channel: channel for channel in CHANNELS} | {
    label: channel for channel, label in CHANNEL_LABELS.items()
}


def normalize_channel(value: object) -> str | None:
    """Map any accepted channel spelling to "1"/"2", or None if invalid.

    Accepts the canonical strings, the integers 1/2 (an automation written
    without quotes), and the panel letters A/B in either case, so
    ``channel: "A"`` in YAML behaves exactly like ``channel: "1"``.
    """
    return _CHANNEL_ALIASES.get(str(value).strip().upper())

# ── Irrigation run log ──
STORAGE_KEY = f"{DOMAIN}_history"
STORAGE_VERSION = 1
#: Runs kept per channel in the persistent store.
RUNS_STORE_CAP = 50
#: Runs exposed on the history sensor's `runs` attribute (the card's read
#: surface; recorder-excluded but still pushed over the websocket).
RUNS_ATTR_CAP = 25
#: Manual switch flaps shorter than this are glitches, not irrigations.
MIN_RUN_S = 3
#: Grace after the switch turns off before reading the final session volume:
#: the device's last 0x501F report (the one carrying the session total) can
#: arrive a few seconds after the valve closes.
SESSION_SETTLE_S = 12
#: Event fired on every recorded run, for automations.
EVENT_IRRIGATION_COMPLETED = f"{DOMAIN}_irrigation_completed"


def history_signal(switch_entity: str) -> str:
    """Dispatcher signal fired when a switch's run history changes."""
    return f"{DOMAIN}_history_{switch_entity}"

# Base URL the bundled Lovelace card is served under (a static path rooted at
# the integration's www/ directory).
URL_BASE = f"/{DOMAIN}"

# Lovelace JS modules to auto-register as dashboard resources. The version is
# appended as a ?v= query string so a version bump invalidates the browser
# cache without users having to clear it manually.
JSMODULES = [
    {"filename": "sonoff-valve-card.js", "version": VERSION},
]
