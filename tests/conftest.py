"""Fixture condivise per i test della quirk SONOFF SWV-ZF2."""

from __future__ import annotations

from unittest import mock

import pytest

# L'import registra la quirk nel registry globale di zhaquirks.
import sonoff_swv_zf2  # noqa: F401
import zigpy.application
import zigpy.device
import zigpy.endpoint
import zigpy.types as t
from zigpy.zcl import foundation
from zigpy.zdo import types as zdo_t

#: Firmware osservato sul dispositivo di riferimento (SWV-ZF2E, ~1.0.7).
SWV_ZF2_FW_VERSION = 0x00001007

#: Signature del dispositivo reale. Endpoint 1 e 2 sono i due canali; il
#: cluster privato Sonoff 0xFC11 e' presente su entrambi.
#:
#: NOTA: profilo/device_type/lista cluster sono la ricostruzione dalla famiglia
#: SWV (PR zigpy/zha-device-handlers#4993). La quirk v2 fa match su
#: manufacturer/model, quindi i test di signature non dipendono dai dettagli
#: qui sotto; vanno pero' allineati a una cattura reale (vedi TODO.md #3).
SWV_ZF2_SIGNATURE = {
    1: {
        "profile_id": 260,
        "device_type": zdo_t.LogicalType.EndDevice,
        "in_clusters": [0x0000, 0x0001, 0x0003, 0x0006, 0xFC11],
        "out_clusters": [0x0019],
    },
    2: {
        "profile_id": 260,
        "device_type": zdo_t.LogicalType.EndDevice,
        "in_clusters": [0x0003, 0x0006, 0xFC11],
        "out_clusters": [],
    },
}


@pytest.fixture
def app() -> mock.MagicMock:
    """ControllerApplication finto: nessuna IO di rete nei test."""
    application = mock.MagicMock(spec=zigpy.application.ControllerApplication)
    application.request = mock.AsyncMock(
        return_value=(foundation.Status.SUCCESS, "done")
    )
    application.get_sequence = mock.MagicMock(return_value=123)
    # Nessun database: evita che l'aggiunta di cluster/endpoint tenti la
    # persistenza appdb durante i test.
    application._dblistener = None
    return application


def build_zigpy_device(
    app: mock.MagicMock,
    manufacturer: str = "SONOFF",
    model: str = "SWV-ZF2",
    signature: dict | None = None,
) -> zigpy.device.Device:
    """Costruisce un dispositivo zigpy grezzo (non ancora quirked)."""
    device = zigpy.device.Device(
        app, t.EUI64.convert("00:0d:6f:00:11:22:33:44"), 0x1234
    )
    device.manufacturer = manufacturer
    device.model = model
    device.node_desc = zdo_t.NodeDescriptor(
        logical_type=zdo_t.LogicalType.EndDevice,
        complex_descriptor_available=0,
        user_descriptor_available=0,
        reserved=0,
        aps_flags=0,
        frequency_band=zdo_t.NodeDescriptor.FrequencyBand.Freq2400MHz,
        mac_capability_flags=zdo_t.NodeDescriptor.MACCapabilityFlags.AllocateAddress,
        manufacturer_code=0x1286,
        maximum_buffer_size=82,
        maximum_incoming_transfer_size=128,
        server_mask=0,
        maximum_outgoing_transfer_size=128,
        descriptor_capability_field=zdo_t.NodeDescriptor.DescriptorCapability.NONE,
    )

    for ep_id, ep_signature in (signature or SWV_ZF2_SIGNATURE).items():
        endpoint = device.add_endpoint(ep_id)
        endpoint.status = zigpy.endpoint.Status.ZDO_INIT
        endpoint.profile_id = ep_signature["profile_id"]
        endpoint.device_type = ep_signature["device_type"]
        for cluster_id in ep_signature["in_clusters"]:
            endpoint.add_input_cluster(cluster_id)
        for cluster_id in ep_signature["out_clusters"]:
            endpoint.add_output_cluster(cluster_id)

    return device


@pytest.fixture
def zigpy_device(app: mock.MagicMock) -> zigpy.device.Device:
    """Dispositivo SWV-ZF2 grezzo, prima della risoluzione della quirk."""
    return build_zigpy_device(app)


@pytest.fixture
def quirked_device(zigpy_device: zigpy.device.Device) -> zigpy.device.Device:
    """Dispositivo SWV-ZF2 con la quirk applicata."""
    from zhaquirks import ZHA_DEVICE_REGISTRY

    return ZHA_DEVICE_REGISTRY.resolve(zigpy_device)


@pytest.fixture
def swv_cluster(quirked_device):
    """Cluster 0xFC11 quirked dell'endpoint 1."""
    return quirked_device.endpoints[1].swvzf2_cluster


@pytest.fixture
def manual_config_cluster(quirked_device):
    """Cluster locale di configurazione 0xFBFC dell'endpoint 1."""
    return quirked_device.endpoints[1].swvzf2_manual_config
