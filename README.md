# zha-sonoff-quirks

Custom ZHA quirk per **SONOFF SWV-ZF2** — elettrovalvola Zigbee per irrigazione a
**due canali** (varianti di mercato `SWV-ZF2E` / `SWV-ZF2U`, stesso firmware
`0x00001007`).

Rispetto al supporto upstream, questa quirk aggiunge il **controllo
dell'irrigazione autonoma on-device**: si configura durata o volume, si accende
lo switch del canale e **la valvola chiude da sola**, anche se Home Assistant è
offline.

## Cosa espone

| Entità | Tipo | Endpoint | Attributo |
|---|---|---|---|
| Water leak | binary_sensor (moisture) | 1 | `0x500C` bit 1 |
| Water depletion | binary_sensor (problem) | 1 | `0x500C` bit 0 / bit 4 |
| Water usage duration CH1 / CH2 | sensor (minuti) | 1 / 2 | `0x501C` |
| Water usage volume | sensor (litri) | 1 | `0x501B` |
| Irrigation mode | select | 1 | `0x501D` byte 0 |
| Irrigation duration | number (0–719 min) | 1 | `0x501D` byte 1–2 |
| Irrigation volume | number (0–10000 L) | 1 | `0x501D` byte 8–9 |
| Fail-safe timeout | number (0–719 min) | 1 | `0x501D` byte 10–11 |

> **La configurazione è globale, non per canale.** `0x501D` esiste solo
> sull'endpoint 1 — verificato sul dispositivo, vedi [TODO.md](TODO.md) #2. Le
> quattro entità di configurazione valgono per **entrambe** le uscite; i due
> switch restano indipendenti, quindi scegli *quale* canale aprire, non come
> configurarlo separatamente.

I sensori di stato/consumo derivano dalla PR upstream
[zigpy/zha-device-handlers#4993](https://github.com/zigpy/zha-device-handlers/pull/4993);
il controllo `manual_default_settings` (`0x501D`) dal pattern bench-verified del
gist di nglessner, companion della PR #4927, qui riscritto senza event-system e
adattato al dual-channel.

## Installazione

### Via HACS (consigliata)

1. HACS → menu ⋮ → **Custom repositories** → aggiungi
   `https://github.com/SimoneAvogadro/zha-sonoff-quirks` con categoria
   **Integration**.
2. Cerca *ZHA Sonoff Quirks*, **Download**.
3. **Riavvia Home Assistant.**
4. `Impostazioni → Dispositivi e servizi → Aggiungi integrazione → ZHA Sonoff
   Quirks` → Invia. Non c'è nulla da configurare: l'integrazione serve solo a
   far importare la quirk all'avvio.
5. Prosegui con il **Reconfigure** del dispositivo (vedi sotto).

### Manuale

1. Copia `custom_components/zha_sonoff_quirks/quirks/sonoff_swv_zf2.py` in una
   cartella del tuo Home Assistant, per esempio `/config/custom_zha_quirks/`.

2. Punta ZHA a quella cartella in `configuration.yaml`:

   ```yaml
   zha:
     custom_quirks_path: /config/custom_zha_quirks/
   ```

3. **Riavvia Home Assistant** (non basta il reload di ZHA: le quirk vengono
   caricate all'avvio).

### Reconfigure (obbligatorio, in entrambi i casi)

Nel device SWV-ZF2 in ZHA premi **Reconfigure**. La valvola è un device sleepy:
**svegliala prima** (premi il pulsante fisico) e tienila sveglia finché il
reconfigure non termina, altrimenti binding e reporting non vengono applicati e
le entità restano `unknown`.

Verifica poi in `Impostazioni → Dispositivi → SWV-ZF2` che compaiano le entità
`number`/`select` di configurazione.

### Verificare che la quirk sia attiva

Nella pagina del dispositivo, *Zigbee info* deve riportare la quirk applicata
come `sonoff_swv_zf2`. In alternativa, nei log:

```yaml
logger:
  logs:
    zhaquirks: debug
```

## Uso: irrigazione autonoma

Il modello d'uso è **configura → accendi**:

1. imposta `Irrigation mode` su `duration` (a tempo) o `capacity` (a volume);
2. imposta `Irrigation duration` (minuti) oppure `Irrigation volume` (litri);
3. imposta `Fail-safe timeout` come rete di sicurezza;
4. accendi lo switch del canale che vuoi irrigare.

La valvola chiude autonomamente al raggiungimento della soglia. Non serve
un'automazione di spegnimento e il ciclo completa anche con HA spento.

Esempio di script:

```yaml
irriga_orto_250_litri:
  sequence:
    - target: {entity_id: select.swv_zf2_irrigation_mode}
      action: select.select_option
      data: {option: capacity}
    - target: {entity_id: number.swv_zf2_irrigation_volume}
      action: number.set_value
      data: {value: 250}
    - target: {entity_id: number.swv_zf2_fail_safe_timeout}
      action: number.set_value
      data: {value: 60}
    - target: {entity_id: switch.swv_zf2_switch}
      action: switch.turn_on
```

> `zha.set_zigbee_cluster_attribute` **non** funziona su `0x501D`: è un array ZCL
> e il servizio non lo gestisce. Usa le entità esposte dalla quirk, che passano
> per il write path raw implementato nel cluster.

### Mappatura canali

`endpoint 1 → switch`, `endpoint 2 → switch_2`. La corrispondenza con le linee
A/B stampate sul corpo della valvola **non è ancora confermata**: verificala a
orecchio (click del motorino) prima di affidarci un'automazione.

Poiché la configurazione è condivisa, per irrigare i due canali con parametri
diversi devi **serializzare**: configura, apri CH1, attendi la chiusura,
riconfigura, apri CH2.

## Sviluppo

```bash
uv venv --python 3.13 .venv
uv pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

Layout:

```
custom_components/zha_sonoff_quirks/
  __init__.py        # importa quirks/ all'avvio -> registrazione nel registry
  config_flow.py     # flow a un click, nessuna opzione
  quirks/
    sonoff_swv_zf2.py  # LA quirk (unica copia; integrità in checksums.sha256)
tests/               # 39 test offline, nessun dispositivo richiesto
```

La quirk è volutamente **self-contained**: non importa nulla
dall'integrazione, così funziona identica sia caricata da HACS sia droppata in
`custom_quirks_path`.

I test girano interamente offline con un `ControllerApplication` finto: nessuna
IO di rete, nessun dispositivo richiesto.

## Stato e limiti noti

Vedi [TODO.md](TODO.md).

**Verificato sul dispositivo** (fw `0x00001007`): la configurazione è globale, e
l'auto-chiusura in modalità `capacity` funziona **su entrambi i canali** — la
valvola chiude al raggiungimento del volume, ben prima del fail-safe.

**Non ancora verificato**: la modalità `duration`; e
`sensor.water_usage_volume` (`0x501B`) non ha mai riportato durante le corse,
quindi non c'è oggi un erogato live su cui costruire una barra di avanzamento.

### Aggiornamento da 0.1.0

Le entità di configurazione hanno perso il suffisso di canale, quindi cambiano
`unique_id` ed `entity_id`. Dopo l'aggiornamento: elimina dal registro le vecchie
`*_ch1` e `*_ch2` rimaste orfane e aggiorna eventuali automazioni o script che le
referenziavano.

## Licenza

Apache-2.0, come `zha-device-handlers`.
