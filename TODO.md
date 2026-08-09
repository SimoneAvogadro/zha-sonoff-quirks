# TODO

Stato al 2026-08-09. I punti 1–5 vengono dall'handoff
([docs/HANDOFF.md](docs/HANDOFF.md)); 6–7 sono emersi scrivendo i test, l'8
usando il dispositivo.

## 1. Test fisico dell'auto-chiusura — FATTO in modalità litri (2026-08-09)

**La valvola si chiude da sola.** Verificato in modalità `capacity` con
`Irrigation volume` = 1 L e `Fail-safe timeout` = 1 min.

Cicli osservati nello storico di HA (orari UTC, durata = `on` → `off`):

| Ora | Canale | Durata |
|---|---|---|
| 14:17:35 | CH1 | 12,2 s |
| 14:22:32 | CH1 | 16,2 s |
| 14:22:54 | CH2 | 16,2 s |
| 14:23:24 | CH2 | 8,1 s |

Le chiusure arrivano **molto prima** del fail-safe di 1 minuto, quindi non è la
rete di sicurezza a intervenire: è il raggiungimento del volume target. Le
durate variano con la pressione, come atteso per un target volumetrico.

**Risolve anche l'incognita aperta dal punto 2**: il canale 2 si auto-chiude
esattamente come il canale 1, usando la configurazione globale. L'irrigazione
autonoma non è una prerogativa di CH1.

Caveat: le durate sono transizioni di stato viste da HA su un dispositivo
sleepy, quindi la chiusura fisica può precedere di poco il report. Il segnale è
comunque ripetuto e coerente su quattro cicli e due canali.

### Residuo

- **Modalità `duration` non ancora provata.** È il percorso citato dal bench di
  riferimento (ZFU: 15 min → 15:01.9) e resta da confermare su questo esemplare.
- ~~`sensor.water_usage_volume` non riporta mai~~ — **risolto in 0.3.0**: il
  dispositivo rifiuta la `configure_reporting` su `0x501B`/`0x501C`
  (`UNSUPPORTED_ATTRIBUTE`). I sensori usano ora `0x5006`/`0x5007`, che il
  dispositivo riporta da solo. Vedi [TESTS.md](TESTS.md).
- **Mappatura fisica CH1/CH2 → linee A/B** ancora da confermare a orecchio.

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

**Incognita aperta dal risultato, poi sciolta**: il canale 2 usa la stessa
configurazione globale e si auto-chiude come il canale 1 — verificato, vedi #1.

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

I punti 1 e 2 sono ora sostanzialmente chiusi: configurazione globale, e
auto-chiusura confermata in modalità litri su entrambi i canali. La proposta si
semplifica (un solo blocco di configurazione, nessuna pretesa di dual-channel) e
ha ora evidenza sul campo che il write path fa davvero quello che dichiara.
Prima di aprirla converrebbe provare anche la modalità `duration`.

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

## 9. Verificare i campi incerti di `0x501F` — DA FARE

Il decode è ricostruito dai dati e documentato in [TESTS.md](TESTS.md), ma due
campi divergono dalla descrizione pubblica per la SWV-ZFU monocanale:

- **byte 3** (modalità): il gist lo dà per riservato. Qui vale `1` in capacity e
  `0` in duration. Confermare con una terza modalità
  (`duration_with_interval`) per vedere se compare `2`.
- **byte 19–20** (volume): il gist lo dà per contatore di frame. Qui cresce col
  volume e combacia con `0x5007`. Confermare con un target grande (es. 50 L),
  dove un contatore di frame e un volume divergerebbero in modo netto.

Da verificare anche se `0x501F` venga riportato sull'endpoint 2 durante una
corsa su CH2 — nel log catturato le corse su CH2 erano precedenti all'avvio del
debug. Il quirk instrada comunque i report di entrambi gli endpoint sull'unico
cluster locale, quindi funziona in ogni caso.

## 10. Attributi non caratterizzati

`0x5008` (cyclic timer), `0x500D` / `0x500E` (start/end time), `0x500F` (volume
giornaliero), `0x5010` (work state). `0x500D`/`0x500E` sono i più interessanti:
darebbero orari assoluti affidabili, che `0x501F` non offre.
