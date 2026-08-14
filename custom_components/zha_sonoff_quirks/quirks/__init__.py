"""ZHA custom quirks bundled with the Sonoff ZHA integration.

Importing this package as a side-effect registers each quirk with the
QuirksV2 builder registry (`add_to_registry()`). ZHA then applies them on
device join / interrogation, exactly as if they had been dropped into the
path configured by `zha.custom_quirks_path`.

The integration's top-level `__init__.py` imports this package at module
load time so the registration happens before ZHA enumerates devices.

Each quirk module must be self-contained (it must not import from
`custom_components.zha_sonoff_quirks.*` other than `quirks.*`) so it keeps
working even if ZHA loads it via `custom_quirks_path` instead of via this
integration's import path.
"""

from . import sonoff_swv_zf2  # noqa: F401  -- import for side-effect
