"""Test della quirk SONOFF ZBMINIR2 con inching (override della quirk built-in).

Il frame di rete e' verificato contro i vettori calcolati traslitterando il
convertitore TypeScript di Zigbee2MQTT (`inchingControlSet`). La risposta del
dispositivo e' simulata reiniettando una ZCL Default Response nella pipeline
reale di zigpy (`Device.packet_received`), cosi' il test copre anche il
matching richiesta/risposta e il tipo di oggetto che `Cluster.request` ritorna.
"""

from __future__ import annotations

import asyncio
import contextvars
import dataclasses
import importlib.abc
import importlib.util
import logging
import pathlib
import sys
import types
from unittest import mock

from conftest import build_zigpy_device
import pytest
import sonoff_zbminir2
from sonoff_zbminir2 import (
    EWELINK_MANUFACTURER_CODE,
    EXPECTED_BUILTIN_ATTRIBUTES,
    INCHING_CLUSTER_ID,
    PROTOCOL_DATA_COMMAND_ID,
    SONOFF_CLUSTER_ID,
    InchingMode,
    SonoffInchingCluster,
    build_inching_payload,
    builtin_entry_is_expected,
    builtin_quirk_is_compatible,
    find_builtin_entry,
    quirk_is_effective,
    register_inching_quirk,
    resolves_to,
)
from zha.application.helpers import write_attributes_safe
from zha.exceptions import ZHAException
from zha.quirks import (
    QUIRK_REGISTRY_ENTRY_ATTR,
    DeviceMatch,
    DeviceRegistry,
    ModelInfo,
    QuirkRegistryEntry,
)
import zhaquirks
from zhaquirks import ZHA_DEVICE_REGISTRY
from zhaquirks.builder import (
    EntityPlatform,
    EntityType,
    NumberDeviceClass,
    QuirkBuilder,
)
from zhaquirks.clusters import CustomCluster
import zhaquirks.sonoff.zbminir2 as builtin_zbminir2
import zigpy.types as t
from zigpy.zcl import AttributeUpdatedEvent, foundation
from zigpy.zcl.clusters.general import OnOff

_QUIRKS_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "zha_sonoff_quirks"
    / "quirks"
)

#: Signature dell'endpoint 1 dei due ZBMINIR2 reali (cluster letti dal device
#: registry di ZHA). Il device_type non e' stato catturato: il match della
#: quirk v2 e' su manufacturer/model, quindi qui e' solo un segnaposto.
ZBMINIR2_SIGNATURE = {
    1: {
        "profile_id": 260,
        "device_type": 0x0100,
        "in_clusters": [
            0x0000, 0x0003, 0x0004, 0x0005, 0x0006, 0x0B05, 0xFC57, 0xFC11,
        ],
        "out_clusters": [0x0003, 0x0006, 0x0019],
    },
}

#: (enabled, mode, mezzi secondi, payload atteso): calcolati traslitterando
#: `inchingControlSet` di Z2M (sonoff.ts, master c46f0b49).
GOLDEN_VECTORS = [
    (True, InchingMode.Turn_off_after_on, 1, "01 17 07 80 80 00 01 00 00 00 10"),
    (True, InchingMode.Turn_off_after_on, 2, "01 17 07 80 80 00 02 00 00 00 13"),
    (True, InchingMode.Turn_off_after_on, 360, "01 17 07 80 80 00 68 01 00 00 78"),
    (True, InchingMode.Turn_off_after_on, 7199, "01 17 07 80 80 00 1f 1c 00 00 12"),
    (False, InchingMode.Turn_off_after_on, 2, "01 17 07 80 00 00 02 00 00 00 93"),
    (True, InchingMode.Turn_on_after_off, 120, "01 17 07 80 81 00 78 00 00 00 68"),
    (False, InchingMode.Turn_on_after_off, 1, "01 17 07 80 01 00 01 00 00 00 91"),
]

BUILTIN_ATTRIBUTE_NAMES = {
    "external_trigger_mode",
    "detach_relay",
    "turbo_mode",
    "network_led",
}

INCHING_ATTRIBUTE_IDS = {0x0000, 0x0001, 0x0002}


# --------------------------------------------------------------------------- #
# Simulazione del dispositivo
# --------------------------------------------------------------------------- #


@dataclasses.dataclass
class SentFrame:
    """Un frame APS consegnato al ControllerApplication finto."""

    cluster: int
    src_ep: int
    dst_ep: int
    sequence: int
    data: bytes
    expect_reply: bool


