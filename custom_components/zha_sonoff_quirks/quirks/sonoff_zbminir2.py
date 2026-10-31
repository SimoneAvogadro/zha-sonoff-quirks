"""SONOFF ZBMINIR2 (Zigbee Mini R2 relay) - inching support - custom ZHA quirk.

Overrides the built-in ``zhaquirks.sonoff.zbminir2`` quirk for the ZBMINIR2
model only (MINI-ZBD keeps the built-in quirk) to add the eWeLink "inching"
function: after every ON the relay turns itself OFF again once a delay has
elapsed (impulse), or, in the opposite mode, after every OFF it turns back ON.

Sources
-------
- Zigbee2MQTT: ``sonoffExtend.inchingControlSet`` and the ``protocolData``
  command of ``customClusterEwelink`` (zigbee-herdsman-converters,
  ``src/devices/sonoff.ts``), used by its ZBMINIR2 definition.
- Upstream ZHA PRs zigpy/zha-device-handlers#5135 and #5317 (not merged),
  which put the same bytes on the air.

Protocol
--------
ZCL cluster-specific command 0x01 (``protocolData``) on the private cluster
0xFC11 of endpoint 1, client to server, manufacturer specific with the eWeLink
code 0x1286, Default Response requested. The argument is 11 raw bytes (Z2M
``LIST_UINT8``: no length prefix)::

    [0]     0x01  command
    [1]     0x17  sub-command (inching)
    [2]     0x07  length of the fields that follow the sequence byte
    [3]     0x80  sequence (constant)
    [4]     mode: 0x80 if inching is enabled, | 0x01 for "turn on after off"
    [5]     0x00  channel 1
    [6..7]  inching time in half-seconds, uint16 little-endian (1..7199)
    [8..9]  0x00  reserved
    [10]    XOR of bytes [0..9]

Write-only setting
------------------
The device never reports its inching configuration, so the three entities live
on a local cluster and show the last values Home Assistant sent successfully.
zigpy persists them in its attribute cache and restores them on restart.

Coexistence with the built-in quirk
-----------------------------------
ZHA's registry inserts every new entry in front of the existing ones, so the
most recently registered match wins. This module imports the built-in quirk
before registering its own entry (ordering guaranteed whatever the import order
of Home Assistant), reuses the built-in ``SonoffCluster`` and replicates the
four built-in entities verbatim, so their unique_ids are unchanged.

For the ZBMINIR2 the override replaces the WHOLE built-in registry entry, so
it only registers while that entry is exactly the one it was written against:
the 0xFC11 attribute set must be the expected one AND the registered built-in
entry must equal the built-in recipe compiled anew (models, filters, cluster
replacements, entities, triggers, names). Any upstream change (e.g. upstream
merges inching, adds an entity or device triggers) makes the override register
nothing, with a warning, and the upstream quirk stays in charge.
"""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any, Final, NamedTuple

from zha.quirks import DeviceRegistry, QuirkRegistryEntry
from zhaquirks import ZHA_DEVICE_REGISTRY, LocalDataCluster
from zhaquirks.builder import NumberDeviceClass, QuirkBuilder, UnitOfTime
import zigpy.types as t
from zigpy.zcl import foundation
from zigpy.zcl.foundation import BaseAttributeDefs, ZCLAttributeDef

# Imported for its side effect BEFORE our entry is registered: the built-in
# quirk registers itself when its module is imported, and ZHA's registry
# resolves to the LAST registered matching entry. Importing it here guarantees
# it is registered before ours however Home Assistant orders the imports;
# zhaquirks.setup() importing it again later is a no-op. If upstream removes
# the module, the compatibility gate below refuses to register anything.
try:
    import zhaquirks.sonoff.zbminir2 as _builtin_zbminir2
except ImportError:
    _builtin_zbminir2 = None

_LOGGER = logging.getLogger(__name__)

MANUFACTURER: Final = "SONOFF"
MODEL: Final = "ZBMINIR2"
#: The other model the built-in quirk covers. It keeps the built-in quirk.
BUILTIN_OTHER_MODEL: Final = "MINI-ZBD"

#: Private eWeLink cluster of the device; the inching command is sent here.
SONOFF_CLUSTER_ID: Final = 0xFC11
#: Local (virtual) cluster holding the inching settings. 0xFBFC/0xFBFD are the
#: SWV-ZF2 local clusters; the ZBMINIR2 itself exposes 0x0000, 0x0003, 0x0004,
#: 0x0005, 0x0006, 0x0B05, 0xFC57, 0xFC11 (in) and 0x0003, 0x0006, 0x0019 (out).
INCHING_CLUSTER_ID: Final = 0xFBFE

