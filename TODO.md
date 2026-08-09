# TODO

Stato al 2026-08-09. I punti 1–5 vengono dall'handoff
([docs/HANDOFF.md](docs/HANDOFF.md)); 6–7 sono emersi scrivendo i test.

## 1. Test fisico dell'auto-chiusura — DA FARE (richiede hardware)

Bench di riferimento su SWV-ZFU: `mode=duration`, 15 min → chiusura a 15:01.9.

Procedura su questo dispositivo:

1. `Irrigation mode CH1` = `duration`, `Irrigation duration CH1` = `2`,
   `Fail-safe timeout CH1` = `5`.
2. Accendi `switch.swv_zf2_switch`, annota l'ora.
3. Verifica che lo switch torni `off` da solo a ~2 min **senza** automazioni.
4. Ripeti con `mode=capacity`, volume basso (es. 5 L), controllando
   `sensor.swv_zf2_water_usage_volume`.

Registra i risultati qui sotto.

| Data | Canale | Modo | Setpoint | Chiusura effettiva | Note |
|---|---|---|---|---|---|
| | | | | | |

## 2. Indipendenza di `0x501D` per endpoint — DA VERIFICARE (richiede hardware)

Zigbee2MQTT espone un solo `manual_default_settings`, quindi non è noto se
scrivere `0x501D` sull'endpoint 2 configuri il canale 2 in modo indipendente.

Test: scrivi durate diverse su CH1 e CH2, poi rileggi `0x501D` su **entrambi**
gli endpoint e confronta.

- Se le letture divergono → config per canale, quirk già corretta.
- Se coincidono → `0x501D` è globale: le entità CH2 vanno rimosse o rese
  alias di CH1, e il README aggiornato di conseguenza.

`tests/test_sonoff_swv_zf2.py::test_channel_2_writes_go_to_channel_2_cluster`
verifica solo l'instradamento software, non il comportamento del firmware.

## 3. Pytest con il test harness zhaquirks — FATTO (parziale)

`tests/` copre offline: signature match sui tre model id, presenza dei cluster su
entrambi gli endpoint, round-trip pack/decode, validazione dei payload,
riparazione delle read-response array malformate, consumo di `0x501D`/`0x501F` in
`_update_attribute`, write path raw, merge del cluster locale, `apply_custom_configuration`.

**Residuo**: `SWV_ZF2_SIGNATURE` in `tests/conftest.py` è ricostruita, non
catturata dal dispositivo. Sostituirla con la signature reale (copiabile da
*Zigbee info* → *Signature* nella pagina del device) e aggiungere un test che
la confronti byte per byte.

## 4. README con installazione — FATTO

Vedi [README.md](README.md): `custom_quirks_path`, riavvio, Reconfigure su device
sleepy, esempi d'uso e mappatura canali.

## 5. Upstream su zigpy/zha-device-handlers — DA FARE

La PR [#4993](https://github.com/zigpy/zha-device-handlers/pull/4993) copre solo i
sensori. Da proporre come commento o PR di follow-up:

- il write path raw per `0x501D` (`zha.set_zigbee_cluster_attribute` non gestisce
  gli array ZCL);
- la riparazione delle read-response con element type array duplicato;
- il cluster locale `0xFBFC` che espande i 12 byte in entità.

Prerequisito: chiudere i punti 1 e 2, altrimenti il dual-channel non è
difendibile in review.

## 6. Fallback enum morto in `decode_manual_default_settings`

I `try/except ValueError` intorno a `IrrigationMode(a[0])` e
`IrrigationAmountUnit(a[7])` non scattano mai: gli enum di zigpy sintetizzano un
membro `undefined_0xNN` invece di sollevare. Con un byte fuori range l'entità
`select` riceve quindi un'opzione non presente nella lista.

Impatto atteso: nullo con firmware sano (byte 0 ∈ {0,1,2}, byte 7 ∈ {0,1}).
Da correggere solo se il device riporta valori inattesi — verificare durante il
test fisico #1 e, in tal caso, sostituire il `try/except` con un controllo di
appartenenza esplicito.

Documentato in `test_decode_survives_unknown_enum_values`.

## 7. Distribuzione via HACS — FATTO (repo), DA COMPLETARE (aggiunta in HACS)

HACS non ha una categoria per le ZHA quirk, quindi il repo segue lo stesso
schema già in uso in `SimoneAvogadro/zha-tuya-quirks`: categoria `integration`,
con `custom_components/zha_sonoff_quirks/` che importa la quirk all'avvio.
Installazione manuale via `custom_quirks_path` resta supportata.

**Residuo**: aggiungere il repo come *custom repository* in HACS. L'operazione
non è esposta via API (né servizio né comando WebSocket accessibile dal proxy
MCP), va fatta dalla UI: HACS → ⋮ → Custom repositories →
`https://github.com/SimoneAvogadro/zha-sonoff-quirks`, categoria *Integration*.