class FakeSonoffDevice:
    """Cattura i frame inviati e risponde come il relay: una Default Response.

    La risposta passa da `Device.packet_received`, cioe' dalla stessa pipeline
    che zigpy usa con una radio vera, e risolve la richiesta pendente.
    """

    def __init__(
        self,
        app: mock.MagicMock,
        status: foundation.Status = foundation.Status.SUCCESS,
        *,
        yield_before_reply: bool = False,
    ) -> None:
        """Aggancia la simulazione a `app.request`."""
        self.frames: list[SentFrame] = []
        self.status = status
        self.yield_before_reply = yield_before_reply
        # Attributo d'istanza del ControllerApplication reale, letto da
        # Device._limit_concurrency sul path di richiesta con risposta.
        app._packet_priority_var = contextvars.ContextVar(
            "request_priority", default=t.PacketPriority.NORMAL
        )
        app.request = mock.AsyncMock(side_effect=self._request)

    async def _request(
        self,
        *,
        device,
        profile,
        cluster,
        src_ep,
        dst_ep,
        sequence,
        data,
        expect_reply=True,
        **kwargs,
    ):
        self.frames.append(
            SentFrame(
                cluster=cluster,
                src_ep=src_ep,
                dst_ep=dst_ep,
                sequence=sequence,
                data=bytes(data),
                expect_reply=expect_reply,
            )
        )
        if self.yield_before_reply:
            # Lascia girare le altre coroutine prima della risposta, come
            # farebbe la latenza radio.
            await asyncio.sleep(0)

        request_hdr, _ = foundation.ZCLHeader.deserialize(bytes(data))
        reply_hdr = foundation.ZCLHeader(
            frame_control=foundation.FrameControl(
                frame_type=foundation.FrameType.GLOBAL_COMMAND,
                is_manufacturer_specific=True,
                direction=foundation.Direction.Server_to_Client,
                disable_default_response=True,
                reserved=0,
            ),
            manufacturer=request_hdr.manufacturer,
            tsn=request_hdr.tsn,
            command_id=foundation.GeneralCommand.Default_Response,
        )
        reply = (
            reply_hdr.serialize()
            + foundation.DefaultResponse(
                command_id=request_hdr.command_id, status=self.status
            ).serialize()
        )
        device.packet_received(
            t.ZigbeePacket(
                src=t.AddrModeAddress(addr_mode=t.AddrMode.NWK, address=device.nwk),
                src_ep=dst_ep,
                dst=t.AddrModeAddress(addr_mode=t.AddrMode.NWK, address=0x0000),
                dst_ep=src_ep,
                tsn=request_hdr.tsn,
                profile_id=profile,
                cluster_id=cluster,
                data=t.SerializableBytes(reply),
                lqi=255,
                rssi=-40,
            )
        )
        return (foundation.Status.SUCCESS, "done")


def _expected_frame(sequence: int, payload_hex: str) -> bytes:
    """Header ZCL atteso (05 86 12 <tsn> 01) seguito dal payload."""
    return bytes([0x05, 0x86, 0x12, sequence, 0x01]) + bytes.fromhex(payload_hex)


def _probe(model: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(manufacturer="SONOFF", model=model)


def _entity_metadata(entry: QuirkRegistryEntry) -> tuple:
    return entry.zha_device_factory.quirk_definition.entity_metadata


def _builtin_entry() -> QuirkRegistryEntry:
    """Entry della quirk built-in, ricavata risolvendo un MINI-ZBD."""
    entry = ZHA_DEVICE_REGISTRY.match_entry(_probe("MINI-ZBD"))
    assert entry is not None
    assert entry.source.module == builtin_zbminir2.__name__
    return entry


@pytest.fixture
def zbminir2(app):
    """ZBMINIR2 con la quirk risolta dal registry globale."""
    device = build_zigpy_device(
        app, model="ZBMINIR2", signature=ZBMINIR2_SIGNATURE
    )
    return ZHA_DEVICE_REGISTRY.resolve(device)


@pytest.fixture
def inching(zbminir2) -> SonoffInchingCluster:
    """Cluster locale di inching dell'endpoint 1."""
    return zbminir2.endpoints[1].sonoff_inching


@pytest.fixture
def relay(app) -> FakeSonoffDevice:
    """Relay simulato che accetta il comando (Default Response SUCCESS)."""
    return FakeSonoffDevice(app)


def _cached(cluster: SonoffInchingCluster) -> dict:
    return {
        name: cluster.get(name)
        for name in ("inching_enabled", "inching_time", "inching_mode")
    }


# --------------------------------------------------------------------------- #
# Payload puro
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("enabled", "mode", "half_seconds", "expected"), GOLDEN_VECTORS
)
def test_payload_matches_z2m_golden_vectors(enabled, mode, half_seconds, expected):
    payload = build_inching_payload(
        enabled=enabled, mode=mode, half_seconds=half_seconds
    )

    assert payload == bytes.fromhex(expected)
    assert len(payload) == 11


def test_payload_checksum_is_xor_of_first_ten_bytes():
    payload = build_inching_payload(
        enabled=True, mode=InchingMode.Turn_on_after_off, half_seconds=4321
    )
    checksum = 0
    for byte in payload[:10]:
        checksum ^= byte

    assert payload[10] == checksum


@pytest.mark.parametrize("half_seconds", [0, 7200, -1, 0x10000])
def test_payload_rejects_out_of_range_time(half_seconds):
    with pytest.raises(ValueError, match="half-seconds"):
        build_inching_payload(
            enabled=True,
            mode=InchingMode.Turn_off_after_on,
            half_seconds=half_seconds,
        )


@pytest.mark.parametrize("half_seconds", [1.5, "2", True, None])
def test_payload_rejects_non_integer_time(half_seconds):
    with pytest.raises(ValueError):
        build_inching_payload(
            enabled=True,
            mode=InchingMode.Turn_off_after_on,
            half_seconds=half_seconds,
        )


@pytest.mark.parametrize("mode", [2, 0x80, 0x81, -1, InchingMode(5), None])
def test_payload_rejects_bad_mode(mode):
    with pytest.raises(ValueError, match="mode"):
        build_inching_payload(enabled=True, mode=mode, half_seconds=2)


@pytest.mark.parametrize("enabled", [2, -1, "yes", None])
def test_payload_rejects_bad_enabled(enabled):
    with pytest.raises(ValueError, match="enabled"):
        build_inching_payload(
            enabled=enabled, mode=InchingMode.Turn_off_after_on, half_seconds=2
        )


# --------------------------------------------------------------------------- #
# Write path: frame inviato
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_write_sends_full_frame_on_fc11(zbminir2, inching, relay):
    """Il frame e' esattamente header 05 86 12 <tsn> 01 + payload Z2M."""
    result = await inching.write_attributes(
        {
            "inching_enabled": 1,
            "inching_time": 360,
            "inching_mode": InchingMode.Turn_off_after_on,
        }
    )

    assert len(relay.frames) == 1
    frame = relay.frames[0]
    assert frame.cluster == SONOFF_CLUSTER_ID == 0xFC11
    assert frame.src_ep == frame.dst_ep == 1
    assert frame.expect_reply is True
    assert frame.data == _expected_frame(
        frame.sequence, "01 17 07 80 80 00 68 01 00 00 78"
    )
    # Nessun traffico verso il cluster locale (non esiste sul dispositivo).
    assert all(f.cluster != INCHING_CLUSTER_ID for f in relay.frames)
    assert [r.status for r in result[0]] == [foundation.Status.SUCCESS] * 3