#: Shenzhen CoolKit (eWeLink) manufacturer code.
EWELINK_MANUFACTURER_CODE: Final = 0x1286
#: ``protocolData`` cluster-specific command id on 0xFC11.
PROTOCOL_DATA_COMMAND_ID: Final = 0x01

#: Inching time range of the ZBMINIR2, in half-seconds (0.5 s .. 3599.5 s).
INCHING_TIME_MIN: Final = 1
INCHING_TIME_MAX: Final = 7199

#: Exact attribute set of the built-in SonoffCluster this override was written
#: against. Any difference means upstream changed and the override must retire.
EXPECTED_BUILTIN_ATTRIBUTES: Final = frozenset(
    {"external_trigger_mode", "detach_relay", "turbo_mode", "network_led"}
)

_PAYLOAD_COMMAND: Final = 0x01
_PAYLOAD_SUBCOMMAND: Final = 0x17
_PAYLOAD_LENGTH: Final = 0x07
_PAYLOAD_SEQUENCE: Final = 0x80
_PAYLOAD_CHANNEL_1: Final = 0x00
_MODE_ENABLED_BIT: Final = 0x80
_MODE_TURN_ON_AFTER_OFF_BIT: Final = 0x01


class InchingMode(t.enum8):
    """What the relay does on its own once the inching time has elapsed."""

    #: The relay turns ON, then OFF again after the time (impulse; default).
    Turn_off_after_on = 0x00
    #: The relay turns OFF, then ON again after the time. Arming this mode may
    #: switch the relay ON immediately.
    Turn_on_after_off = 0x01


#: Defined mode values. zigpy enums turn unknown values into pseudo-members
#: (``undefined_0x05``) instead of raising, so membership is checked here.
_VALID_MODES: Final = frozenset(int(m) for m in InchingMode.__members__.values())


class ProtocolDataCommand(t.Struct):
    """Argument of the eWeLink ``protocolData`` command (Z2M ``LIST_UINT8``)."""

    #: Raw bytes, serialized without a length prefix.
    data: t.List[t.uint8_t]


def build_inching_payload(*, enabled: Any, mode: Any, half_seconds: Any) -> bytes:
    """Build the 11-byte ``protocolData`` payload configuring inching.

    `enabled` is a boolean (0/1 accepted), `mode` an `InchingMode` value and
    `half_seconds` the inching time as an integer number of half-seconds.
    Raises ValueError on any out-of-range or mistyped field.
    """
    if enabled not in (True, False):
        raise ValueError(f"inching enabled flag must be a boolean, got {enabled!r}")
    if isinstance(mode, bool) or not isinstance(mode, int) or mode not in _VALID_MODES:
        raise ValueError(
            f"inching mode must be one of {sorted(_VALID_MODES)}, got {mode!r}"
        )
    if isinstance(half_seconds, bool) or not isinstance(half_seconds, int):
        raise ValueError(
            "inching time must be an integer number of half-seconds, "
            f"got {half_seconds!r}"
        )
    if not INCHING_TIME_MIN <= half_seconds <= INCHING_TIME_MAX:
        raise ValueError(
            f"inching time must be {INCHING_TIME_MIN}..{INCHING_TIME_MAX} "
            f"half-seconds (0.5..3599.5 s), got {half_seconds}"
        )

    flags = _MODE_ENABLED_BIT if enabled else 0x00
    if int(mode) == InchingMode.Turn_on_after_off:
        flags |= _MODE_TURN_ON_AFTER_OFF_BIT

    payload = bytearray(
        [
            _PAYLOAD_COMMAND,
            _PAYLOAD_SUBCOMMAND,
            _PAYLOAD_LENGTH,
            _PAYLOAD_SEQUENCE,
            flags,
            _PAYLOAD_CHANNEL_1,
            half_seconds & 0xFF,
            (half_seconds >> 8) & 0xFF,
            0x00,
            0x00,
        ]
    )
    checksum = 0
    for byte in payload:
        checksum ^= byte
    payload.append(checksum)
    return bytes(payload)


def _as_int(value: Any, name: str) -> int:
    """Coerce an integral value (int, bool, enum, integral float or str)."""
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{name}: expected an integer, got {value!r}")
        return int(value)
    if isinstance(value, str):
        value = value.strip()
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}: expected an integer, got {value!r}") from exc


