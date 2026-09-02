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

- ~~Modalità `duration` non ancora provata~~ — **verificata il 2026-08-10**:
  corsa da 1 min su CH1 avviata via `irrigation_by_minutes`, auto-chiusura
  on-device dopo 62 s (8 L erogati), registrata dal run log 0.5.x come
  `completed`.
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

## 8. Adozione da parte di `tuya_irrigation` — DA DISMETTERE

L'integrazione `tuya_irrigation` (repo `tuya-cards-for-ha`) ha già adottato via
discovery lo switch della SWV-ZF2: esistono
`sensor.sonoff_swv_zf2_switch_irrigation_history` e
`sensor.sonoff_swv_zf2_switch_irrigation_water_total`.

**Verificato il 2026-08-10**: il run-log si popola (start/end/durata corretti,
8 corse del test del 2026-08-09 registrate) ma è monco — `liters`, `mode` e
`target` sempre `null` (mancano le DP Tuya), `water_total` fermo a 0, e copre
solo il canale 1. Dalla 0.5.0 questa integrazione ha uno storico nativo
per-canale arricchito con litri/modalità/target dai sensori `session_*`, che
lo rende superfluo.

**Residuo**: escludere la SWV-ZF2 dalla discovery di `tuya_irrigation` (lavoro
sul repo `tuya-cards-for-ha`) per evitare il doppio run-log sullo stesso
switch. Da chiarire lì anche l'effetto del keep-alive orario e dello sweep di
chiusura allo shutdown su una valvola che si chiude da sola on-device.

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

## 11. Default di fabbrica in galloni US — corretto nel quirk, da riverificare sul campo

Il payload `0x501D` di fabbrica letto dal dispositivo il 2026-08-09 (fw
`0x00001007`) è `[0,0,5,0,0,0,0,0,0,1,0,1]`: **byte 7 = 0, cioè gallone US**,
mentre l'entità del volume è esposta in litri. Il merge con la cache
conservava l'unità di fabbrica: scrivendo solo il volume, la UI diceva litri e
il dispositivo erogava galloni. Corretto: il quirk ora forza byte 7 = litro
quando si scrive `capacity_amount` senza un'unità esplicita (vedi
[TESTS.md](TESTS.md), «Capacity unit gotcha»).

Conseguenze da verificare:

- i test di auto-chiusura in modalità capacity del punto #1 (target «1 L»,
  chiusure in 8–16 s) sono stati con ogni probabilità eseguiti **in galloni**:
  l'unità in cache era ancora quella di fabbrica. 1 gal ≈ 3,79 L, coerente con
  le durate osservate più lunghe del previsto per 1 L.
- **riverificare sul dispositivo l'accuratezza del volume erogato in litri**
  dopo la correzione: target noto (es. 5 L), confronto con `0x5007` e con il
  volume reale raccolto.

## 10. Storico agganciato all'`entity_id` dello switch — LIMITE NOTO

Lo store del run log (`.storage/zha_sonoff_quirks_history`) e gli `unique_id`
dei sensori history/water-total incorporano l'`entity_id` dello switch del
canale. Rinominare quell'entity_id (operazione lecita in HA) fa ripartire lo
storico da zero: nuove entità sensore, contatore `total_increasing` azzerato,
corse precedenti orfane sotto la vecchia chiave (non perse: restano nel file).
Fix corretto: chiavare per `id` di registry dello switch (stabile ai rename)
con migrazione dello store. Rimandato: rename raro, danno limitato.

## 12. Device action — PARCHEGGIATE nel branch `device_actions`

Servivano a una cosa sola: mostrare nell'editor automazioni un selettore di
linea etichettato coi nomi del dispositivo (`A — Giardino`), impossibile in un
servizio perché `services.yaml` è statico e uguale per ogni valvola. Scritte
nella 0.10.0, **non hanno mai funzionato**: HA non ci ha mai interrogati.

Causa, dal sorgente di HA 2026.8.3
(`components/device_automation/__init__.py`): i domini candidati di un
dispositivo si raccolgono dai **`config_entries` del device** e dal
**`.domain` delle entità** che ci stanno sopra — mai dal `.platform` che le ha
create. Il nostro config entry non è sul device ZHA, perché `entity.py`
aggancia le entità via registro proprio per NON creare device duplicati.

E non è aggirabile: dalla 2026.8 il device registry è alla versione 3 e *«a
device belongs to a single config entry»*, con gli identifiers unici per
config entry e non più globalmente
([dev blog, 2026-07-21](https://developers.home-assistant.io/blog/2026/07/21/device-registry-single-config-entry/)).
`async_update_device(add_config_entry_id=…)` è deprecato; il sostituto
`new_config_entry_id` **trasferisce** la proprietà, cioè strapperebbe la
valvola a ZHA. Non esiste un modo documentato perché un'integrazione
contribuisca device action al dispositivo di un'altra.

L'unica strada rimasta, se un giorno la si vuole: **un device nostro**, con
identifiers nostri e `via_device` verso quello ZHA (la risoluzione di
`via_device` è ancora globale, verificato nel sorgente), su cui spostare le
nostre entità. Le device action funzionerebbero perché quel device lo
possediamo noi. Costo: i nostri sensori non sono più nella pagina della
valvola ma in un dispositivo figlio.

Da verificare prima di provarci: cosa succede al device figlio quando la
valvola ZHA viene rimossa.