def test_frame_constants_match_protocol():
    assert EWELINK_MANUFACTURER_CODE == 0x1286
    assert PROTOCOL_DATA_COMMAND_ID == 0x01
    assert INCHING_CLUSTER_ID == 0xFBFE


@pytest.mark.parametrize(
    ("enabled", "mode", "half_seconds", "expected"), GOLDEN_VECTORS
)
@pytest.mark.asyncio
async def test_write_frames_match_golden_vectors(
    zbminir2, inching, relay, enabled, mode, half_seconds, expected
):
    """I valori che mandano le entita' ZHA producono i vettori di Z2M.

    switch -> 1/0, number -> int(valore / 0.5), select -> membro dell'enum.
    """
    await inching.write_attributes(
        {
            "inching_enabled": 1 if enabled else 0,
            "inching_time": int((half_seconds * 0.5) / 0.5),
            "inching_mode": InchingMode[mode.name],
        }
    )

    frame = relay.frames[-1]
    assert frame.data == _expected_frame(frame.sequence, expected)


@pytest.mark.asyncio
async def test_write_accepts_name_id_and_attribute_def_keys(inching, relay):
    defs = SonoffInchingCluster.AttributeDefs
    await inching.write_attributes(
        {
            defs.inching_enabled: True,
            0x0001: 120,
            "inching_mode": "Turn_on_after_off",
        }
    )

    frame = relay.frames[-1]
    assert frame.data == _expected_frame(
        frame.sequence, "01 17 07 80 81 00 78 00 00 00 68"
    )


@pytest.mark.asyncio
async def test_write_accepts_select_option_text_for_mode(inching, relay):
    """Il testo dell'opzione del select (spazi al posto di _) e' accettato."""
    await inching.write_attributes({"inching_mode": "Turn on after off"})

    assert inching.get("inching_mode") == InchingMode.Turn_on_after_off


@pytest.mark.asyncio
async def test_write_through_zha_helper_succeeds(inching, relay):
    """Il contratto di ritorno e' quello atteso da write_attributes_safe di ZHA."""
    await write_attributes_safe(inching, {"inching_time": 360})

    assert inching.get("inching_time") == 360


@pytest.mark.asyncio
async def test_empty_write_sends_nothing(inching, relay):
    result = await inching.write_attributes({})

    assert relay.frames == []
    assert result[0][0].status == foundation.Status.SUCCESS


# --------------------------------------------------------------------------- #
# Validazione
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw_time", [0, 7200, -2, 2.5, "abc"])
@pytest.mark.asyncio
async def test_write_rejects_bad_time(inching, relay, raw_time):
    before = _cached(inching)

    with pytest.raises(ValueError, match="inching_time"):
        await inching.write_attributes({"inching_time": raw_time})

    assert relay.frames == []
    assert _cached(inching) == before


@pytest.mark.parametrize("mode", [2, 0x80, "Turn_sideways", 1.5])
@pytest.mark.asyncio
async def test_write_rejects_bad_mode(inching, relay, mode):
    with pytest.raises(ValueError, match="inching_mode"):
        await inching.write_attributes({"inching_mode": mode})

    assert relay.frames == []


@pytest.mark.parametrize("enabled", [2, -1, "on"])
@pytest.mark.asyncio
async def test_write_rejects_bad_enabled(inching, relay, enabled):
    with pytest.raises(ValueError, match="inching_enabled"):
        await inching.write_attributes({"inching_enabled": enabled})

    assert relay.frames == []


@pytest.mark.parametrize("key", ["inching_speed", 0x0042])
@pytest.mark.asyncio
async def test_write_rejects_unknown_attribute(inching, relay, key):
    with pytest.raises(ValueError, match="unknown"):
        await inching.write_attributes({key: 1})

    assert relay.frames == []


@pytest.mark.asyncio
async def test_one_bad_value_blocks_the_whole_write(inching, relay):
    """Validazione prima dell'invio: nessun frame parziale."""
    with pytest.raises(ValueError):
        await inching.write_attributes(
            {"inching_enabled": 1, "inching_time": 0}
        )

    assert relay.frames == []
    assert inching.get("inching_enabled") == t.Bool.false


# --------------------------------------------------------------------------- #
# Merge con lo stato in cache
# --------------------------------------------------------------------------- #


def test_defaults_are_inching_off_one_second_turn_off_after_on(inching):
    assert inching.get("inching_enabled") == t.Bool.false
    assert inching.get("inching_time") == 2
    assert inching.get("inching_mode") == InchingMode.Turn_off_after_on


@pytest.mark.asyncio
async def test_first_write_merges_with_defaults(inching, relay):
    await inching.write_attributes({"inching_enabled": 1})

    frame = relay.frames[-1]
    assert frame.data == _expected_frame(
        frame.sequence, "01 17 07 80 80 00 02 00 00 00 13"
    )


@pytest.mark.asyncio
async def test_writing_only_time_keeps_enabled_and_mode(inching, relay):
    await inching.write_attributes(
        {"inching_enabled": 1, "inching_mode": InchingMode.Turn_on_after_off}
    )
    await inching.write_attributes({"inching_time": 120})

    assert len(relay.frames) == 2
    frame = relay.frames[-1]
    assert frame.data == _expected_frame(
        frame.sequence, "01 17 07 80 81 00 78 00 00 00 68"
    )
    assert relay.frames[0].sequence != relay.frames[1].sequence


