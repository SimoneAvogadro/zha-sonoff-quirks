"""Test della quirk SONOFF SWV-ZF2 (dual-channel water valve)."""

from __future__ import annotations

from unittest import mock

from conftest import build_zigpy_device
import pytest
from sonoff_swv_zf2 import (
    CONFIG_ENDPOINT,
    MANUAL_SETTINGS_LEN,
    IrrigationAmountUnit,
    IrrigationMode,
    IrrigationSessionState,
    ManualDefaultSettingsPayload,
    SWVZF2Cluster,
    SWVZF2ManualConfigCluster,
    SWVZF2ProgressCluster,
    ValveState,
    decode_irrigation_status,
    decode_manual_default_settings,
    normalize_manual_default_settings,
    pack_manual_default_settings,
)
import zigpy.types as t
from zigpy.zcl import foundation

# Payload di riferimento: mode=capacity, durata 5 min, intervallo 0/0,
# unita' litri, volume 250 L, fail-safe 30 min.
REFERENCE_SETTINGS = {
    "irrigation_mode": IrrigationMode.capacity,
    "irrigation_total_duration": 5,
    "interval_irrigation_duration": 0,
    "interval_pause": 0,
    "capacity_unit": IrrigationAmountUnit.liter,
    "capacity_amount": 250,
    "fail_safe": 30,
}
REFERENCE_WIRE = [
    0x01, 0x00, 0x05, 0x00, 0x00, 0x00,
    0x00, 0x01, 0x00, 0xFA, 0x00, 0x1E,
]

# Payload 0x501F catturati dal dispositivo il 2026-08-09 (log ZHA debug), corsa
# in modalita' duration da 5 minuti. Vedi TESTS.md.
STATUS_PREAMBLE_WIRE = [0, 0, 1, 0, 50, 11, 195, 23, 50, 11, 196, 67, 0, 0, 1]
STATUS_RUNNING_WIRE = [
    2, 0, 1, 0, 50, 11, 195, 23, 50, 11, 196, 67,
    50, 11, 195, 29, 0, 0, 1, 0, 1,
]
STATUS_FINISHED_WIRE = [
    1, 0, 1, 0, 50, 11, 195, 23, 50, 11, 196, 67,
    50, 11, 196, 68, 0, 0, 1, 0, 30,
]
# Preambolo di una corsa in modalita' capacity: finestra = fail-safe (60 s).
STATUS_CAPACITY_PREAMBLE_WIRE = [
    0, 0, 1, 1, 50, 11, 194, 32, 50, 11, 194, 92, 0, 0, 1,
]


# --------------------------------------------------------------------------- #
# Signature / registrazione
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("model", ["SWV-ZF2", "SWV-ZF2U", "SWV-ZF2E"])
def test_quirk_matches_all_models(app, model):
    """La quirk risolve per tutti i model id dichiarati."""
    from zhaquirks import ZHA_DEVICE_REGISTRY

    device = build_zigpy_device(app, model=model)
    quirked = ZHA_DEVICE_REGISTRY.resolve(device)

    assert quirked is not device, f"quirk non applicata al model {model}"
    assert isinstance(quirked.endpoints[1].swvzf2_cluster, SWVZF2Cluster)


def test_quirk_does_not_match_other_devices(app):
    """Un dispositivo non SWV-ZF2 non deve essere quirked da questo modulo."""
    from zhaquirks import ZHA_DEVICE_REGISTRY

    device = build_zigpy_device(app, manufacturer="SONOFF", model="SWV-NOT-ZF2")
    quirked = ZHA_DEVICE_REGISTRY.resolve(device)

    assert not hasattr(quirked.endpoints[1], "swvzf2_cluster")


def test_swv_cluster_replaced_on_both_endpoints(quirked_device):
    """0xFC11 e' sostituito su entrambi gli endpoint (sensori per canale)."""
    for ep_id in (1, 2):
        endpoint = quirked_device.endpoints[ep_id]
        assert isinstance(endpoint.swvzf2_cluster, SWVZF2Cluster)
        assert endpoint.in_clusters[0xFC11] is endpoint.swvzf2_cluster


