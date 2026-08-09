# SONOFF SWV-ZF2 — reverse-engineered Zigbee attributes (cluster `0xFC11`)

Field notes from a **SONOFF SWV-ZF2E**, dual-channel Zigbee smart water valve,
firmware `0x00001007`, paired to ZHA. Everything below was observed on the wire
with ZHA debug logging during real irrigation runs on 2026-08-09 — not guessed
from documentation.

Written in English on purpose: this device is poorly documented and the
information is hard to find. If it saves you an afternoon, it did its job.

Device identifies over Zigbee as manufacturer `SONOFF`, model `SWV-ZF2`. The
`E`/`U` suffix on the box is the market variant only. Sibling models (`SWV`,
`SWV-ZFU`, `SWV-ZFE`, `SWV-ZNE`) share most of this cluster; the dual-channel
specifics are `ZF2`.

## Summary of findings

| Finding | Consequence |
|---|---|
| `0x501F` is a **live progress feed**, one report every ~6 s while irrigating | The only real-time source of elapsed time and delivered volume |
| `0x5006` / `0x5007` are the **live consumption counters**, reported every ~60 s | Use these, not `0x501B`/`0x501C` |
| `0x501B` / `0x501C` **reject `configure_reporting`** | Sensors bound to them stay `unknown` forever |
| `0x500C` is `uint8` on the wire, **not `enum8`** | Declaring `enum8` makes `configure_reporting` fail with `INVALID_DATA_TYPE` |
| `0x501D` exists **only on endpoint 1** | Irrigation config is global; it is *not* per channel |
| Timestamps inside `0x501F` are an internal counter, **not a clock** | Only differences are meaningful |

## Attribute map

Cluster `0xFC11`, present on endpoint 1 and endpoint 2. No manufacturer code —
these are plain reads/writes.

| ID | Name | Type | Notes |
|---|---|---|---|
| `0x5006` | valve open duration | `uint32` | Minutes. Live, per channel. Reported ~every 60 s during a run. |
| `0x5007` | irrigation volume | `uint32` | Litres. Live. Reported alongside `0x5006`. |
| `0x5008` | cyclic timer | — | Not characterised here. |
| `0x500C` | valve state | **`uint8`** | Bitmask, see below. |
| `0x500D` | irrigation start time | — | Not characterised here. |
| `0x500E` | irrigation end time | — | Not characterised here. |
| `0x500F` | daily irrigation volume | — | Not characterised here. |
| `0x5010` | work state | — | Not characterised here. |
| `0x501B` | water usage volume | `uint32` | Litres. Mirrors `0x5007` but rarely reported. |
| `0x501C` | water usage duration | `uint32` | Minutes. Mirrors `0x5006` but rarely reported. |
| `0x501D` | manual default settings | `array` of `uint8`, 12 bytes | Irrigation config. **Endpoint 1 only.** |
| `0x501F` | irrigation status | `array` of `uint8`, 15 or 21 bytes | Live session progress. |

### `0x500C` — valve state bitmask

| Bit | Meaning |
|---|---|
| 0 | water shortage, channel 1 |
| 1 | water leakage |
| 2 | anti-frost alarm |
| 4 | water shortage, channel 2 |

**The wire type is `uint8` (`DataTypeId` `0x20`), not `enum8` (`0x30`).** If you
declare it as `enum8`, `configure_reporting` is answered with:

```
Configure_Reporting_rsp(status=INVALID_DATA_TYPE, attrid=0x500C)
```

and the attribute never reports. Observed request that fails:

```
AttributeReportingConfig(attrid=0x500C, datatype=<DataTypeId.enum8: 48>,
                         min_interval=30, max_interval=900, reportable_change=1)
```

while the device's own read response says:

```
ReadAttributeRecord(attrid=20492, status=SUCCESS,
                    value=TypeValue(type=uint8_t, value=0))
```

### `0x501D` — manual default settings (write to configure a run)

12-byte `uint8` array, **big-endian** multi-byte fields.