@pytest.mark.asyncio
async def test_merge_uses_values_restored_as_plain_ints(inching, relay):
    """Dopo un riavvio zigpy ripristina dal DB interi, non membri dell'enum."""
    inching._update_attribute(0x0000, 1)
    inching._update_attribute(0x0002, 1)

    await inching.write_attributes({"inching_time": 1})

    frame = relay.frames[-1]
    assert frame.data == _expected_frame(
        frame.sequence, "01 17 07 80 81 00 01 00 00 00 11"
    )


@pytest.mark.asyncio
async def test_concurrent_writes_do_not_lose_updates(app, inching):
    """Due entita' scritte insieme: la seconda vede la prima, non la cache vecchia."""
    relay = FakeSonoffDevice(app, yield_before_reply=True)

    await asyncio.gather(
        inching.write_attributes({"inching_time": 360}),
        inching.write_attributes({"inching_enabled": 1}),
    )

    assert len(relay.frames) == 2
    last = relay.frames[-1]
    assert last.data == _expected_frame(
        last.sequence, "01 17 07 80 80 00 68 01 00 00 78"
    )
    assert _cached(inching) == {
        "inching_enabled": t.Bool.true,
        "inching_time": 360,
        "inching_mode": InchingMode.Turn_off_after_on,
    }


# --------------------------------------------------------------------------- #
# Esito della scrittura e cache
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_success_updates_all_three_attributes(inching, relay):
    """Anche gli attributi non scritti finiscono in cache (e quindi nel DB)."""
    updated: list = []
    inching.on_event(AttributeUpdatedEvent.event_type, updated.append)

    result = await inching.write_attributes({"inching_time": 360})

    assert {event.attribute_id for event in updated} == INCHING_ATTRIBUTE_IDS
    assert _cached(inching) == {
        "inching_enabled": t.Bool.false,
        "inching_time": 360,
        "inching_mode": InchingMode.Turn_off_after_on,
    }
    assert isinstance(inching.get("inching_enabled"), t.Bool)
    assert isinstance(inching.get("inching_time"), t.uint16_t)
    assert isinstance(inching.get("inching_mode"), InchingMode)
    # Un record per attributo scritto, con l'attrid del cluster locale.
    assert [(r.status, r.attrid) for r in result[0]] == [
        (foundation.Status.SUCCESS, 0x0001)
    ]


@pytest.mark.asyncio
async def test_device_failure_status_leaves_cache_untouched(app, inching):
    """Un firmware che rifiuta il comando non cambia lo stato mostrato da HA."""
    FakeSonoffDevice(app, status=foundation.Status.UNSUP_MANUF_CLUSTER_COMMAND)
    updated: list = []
    inching.on_event(AttributeUpdatedEvent.event_type, updated.append)
    before = _cached(inching)

    result = await inching.write_attributes(
        {"inching_enabled": 1, "inching_time": 360}
    )

    assert [(r.status, r.attrid) for r in result[0]] == [
        (foundation.Status.UNSUP_MANUF_CLUSTER_COMMAND, 0x0000),
        (foundation.Status.UNSUP_MANUF_CLUSTER_COMMAND, 0x0001),
    ]
    assert updated == []
    assert _cached(inching) == before


@pytest.mark.asyncio
async def test_device_failure_surfaces_through_zha_helper(app, inching):
    FakeSonoffDevice(app, status=foundation.Status.UNSUP_MANUF_CLUSTER_COMMAND)

    with pytest.raises(ZHAException, match="inching_time"):
        await write_attributes_safe(inching, {"inching_time": 360})

    assert inching.get("inching_time") == 2


@pytest.mark.parametrize("error", [TimeoutError, RuntimeError("radio down")])
@pytest.mark.asyncio
async def test_exception_leaves_cache_untouched(zbminir2, inching, error):
    fc11 = zbminir2.endpoints[1].in_clusters[SONOFF_CLUSTER_ID]
    updated: list = []
    inching.on_event(AttributeUpdatedEvent.event_type, updated.append)
    before = _cached(inching)

    with mock.patch.object(fc11, "request", side_effect=error) as request:
        with pytest.raises(type(error) if isinstance(error, Exception) else error):
            await inching.write_attributes({"inching_enabled": 1})

    request.assert_awaited_once()
    assert updated == []
    assert _cached(inching) == before


@pytest.mark.asyncio
async def test_request_uses_cluster_command_with_manufacturer_code(
    zbminir2, inching
):
    """Chiamata a Cluster.request: comando di cluster 0x01, codice 0x1286."""
    fc11 = zbminir2.endpoints[1].in_clusters[SONOFF_CLUSTER_ID]
    reply = foundation.DefaultResponse(
        command_id=0x01, status=foundation.Status.SUCCESS
    )
    with mock.patch.object(fc11, "request", return_value=reply) as request:
        await inching.write_attributes({"inching_enabled": 1})

    args, kwargs = request.await_args
    assert args[0] is False
    assert args[1] == PROTOCOL_DATA_COMMAND_ID
    assert args[2](*args[3:]).serialize() == bytes.fromhex(
        "01 17 07 80 80 00 02 00 00 00 13"
    )
    assert kwargs["manufacturer"] == EWELINK_MANUFACTURER_CODE
    # Default Response richiesta, come Z2M/zigbee-herdsman.
    assert kwargs.get("expect_reply", True) is True
    assert kwargs.get("disable_default_response") in (None, False)


@pytest.mark.asyncio
async def test_reply_without_status_counts_as_success(zbminir2, inching):
    fc11 = zbminir2.endpoints[1].in_clusters[SONOFF_CLUSTER_ID]
    with mock.patch.object(fc11, "request", return_value=None):
        result = await inching.write_attributes({"inching_enabled": 1})

    assert result[0][0].status == foundation.Status.SUCCESS
    assert inching.get("inching_enabled") == t.Bool.true