def test_manual_config_cluster_only_on_config_endpoint(quirked_device):
    """0xFBFC esiste solo sull'endpoint 1: 0x501D e' globale, non per canale.

    Sull'endpoint 2 il dispositivo risponde alla read senza restituire
    l'attributo, quindi esporre li' un secondo blocco di configurazione
    produrrebbe entita' che falliscono a ogni scrittura.
    """
    ep1 = quirked_device.endpoints[CONFIG_ENDPOINT]
    assert isinstance(ep1.swvzf2_manual_config, SWVZF2ManualConfigCluster)
    assert ep1.in_clusters[0xFBFC] is ep1.swvzf2_manual_config

    assert not hasattr(quirked_device.endpoints[2], "swvzf2_manual_config")
    assert 0xFBFC not in quirked_device.endpoints[2].in_clusters


def test_attribute_definitions(quirked_device):
    """Gli id degli attributi del cluster privato sono quelli attesi."""
    attrs = SWVZF2Cluster.AttributeDefs
    assert attrs.water_valve_state.id == 0x500C
    assert attrs.water_usage_volume.id == 0x501B
    assert attrs.water_usage_duration.id == 0x501C
    assert attrs.manual_default_settings.id == 0x501D
    assert attrs.manual_default_settings.zcl_type == foundation.DataTypeId.array


# --------------------------------------------------------------------------- #
# Codifica / decodifica 0x501D
# --------------------------------------------------------------------------- #


def test_pack_matches_reference_wire_format():
    """Pack produce esattamente i 12 byte attesi, campi big-endian."""
    payload = pack_manual_default_settings(**REFERENCE_SETTINGS)

    assert len(payload) == MANUAL_SETTINGS_LEN
    assert list(payload) == REFERENCE_WIRE


def test_decode_matches_reference_settings():
    """Decode ricostruisce i campi nominati dai 12 byte."""
    assert decode_manual_default_settings(REFERENCE_WIRE) == REFERENCE_SETTINGS


@pytest.mark.parametrize(
    "settings",
    [
        REFERENCE_SETTINGS,
        {
            "irrigation_mode": IrrigationMode.duration,
            "irrigation_total_duration": 719,
            "interval_irrigation_duration": 0,
            "interval_pause": 0,
            "capacity_unit": IrrigationAmountUnit.us_gallon,
            "capacity_amount": 0,
            "fail_safe": 719,
        },
        {
            "irrigation_mode": IrrigationMode.duration_with_interval,
            "irrigation_total_duration": 60,
            "interval_irrigation_duration": 5,
            "interval_pause": 10,
            "capacity_unit": IrrigationAmountUnit.liter,
            "capacity_amount": 10000,
            "fail_safe": 0,
        },
    ],
)
def test_pack_decode_round_trip(settings):
    """Pack -> decode e' l'identita' su tutto il dominio dichiarato."""
    assert decode_manual_default_settings(pack_manual_default_settings(**settings)) == (
        settings
    )


def test_decode_accepts_zcl_array_wrapper():
    """Un foundation.Array decodificato dal wire viene accettato."""
    array = foundation.Array(
        type=foundation.DataTypeId.uint8,
        value=ManualDefaultSettingsPayload(REFERENCE_WIRE),
    )
    assert decode_manual_default_settings(array) == REFERENCE_SETTINGS


def test_decode_survives_unknown_enum_values():
    """Valori di enum fuori range non fanno esplodere il decode.

    NOTA: gli enum di zigpy sintetizzano un membro `undefined_0xNN` invece di
    sollevare ValueError, quindi il ramo di fallback in
    `decode_manual_default_settings` non viene mai preso. Il test documenta il
    comportamento reale; vedi TODO.md #6.
    """
    wire = list(REFERENCE_WIRE)
    wire[0] = 0x7F  # modalita' sconosciuta
    wire[7] = 0x7F  # unita' sconosciuta

    decoded = decode_manual_default_settings(wire)

    assert isinstance(decoded["irrigation_mode"], IrrigationMode)
    assert isinstance(decoded["capacity_unit"], IrrigationAmountUnit)
    assert int(decoded["irrigation_mode"]) == 0x7F
    assert int(decoded["capacity_unit"]) == 0x7F


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        list(range(MANUAL_SETTINGS_LEN - 1)),
        list(range(MANUAL_SETTINGS_LEN + 1)),
    ],
)
def test_normalize_rejects_wrong_length(value):
    """Solo payload di esattamente 12 byte sono accettati."""
    with pytest.raises(ValueError):
        normalize_manual_default_settings(value)


