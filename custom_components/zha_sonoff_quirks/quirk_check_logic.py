"""Decision logic for the one-shot cold-boot ZHA reload.

No Home Assistant imports on purpose: everything here is unit-testable with
plain pytest (the venv has no ``homeassistant`` package), and the HA-facing
glue in ``__init__.py`` stays thin (same split as ``history_logic.py``).

On a cold start ZHA resolves the quirks of its devices before this
integration's quirks are registered (see the ``__init__.py`` docstring), so:

- an SWV-ZF2 comes up with no quirk at all;
- a ZBMINIR2 comes up on the built-in zhaquirks quirk, without the inching
  override.

One ZHA reload, once the bundled quirks are registered, fixes both. Every
check here is fail-closed: when an API it relies on is missing or looks
different, it reports "no reload" rather than risk a full Zigbee network
restart on every boot. In particular a ZBMINIR2 counts only when it sits on
the built-in entry itself (by identity): on any other quirk (e.g. one from
``custom_quirks_path``, which ZHA purges and re-registers in front at every
gateway start) a reload would land on that quirk again.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReloadDecision:
    """ZHA devices that a reload would fix, grouped by the reason."""

    #: SWV devices ZHA set up with no quirk applied.
    swv_unquirked: tuple[Any, ...] = ()
    #: ZBMINIR2 devices resolved to the built-in entry the override replaces.
    zbminir2_on_builtin_quirk: tuple[Any, ...] = ()

    @property
    def reload(self) -> bool:
        """Whether ZHA should be reloaded."""
        return bool(self.swv_unquirked or self.zbminir2_on_builtin_quirk)

    def describe(self) -> str:
        """Human-readable list of the devices that triggered the reload."""
        parts = []
        if self.swv_unquirked:
            parts.append(
                f"SWV device(s) {_names(self.swv_unquirked)} initialized "
                "without the quirk"
            )
        if self.zbminir2_on_builtin_quirk:
            parts.append(
                f"ZBMINIR2 device(s) {_names(self.zbminir2_on_builtin_quirk)} "
                "initialized on ZHA's built-in quirk, without the inching override"
            )
        return "; ".join(parts)


def _names(devices: Iterable[Any]) -> str:
    return ", ".join(str(getattr(device, "name", device)) for device in devices)


def decide_zha_reload(
    devices: Iterable[Any],
    *,
    already_reloaded: bool,
    swv_models: Collection[str],
    zbminir2_model: tuple[str, str],
    zbminir2_builtin_entry: Any,
    zbminir2_effective: bool,
    entry_attr: Any,
) -> ReloadDecision:
    """Decide whether ZHA must be reloaded once to apply the bundled quirks.

    `devices` are ZHA devices (``gateway.devices.values()``). An SWV counts
    only when its ``quirk_applied`` is explicitly False, exactly as before
    ZBMINIR2 support existed.

    A ZBMINIR2 counts when the registry entry stored on its zigpy device
    (``device.device``, attribute `entry_attr`, i.e.
    ``zha.quirks.QUIRK_REGISTRY_ENTRY_ATTR``) is, by identity,
    `zbminir2_builtin_entry`: the built-in entry the override replaces, the
    only one a reload is sure to move the relay off. Any other entry (ours, a
    custom quirk ZHA re-registers at every start, or a copy of an entry should
    ZHA ever store one) does not count. The ZBMINIR2 check is skipped entirely
    unless the override is registered (`zbminir2_builtin_entry` not None),
    still wins in the registry (`zbminir2_effective`, otherwise a reload would
    land on the winning quirk again) and `entry_attr` is a usable attribute
    name. A device without that attribute is skipped too: an unquirked device
    and a renamed ZHA attribute look the same.

    Nothing is reported when ZHA was already reloaded in this HA session.
    """
    if already_reloaded:
        return ReloadDecision()

    check_zbminir2 = (
        zbminir2_builtin_entry is not None
        and zbminir2_effective is True
        and isinstance(entry_attr, str)
        and bool(entry_attr)
    )
    swv: list[Any] = []
    zbminir2: list[Any] = []
    for device in devices:
        if (
            getattr(device, "model", None) in swv_models
            and getattr(device, "quirk_applied", None) is False
        ):
            swv.append(device)
            continue
        if check_zbminir2 and _on_builtin_quirk(
            device, zbminir2_model, zbminir2_builtin_entry, entry_attr
        ):
            zbminir2.append(device)
    return ReloadDecision(tuple(swv), tuple(zbminir2))


def _on_builtin_quirk(
    device: Any, model_info: tuple[str, str], builtin_entry: Any, entry_attr: str
) -> bool:
    """Whether `device` is a ZBMINIR2 resolved to exactly `builtin_entry`."""
    zigpy_device = getattr(device, "device", None)
    if zigpy_device is None:
        return False
    # Match on the zigpy device, like the registry does: the model ZHA shows
    # can be rewritten by a declarative quirk.
    manufacturer, model = model_info
    if (
        getattr(zigpy_device, "manufacturer", None) != manufacturer
        or getattr(zigpy_device, "model", None) != model
    ):
        return False
    return getattr(zigpy_device, entry_attr, None) is builtin_entry


def registry_entry_attr() -> str | None:
    """Name of the attribute ZHA stores a resolved device's quirk entry under.

    None if the ZHA library does not expose it (API drift): the ZBMINIR2
    check is then skipped.
    """
    try:
        from zha.quirks import QUIRK_REGISTRY_ENTRY_ATTR
    except Exception:  # noqa: BLE001 - ZHA library internals, fail closed
        return None
    if isinstance(QUIRK_REGISTRY_ENTRY_ATTR, str) and QUIRK_REGISTRY_ENTRY_ATTR:
        return QUIRK_REGISTRY_ENTRY_ATTR
    return None


def zbminir2_override_state(module: Any) -> tuple[Any, bool]:
    """Return the built-in entry the override replaces and whether it wins.

    `module` is ``quirks.sonoff_zbminir2``, which is None when it failed to
    import. Returns (None, False) when the override is not registered or does
    not expose a built-in entry distinct from its own; any error in the
    self-check counts as "does not win".
    """
    entry = getattr(module, "REGISTRY_ENTRY", None)
    builtin_entry = getattr(module, "BUILTIN_ENTRY", None)
    if entry is None or builtin_entry is None or builtin_entry is entry:
        return None, False
    try:
        effective = module.quirk_is_effective() is True
    except Exception:  # noqa: BLE001 - a failing self-check means "not ours"
        effective = False
    return builtin_entry, effective