# --------------------------------------------------------------------------- #
# Risoluzione della quirk
# --------------------------------------------------------------------------- #


def test_module_state_reports_registration():
    assert sonoff_zbminir2.REGISTERED is True
    assert isinstance(sonoff_zbminir2.REGISTRY_ENTRY, QuirkRegistryEntry)
    assert quirk_is_effective() is True


def test_module_state_exposes_the_builtin_entry_it_replaces():
    """L'integrazione ricarica ZHA solo per i relè rimasti su QUESTA entry."""
    assert sonoff_zbminir2.BUILTIN_ENTRY is _builtin_entry()
    assert sonoff_zbminir2.BUILTIN_ENTRY is not sonoff_zbminir2.REGISTRY_ENTRY


def test_zbminir2_resolves_to_our_entry(app, zbminir2):
    device = build_zigpy_device(app, model="ZBMINIR2", signature=ZBMINIR2_SIGNATURE)

    assert ZHA_DEVICE_REGISTRY.match_entry(device) is sonoff_zbminir2.REGISTRY_ENTRY
    assert (
        getattr(zbminir2, QUIRK_REGISTRY_ENTRY_ATTR)
        is sonoff_zbminir2.REGISTRY_ENTRY
    )


def test_resolved_device_has_builtin_cluster_and_local_cluster(zbminir2):
    ep1 = zbminir2.endpoints[1]

    # Classe built-in riusata, non ridefinita.
    assert type(ep1.in_clusters[SONOFF_CLUSTER_ID]) is builtin_zbminir2.SonoffCluster
    assert isinstance(ep1.sonoff_inching, SonoffInchingCluster)
    assert ep1.in_clusters[INCHING_CLUSTER_ID] is ep1.sonoff_inching
    assert INCHING_CLUSTER_ID not in ep1.out_clusters
    # Il resto della signature resta intatto.
    for cluster_id in ZBMINIR2_SIGNATURE[1]["in_clusters"]:
        assert cluster_id in ep1.in_clusters


def test_local_cluster_id_does_not_collide_with_device_clusters():
    taken = set(ZBMINIR2_SIGNATURE[1]["in_clusters"]) | set(
        ZBMINIR2_SIGNATURE[1]["out_clusters"]
    )
    # 0xFBFC/0xFBFD sono i cluster locali della SWV-ZF2.
    assert INCHING_CLUSTER_ID not in taken | {0xFBFC, 0xFBFD}


def test_mini_zbd_keeps_builtin_quirk(app):
    device = build_zigpy_device(app, model="MINI-ZBD", signature=ZBMINIR2_SIGNATURE)
    quirked = ZHA_DEVICE_REGISTRY.resolve(device)

    entry = getattr(quirked, QUIRK_REGISTRY_ENTRY_ATTR)
    assert entry is not sonoff_zbminir2.REGISTRY_ENTRY
    assert entry.source.module == builtin_zbminir2.__name__
    ep1 = quirked.endpoints[1]
    assert type(ep1.in_clusters[SONOFF_CLUSTER_ID]) is builtin_zbminir2.SonoffCluster
    assert INCHING_CLUSTER_ID not in ep1.in_clusters
    assert not hasattr(ep1, "sonoff_inching")


def test_our_entry_applies_only_to_zbminir2():
    match = sonoff_zbminir2.REGISTRY_ENTRY.device_match
    assert match.applies_to == (ModelInfo("SONOFF", "ZBMINIR2"),)


def test_other_sonoff_models_are_not_touched(app):
    device = build_zigpy_device(app, model="ZBMINIL2", signature=ZBMINIR2_SIGNATURE)
    quirked = ZHA_DEVICE_REGISTRY.resolve(device)

    assert not hasattr(quirked.endpoints[1], "sonoff_inching")


def test_builtin_entry_is_still_registered():
    """L'override non rimuove la quirk built-in, la precede soltanto."""
    entries = [
        e for e in ZHA_DEVICE_REGISTRY if e.source.module == builtin_zbminir2.__name__
    ]

    assert len(entries) == 1
    assert ModelInfo("SONOFF", "ZBMINIR2") in entries[0].device_match.applies_to


# --------------------------------------------------------------------------- #
# Entita'
# --------------------------------------------------------------------------- #


def test_inherited_entities_are_identical_to_builtin():
    """Le 4 entita' built-in sono replicate campo per campo.

    Il confronto e' con i metadati che la quirk built-in registra davvero, non
    con una copia scritta qui: se upstream li cambia, il test lo segnala.
    """
    builtin_meta = _entity_metadata(_builtin_entry())
    ours = _entity_metadata(sonoff_zbminir2.REGISTRY_ENTRY)

    assert len(builtin_meta) == 4
    assert ours[: len(builtin_meta)] == builtin_meta


def test_inherited_entities_keep_unique_id_suffixes_and_values():
    builtin_meta = _entity_metadata(_builtin_entry())
    ours = _entity_metadata(sonoff_zbminir2.REGISTRY_ENTRY)[: len(builtin_meta)]

    def summary(meta):
        return [
            (
                m.resolved_unique_id_suffix,
                m.attribute_name,
                m.translation_key,
                m.endpoint_id,
                m.cluster_id,
                getattr(m, "off_value", None),
                getattr(m, "on_value", None),
            )
            for m in meta
        ]

    assert summary(ours) == summary(builtin_meta)
    assert {m.attribute_name for m in ours} == BUILTIN_ATTRIBUTE_NAMES
    turbo = next(m for m in ours if m.attribute_name == "turbo_mode")
    assert (turbo.off_value, turbo.on_value) == (9, 20)