def test_normalize_rejects_out_of_range_bytes():
    """Un elemento fuori dal range uint8 e' un errore esplicito."""
    with pytest.raises(ValueError):
        normalize_manual_default_settings([300] + [0] * (MANUAL_SETTINGS_LEN - 1))


def test_valve_state_bitmask():
    """I bit di 0x500C usati dai binary sensor sono quelli documentati."""
    assert ValveState.Water_Shortage == 0b0001
    assert ValveState.Water_Leakage == 0b0010
    assert ValveState.Anti_Frost_Alarm == 0b0100
    assert ValveState.Water_Shortage_Channel_2 == 0b0001_0000
    assert ValveState.Water_Shortage_And_Leakage & ValveState.Water_Leakage


# --------------------------------------------------------------------------- #
# deserialize(): riparazione delle read-response array malformate
# --------------------------------------------------------------------------- #


def _read_response_frame(element_type: foundation.DataTypeId) -> bytes:
    """Costruisce una Read_Attributes_rsp per 0x501D con l'element type dato."""
    header = foundation.ZCLHeader.general(
        tsn=1,
        command_id=foundation.GeneralCommand.Read_Attributes_rsp,
        direction=foundation.Direction.Server_to_Client,
    )
    body = (
        SWVZF2Cluster.AttributeDefs.manual_default_settings.id.serialize()
        + foundation.Status.SUCCESS.serialize()
        + foundation.DataTypeId.array.serialize()
        + element_type.serialize()
        + ManualDefaultSettingsPayload(REFERENCE_WIRE).serialize()
    )
    return header.serialize() + body


def test_deserialize_accepts_wellformed_array(swv_cluster):
    """Il frame corretto (element type uint8) si deserializza normalmente."""
    frame = _read_response_frame(foundation.DataTypeId.uint8)

    _hdr, args = swv_cluster.deserialize(frame)

    record = args.status_records[0]
    assert record.attrid == SWVZF2Cluster.AttributeDefs.manual_default_settings.id
    assert isinstance(record.value, foundation.Array)
    assert list(record.value.value) == REFERENCE_WIRE


def test_deserialize_repairs_duplicated_array_element_type(swv_cluster):
    """Il frame malformato (element type = array) viene riparato e decodificato."""
    frame = _read_response_frame(foundation.DataTypeId.array)

    _hdr, args = swv_cluster.deserialize(frame)

    record = args.status_records[0]
    assert record.attrid == SWVZF2Cluster.AttributeDefs.manual_default_settings.id
    assert isinstance(record.value, foundation.Array)
    assert list(record.value.value) == REFERENCE_WIRE


def test_deserialize_reraises_unrepairable_frames(swv_cluster):
    """Un frame che non e' la read-response nota resta un errore."""
    with pytest.raises(ValueError, match="too short"):
        swv_cluster.deserialize(b"\x00")


# --------------------------------------------------------------------------- #
# _update_attribute: gli array non devono finire in appdb
# --------------------------------------------------------------------------- #


def test_update_attribute_consumes_manual_settings(swv_cluster, manual_config_cluster):
    """0x501D non viene propagato al cluster ma popola la config locale."""
    listener = mock.MagicMock()
    swv_cluster.add_listener(listener)

    swv_cluster.update_attribute(
        SWVZF2Cluster.AttributeDefs.manual_default_settings.id,
        ManualDefaultSettingsPayload(REFERENCE_WIRE),
    )

    listener.attribute_updated.assert_not_called()
    assert (
        manual_config_cluster.get("capacity_amount")
        == REFERENCE_SETTINGS["capacity_amount"]
    )
    assert manual_config_cluster.get("irrigation_mode") == IrrigationMode.capacity


def test_update_attribute_consumes_irrigation_status(swv_cluster, progress_cluster):
    """0x501F non arriva a ZHA come array ma popola il cluster di avanzamento."""
    listener = mock.MagicMock()
    swv_cluster.add_listener(listener)

    swv_cluster.update_attribute(0x501F, STATUS_RUNNING_WIRE)

    listener.attribute_updated.assert_not_called()
    assert progress_cluster.get("session_volume") == 1


