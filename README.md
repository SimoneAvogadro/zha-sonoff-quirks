# zha-sonoff-quirks

Custom ZHA quirk for the **SONOFF SWV-ZF2** — a **dual-channel** Zigbee
irrigation valve (sold as `SWV-ZF2E` / `SWV-ZF2U` depending on market, same
firmware `0x00001007`).

On top of upstream support, this quirk adds **control of the valve's on-device
autonomous irrigation**: set a duration or a volume, turn the channel's switch
on, and **the valve closes by itself** — even if Home Assistant is offline.

## What it exposes

| Entity | Type | Endpoint | Attribute |
|---|---|---|---|
| Water leak | binary_sensor (moisture) | 1 | `0x500C` bit 1 |
| Water depletion | binary_sensor (problem) | 1 | `0x500C` bit 0 / bit 4 |
| Water usage duration CH1 / CH2 | sensor (minutes) | 1 / 2 | `0x5006` |
| Water usage volume | sensor (litres) | 1 | `0x5007` |
| Irrigating | binary_sensor (running) | 1 | `0x501F` byte 0 |
| Session volume | sensor (litres) | 1 | `0x501F` bytes 19–20 |
| Session elapsed | sensor (seconds) | 1 | `0x501F` current − start |
| Session target duration | sensor (seconds) | 1 | `0x501F` end − start |
| Irrigation mode | select | 1 | `0x501D` byte 0 |
| Irrigation duration | number (0–719 min) | 1 | `0x501D` bytes 1–2 |
| Irrigation volume | number (0–10000 L) | 1 | `0x501D` bytes 8–9 |
| Fail-safe timeout | number (0–719 min) | 1 | `0x501D` bytes 10–11 |
| Irrigation history CH1 / CH2 | sensor (timestamp + `runs` attribute) | — | run log (integration) |
| Irrigation water total CH1 / CH2 | sensor (litres, `total_increasing`) | — | run log (integration) |

> **The configuration is global, not per channel.** `0x501D` only exists on
> endpoint 1 — verified on the device, see [TODO.md](TODO.md) #2. The four
> configuration entities apply to **both** outlets; the two switches stay
> independent, so you choose *which* channel to open, not how to configure each
> one separately.

The four session entities come from `0x501F`, which the device reports
spontaneously **every ~6 seconds while it is running**: that is the source for a
real-time progress bar. The attribute layout and the rest of the reverse
engineering are documented in [TESTS.md](TESTS.md).

The two history entity pairs (0.5.0+) are **not** device attributes: the valve
keeps no run log, so the integration records one server-side. Every run — no
matter how it started: the services below, an automation, a bare
`switch.turn_on`, the physical button, the firmware's own auto-close — is
observed on the channel switches and persisted (last 50 per channel) in
`.storage/zha_sonoff_quirks_history`, surviving restarts. Each record carries
start/end, duration, litres (from the session feed), mode, target, source and
an inferred close reason (`completed` vs `manual_off`). The water-total
sensors plug straight into HA's water dashboard and long-term statistics, and
each recorded run also fires a `zha_sonoff_quirks_irrigation_completed` event
for automations. When both channels run simultaneously the litres cannot be
attributed (the session feed is global) and are recorded as unknown.

> **Startup note.** ZHA loads before this integration on a cold Home
> Assistant start, so the SWV devices can come up without the quirk (all the
> entities above `unavailable`). The integration detects that after startup
> and reloads ZHA once, automatically; you may notice ZHA briefly restarting
> right after boot. This is expected.