def test_inching_entities():
    meta = _entity_metadata(sonoff_zbminir2.REGISTRY_ENTRY)[4:]
    by_name = {m.attribute_name: m for m in meta}

    assert list(by_name) == ["inching_enabled", "inching_time", "inching_mode"]
    for m in meta:
        assert m.cluster_id == INCHING_CLUSTER_ID
        assert m.endpoint_id == 1
        assert m.entity_type == EntityType.CONFIG
        assert m.resolved_unique_id_suffix == m.attribute_name
        assert m.translation_key == m.attribute_name

    enabled = by_name["inching_enabled"]
    assert enabled.entity_platform == EntityPlatform.SWITCH
    assert enabled.fallback_name == "Inching"
    assert (enabled.off_value, enabled.on_value) == (0, 1)

    time_ = by_name["inching_time"]
    assert time_.entity_platform == EntityPlatform.NUMBER
    assert (time_.min, time_.max, time_.step) == (0.5, 3599.5, 0.5)
    assert time_.multiplier == 0.5
    assert time_.mode == "box"
    assert time_.unit == "s"
    assert time_.device_class == NumberDeviceClass.DURATION
    assert time_.fallback_name == "Inching time"

    mode = by_name["inching_mode"]
    assert mode.entity_platform == EntityPlatform.SELECT
    assert mode.enum is InchingMode
    assert mode.fallback_name == "Inching mode"


def test_number_range_maps_onto_device_range():
    """min/max del number, divisi per il multiplier, coincidono con 1..7199."""
    time_ = _entity_metadata(sonoff_zbminir2.REGISTRY_ENTRY)[5]

    assert int(time_.min / time_.multiplier) == 1
    assert int(time_.max / time_.multiplier) == 7199


def test_select_options():
    assert [m.name for m in InchingMode] == ["Turn_off_after_on", "Turn_on_after_off"]
    assert [int(m) for m in InchingMode] == [0x00, 0x01]


# --------------------------------------------------------------------------- #
# Gate di compatibilita' e self-check del registry
# --------------------------------------------------------------------------- #


def _fake_builtin(attribute_names, cluster_id=0xFC11, with_enum=True):
    """Finto `zhaquirks.sonoff.zbminir2` con un insieme di attributi scelto."""
    attrs = [types.SimpleNamespace(name=name) for name in sorted(attribute_names)]

    class AttributeDefs:
        def __iter__(self):
            return iter(attrs)

    cluster = types.SimpleNamespace(
        cluster_id=cluster_id, AttributeDefs=AttributeDefs()
    )
    module = types.SimpleNamespace(SonoffCluster=cluster)
    if with_enum:
        module.SonoffExternalSwitchTriggerType = (
            builtin_zbminir2.SonoffExternalSwitchTriggerType
        )
    return module


def test_gate_accepts_current_builtin():
    assert EXPECTED_BUILTIN_ATTRIBUTES == frozenset(BUILTIN_ATTRIBUTE_NAMES)
    assert builtin_quirk_is_compatible(builtin_zbminir2) is True


@pytest.mark.parametrize(
    "names",
    [
        BUILTIN_ATTRIBUTE_NAMES | {"inching_control"},
        BUILTIN_ATTRIBUTE_NAMES | {"inching_enable", "inching_mode"},
        BUILTIN_ATTRIBUTE_NAMES - {"network_led"},
        set(),
    ],
)
def test_gate_refuses_changed_attribute_set(names):
    assert builtin_quirk_is_compatible(_fake_builtin(names)) is False


def test_gate_accepts_fake_with_same_attribute_set():
    """Controllo di sanita' del finto modulo usato dai test di rifiuto."""
    assert builtin_quirk_is_compatible(_fake_builtin(BUILTIN_ATTRIBUTE_NAMES))


def test_gate_refuses_different_cluster_id():
    fake = _fake_builtin(BUILTIN_ATTRIBUTE_NAMES, cluster_id=0xFC57)
    assert builtin_quirk_is_compatible(fake) is False


@pytest.mark.parametrize(
    "module",
    [
        None,
        types.SimpleNamespace(),
        _fake_builtin(BUILTIN_ATTRIBUTE_NAMES, with_enum=False),
    ],
)
def test_gate_refuses_missing_pieces(module):
    assert builtin_quirk_is_compatible(module) is False


def test_register_refuses_incompatible_builtin(caplog):
    registry = DeviceRegistry()
    fake = _fake_builtin(BUILTIN_ATTRIBUTE_NAMES | {"inching_control"})

    with caplog.at_level(logging.WARNING, logger=sonoff_zbminir2.__name__):
        registration = register_inching_quirk(fake, registry)

    assert registration is None
    assert list(registry) == []
    assert "inching_control" in caplog.text


def test_register_into_fresh_registry_wins():
    builtin = _builtin_entry()
    registry = DeviceRegistry()
    registry.register(builtin)

    registration = register_inching_quirk(builtin_zbminir2, registry)

    assert registration is not None
    assert registration.builtin_entry is builtin
    assert list(registry) == [registration.entry, builtin]
    assert resolves_to(registration.entry, registry) is True


def test_register_rolls_back_when_self_check_fails(caplog):
    """Se il registry non risolve il modello sulla nostra entry, si ritira."""

    class StubbornRegistry(DeviceRegistry):
        def match_entry(self, zigpy_device):
            return None

    builtin = _builtin_entry()
    registry = StubbornRegistry()
    registry.register(builtin)
    with caplog.at_level(logging.WARNING, logger=sonoff_zbminir2.__name__):
        registration = register_inching_quirk(builtin_zbminir2, registry)

    assert registration is None
    assert list(registry) == [builtin]
    assert caplog.records


# --------------------------------------------------------------------------- #
# Gate sull'intera entry built-in, non solo sul cluster 0xFC11
# --------------------------------------------------------------------------- #


class _CustomOnOff(CustomCluster, OnOff):
    """OnOff personalizzato, come quello che aggiunge la PR upstream #5317."""