def test_update_attribute_ignores_malformed_irrigation_status(
    swv_cluster, progress_cluster
):
    """Un payload 0x501F di lunghezza ignota viene scartato, non solleva."""
    swv_cluster.update_attribute(0x501F, [0, 1, 2])

    assert progress_cluster.get("session_volume") is None


def test_update_attribute_passes_through_scalars(swv_cluster):
    """Gli attributi scalari continuano ad arrivare a ZHA."""
    swv_cluster.update_attribute(
        SWVZF2Cluster.AttributeDefs.water_usage_volume.id, 42
    )

    assert swv_cluster.get("water_usage_volume") == 42


def test_update_attribute_ignores_invalid_manual_payload(
    swv_cluster, manual_config_cluster
):
    """Un payload 0x501D di lunghezza sbagliata viene scartato, non solleva."""
    swv_cluster.update_attribute(
        SWVZF2Cluster.AttributeDefs.manual_default_settings.id, [1, 2, 3]
    )

    assert manual_config_cluster.get("capacity_amount") is None


# --------------------------------------------------------------------------- #
# Write path: raw array su 0x501D
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_write_manual_settings_uses_raw_array_path(swv_cluster):
    """La write di 0x501D passa da write_attributes_raw con un Array uint8."""
    payload = pack_manual_default_settings(**REFERENCE_SETTINGS)

    with mock.patch.object(
        swv_cluster,
        "write_attributes_raw",
        new=mock.AsyncMock(
            return_value=[
                [
                    foundation.WriteAttributesStatusRecord(
                        status=foundation.Status.SUCCESS
                    )
                ]
            ]
        ),
    ) as raw:
        result = await swv_cluster.write_attributes(
            {SWVZF2Cluster.AttributeDefs.manual_default_settings.id: payload}
        )

    raw.assert_awaited_once()
    written_attrs = raw.await_args[0][0]
    attr = written_attrs[0]
    assert attr.attrid == SWVZF2Cluster.AttributeDefs.manual_default_settings.id
    assert attr.value.type == foundation.DataTypeId.array
    assert attr.value.value.type == foundation.DataTypeId.uint8
    assert list(attr.value.value.value) == REFERENCE_WIRE
    assert result[0][0].status == foundation.Status.SUCCESS


@pytest.mark.asyncio
async def test_successful_write_syncs_local_config(swv_cluster, manual_config_cluster):
    """Dopo una write riuscita le entita' locali riflettono il nuovo stato."""
    payload = pack_manual_default_settings(**REFERENCE_SETTINGS)

    with mock.patch.object(
        swv_cluster,
        "write_attributes_raw",
        new=mock.AsyncMock(
            return_value=[
                [
                    foundation.WriteAttributesStatusRecord(
                        status=foundation.Status.SUCCESS
                    )
                ]
            ]
        ),
    ):
        await swv_cluster.write_attributes(
            {SWVZF2Cluster.AttributeDefs.manual_default_settings.id: payload}
        )

    assert manual_config_cluster.get("fail_safe") == REFERENCE_SETTINGS["fail_safe"]


@pytest.mark.asyncio
async def test_failed_write_does_not_sync_local_config(
    swv_cluster, manual_config_cluster
):
    """Se il dispositivo rifiuta la write, la cache locale non viene aggiornata."""
    payload = pack_manual_default_settings(**REFERENCE_SETTINGS)

    with mock.patch.object(
        swv_cluster,
        "write_attributes_raw",
        new=mock.AsyncMock(
            return_value=[
                [
                    foundation.WriteAttributesStatusRecord(
                        status=foundation.Status.FAILURE,
                        attrid=SWVZF2Cluster.AttributeDefs.manual_default_settings.id,
                    )
                ]
            ]
        ),
    ):
        result = await swv_cluster.write_attributes(
            {SWVZF2Cluster.AttributeDefs.manual_default_settings.id: payload}
        )

    assert result[0][0].status == foundation.Status.FAILURE
    assert manual_config_cluster.get("fail_safe") is None