| Byte | Field | Range |
|---|---|---|
| 0 | irrigation mode | `0` duration, `1` capacity, `2` duration with interval |
| 1–2 | total duration | minutes, 0–719 |
| 3–4 | interval irrigation duration | minutes |
| 5–6 | interval pause | minutes |
| 7 | capacity unit | `0` US gallon, `1` litre |
| 8–9 | capacity amount | 0–10000 |
| 10–11 | fail-safe timeout | minutes, 0–719 |

**This attribute lives only on endpoint 1.** A read on endpoint 2 is answered —
the device is awake and replying — but without a value:

```
ep2 ReadAttributesResponse(attrid=20509, status=UNSUPPORTED_ATTRIBUTE)
```

So on a dual-channel ZF2 the irrigation configuration is **global**. The two
`OnOff` switches (endpoint 1 and 2) are independent — you choose *which* channel
to open — but both run with the same settings. This matches Zigbee2MQTT, which
exposes a single `manual_default_settings` for the family.

Two more practical notes:

- Home Assistant's `zha.set_zigbee_cluster_attribute` **cannot write this**: it
  does not handle ZCL arrays. You need a raw write path
  (`foundation.Array` + `write_attributes_raw`).
- Some SWV firmwares answer a read of this attribute with the array element type
  duplicated (`array` where `uint8` belongs), which zigpy cannot deserialize. On
  this firmware that did not occur, but the workaround is cheap: rewrite the
  element type byte before deserializing.

### `0x501F` — irrigation status (live progress) ⭐

The interesting one. **Reported spontaneously every ~6 seconds while the valve
is irrigating.** No `configure_reporting` needed — and none is accepted.

Two variants:

```
15 bytes   header(4) + start(4) + end(4) + tail(3)
21 bytes   header(4) + start(4) + end(4) + current(4) + tail(3) + volume(2)
```

| Byte | Field | Values |
|---|---|---|
| 0 | session state | `0` preamble, `2` running, `1` finished |
| 1 | reserved | always `0x00` observed |
| 2 | format marker | always `0x01` observed |
| 3 | irrigation mode | `0` duration, `1` capacity — same encoding as `0x501D` byte 0 |
| 4–7 | session start | `uint32` BE, internal counter |
| 8–11 | session end | `uint32` BE — `end − start` = target duration in seconds |
| 12–15 | current time | `uint32` BE, advances 6 per report (21-byte only) |
| 16–18 | tail | always `0x00 0x00 0x01` observed |
| 19–20 | **volume delivered this session** | `uint16` BE, litres (21-byte only) |

The 15-byte variant is emitted ~2 s **before** the valve opens: it announces the
planned window without a current time or a volume.

#### Timestamps are not a clock

Decoded as a Zigbee epoch (2000-01-01) the start byte of a run at 14:46:47 UTC
gives 23:00:39 — off by hours. Treat `start`/`end`/`current` as an **internal
monotonic counter**. Only differences are authoritative:

- `end − start` = target duration of the session, in seconds
- `current − start` = elapsed time, in seconds

Do not surface these as absolute times in a UI. They will be wrong.

#### Byte 3 and byte 19–20: where public sources disagree

A widely referenced gist for the single-channel **SWV-ZFU** describes byte 3 as
reserved and byte 20 as a per-session frame counter. Neither matches what this
ZF2 does:

- **Byte 3 tracks the mode.** It was `1` throughout a capacity-mode run and `0`
  throughout a duration-mode run, matching `0x501D` byte 0.
- **Bytes 19–20 track volume, not frames.** Over one 5-minute run the value went
  `0 → 30` across 51 reports — it cannot be a frame counter, and values repeat
  (`3, 3`, `5, 5`, `7, 7`). It rises in lockstep with `0x5007`, which went
  `8 → 38` over the same run: **+30 on both.**

The gist author tested a single-channel valve in duration mode only, where
byte 3 is always `0`. Both readings can be true for their device and wrong for
this one — verify on your own hardware before relying on either.