def _builtin_variant(
    *, models=("ZBMINIR2", "MINI-ZBD"), turbo_key="turbo_mode", extra=None
) -> QuirkRegistryEntry:
    """Entry come quella di zhaquirks.sonoff.zbminir2, con le modifiche scelte.

    La provenienza e' quella del modulo built-in, come se la registrasse una
    futura release di zha-quirks: stessi attributi 0xFC11, altro cambiato.
    """
    cluster = builtin_zbminir2.SonoffCluster
    attrs = cluster.AttributeDefs
    builder = QuirkBuilder("SONOFF", models[0], registry=DeviceRegistry())
    for model in models[1:]:
        builder.applies_to("SONOFF", model)
    builder = (
        builder.replaces(cluster)
        .enum(
            attrs.external_trigger_mode.name,
            builtin_zbminir2.SonoffExternalSwitchTriggerType,
            cluster.cluster_id,
            translation_key="external_trigger_mode",
            fallback_name="External trigger mode",
        )
        .switch(
            attrs.turbo_mode.name,
            cluster.cluster_id,
            off_value=9,
            on_value=20,
            translation_key=turbo_key,
            fallback_name="Turbo mode",
        )
        .switch(
            attrs.detach_relay.name,
            cluster.cluster_id,
            translation_key="detach_relay",
            fallback_name="Detach relay",
        )
        .switch(
            attrs.network_led.name,
            cluster.cluster_id,
            translation_key="network_led",
            fallback_name="Network LED",
        )
    )
    if extra is not None:
        builder = extra(builder)
    entry = builder.add_to_registry()
    return dataclasses.replace(
        entry,
        source=dataclasses.replace(entry.source, module=builtin_zbminir2.__name__),
    )


#: Cambi upstream che lasciano intatti gli attributi di 0xFC11 (il gate sul
#: cluster passa) ma che l'override, sostituendo l'intera entry per lo
#: ZBMINIR2, nasconderebbe.
UPSTREAM_CHANGES = {
    "extra entity": {
        "extra": lambda b: b.switch(
            "on_off", OnOff.cluster_id, translation_key="relay", fallback_name="Relay"
        )
    },
    "replace_cluster_occurrences": {
        "extra": lambda b: b.replace_cluster_occurrences(_CustomOnOff)
    },
    "device automation triggers": {
        "extra": lambda b: b.device_automation_triggers(
            {("remote_button_short_press", "button"): {"command": "toggle"}}
        )
    },
    "firmware filter": {
        "extra": lambda b: b.firmware_version_filter(min_version=0x00001100)
    },
    "friendly name": {
        "extra": lambda b: b.friendly_name(model="Mini R2", manufacturer="SONOFF")
    },
    "changed translation key": {"turbo_key": "turbo"},
    "MINI-ZBD split off": {"models": ("ZBMINIR2",)},
}


def test_builtin_variant_helper_reproduces_the_real_entry():
    """Controllo di sanita' dell'helper: senza modifiche e' la entry vera."""
    assert _builtin_variant() == _builtin_entry()


def test_find_builtin_entry_in_the_global_registry():
    assert find_builtin_entry(builtin_zbminir2, ZHA_DEVICE_REGISTRY) is _builtin_entry()


def test_find_builtin_entry_is_none_when_missing():
    registry = DeviceRegistry()
    registry.register(sonoff_zbminir2.REGISTRY_ENTRY)

    assert find_builtin_entry(builtin_zbminir2, registry) is None
    assert find_builtin_entry(None, ZHA_DEVICE_REGISTRY) is None


def test_find_builtin_entry_is_none_when_ambiguous():
    registry = DeviceRegistry()
    registry.register(_builtin_variant())
    registry.register(_builtin_variant(turbo_key="turbo"))

    assert find_builtin_entry(builtin_zbminir2, registry) is None


def test_find_builtin_entry_survives_registry_errors():
    class BrokenRegistry(DeviceRegistry):
        def __iter__(self):
            raise RuntimeError("boom")

    assert find_builtin_entry(builtin_zbminir2, BrokenRegistry()) is None


def test_real_builtin_entry_is_the_expected_one():
    assert builtin_entry_is_expected(builtin_zbminir2, _builtin_entry()) is True


@pytest.mark.parametrize("change", list(UPSTREAM_CHANGES))
def test_gate_refuses_upstream_changes_beyond_the_cluster(change):
    variant = _builtin_variant(**UPSTREAM_CHANGES[change])

    assert builtin_quirk_is_compatible(builtin_zbminir2) is True
    assert builtin_entry_is_expected(builtin_zbminir2, variant) is False


@pytest.mark.parametrize("change", list(UPSTREAM_CHANGES))
def test_register_refuses_upstream_changes_beyond_the_cluster(change, caplog):
    variant = _builtin_variant(**UPSTREAM_CHANGES[change])
    registry = DeviceRegistry()
    registry.register(variant)

    with caplog.at_level(logging.WARNING, logger=sonoff_zbminir2.__name__):
        registration = register_inching_quirk(builtin_zbminir2, registry)

    assert registration is None
    assert list(registry) == [variant]
    assert "built-in" in caplog.text


def test_register_refuses_when_the_builtin_entry_is_missing(caplog):
    registry = DeviceRegistry()

    with caplog.at_level(logging.WARNING, logger=sonoff_zbminir2.__name__):
        registration = register_inching_quirk(builtin_zbminir2, registry)

    assert registration is None
    assert list(registry) == []
    assert "built-in" in caplog.text


@pytest.mark.parametrize("entry", [None, object(), "entry"])
def test_builtin_entry_check_fails_closed(entry):
    assert builtin_entry_is_expected(builtin_zbminir2, entry) is False


def test_builtin_entry_check_fails_closed_on_a_broken_module():
    assert builtin_entry_is_expected(None, _builtin_entry()) is False