@pytest.mark.asyncio
async def test_write_rejects_malformed_payload(swv_cluster):
    """Un payload di lunghezza sbagliata non raggiunge mai la rete."""
    with mock.patch.object(
        swv_cluster, "write_attributes_raw", new=mock.AsyncMock()
    ) as raw:
        with pytest.raises(ValueError):
            await swv_cluster.write_attributes(
                {SWVZF2Cluster.AttributeDefs.manual_default_settings.id: [1, 2, 3]}
            )

    raw.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_attributes_use_standard_write_path(swv_cluster):
    """Gli attributi non-array usano il path standard di zigpy."""
    with mock.patch.object(
        swv_cluster,
        "write_attributes_raw",
        new=mock.AsyncMock(),
    ) as raw:
        with mock.patch(
            "zigpy.zcl.Cluster.write_attributes",
            new=mock.AsyncMock(
                return_value=[
                    [
                        foundation.WriteAttributesStatusRecord(
                            status=foundation.Status.SUCCESS
                        )
                    ]
                ]
            ),
        ) as standard:
            await swv_cluster.write_attributes({"water_usage_volume": 1})

    raw.assert_not_awaited()
    standard.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Cluster locale di configurazione (entita' HA)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_local_write_merges_with_cached_settings(
    swv_cluster, manual_config_cluster
):
    """Scrivere una sola entita' conserva gli altri campi gia' noti."""
    manual_config_cluster.update_from_manual_default_settings(REFERENCE_WIRE)

    with mock.patch.object(
        swv_cluster, "write_attributes", new=mock.AsyncMock(return_value=[[]])
    ) as write:
        await manual_config_cluster.write_attributes({"capacity_amount": 500})

    written = write.await_args[0][0][
        SWVZF2Cluster.AttributeDefs.manual_default_settings.id
    ]
    decoded = decode_manual_default_settings(written)

    assert decoded["capacity_amount"] == 500
    # tutti gli altri campi invariati
    assert decoded["irrigation_mode"] == REFERENCE_SETTINGS["irrigation_mode"]
    assert decoded["fail_safe"] == REFERENCE_SETTINGS["fail_safe"]
    assert (
        decoded["irrigation_total_duration"]
        == REFERENCE_SETTINGS["irrigation_total_duration"]
    )


@pytest.mark.asyncio
async def test_local_write_reads_device_when_cache_is_empty(
    swv_cluster, manual_config_cluster
):
    """Senza cache, la quirk legge prima 0x501D dal dispositivo."""

    async def _read(_attributes):
        manual_config_cluster.update_from_manual_default_settings(REFERENCE_WIRE)
        return ({}, {})

    with mock.patch.object(
        swv_cluster, "read_attributes", new=mock.AsyncMock(side_effect=_read)
    ) as read:
        with mock.patch.object(
            swv_cluster, "write_attributes", new=mock.AsyncMock(return_value=[[]])
        ) as write:
            await manual_config_cluster.write_attributes({"fail_safe": 60})

    read.assert_awaited_once()
    written = write.await_args[0][0][
        SWVZF2Cluster.AttributeDefs.manual_default_settings.id
    ]
    assert decode_manual_default_settings(written)["fail_safe"] == 60


@pytest.mark.asyncio
async def test_local_write_coerces_enums(swv_cluster, manual_config_cluster):
    """I valori enum arrivati da HA come int vengono convertiti."""
    manual_config_cluster.update_from_manual_default_settings(REFERENCE_WIRE)

    with mock.patch.object(
        swv_cluster, "write_attributes", new=mock.AsyncMock(return_value=[[]])
    ) as write:
        await manual_config_cluster.write_attributes(
            {"irrigation_mode": 2, "capacity_unit": 0}
        )

    written = write.await_args[0][0][
        SWVZF2Cluster.AttributeDefs.manual_default_settings.id
    ]
    decoded = decode_manual_default_settings(written)

    assert decoded["irrigation_mode"] == IrrigationMode.duration_with_interval
    assert decoded["capacity_unit"] == IrrigationAmountUnit.us_gallon


@pytest.mark.asyncio
async def test_config_writes_never_touch_endpoint_2(quirked_device):
    """La config va sempre sul cluster 0xFC11 dell'endpoint 1, mai sull'ep2."""
    manual_config = quirked_device.endpoints[CONFIG_ENDPOINT].swvzf2_manual_config
    manual_config.update_from_manual_default_settings(REFERENCE_WIRE)

    with mock.patch.object(
        quirked_device.endpoints[CONFIG_ENDPOINT].swvzf2_cluster,
        "write_attributes",
        new=mock.AsyncMock(return_value=[[]]),
    ) as ep1_write:
        with mock.patch.object(
            quirked_device.endpoints[2].swvzf2_cluster,
            "write_attributes",
            new=mock.AsyncMock(return_value=[[]]),
        ) as ep2_write:
            await manual_config.write_attributes({"fail_safe": 15})

    ep1_write.assert_awaited_once()
    ep2_write.assert_not_awaited()