## Reporting: what the device accepts

This is the part that silently breaks integrations.

| Attribute | `configure_reporting` result |
|---|---|
| `0x500C` as `enum8` | `INVALID_DATA_TYPE` — declare it `uint8` instead |
| `0x501B` | `UNSUPPORTED_ATTRIBUTE` |
| `0x501C` | `UNSUPPORTED_ATTRIBUTE` (both endpoints) |
| `0x5006`, `0x5007` | never configured — they report on their own |
| `0x501F` | never configured — reports on its own every ~6 s |

If your sensors for water usage sit on `0x501B`/`0x501C` with a reporting
config, they will stay `unknown` indefinitely. Move them to `0x5006`/`0x5007`
and drop the reporting config entirely.

Note also that `0x501D` and `0x501F` are ZCL **arrays**, which Home Assistant's
ZHA attribute database cannot persist — it logs `type Array is not supported` on
every report. Consume them in the quirk and expose decoded scalars instead.

## Autonomous irrigation — how it actually works

The valve closes **itself**, on-device. Home Assistant is not in the loop and
does not need to be running.

1. Write the configuration to `0x501D` (mode, duration or volume, fail-safe).
2. Turn on the `OnOff` switch for the channel you want.
3. The valve closes on its own when the target is reached.

Verified in capacity mode with a 1-unit target and a 1-minute fail-safe:

| Channel | Session length |
|---|---|
| CH1 | 12.2 s |
| CH1 | 16.2 s |
| CH2 | 16.2 s |
| CH2 | 8.1 s |

All well inside the fail-safe window, so the fail-safe was not what closed the
valve — the volume target was. Durations vary with line pressure, as expected
for a volumetric target. **Channel 2 auto-closes exactly like channel 1**, using
the shared global configuration.

A duration-mode run configured for 5 minutes produced `end − start = 300`
seconds exactly in `0x501F`, and ran 302 s wall-clock.

## Gotchas

- **It is a sleepy battery device.** Wake it (press the physical button) before
  any reconfigure, and keep it awake until the bind and reporting setup finish.
  Otherwise entities stay `unknown` and it looks like a quirk bug.
- **Sonoff arrays use a 1-byte length field** inside `0xFC11`, where ZCL
  specifies 2 bytes. This has broken parsers elsewhere; see
  [zigbee-herdsman-converters#12599](https://github.com/Koenkk/zigbee-herdsman-converters/issues/12599).
- **`unknown` on a usage sensor is usually a reporting problem, not a device
  problem.** Check the `Configure_Reporting_rsp` status in a ZHA debug log
  before assuming the attribute is dead.

## Reproducing this

Enable ZHA debug logging, run an irrigation cycle, and grep the capture:

```yaml
logger:
  logs:
    zigpy.zcl: debug
    zigpy.application: debug
```

Then look for decoded frames on your device's NWK address:

```bash
grep "Decoded ZCL frame:" zha.log | grep "0xfc11"
```

The quirk in this repository decodes everything described above; the byte
layouts are exercised by unit tests against payloads captured verbatim from the
device — see `tests/test_sonoff_swv_zf2.py`.

## Sources and prior art

- [zigpy/zha-device-handlers#4993](https://github.com/zigpy/zha-device-handlers/pull/4993)
  — sensors for the SWV family
- [gist by nglessner](https://gist.github.com/nglessner/45dc518dd30b98826e4fb98277a1192b)
  — `0x501F` / `0x501D` on the single-channel SWV-ZFU, companion to
  [PR #4927](https://github.com/zigpy/zha-device-handlers/pull/4927)
- [Zigbee2MQTT — SWV-ZF2](https://www.zigbee2mqtt.io/devices/SWV-ZF2.html)
- [Koenkk/zigbee-herdsman-converters#12599](https://github.com/Koenkk/zigbee-herdsman-converters/issues/12599)
  — array length-field quirk in Sonoff's `0xFC11`

Corrections welcome — open an issue with a debug capture attached.