def _coerce_enabled(value: Any) -> t.Bool:
    """Coerce what the switch entity sends (1/0) or a boolean."""
    number = _as_int(value, "inching_enabled")
    if number not in (0, 1):
        raise ValueError(f"inching_enabled: expected 0/1 or a boolean, got {value!r}")
    return t.Bool(number)


def _coerce_time(value: Any) -> t.uint16_t:
    """Coerce what the number entity sends: raw half-seconds."""
    number = _as_int(value, "inching_time")
    if not INCHING_TIME_MIN <= number <= INCHING_TIME_MAX:
        raise ValueError(
            f"inching_time: expected {INCHING_TIME_MIN}..{INCHING_TIME_MAX} "
            f"half-seconds (0.5..3599.5 s), got {value!r}"
        )
    return t.uint16_t(number)


def _coerce_mode(value: Any) -> InchingMode:
    """Coerce what the select entity sends (an InchingMode), a name or a value."""
    if isinstance(value, str):
        member = InchingMode.__members__.get(value.strip().replace(" ", "_"))
        if member is not None:
            return member
    try:
        number = _as_int(value, "inching_mode")
    except ValueError:
        number = None
    if number not in _VALID_MODES:
        options = ", ".join(f"{m.name} ({int(m)})" for m in InchingMode)
        raise ValueError(f"inching_mode: expected one of {options}, got {value!r}")
    return InchingMode(number)


#: Attribute name -> coercer, in payload order (enabled, time, mode).
_COERCERS: Final = {
    "inching_enabled": _coerce_enabled,
    "inching_time": _coerce_time,
    "inching_mode": _coerce_mode,
}


class SonoffInchingCluster(LocalDataCluster):
    """Local cluster holding the inching settings last accepted by the relay.

    Each entity writes one attribute, the device wants all three in a single
    command: a write is validated, merged with the cached values, sent as one
    ``protocolData`` command on the real 0xFC11 cluster of the same endpoint,
    and cached only once the device has accepted it.
    """

    cluster_id = INCHING_CLUSTER_ID
    ep_attribute = "sonoff_inching"

    class AttributeDefs(BaseAttributeDefs):
        """Virtual attributes: they are never read from or written to the device."""

        inching_enabled: Final = ZCLAttributeDef(id=0x0000, type=t.Bool)
        #: Raw half-seconds; the number entity scales it by 0.5.
        inching_time: Final = ZCLAttributeDef(id=0x0001, type=t.uint16_t)
        inching_mode: Final = ZCLAttributeDef(id=0x0002, type=InchingMode)

    _DEFAULT_VALUES = {
        AttributeDefs.inching_enabled.id: t.Bool.false,
        AttributeDefs.inching_time.id: t.uint16_t(2),
        AttributeDefs.inching_mode.id: InchingMode.Turn_off_after_on,
    }
    _VALID_ATTRIBUTES = set(_DEFAULT_VALUES)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the cluster and the lock serializing writes."""
        super().__init__(*args, **kwargs)
        # Without serialization two entities written together would both merge
        # with the same stale cache and the second command would undo the first.
        self._write_lock = asyncio.Lock()

    async def write_attributes(
        self,
        attributes: dict[str | int | ZCLAttributeDef, Any],
        manufacturer: Any = None,
        **kwargs: Any,
    ) -> list[list[foundation.WriteAttributesStatusRecord]]:
        """Validate, merge and send the inching settings as one command.

        `manufacturer` is ignored: the command always carries the eWeLink
        code. Bad input raises ValueError before anything is sent. A
        non-SUCCESS Default Response is returned as failure records; transport
        errors propagate. In both cases the cache is left untouched.
        """
        requested = self._coerce_request(attributes)
        if not requested:
            return [
                [foundation.WriteAttributesStatusRecord(foundation.Status.SUCCESS)]
            ]
        written_ids = [self.find_attribute(name).id for name in requested]

        async with self._write_lock:
            effective = {
                name: requested[name] if name in requested else coerce(self.get(name))
                for name, coerce in _COERCERS.items()
            }
            payload = build_inching_payload(
                enabled=bool(effective["inching_enabled"]),
                mode=effective["inching_mode"],
                half_seconds=int(effective["inching_time"]),
            )
            response = await self._send_protocol_data(payload)

            # zigpy returns the matched reply; for this command it is a ZCL
            # Default Response (command_id, status).
            status = getattr(response, "status", None)
            if status is not None and status != foundation.Status.SUCCESS:
                status = foundation.Status(status)
                self.warning("Device rejected the inching command: %s", status)
                return [
                    [
                        foundation.WriteAttributesStatusRecord(status, attrid)
                        for attrid in written_ids
                    ]
                ]

            # Cache all three effective values, not only the written ones, so
            # every entity shows (and zigpy persists) what the device now has.
            for name, value in effective.items():
                self._update_attribute(self.find_attribute(name).id, value)

        return [
            [
                foundation.WriteAttributesStatusRecord(
                    foundation.Status.SUCCESS, attrid
                )
                for attrid in written_ids
            ]
        ]

    def _coerce_request(
        self, attributes: dict[str | int | ZCLAttributeDef, Any]
    ) -> dict[str, Any]:
        """Resolve keys (name, id or definition) and coerce/validate values."""
        requested: dict[str, Any] = {}
        for key, value in attributes.items():
            try:
                name = self.find_attribute(key).name
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError(f"unknown inching attribute: {key!r}") from exc
            if name not in _COERCERS:
                raise ValueError(f"unknown inching attribute: {key!r}")
            requested[name] = _COERCERS[name](value)
        return requested

    async def _send_protocol_data(self, payload: bytes) -> Any:
        """Send `payload` as ``protocolData`` on the endpoint's 0xFC11 cluster."""
        sonoff_cluster = self.endpoint.in_clusters.get(SONOFF_CLUSTER_ID)
        if sonoff_cluster is None:
            raise RuntimeError(
                f"cluster 0x{SONOFF_CLUSTER_ID:04X} is missing on endpoint "
                f"{self.endpoint.endpoint_id}: cannot send the inching command"
            )
        self.debug("Sending inching protocolData payload: %s", payload.hex(" "))
        return await sonoff_cluster.request(
            False,
            PROTOCOL_DATA_COMMAND_ID,
            ProtocolDataCommand,
            list(payload),
            manufacturer=EWELINK_MANUFACTURER_CODE,
        )