@pytest.mark.asyncio
async def test_write_reports_when_device_withholds_settings(
    swv_cluster, manual_config_cluster
):
    """Se la read non restituisce 0x501D, l'errore non dice "leggi prima".

    E' il caso osservato sull'endpoint 2 prima della correzione: il dispositivo
    risponde ma senza l'attributo, e il vecchio messaggio suggeriva un'azione
    che era gia' stata eseguita.
    """
    with mock.patch.object(
        swv_cluster, "read_attributes", new=mock.AsyncMock(return_value=({}, {}))
    ) as read:
        with pytest.raises(ValueError, match="did not return manual_default_settings"):
            await manual_config_cluster.write_attributes({"fail_safe": 15})

    read.assert_awaited_once()


# --------------------------------------------------------------------------- #
# Configurazione al bind/reconfigure
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_apply_custom_configuration_reads_manual_settings(swv_cluster):
    """Il (re)configure legge 0x501D per inizializzare le entita'."""
    with mock.patch.object(
        swv_cluster, "read_attributes", new=mock.AsyncMock(return_value=({}, {}))
    ) as read:
        await swv_cluster.apply_custom_configuration()

    read.assert_awaited_once_with(
        [SWVZF2Cluster.AttributeDefs.manual_default_settings.id]
    )


@pytest.mark.asyncio
async def test_apply_custom_configuration_survives_read_failure(swv_cluster):
    """Un dispositivo sleepy che non risponde non blocca il configure."""
    with mock.patch.object(
        swv_cluster,
        "read_attributes",
        new=mock.AsyncMock(side_effect=TimeoutError("no response")),
    ):
        await swv_cluster.apply_custom_configuration()


@pytest.mark.asyncio
async def test_read_attributes_syncs_local_config(swv_cluster, manual_config_cluster):
    """Una read riuscita di 0x501D popola le entita' di configurazione."""
    attr_id = SWVZF2Cluster.AttributeDefs.manual_default_settings.id

    with mock.patch(
        "zigpy.zcl.Cluster.read_attributes",
        new=mock.AsyncMock(
            return_value=({attr_id: ManualDefaultSettingsPayload(REFERENCE_WIRE)}, {})
        ),
    ):
        await swv_cluster.read_attributes([attr_id])

    assert manual_config_cluster.get("irrigation_mode") == IrrigationMode.capacity
    assert manual_config_cluster.get("capacity_amount") == 250


# --------------------------------------------------------------------------- #
# 0x501F — avanzamento della sessione
# --------------------------------------------------------------------------- #


def test_decode_irrigation_status_running():
    """Il payload da 21 byte porta stato, modalita', tempi e volume."""
    decoded = decode_irrigation_status(STATUS_RUNNING_WIRE)

    assert decoded == {
        "session_state": IrrigationSessionState.running,
        "session_mode": IrrigationMode.duration,
        # end - start = 300 s, i 5 minuti impostati sul dispositivo
        "session_target_duration": 300,
        # current - start = 6 s, un tick dopo l'apertura
        "session_elapsed": 6,
        "session_volume": 1,
    }


def test_decode_irrigation_status_finished():
    """L'ultimo report della corsa porta il volume totale erogato."""
    decoded = decode_irrigation_status(STATUS_FINISHED_WIRE)

    assert decoded["session_state"] == IrrigationSessionState.finished
    assert decoded["session_elapsed"] == 301
    assert decoded["session_volume"] == 30


def test_decode_irrigation_status_preamble():
    """Il payload da 15 byte annuncia la corsa: nessun current, nessun volume."""
    decoded = decode_irrigation_status(STATUS_PREAMBLE_WIRE)

    assert decoded["session_state"] == IrrigationSessionState.preamble
    assert decoded["session_target_duration"] == 300
    assert decoded["session_elapsed"] == 0
    assert decoded["session_volume"] == 0


