"""SONOFF SWV-ZF2 (dual-channel) - Zigbee smart water valve - custom ZHA quirk.

Fonti (entrambe revisionate riga per riga, nessun codice di rete/IO):
- Sensori: PR zigpy/zha-device-handlers#4993 (xiaoliu0001), confermata da terzi
  su SWV-ZF2U fw 0x00001007 (stesso firmware di questo dispositivo).
- Controllo 0x501D manual_default_settings: pattern bench-verified su fw
  0x00001007 (gist nglessner, companion della PR #4927), qui riscritto senza
  event-system per massima compatibilita' e adattato al dual-channel
  (cluster 0xFC11 presente su endpoint 1 e 2 -> config per canale).

Modello d'uso "irrigazione a litri / temporizzata":
  1. impostare le entita' del canale: modalita' (duration/capacity),
     durata (min) o volume (L), fail-safe (min);
  2. accendere lo switch del canale: la valvola si chiude AUTONOMAMENTE
     sul dispositivo (funziona anche se HA e' offline).

Formato wire 0x501D (12 byte uint8 array, campi big-endian):
  [0]     irrigation_mode   0=duration 1=capacity 2=duration_with_interval
  [1..2]  irrigation_total_duration  uint16, minuti, 0-719
  [3..4]  interval_irrigation_duration uint16, minuti
  [5..6]  interval_pause    uint16, minuti
  [7]     capacity_unit     0=US gallon 1=litro
  [8..9]  capacity_amount   uint16, 0-10000
  [10..11] fail_safe        uint16, minuti, 0-719
"""

from typing import Any, Final

from zigpy.quirks import CustomCluster
from zigpy.quirks.v2 import (
    QuirkBuilder,
    ReportingConfig,
    SensorDeviceClass,
    SensorStateClass,
)
from zigpy.quirks.v2.homeassistant import EntityType, UnitOfTime, UnitOfVolume
from zigpy.quirks.v2.homeassistant.binary_sensor import BinarySensorDeviceClass
from zigpy.quirks.v2.homeassistant.number import NumberDeviceClass
import zigpy.types as t
from zigpy.zcl import foundation
from zigpy.zcl.foundation import BaseAttributeDefs, ZCLAttributeDef

from zhaquirks import LocalDataCluster

MANUAL_SETTINGS_LEN: Final = 12


class ValveState(t.enum8):
    """Water valve abnormal state (bitmask su 0x500C)."""

    Normal = 0
    Water_Shortage = 1 << 0
    Water_Leakage = 1 << 1
    Anti_Frost_Alarm = 1 << 2
    Water_Shortage_Channel_2 = 1 << 4
    Water_Shortage_And_Leakage = Water_Shortage | Water_Leakage
    Water_Shortage_And_Frost = Water_Shortage | Anti_Frost_Alarm
    Water_Leakage_And_Frost = Water_Leakage | Anti_Frost_Alarm
    All_Alarms = Water_Shortage | Water_Leakage | Anti_Frost_Alarm


class IrrigationMode(t.enum8):
    """Modalita' di irrigazione manuale (byte 0 di 0x501D)."""

    duration = 0x00
    capacity = 0x01
    duration_with_interval = 0x02


class IrrigationAmountUnit(t.enum8):
    """Unita' capacita' (byte 7 di 0x501D)."""

    us_gallon = 0x00
    liter = 0x01


class ManualDefaultSettingsPayload(
    t.LVList, item_type=t.uint8_t, length_type=t.uint16_t
):
    """Payload array ZCL usato da manual_default_settings (0x501D)."""

    def __init__(self, value=()):
        """Coercizione anche da un wrapper Array ZCL decodificato."""
        if isinstance(value, foundation.Array):
            value = value.value
        super().__init__(value)


def _uint8(value: Any, field_name: str) -> int:
    try:
        int_value = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if not 0 <= int_value <= 0xFF:
        raise ValueError(f"{field_name} must be in the range 0..255")
    return int_value


