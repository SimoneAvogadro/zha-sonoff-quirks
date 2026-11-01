"""ZHA custom quirks bundled with the Sonoff ZHA integration.

Importing this package as a side-effect registers each quirk with the
QuirksV2 builder registry (`add_to_registry()`). ZHA then applies them on
device join / interrogation, exactly as if they had been dropped into the
path configured by `zha.custom_quirks_path`.

The integration's top-level `__init__.py` imports this package at module
load time so the registration happens before ZHA enumerates devices.

Bundled quirks:

- `sonoff_swv_zf2`: SONOFF SWV-ZF2 dual-channel water valve.
- `sonoff_zbminir2`: inching support for the SONOFF ZBMINIR2 relay, as an
  override of the built-in zhaquirks quirk. It is optional: any error while
  importing it is logged and leaves `sonoff_zbminir2` set to None, without
  affecting the SWV-ZF2 quirk or the integration. An ImportError (zha-quirks
  older than 2.x) is logged as one warning without traceback.

Neither import can break the integration: an error importing the SWV-ZF2
quirk is logged with its traceback and leaves `sonoff_swv_zf2` set to None.

Each quirk module must be self-contained (it must not import from
`custom_components.zha_sonoff_quirks.*` other than `quirks.*`) so it keeps
working even if ZHA loads it via `custom_quirks_path` instead of via this
integration's import path.
"""

import logging

_LOGGER = logging.getLogger(__name__)

try:
    from . import sonoff_swv_zf2  # noqa: F401  -- import for side-effect
except Exception:  # e.g. a ZHA API it relies on removed by a later HA release
    # Fail here, loudly, rather than taking the whole integration down with it:
    # the integration still loads and the ZBMINIR2 override still applies.
    sonoff_swv_zf2 = None
    _LOGGER.exception(
        "Could not load the SONOFF SWV-ZF2 quirk; SWV-ZF2 valves keep the "
        "built-in ZHA quirk, without the irrigation configuration entities"
    )

try:
    from . import sonoff_zbminir2  # noqa: F401  -- import for side-effect
except ImportError as err:
    # Expected on ZHA stacks older than zha-quirks 2.x (still allowed by
    # hacs.json), which lack the APIs the override is built on: one warning,
    # no traceback, at every start — even for users with no ZBMINIR2.
    sonoff_zbminir2 = None
    _LOGGER.warning(
        "The SONOFF ZBMINIR2 inching quirk needs zha-quirks 2.x (ZHA 2.x) and "
        "is not loaded with this version (%s); ZBMINIR2 devices keep the "
        "built-in ZHA quirk",
        err,
    )
except Exception:  # an optional override must never break the SWV-ZF2 quirk
    sonoff_zbminir2 = None
    _LOGGER.exception(
        "Could not load the SONOFF ZBMINIR2 inching quirk; ZBMINIR2 devices "
        "keep the built-in ZHA quirk"
    )