def test_decode_irrigation_status_reads_capacity_mode():
    """Il byte 3 distingue capacity da duration.

    In capacity mode la finestra annunciata e' il fail-safe (60 s), non un
    obiettivo di durata: e' il dispositivo a chiudere al volume raggiunto.
    """
    decoded = decode_irrigation_status(STATUS_CAPACITY_PREAMBLE_WIRE)

    assert decoded["session_mode"] == IrrigationMode.capacity
    assert decoded["session_target_duration"] == 60


def test_decode_irrigation_status_accepts_zcl_array_wrapper():
    """Sul filo l'attributo arriva incapsulato in un foundation.Array."""
    array = foundation.Array(
        type=foundation.DataTypeId.uint8,
        value=ManualDefaultSettingsPayload(STATUS_RUNNING_WIRE),
    )
    assert decode_irrigation_status(array)["session_volume"] == 1


@pytest.mark.parametrize("value", [None, [], [0, 1, 2], list(range(30))])
def test_decode_irrigation_status_rejects_unknown_payloads(value):
    """Lunghezze diverse da 15/21 non sono riconosciute: None, non eccezione."""
    assert decode_irrigation_status(value) is None


def test_progress_cluster_only_on_config_endpoint(quirked_device):
    """0xFBFD e' unico: i report dei due canali confluiscono li'."""
    ep1 = quirked_device.endpoints[CONFIG_ENDPOINT]
    assert isinstance(ep1.swvzf2_progress, SWVZF2ProgressCluster)
    assert not hasattr(quirked_device.endpoints[2], "swvzf2_progress")


def test_progress_cluster_exposes_decoded_fields(swv_cluster, progress_cluster):
    """Un report 0x501F popola tutti gli attributi di avanzamento."""
    swv_cluster.update_attribute(0x501F, STATUS_FINISHED_WIRE)

    assert progress_cluster.get("session_target_duration") == 300
    assert progress_cluster.get("session_elapsed") == 301
    assert progress_cluster.get("session_volume") == 30
    assert progress_cluster.get("session_mode") == IrrigationMode.duration


def test_irrigating_flag_tracks_session_state(swv_cluster, progress_cluster):
    """`irrigating` e' vero solo mentre lo stato e' `running`."""
    swv_cluster.update_attribute(0x501F, STATUS_RUNNING_WIRE)
    assert progress_cluster.get("irrigating") == t.Bool.true

    swv_cluster.update_attribute(0x501F, STATUS_FINISHED_WIRE)
    assert progress_cluster.get("irrigating") == t.Bool.false


def test_endpoint_2_status_routes_to_the_single_progress_cluster(
    quirked_device, progress_cluster
):
    """Un report 0x501F dall'endpoint 2 aggiorna comunque l'avanzamento."""
    quirked_device.endpoints[2].swvzf2_cluster.update_attribute(
        0x501F, STATUS_RUNNING_WIRE
    )

    assert progress_cluster.get("session_volume") == 1


# --------------------------------------------------------------------------- #
# Tipi ZCL e sorgenti dei sensori di consumo
# --------------------------------------------------------------------------- #


def test_valve_state_is_declared_uint8_not_enum8():
    """0x500C va dichiarato uint8: il dispositivo rifiuta enum8.

    Con enum8 la configure_reporting torna INVALID_DATA_TYPE e i binary sensor
    di allarme non riportano mai. Vedi TESTS.md.
    """
    assert (
        SWVZF2Cluster.AttributeDefs.water_valve_state.zcl_type
        == foundation.DataTypeId.uint8
    )


def test_live_counters_are_defined():
    """0x5006/0x5007 sono i contatori che il dispositivo riporta davvero."""
    assert SWVZF2Cluster.AttributeDefs.valve_open_duration.id == 0x5006
    assert SWVZF2Cluster.AttributeDefs.irrigation_volume.id == 0x5007
    assert SWVZF2Cluster.AttributeDefs.irrigation_status.id == 0x501F


def test_live_counters_reach_zha(swv_cluster):
    """I contatori live sono attributi normali: passano dritti a ZHA."""
    swv_cluster.update_attribute(0x5006, 5)
    swv_cluster.update_attribute(0x5007, 34)

    assert swv_cluster.get("valve_open_duration") == 5
    assert swv_cluster.get("irrigation_volume") == 34
