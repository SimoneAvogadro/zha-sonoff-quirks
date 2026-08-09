# zha-sonoff-quirks — HANDOFF per Claude Code

**Da**: sessione Claude web (2026-08-09) · **SHA256 sorgente**: `16908bf9bd7de570c40f0a654d04be2aa89f2987587b2cb90253cc4ebf84e337`

> Copia in-repo del documento originale su Google Drive
> (`AI/Claude/zha-sonoff-quirks/HANDOFF.md`). Il code block Python è stato
> estratto in [`sonoff_swv_zf2.py`](../sonoff_swv_zf2.py) — SHA256 verificato e
> combaciante — e qui sostituito da un rimando. Lo stato di avanzamento dei TODO
> è tracciato in [`TODO.md`](../TODO.md).

## Cosa fare

Estrarre il code block Python qui sotto in `sonoff_swv_zf2.py` nella root del repo
(o in `custom_zha_quirks/` se si adotta quella struttura). Il file e' pronto e validato:
import + registrazione OK contro zigpy 2.1.0 / zha-quirks 2.2.0, round-trip pack/decode testato.

## Contesto tecnico

- **Dispositivo**: SONOFF SWV-ZF2E (dual-channel water valve). Via Zigbee si annuncia
  come modello `SWV-ZF2`, fw `0x00001007` (~1.0.7). Il suffisso E/U e' solo il mercato.
- **Stato upstream**: PR zigpy/zha-device-handlers#4993 (aperta, maggio 2026) aggiunge i
  sensori per la famiglia SWV1C/SWV2C; NON espone i controlli di irrigazione autonoma.
  Questa quirk = sensori della #4993 + controllo `manual_default_settings` (0x501D)
  derivato dal gist bench-verified di nglessner (companion PR #4927, stesso fw 0x00001007),
  riscritto senza event-system zigpy e adattato al dual-channel.
- **Meccanismo chiave**: 0x501D (array ZCL 12 byte, campi BE) configura il one-shot
  autonomo: mode (0=duration/1=capacity/2=duration_with_interval), durata min (0-719),
  unita' (0=US gal, 1=litro), volume (0-10000), fail_safe min (0-719).
  Si scrive la config, si accende lo switch del canale, la valvola chiude DA SOLA
  on-device (safe anche con HA offline). `zha.set_zigbee_cluster_attribute` non gestisce
  gli array ZCL: per questo il cluster implementa il write path raw
  (foundation.Array + write_attributes_raw).
- **Dual-channel**: cluster 0xFC11 su endpoint 1 e 2; la quirk replica config cluster
  locale (0xFBFC, LocalDataCluster) ed entita' per entrambi. NON verificato empiricamente
  se 0x501D su ep2 configuri il canale 2 in modo indipendente (Z2M espone un solo
  manual_default_settings): da testare sul dispositivo; fallback atteso = config su ep1
  vale per entrambi.
- **Robustezza**: deserialize() ripara le read-response 0x501D con element-type array
  duplicato (bug noto famiglia SWV); _update_attribute consuma 0x501D e 0x501F per
  evitare gli errori appdb "type Array is not supported".
- **Endpoint mapping fisico**: ep1 = `switch`, ep2 = `switch_2`; corrispondenza con le
  linee A/B stampate sul corpo da confermare empiricamente (click del motorino).

## TODO suggeriti per il repo

1. Test fisico: mode=duration 2 min su CH1 -> verifica auto-chiusura (bench ZFU: 15 min -> 15:01.9).
2. Verifica indipendenza 0x501D per-endpoint (CH2).
3. Pytest con zhaquirks test harness (signature match SWV-ZF2).
4. README con installazione (custom_quirks_path, restart, Reconfigure obbligatorio su device sleepy).
5. Valutare upstream: commento/PR su zigpy/zha-device-handlers#4993 con l'estensione 0x501D.

## Sorgente `sonoff_swv_zf2.py`

Il code block dell'originale è stato estratto verbatim in
[`../sonoff_swv_zf2.py`](../sonoff_swv_zf2.py).

Verifica dell'integrità:

```bash
sha256sum sonoff_swv_zf2.py
# 16908bf9bd7de570c40f0a654d04be2aa89f2987587b2cb90253cc4ebf84e337
```