def _builtin_attribute_names(builtin: Any) -> frozenset[str] | None:
    """Return the attribute names of the built-in SonoffCluster, None if absent."""
    try:
        return frozenset(attr.name for attr in builtin.SonoffCluster.AttributeDefs)
    except Exception:  # fail-safe: any surprise means "changed"
        return None


def builtin_quirk_is_compatible(builtin: Any) -> bool:
    """Return True if the built-in quirk module has exactly the expected shape.

    This is the module half of the compatibility gate (the registry entry half
    is `builtin_entry_is_expected`). The override reuses the built-in
    ``SonoffCluster`` (0xFC11) and ``SonoffExternalSwitchTriggerType``, so it
    is only safe while that cluster has exactly the four attributes it was
    written against. Anything else (upstream added inching, renamed or removed
    an attribute, removed the module) returns False.
    """
    names = _builtin_attribute_names(builtin)
    if names != EXPECTED_BUILTIN_ATTRIBUTES:
        return False
    if not hasattr(builtin, "SonoffExternalSwitchTriggerType"):
        return False
    return getattr(builtin.SonoffCluster, "cluster_id", None) == SONOFF_CLUSTER_ID


def _add_builtin_entities(builder: QuirkBuilder, builtin: Any) -> QuirkBuilder:
    """Add the four entities of the built-in quirk, exactly as it defines them.

    Same attribute names, hence the same unique_ids "{ieee}-1-<attribute_name>",
    so entities the user renamed keep their entity_id and history. Shared by
    the override and by the replica the gate compares the registered built-in
    entry with: the override only registers while this copy is verified
    identical to upstream's at runtime.
    """
    sonoff_cluster = builtin.SonoffCluster
    attrs = sonoff_cluster.AttributeDefs
    return (
        builder.enum(
            attrs.external_trigger_mode.name,
            builtin.SonoffExternalSwitchTriggerType,
            sonoff_cluster.cluster_id,
            translation_key="external_trigger_mode",
            fallback_name="External trigger mode",
        )
        .switch(
            attrs.turbo_mode.name,
            sonoff_cluster.cluster_id,
            off_value=9,
            on_value=20,
            translation_key="turbo_mode",
            fallback_name="Turbo mode",
        )
        .switch(
            attrs.detach_relay.name,
            sonoff_cluster.cluster_id,
            translation_key="detach_relay",
            fallback_name="Detach relay",
        )
        .switch(
            attrs.network_led.name,
            sonoff_cluster.cluster_id,
            translation_key="network_led",
            fallback_name="Network LED",
        )
    )


