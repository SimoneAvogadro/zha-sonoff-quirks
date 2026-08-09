"""Constants for the ZHA Sonoff Quirks integration."""

DOMAIN = "zha_sonoff_quirks"
VERSION = "0.4.0"

# Base URL the bundled Lovelace card is served under (a static path rooted at
# the integration's www/ directory).
URL_BASE = f"/{DOMAIN}"

# Lovelace JS modules to auto-register as dashboard resources. The version is
# appended as a ?v= query string so a version bump invalidates the browser
# cache without users having to clear it manually.
JSMODULES = [
    {"filename": "sonoff-valve-card.js", "version": VERSION},
]