The state and usage sensors derive from upstream PR
[zigpy/zha-device-handlers#4993](https://github.com/zigpy/zha-device-handlers/pull/4993);
the `manual_default_settings` (`0x501D`) control comes from the bench-verified
pattern in nglessner's gist, companion to PR #4927, rewritten here without the
event system and adapted to the dual-channel model.

## Installation

### Via HACS (recommended)

1. HACS → ⋮ menu → **Custom repositories** → add
   `https://github.com/SimoneAvogadro/zha-sonoff-quirks` with category
   **Integration**.
2. Search for *Sonoff ZHA*, then **Download**.
3. **Restart Home Assistant.**
4. `Settings → Devices & services → Add integration → Sonoff ZHA` →
   Submit. There is nothing to configure: the integration exists only to make
   the quirk get imported at startup.
5. Continue with the device **Reconfigure** (see below).

### Manual

1. Copy `custom_components/zha_sonoff_quirks/quirks/sonoff_swv_zf2.py` into a
   folder on your Home Assistant, e.g. `/config/custom_zha_quirks/`.

2. Point ZHA at that folder in `configuration.yaml`:

   ```yaml
   zha:
     custom_quirks_path: /config/custom_zha_quirks/
   ```

3. **Restart Home Assistant** — reloading ZHA is not enough, quirks are loaded
   at startup.

### Reconfigure (required either way)

On the SWV-ZF2 device page in ZHA, press **Reconfigure**. The valve is a sleepy
battery device: **wake it first** (press the physical button) and keep it awake
until the reconfigure finishes, otherwise binding and reporting are never
applied and the entities stay `unknown`.

Then check under `Settings → Devices → SWV-ZF2` that the configuration
`number` / `select` entities have appeared.

### Checking that the quirk is active

On the device page, *Zigbee info* should report the applied quirk as
`sonoff_swv_zf2`. Alternatively, in the logs:

```yaml
logger:
  logs:
    zhaquirks: debug
```

## Services (for automations)

The integration bundles the "configure, then open" sequence into two
device-centric services, so automations don't need to know the entity layout:

```yaml
action: zha_sonoff_quirks.irrigation_by_liters
data:
  device_id: abc123...        # device picker (SONOFF SWV-ZF2 only)
  channel: "2"                # radio buttons: Channel 1 / Channel 2
  liters: 250
  fail_safe_minutes: 60       # optional; leaves the current value if omitted

action: zha_sonoff_quirks.irrigation_by_minutes
data:
  device_id: abc123...
  channel: "1"
  minutes: 15
```

Minutes rather than seconds by design: `0x501D` has 1-minute granularity
(0–719). The service writes mode + target (+ fail-safe when given) and turns
the chosen channel's switch on — the valve closes by itself on-device, so there
is nothing to stop server-side. Stopping early is a plain `switch.turn_off` on
the channel switch. Entities are resolved from the registry by `unique_id`, so
renaming entities does not break the services. Starting a channel that is
already irrigating raises an error instead of silently retargeting the run.

## Lovelace card

The integration ships and auto-registers `sonoff-valve-card` — same look and
feel as the Tuya irrigation card, adapted to this valve's model: one shared
configuration block (liters or minutes) and **two green start buttons, one per
line**. The running line's button turns into a red stop. Progress comes from
the `0x501F` session feed (device truth: survives a browser refresh and shows
automation-started runs too).

When idle, the "last session" row shows the previous run (start time, duration,
litres) and — from 0.5.0 — a chevron that expands the **run history**: a
scrollable list of the recent runs of both lines merged (start time, line,
duration, litres and an outcome dot), fed by the integration's history sensors.
From 0.7.0 the list matches the Tuya irrigation card in
[tuya-cards-for-ha](https://github.com/simoneavogadro/tuya-cards-for-ha) row for
row, bar the line column. Configs saved with 0.4.0 pick the history entities up
automatically at runtime; no need to reopen the editor.

Add it from the dashboard card picker (*Sonoff Valve (Irrigation)*): pick the
device in the visual editor and it resolves all entities by `unique_id`,
storing them in the card config. Optional fields rename the card and the two
lines. If the card does not appear in the picker, hard-refresh the browser
after the first restart.

## Usage: autonomous irrigation

The model is **configure → turn on**:

1. set `Irrigation mode` to `duration` (timed) or `capacity` (by volume);
2. set `Irrigation duration` (minutes) or `Irrigation volume` (litres);
3. set `Fail-safe timeout` as a safety net;
4. turn on the switch for the channel you want to irrigate.

The valve closes on its own once the target is reached. No shutdown automation
is needed, and the cycle completes even with Home Assistant powered off.

Example script:

```yaml
irrigate_garden_250_litres:
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

> `zha.set_zigbee_cluster_attribute` **does not work** on `0x501D`: it is a ZCL
> array and the service cannot handle it. Use the entities exposed by the quirk,
> which go through the raw write path implemented in the cluster.

### Channel mapping

`endpoint 1 → switch`, `endpoint 2 → switch_2`. Which one corresponds to the
A/B lines printed on the valve body is **not yet confirmed**: check it by ear
(listen for the motor click) before trusting it in an automation.

Because the configuration is shared, irrigating the two channels with different
parameters means **serialising**: configure, open CH1, wait for it to close,
reconfigure, open CH2.

## Development

```bash
uv venv --python 3.13 .venv
uv pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

Layout:

```
custom_components/zha_sonoff_quirks/
  __init__.py        # imports quirks/ at startup; serves the card; services
  config_flow.py     # one-click flow, no options
  services.py        # irrigation_by_liters / irrigation_by_minutes
  services.yaml      # field selectors only; labels live in strings.json
  icons.json         # per-action icons, so the action picker stays readable
  strings.json       # source of truth; translations/ mirrors its key tree
  quirks/
    sonoff_swv_zf2.py  # THE quirk (single copy; integrity in checksums.sha256)
  www/
    sonoff-valve-card.js  # Lovelace card (auto-registered as a resource)
tests/               # 98 offline tests, no device required
```

Action names are deliberately **front-loaded with the unit** (*Liters🪣💧
(🌱volume irrigation)* / *Minutes⏰💧 (🌱timed irrigation)*): Home Assistant's
action picker renders one truncated line per action, so a shared prefix makes
the two indistinguishable on mobile. The emoji and the per-action icons in
`icons.json` carry the same distinction without depending on the reader's
language. `tests/test_translations.py` enforces the prefix rule for every
translation.

The quirk is deliberately **self-contained**: it imports nothing from the
integration, so it behaves identically whether loaded via HACS or dropped into
`custom_quirks_path`.

The tests run entirely offline against a fake `ControllerApplication`: no
network I/O, no device needed. The byte layouts are exercised against payloads
captured verbatim from a real valve.

## Status and known limits

See [TODO.md](TODO.md).

**Verified on the device** (fw `0x00001007`): the configuration is global, and
autonomous closing in `capacity` mode works **on both channels** — the valve
closes when the volume target is reached, well before the fail-safe.

**Not yet verified**: `duration` mode as a timed auto-close. The `0x501F` feed
reports it correctly, but the actual closing time has not been measured.

The usage sensors were moved from `0x501B`/`0x501C` to `0x5006`/`0x5007`: the
former reject `configure_reporting` and left the entities `unknown` forever.
Details in [TESTS.md](TESTS.md).

### Upgrading from 0.1.0

The configuration entities lost their channel suffix, so their `unique_id` and
`entity_id` changed. After upgrading: delete the orphaned `*_ch1` and `*_ch2`
entities from the registry, and update any automation or script that referenced
them.

## License

Apache-2.0, same as `zha-device-handlers`.