def _builtin_replica(builtin: Any) -> QuirkRegistryEntry:
    """Compile the built-in quirk as this override knows it, off the real registry."""
    builder = (
        QuirkBuilder(MANUFACTURER, MODEL, registry=DeviceRegistry())
        .applies_to(MANUFACTURER, BUILTIN_OTHER_MODEL)
        .replaces(builtin.SonoffCluster)
    )
    return _add_builtin_entities(builder, builtin).add_to_registry()


def find_builtin_entry(
    builtin: Any, registry: DeviceRegistry
) -> QuirkRegistryEntry | None:
    """Return the entry the built-in module registered for the ZBMINIR2, or None.

    Found by provenance (``source.module``) rather than by resolving a probe,
    since another ZBMINIR2 quirk (e.g. from ``custom_quirks_path``) may be
    winning already. None unless exactly one entry qualifies.
    """
    module_name = getattr(builtin, "__name__", None)
    if not isinstance(module_name, str) or not module_name:
        return None
    try:
        found = [
            entry
            for entry in registry
            if getattr(getattr(entry, "source", None), "module", None) == module_name
            and (MANUFACTURER, MODEL) in tuple(entry.device_match.applies_to)
        ]
    except Exception:  # fail-safe: an unreadable registry means "not found"
        _LOGGER.debug("Could not look up the built-in ZBMINIR2 quirk", exc_info=True)
        return None
    return found[0] if len(found) == 1 else None


#: Registry entry fields compared by the gate, with a label for the warning.
_ENTRY_FIELDS: Final = {
    "device_match": "models/filters",
    "zigpy_transforms": "cluster and endpoint changes",
    "zha_device_factory": "entities and device metadata",
}


def _builtin_entry_mismatch(builtin: Any, entry: Any) -> str | None:
    """Return None if `entry` is the expected built-in entry, else what differs."""
    try:
        replica = _builtin_replica(builtin)
        # QuirkRegistryEntry equality covers everything but the provenance.
        if (entry == replica) is True:
            return None
        differing = [
            label
            for name, label in _ENTRY_FIELDS.items()
            if getattr(entry, name, None) != getattr(replica, name, None)
        ]
    except Exception:  # fail-safe: a failed comparison means "changed"
        _LOGGER.debug("Could not compare the built-in ZBMINIR2 quirk", exc_info=True)
        return "comparison failed"
    return ", ".join(differing) or "registry entry type"


def builtin_entry_is_expected(builtin: Any, entry: Any) -> bool:
    """Return True if `entry` is exactly the built-in quirk this override replicates.

    The override replaces the WHOLE built-in entry for the ZBMINIR2, so the
    0xFC11 attribute check alone is not enough: an upstream release adding an
    entity, a cluster replacement, device triggers or a firmware filter, or
    changing a translation key or the models, would be silently masked.
    `entry` must equal the built-in recipe compiled anew (registry-entry
    equality: models, filters, zigpy transforms and the whole ZHA quirk
    definition, provenance excluded). Any error means "not expected".
    """
    return _builtin_entry_mismatch(builtin, entry) is None


def _quirk_builder(builtin: Any) -> QuirkBuilder:
    """Build the override on top of the built-in module's classes."""
    inching = SonoffInchingCluster.AttributeDefs
    builder = (
        # ZBMINIR2 only: MINI-ZBD keeps the built-in quirk.
        QuirkBuilder(MANUFACTURER, MODEL)
        .replaces(builtin.SonoffCluster)
        .adds(SonoffInchingCluster)
    )
    return (
        _add_builtin_entities(builder, builtin)
        # Inching (write-only on the device, see the module docstring).
        .switch(
            inching.inching_enabled.name,
            SonoffInchingCluster.cluster_id,
            translation_key="inching_enabled",
            fallback_name="Inching",
        )
        .number(
            inching.inching_time.name,
            SonoffInchingCluster.cluster_id,
            min_value=0.5,
            max_value=3599.5,
            step=0.5,
            multiplier=0.5,
            mode="box",
            unit=UnitOfTime.SECONDS,
            device_class=NumberDeviceClass.DURATION,
            translation_key="inching_time",
            fallback_name="Inching time",
        )
        .enum(
            inching.inching_mode.name,
            InchingMode,
            SonoffInchingCluster.cluster_id,
            translation_key="inching_mode",
            fallback_name="Inching mode",
        )
    )