def normalize_manual_default_settings(value: Any) -> ManualDefaultSettingsPayload:
    """Normalizza un payload 0x501D a un array di 12 uint8."""
    if value is None:
        raise ValueError("manual_default_settings cannot be None")
    if isinstance(value, foundation.Array):
        value = value.value
    try:
        payload = ManualDefaultSettingsPayload(value)
    except TypeError as exc:
        raise ValueError(
            "manual_default_settings must be an iterable of 12 bytes"
        ) from exc
    if len(payload) != MANUAL_SETTINGS_LEN:
        raise ValueError(
            f"manual_default_settings must contain exactly {MANUAL_SETTINGS_LEN} "
            f"bytes, got {len(payload)}"
        )
    return ManualDefaultSettingsPayload(
        [_uint8(item, f"manual_default_settings[{i}]") for i, item in enumerate(payload)]
    )


def decode_manual_default_settings(value: Any) -> dict[str, Any]:
    """Decodifica il payload 0x501D in campi nominati."""
    a = list(normalize_manual_default_settings(value))
    try:
        mode = IrrigationMode(a[0])
    except ValueError:
        mode = IrrigationMode.duration
    try:
        unit = IrrigationAmountUnit(a[7])
    except ValueError:
        unit = IrrigationAmountUnit.liter
    return {
        "irrigation_mode": mode,
        "irrigation_total_duration": (a[1] << 8) | a[2],
        "interval_irrigation_duration": (a[3] << 8) | a[4],
        "interval_pause": (a[5] << 8) | a[6],
        "capacity_unit": unit,
        "capacity_amount": (a[8] << 8) | a[9],
        "fail_safe": (a[10] << 8) | a[11],
    }


def pack_manual_default_settings(
    *,
    irrigation_mode: IrrigationMode,
    irrigation_total_duration: int,
    interval_irrigation_duration: int,
    interval_pause: int,
    capacity_unit: IrrigationAmountUnit,
    capacity_amount: int,
    fail_safe: int,
) -> ManualDefaultSettingsPayload:
    """Impacchetta i campi nel payload wire di 12 byte."""
    payload = bytearray(MANUAL_SETTINGS_LEN)
    payload[0] = int(IrrigationMode(irrigation_mode))
    td = int(irrigation_total_duration)
    payload[1] = (td >> 8) & 0xFF
    payload[2] = td & 0xFF
    idur = int(interval_irrigation_duration)
    payload[3] = (idur >> 8) & 0xFF
    payload[4] = idur & 0xFF
    ip = int(interval_pause)
    payload[5] = (ip >> 8) & 0xFF
    payload[6] = ip & 0xFF
    payload[7] = int(IrrigationAmountUnit(capacity_unit))
    vol = int(capacity_amount)
    payload[8] = (vol >> 8) & 0xFF
    payload[9] = vol & 0xFF
    fs = int(fail_safe)
    payload[10] = (fs >> 8) & 0xFF
    payload[11] = fs & 0xFF
    return ManualDefaultSettingsPayload(payload)