def test_self_check_survives_registry_errors():
    class BrokenRegistry(DeviceRegistry):
        def match_entry(self, zigpy_device):
            raise RuntimeError("boom")

    assert resolves_to(sonoff_zbminir2.REGISTRY_ENTRY, BrokenRegistry()) is False


def test_quirk_is_effective_detects_a_later_registration():
    """Una quirk ZBMINIR2 registrata dopo la nostra la scavalca: va rilevato."""
    later = QuirkRegistryEntry(
        device_match=DeviceMatch(applies_to=(ModelInfo("SONOFF", "ZBMINIR2"),)),
    )

    with ZHA_DEVICE_REGISTRY.preserve_state():
        ZHA_DEVICE_REGISTRY.register(later)
        assert quirk_is_effective() is False

    assert quirk_is_effective() is True


# --------------------------------------------------------------------------- #
# Pacchetto quirks: import protetto
# --------------------------------------------------------------------------- #


def _load_quirks_package(name: str):
    spec = importlib.util.spec_from_file_location(
        name,
        _QUIRKS_DIR / "__init__.py",
        submodule_search_locations=[str(_QUIRKS_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_quirks_package_imports_both_quirks():
    name = "_isolated_quirks_ok"
    with ZHA_DEVICE_REGISTRY.preserve_state(), mock.patch.dict(sys.modules):
        package = _load_quirks_package(name)

        assert package.sonoff_swv_zf2 is sys.modules[f"{name}.sonoff_swv_zf2"]
        assert package.sonoff_zbminir2 is sys.modules[f"{name}.sonoff_zbminir2"]
        assert package.sonoff_zbminir2.REGISTERED is True


def test_broken_zbminir2_module_cannot_break_the_package(caplog):
    """Un errore nel modulo ZBMINIR2 e' loggato; la SWV-ZF2 resta caricata."""
    name = "_isolated_quirks_broken"
    with ZHA_DEVICE_REGISTRY.preserve_state(), mock.patch.dict(sys.modules):
        # None in sys.modules fa fallire l'import con ImportError.
        sys.modules[f"{name}.sonoff_zbminir2"] = None
        with caplog.at_level(logging.WARNING):
            package = _load_quirks_package(name)

        assert package.sonoff_swv_zf2 is sys.modules[f"{name}.sonoff_swv_zf2"]
        assert package.sonoff_zbminir2 is None

    assert "ZBMINIR2" in caplog.text


def _package_records(caplog, name):
    return [record for record in caplog.records if record.name == name]


def test_zha_quirks_1x_logs_one_warning_without_traceback(caplog, monkeypatch):
    """zha-quirks < 2.0 (HA 2024.8+ lo consente) non ha ZHA_DEVICE_REGISTRY.

    L'import del modulo fallisce sempre: un solo warning, senza traceback,
    che dice cosa serve, invece di un ERROR con traceback a ogni avvio.
    """
    name = "_isolated_quirks_old_zha_quirks"
    monkeypatch.delattr(zhaquirks, "ZHA_DEVICE_REGISTRY")
    with ZHA_DEVICE_REGISTRY.preserve_state(), mock.patch.dict(sys.modules):
        with caplog.at_level(logging.DEBUG, logger=name):
            package = _load_quirks_package(name)

        assert package.sonoff_swv_zf2 is sys.modules[f"{name}.sonoff_swv_zf2"]
        assert package.sonoff_zbminir2 is None

    records = _package_records(caplog, name)
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].exc_info is None
    message = records[0].getMessage()
    assert "zha-quirks 2" in message
    assert "ZHA_DEVICE_REGISTRY" in message


class _ExplodingModule(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Fa sollevare RuntimeError all'import di un modulo preciso."""

    def __init__(self, fullname: str) -> None:
        self.fullname = fullname

    def find_spec(self, fullname, path, target=None):
        if fullname != self.fullname:
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        raise RuntimeError("unexpected bug")


def test_unexpected_error_in_zbminir2_module_keeps_the_traceback(caplog):
    """Un bug vero (non un'API mancante) resta un ERROR con traceback."""
    name = "_isolated_quirks_bug"
    finder = _ExplodingModule(f"{name}.sonoff_zbminir2")
    with (
        ZHA_DEVICE_REGISTRY.preserve_state(),
        mock.patch.dict(sys.modules),
        mock.patch.object(sys, "meta_path", [finder, *sys.meta_path]),
        caplog.at_level(logging.DEBUG, logger=name),
    ):
        package = _load_quirks_package(name)

        assert package.sonoff_swv_zf2 is sys.modules[f"{name}.sonoff_swv_zf2"]
        assert package.sonoff_zbminir2 is None

    records = _package_records(caplog, name)
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None
    assert "unexpected bug" in caplog.text


def test_broken_swv_module_cannot_break_the_package(caplog):
    """Un errore nella quirk SWV-ZF2 e' un ERROR con traceback, non un crash.

    Senza la protezione l'eccezione risalirebbe fino all'import
    dell'integrazione, che non si caricherebbe affatto.
    """
    name = "_isolated_quirks_swv_bug"
    finder = _ExplodingModule(f"{name}.sonoff_swv_zf2")
    with (
        ZHA_DEVICE_REGISTRY.preserve_state(),
        mock.patch.dict(sys.modules),
        mock.patch.object(sys, "meta_path", [finder, *sys.meta_path]),
        caplog.at_level(logging.DEBUG, logger=name),
    ):
        package = _load_quirks_package(name)

        assert package.sonoff_swv_zf2 is None
        assert package.sonoff_zbminir2 is sys.modules[f"{name}.sonoff_zbminir2"]
        assert package.sonoff_zbminir2.REGISTERED is True

    records = _package_records(caplog, name)
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None
    assert "SWV-ZF2" in records[0].getMessage()
    assert "unexpected bug" in caplog.text