def resolves_to(entry: QuirkRegistryEntry, registry: DeviceRegistry) -> bool:
    """Return True if `registry` resolves a SONOFF ZBMINIR2 to exactly `entry`."""
    probe = SimpleNamespace(manufacturer=MANUFACTURER, model=MODEL)
    try:
        return registry.match_entry(probe) is entry
    except Exception:  # a failing self-check means "not ours"
        _LOGGER.debug("ZBMINIR2 registry self-check failed", exc_info=True)
        return False


def _withdraw(entry: QuirkRegistryEntry, registry: DeviceRegistry) -> None:
    """Remove `entry` from `registry` if it is (still) registered there."""
    try:
        if any(candidate is entry for candidate in registry):
            registry.remove(entry)
    except Exception:  # best effort, already logged by caller
        _LOGGER.debug("Could not withdraw the ZBMINIR2 override", exc_info=True)


class Registration(NamedTuple):
    """Outcome of a successful `register_inching_quirk`."""

    #: The override's registry entry.
    entry: QuirkRegistryEntry
    #: The built-in entry it overrides for the ZBMINIR2, verified unchanged.
    builtin_entry: QuirkRegistryEntry


def register_inching_quirk(
    builtin: Any, registry: DeviceRegistry | None = None
) -> Registration | None:
    """Register the ZBMINIR2 inching override; return the entries or None.

    `registry` defaults to ZHA's global one. Nothing is registered unless the
    built-in module has the expected shape and its entry, registered exactly
    once in `registry`, is exactly the expected one. After registering, the
    registry must resolve a ZBMINIR2 to the new entry, otherwise the entry is
    withdrawn and None is returned.
    """
    target = ZHA_DEVICE_REGISTRY if registry is None else registry
    if not builtin_quirk_is_compatible(builtin):
        found = _builtin_attribute_names(builtin)
        _LOGGER.warning(
            "The built-in ZHA quirk for SONOFF ZBMINIR2 has changed (0xFC11 "
            "attributes: %s; expected: %s): the inching override is not "
            "registered and the built-in quirk stays in charge",
            sorted(found) if found is not None else "unavailable",
            sorted(EXPECTED_BUILTIN_ATTRIBUTES),
        )
        return None

    builtin_entry = find_builtin_entry(builtin, target)
    if builtin_entry is None:
        _LOGGER.warning(
            "The built-in ZHA quirk for SONOFF ZBMINIR2 is not registered "
            "exactly once in the ZHA quirk registry: the inching override is "
            "not registered"
        )
        return None
    mismatch = _builtin_entry_mismatch(builtin, builtin_entry)
    if mismatch is not None:
        _LOGGER.warning(
            "The built-in ZHA quirk for SONOFF ZBMINIR2 has changed (%s differ "
            "from what the inching override replicates): the override is not "
            "registered and the built-in quirk stays in charge",
            mismatch,
        )
        return None

    entry = _quirk_builder(builtin).add_to_registry(target)
    if resolves_to(entry, target):
        _LOGGER.debug("SONOFF ZBMINIR2 inching override registered")
        return Registration(entry, builtin_entry)

    _LOGGER.warning(
        "The ZHA quirk registry does not resolve SONOFF ZBMINIR2 to the inching "
        "override after registering it: withdrawing the override"
    )
    _withdraw(entry, target)
    return None


def quirk_is_effective() -> bool:
    """Return True if a ZBMINIR2 resolved now would get this override.

    False if the override was not registered or if another quirk for the same
    model was registered after it (e.g. a user custom quirk).
    """
    return REGISTRY_ENTRY is not None and resolves_to(
        REGISTRY_ENTRY, ZHA_DEVICE_REGISTRY
    )


_REGISTRATION: Final = register_inching_quirk(_builtin_zbminir2, ZHA_DEVICE_REGISTRY)
#: Registry entry of the override, or None if it was not registered.
REGISTRY_ENTRY: Final = _REGISTRATION.entry if _REGISTRATION is not None else None
#: The built-in quirk's entry the override replaces for the ZBMINIR2 (None if
#: not registered). A relay resolved to THIS entry, by identity, is one a ZHA
#: reload moves onto the override; a relay on any other entry is not.
BUILTIN_ENTRY: Final = (
    _REGISTRATION.builtin_entry if _REGISTRATION is not None else None
)
#: Whether the override is registered.
REGISTERED: Final = REGISTRY_ENTRY is not None
