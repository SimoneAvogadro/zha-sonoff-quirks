# TODO

Stato al 2026-08-09. I punti 1–5 vengono dall'handoff
([docs/HANDOFF.md](docs/HANDOFF.md)); 6–7 sono emersi scrivendo i test, l'8
usando il dispositivo.

## 1. Test fisico dell'auto-chiusura — DA FARE (richiede hardware)

Bench di riferimento su SWV-ZFU: `mode=duration`, 15 min → chiusura a 15:01.9.

Procedura su questo dispositivo:

1. `Irrigation mode` = `duration`, `Irrigation duration` = `2`,
   `Fail-safe timeout` = `5`.
2. Accendi `switch.swv_zf2_switch`, annota l'ora.
3. Verifica che lo switch torni `off` da solo a ~2 min **senza** automazioni.
4. Ripeti con `mode=capacity`, volume basso (es. 5 L), controllando
   `sensor.swv_zf2_water_usage_volume`.
5. **Ripeti su `switch.swv_zf2_switch_2`** con la stessa config globale, per
   sciogliere l'incognita aperta dal punto 2.

Registra i risultati qui sotto.

| Data | Canale | Modo | Setpoint | Chiusura effettiva | Note |
|---|---|---|---|---|---|
| | | | | | |

## 2. Indipendenza di `0x501D` per endpoint — FATTO (2026-08-09): è GLOBALE

**Esito: la configurazione non è per canale.** `0x501D` esiste solo
sull'endpoint 1.

Come si è visto: impostando `Irrigation duration CH2` la scrittura falliva con
`manual_default_settings are not initialized yet`. Dal traceback, la rilettura
che il cluster locale tenta prima di scrivere **non ha sollevato eccezioni** —
quindi il dispositivo ha risposto, semplicemente senza un valore per `0x501D`
sull'endpoint 2. Conferma dagli stati: tutte le entità CH2 `unknown`, tutte le
CH1 con valori reali letti dal dispositivo. Coerente con Zigbee2MQTT, che espone
un solo `manual_default_settings` per questa famiglia.

Conseguenze applicate in 0.2.0:

- rimosse le entità di configurazione CH2 e il cluster locale `0xFBFC` sull'ep2;
- rimosso il suffisso di canale dai nomi (`Irrigation mode`, non `... CH1`);
- messaggio d'errore corretto: diceva «leggi prima il dispositivo» proprio dopo
  averlo letto;
- `0xFC11` resta sostituito su entrambi gli endpoint: i sensori di consumo per
  canale (`0x501C`) sono un'altra cosa e non sono stati smentiti.

**Nuova incognita aperta dal risultato**: cosa fa il canale 2 all'apertura? Usa
la stessa configurazione globale e si auto-chiude anche lui, o l'irrigazione
autonoma vale solo per CH1? Da verificare insieme al test #1.

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

Il punto 2 è chiuso (config globale), quindi la proposta si semplifica: un solo
blocco di configurazione, nessuna pretesa di dual-channel. Resta il punto 1 come
prerequisito: senza il test di auto-chiusura non c'è evidenza che il write path
faccia davvero quello che dichiara.

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

## 8. Adozione da parte di `tuya_irrigation` — DA CAPIRE

L'integrazione `tuya_irrigation` (repo `tuya-cards-for-ha`) ha già adottato via
discovery lo switch della SWV-ZF2: esistono
`sensor.sonoff_swv_zf2_switch_irrigation_history` e
`sensor.sonoff_swv_zf2_switch_irrigation_water_total`.

Non è chiaro se sia un bene o un problema. Da chiarire:

- il keep-alive orario e lo sweep di chiusura allo shutdown agiscono su questa
  valvola? Con che effetto su un dispositivo che si chiude già da solo?
- il run-log/storico si popola correttamente, o resta vuoto perché mancano le
  DP Tuya che si aspetta?
- conviene lasciarla fare (si ottiene lo storico gratis) o escludere il
  dispositivo dalla discovery?

Rilevante anche per la card: se lo storico funziona, una card Sonoff potrebbe
riusarlo invece di rinunciarvi.