class SWVZF2Cluster(CustomCluster):
    """Cluster privato Sonoff 0xFC11 per SWV-ZF2 (per endpoint/canale)."""

    cluster_id = 0xFC11
    ep_attribute = "swvzf2_cluster"

    class AttributeDefs(BaseAttributeDefs):
        """Attributi del cluster privato Sonoff."""

        water_valve_state = ZCLAttributeDef(
            id=0x500C,
            type=ValveState,
            manufacturer_code=None,
        )
        # Durata irrigazione oraria (minuti)
        water_usage_duration = ZCLAttributeDef(
            id=0x501C,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # Volume irrigazione orario (litri)
        water_usage_volume = ZCLAttributeDef(
            id=0x501B,
            type=t.uint32_t,
            manufacturer_code=None,
        )
        # Impostazioni irrigazione manuale singola (array 12 byte)
        manual_default_settings = ZCLAttributeDef(
            id=0x501D,
            type=ManualDefaultSettingsPayload,
            zcl_type=foundation.DataTypeId.array,
            manufacturer_code=None,
        )

    def _repair_malformed_array_read_response(self, data: bytes) -> bytes | None:
        """Ripara le read-response 0x501D con element-type array duplicato.

        Alcune firmware della famiglia SWV rispondono al read di un attributo
        array con DataTypeId.array ripetuto anche come element type; zigpy non
        riesce a deserializzare. Riscriviamo l'element type in uint8.
        """
        try:
            hdr, payload = foundation.ZCLHeader.deserialize(data)
        except Exception:
            return None
        if (
            hdr.frame_control.frame_type != foundation.FrameType.GLOBAL_COMMAND
            or hdr.direction != foundation.Direction.Server_to_Client
            or hdr.command_id != foundation.GeneralCommand.Read_Attributes_rsp
        ):
            return None
        malformed_prefix = (
            self.AttributeDefs.manual_default_settings.id.serialize()
            + foundation.Status.SUCCESS.serialize()
            + foundation.DataTypeId.array.serialize()
            + foundation.DataTypeId.array.serialize()
        )
        repaired_prefix = (
            self.AttributeDefs.manual_default_settings.id.serialize()
            + foundation.Status.SUCCESS.serialize()
            + foundation.DataTypeId.array.serialize()
            + foundation.DataTypeId.uint8.serialize()
        )
        if malformed_prefix not in payload:
            return None
        return hdr.serialize() + payload.replace(malformed_prefix, repaired_prefix)

    def deserialize(self, data: bytes):
        """Deserializza, riparando i frame array malformati quando possibile."""
        try:
            return super().deserialize(data)
        except Exception as exc:
            repaired = self._repair_malformed_array_read_response(data)
            if repaired is not None:
                return super().deserialize(repaired)
            self.warning(
                "Failed to deserialize SWV-ZF2 cluster frame on endpoint %s: %s; raw=%s",
                self.endpoint.endpoint_id,
                exc,
                data.hex(" "),
            )
            raise

    def _sync_manual_config(self, value: Any) -> None:
        """Propaga un payload 0x501D al cluster locale di configurazione."""
        local = getattr(self.endpoint, "swvzf2_manual_config", None)
        if local is None:
            return
        try:
            local.update_from_manual_default_settings(value)
        except ValueError as exc:
            self.warning(
                "Ignoring invalid manual_default_settings payload %r: %s", value, exc
            )

    def _update_attribute(self, attrid, value):
        """Intercetta gli attributi array per evitare errori appdb.

        - 0x501D: decodificato nel cluster locale e consumato (il salvataggio
          appdb del tipo Array non e' supportato e loggherebbe un errore a ogni
          report).
        - 0x501F (irrigation schedule status, array, non caratterizzato su
          ZF2): consumato silenziosamente per lo stesso motivo.
        """
        if attrid == self.AttributeDefs.manual_default_settings.id:
            self._sync_manual_config(value)
            return
        if attrid == 0x501F:
            return
        super()._update_attribute(attrid, value)

    async def apply_custom_configuration(self, *args, **kwargs):
        """Legge 0x501D al (re)configure per inizializzare le entita'."""
        try:
            await self.read_attributes(
                [self.AttributeDefs.manual_default_settings.id]
            )
        except Exception as exc:
            self.warning(
                "Unable to read manual_default_settings during configuration: %s; "
                "continuing",
                exc,
            )

    async def read_attributes(self, attributes, *args, **kwargs):
        """Legge gli attributi propagando 0x501D al cluster locale."""
        result = await super().read_attributes(attributes, *args, **kwargs)
        success = result[0] if result else {}
        for key, value in dict(success).items():
            attr_id = key if isinstance(key, int) else getattr(key, "id", None)
            if attr_id == self.AttributeDefs.manual_default_settings.id or key == (
                self.AttributeDefs.manual_default_settings.name
            ):
                self._sync_manual_config(value)
        return result

    async def write_attributes(
        self,
        attributes: dict[str | int | foundation.ZCLAttributeDef, Any],
        manufacturer: int | None = None,
        **kwargs,
    ) -> list[list[foundation.WriteAttributesStatusRecord]]:
        """Scrive gli attributi instradando 0x501D sul path raw per array."""
        other_attributes: dict[Any, Any] = {}
        manual_value: Any | None = None

        for attr, value in attributes.items():
            attr_def = self.find_attribute(attr)
            if attr_def.id == self.AttributeDefs.manual_default_settings.id:
                manual_value = value
            else:
                other_attributes[attr] = value

        results: list[foundation.WriteAttributesStatusRecord] = []

        if other_attributes:
            results.extend(
                (
                    await super().write_attributes(
                        other_attributes, manufacturer, **kwargs
                    )
                )[0]
            )

        if manual_value is not None:
            payload = normalize_manual_default_settings(manual_value)
            zcl_attr = foundation.Attribute(
                self.AttributeDefs.manual_default_settings.id,
                foundation.TypeValue(),
            )
            zcl_attr.value.type = foundation.DataTypeId.array
            zcl_attr.value.value = foundation.Array(
                type=foundation.DataTypeId.uint8,
                value=payload,
            )
            raw_result = await self.write_attributes_raw([zcl_attr], manufacturer)
            record = None
            if raw_result and isinstance(raw_result[0], list) and raw_result[0]:
                record = raw_result[0][0]
            status = getattr(record, "status", foundation.Status.SUCCESS)
            results.append(
                foundation.WriteAttributesStatusRecord(
                    status=status,
                    attrid=self.AttributeDefs.manual_default_settings.id,
                )
            )
            if status == foundation.Status.SUCCESS:
                # Aggiorna il cluster locale con quanto appena scritto.
                self._sync_manual_config(payload)

        return [results]


class SWVZF2ManualConfigCluster(LocalDataCluster):
    """Cluster locale che espone 0x501D come attributi individuali.

    Le scritture su singoli attributi vengono unite allo stato in cache,
    reimpacchettate nei 12 byte wire e inoltrate a SWVZF2Cluster dello
    stesso endpoint come un'unica write di manual_default_settings.
    """

    cluster_id = 0xFBFC
    ep_attribute = "swvzf2_manual_config"

    class AttributeDefs(BaseAttributeDefs):
        """Attributi virtuali, uno per campo di manual_default_settings."""

        irrigation_mode: Final = ZCLAttributeDef(id=0x0000, type=IrrigationMode)
        irrigation_total_duration: Final = ZCLAttributeDef(
            id=0x0001, type=t.uint16_t
        )
        interval_irrigation_duration: Final = ZCLAttributeDef(
            id=0x0002, type=t.uint16_t
        )
        interval_pause: Final = ZCLAttributeDef(id=0x0003, type=t.uint16_t)
        capacity_unit: Final = ZCLAttributeDef(
            id=0x0004, type=IrrigationAmountUnit
        )
        capacity_amount: Final = ZCLAttributeDef(id=0x0005, type=t.uint16_t)
        fail_safe: Final = ZCLAttributeDef(id=0x0006, type=t.uint16_t)

    _SETTING_ATTRS = (
        AttributeDefs.irrigation_mode,
        AttributeDefs.irrigation_total_duration,
        AttributeDefs.interval_irrigation_duration,
        AttributeDefs.interval_pause,
        AttributeDefs.capacity_unit,
        AttributeDefs.capacity_amount,
        AttributeDefs.fail_safe,
    )

    def update_from_manual_default_settings(self, value: Any) -> None:
        """Aggiorna gli attributi locali da un payload 0x501D."""
        decoded = decode_manual_default_settings(value)
        for name, attr_value in decoded.items():
            self._update_attribute(self.find_attribute(name), attr_value)

    def _current_settings(self) -> dict[str, Any]:
        settings: dict[str, Any] = {}
        for attr_def in self._SETTING_ATTRS:
            value = self.get(attr_def.id)
            if value is None:
                raise ValueError(
                    "manual_default_settings are not initialized yet; "
                    "read the device first"
                )
            settings[attr_def.name] = value
        return settings

    async def write_attributes(
        self,
        attributes: dict[str | int | ZCLAttributeDef, Any],
        *args,
        **kwargs,
    ) -> list:
        """Unisce le modifiche allo stato in cache e scrive il payload."""
        try:
            settings = self._current_settings()
        except ValueError:
            await self.endpoint.swvzf2_cluster.read_attributes(
                [SWVZF2Cluster.AttributeDefs.manual_default_settings.id]
            )
            settings = self._current_settings()

        for attr, value in attributes.items():
            attr_name = self.find_attribute(attr).name
            if attr_name == "irrigation_mode":
                settings[attr_name] = IrrigationMode(int(value))
            elif attr_name == "capacity_unit":
                settings[attr_name] = IrrigationAmountUnit(int(value))
            else:
                settings[attr_name] = int(value)

        payload = pack_manual_default_settings(
            irrigation_mode=settings["irrigation_mode"],
            irrigation_total_duration=int(settings["irrigation_total_duration"]),
            interval_irrigation_duration=int(
                settings["interval_irrigation_duration"]
            ),
            interval_pause=int(settings["interval_pause"]),
            capacity_unit=settings["capacity_unit"],
            capacity_amount=int(settings["capacity_amount"]),
            fail_safe=int(settings["fail_safe"]),
        )

        return await self.endpoint.swvzf2_cluster.write_attributes(
            {SWVZF2Cluster.AttributeDefs.manual_default_settings.id: payload}
        )


def _register_channel_entities(builder: QuirkBuilder, ep: int) -> QuirkBuilder:
    """Registra le entita' di controllo irrigazione per un canale/endpoint."""
    ch = f"ch{ep}"
    return (
        builder.enum(
            SWVZF2ManualConfigCluster.AttributeDefs.irrigation_mode.name,
            IrrigationMode,
            SWVZF2ManualConfigCluster.cluster_id,
            endpoint_id=ep,
            entity_type=EntityType.CONFIG,
            unique_id_suffix=f"irrigation_mode_{ch}",
            translation_key=f"irrigation_mode_{ch}",
            fallback_name=f"Irrigation mode CH{ep}",
        )
        .number(
            SWVZF2ManualConfigCluster.AttributeDefs.irrigation_total_duration.name,
            SWVZF2ManualConfigCluster.cluster_id,
            endpoint_id=ep,
            entity_type=EntityType.CONFIG,
            min_value=0,
            max_value=719,
            step=1,
            unit=UnitOfTime.MINUTES,
            device_class=NumberDeviceClass.DURATION,
            unique_id_suffix=f"irrigation_duration_{ch}",
            translation_key=f"irrigation_duration_{ch}",
            fallback_name=f"Irrigation duration CH{ep}",
        )
        .number(
            SWVZF2ManualConfigCluster.AttributeDefs.capacity_amount.name,
            SWVZF2ManualConfigCluster.cluster_id,
            endpoint_id=ep,
            entity_type=EntityType.CONFIG,
            min_value=0,
            max_value=10000,
            step=1,
            unit=UnitOfVolume.LITERS,
            unique_id_suffix=f"irrigation_volume_{ch}",
            translation_key=f"irrigation_volume_{ch}",
            fallback_name=f"Irrigation volume CH{ep}",
        )
        .number(
            SWVZF2ManualConfigCluster.AttributeDefs.fail_safe.name,
            SWVZF2ManualConfigCluster.cluster_id,
            endpoint_id=ep,
            entity_type=EntityType.CONFIG,
            min_value=0,
            max_value=719,
            step=1,
            unit=UnitOfTime.MINUTES,
            unique_id_suffix=f"fail_safe_{ch}",
            translation_key=f"fail_safe_{ch}",
            fallback_name=f"Fail-safe timeout CH{ep}",
        )
    )


_builder = (
    QuirkBuilder("SONOFF", "SWV-ZF2")
    .also_applies_to("SONOFF", "SWV-ZF2U")
    .also_applies_to("SONOFF", "SWV-ZF2E")
    .replaces(SWVZF2Cluster)
    .replaces(SWVZF2Cluster, endpoint_id=2)
    .adds(SWVZF2ManualConfigCluster)
    .adds(SWVZF2ManualConfigCluster, endpoint_id=2)
    # Perdita acqua (bit1 di 0x500C)
    .binary_sensor(
        SWVZF2Cluster.AttributeDefs.water_valve_state.name,
        SWVZF2Cluster.cluster_id,
        device_class=BinarySensorDeviceClass.MOISTURE,
        attribute_converter=lambda x: x & ValveState.Water_Leakage,
        unique_id_suffix="water_leak_status",
        reporting_config=ReportingConfig(
            min_interval=30, max_interval=900, reportable_change=1
        ),
        translation_key="water_leak",
        fallback_name="Water leak",
    )
    # Mancanza acqua (bit0 canale 1 / bit4 canale 2 di 0x500C)
    .binary_sensor(
        SWVZF2Cluster.AttributeDefs.water_valve_state.name,
        SWVZF2Cluster.cluster_id,
        device_class=BinarySensorDeviceClass.PROBLEM,
        attribute_converter=lambda x: x
        & (ValveState.Water_Shortage | ValveState.Water_Shortage_Channel_2),
        unique_id_suffix="water_depletion_status",
        translation_key="water_depletion",
        fallback_name="Water depletion",
    )
    # Durata irrigazione - canale 1 (endpoint 1, 0x501C)
    .sensor(
        attribute_name=SWVZF2Cluster.AttributeDefs.water_usage_duration.name,
        cluster_id=SWVZF2Cluster.cluster_id,
        endpoint_id=1,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        unit=UnitOfTime.MINUTES,
        unique_id_suffix="water_usage_duration_ch1",
        reporting_config=ReportingConfig(
            min_interval=30, max_interval=900, reportable_change=1
        ),
        translation_key="water_usage_duration_ch1",
        fallback_name="Water usage duration CH1",
    )
    # Durata irrigazione - canale 2 (endpoint 2, 0x501C)
    .sensor(
        attribute_name=SWVZF2Cluster.AttributeDefs.water_usage_duration.name,
        cluster_id=SWVZF2Cluster.cluster_id,
        endpoint_id=2,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        unit=UnitOfTime.MINUTES,
        unique_id_suffix="water_usage_duration_ch2",
        reporting_config=ReportingConfig(
            min_interval=30, max_interval=900, reportable_change=1
        ),
        translation_key="water_usage_duration_ch2",
        fallback_name="Water usage duration CH2",
    )
    # Volume irrigazione (litri, 0x501B)
    .sensor(
        attribute_name=SWVZF2Cluster.AttributeDefs.water_usage_volume.name,
        cluster_id=SWVZF2Cluster.cluster_id,
        device_class=SensorDeviceClass.VOLUME,
        state_class=SensorStateClass.TOTAL_INCREASING,
        unit=UnitOfVolume.LITERS,
        unique_id_suffix="water_usage_volume",
        reporting_config=ReportingConfig(
            min_interval=30, max_interval=900, reportable_change=1
        ),
        translation_key="water_usage_volume",
        fallback_name="Water usage volume",
    )
)

_builder = _register_channel_entities(_builder, 1)
_builder = _register_channel_entities(_builder, 2)
_builder.add_to_registry()
