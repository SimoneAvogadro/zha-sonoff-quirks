/**
 * Pure-logic tests for the Zigbee signal-quality icon of sonoff-valve-card.js
 * (sqLevel / sqResolve / sqRead / sqTitle / sqSvg), the same icon the two cards
 * of tuya-cards-for-ha show left of the battery. The card is loaded in a
 * node:vm context with the few browser globals its top level touches stubbed.
 *
 * Run with:  node tests/signal-quality.test.js   (or via tests/test_card_signal.py)
 */
"use strict";

const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");
const assert = require("node:assert/strict");

const SRC = path.join(__dirname, "..", "custom_components", "zha_sonoff_quirks", "www", "sonoff-valve-card.js");
const ctx = vm.createContext({
  console: { info() {}, log: console.log, warn() {}, error: console.error },
  HTMLElement: class {},
  customElements: { get() { return undefined; }, define() {} },
  window: {},
  navigator: { language: "it-IT" },
  localStorage: { getItem() { return null; } },
});
vm.runInContext(fs.readFileSync(SRC, "utf8"), ctx);

let failures = 0;
function test(name, fn) {
  try { fn(); console.log(`  ok  ${name}`); }
  catch (err) { failures++; console.log(`FAIL  ${name}\n      ${err.message}`); }
}

const { sqLevel, sqResolve, sqRead, sqTitle, sqSvg } = ctx;
const st = (state) => ({ state, attributes: {} });
// hass.entities is the frontend's entity-registry display map
// (entity_id → { entity_id, device_id, … }).
// Objects built inside the vm realm have another Object prototype, which the
// strict deepEqual rejects: normalise through JSON before comparing.
const plain = (o) => JSON.parse(JSON.stringify(o));
const reg = (m) => Object.fromEntries(Object.entries(m).map(([eid, dev]) => [eid, { entity_id: eid, device_id: dev }]));

// ── level thresholds ──
test("LQI thresholds → 4 levels", () => {
  assert.equal(sqLevel(255, null), 4);
  assert.equal(sqLevel(200, null), 4);
  assert.equal(sqLevel(199, null), 3);
  assert.equal(sqLevel(150, null), 3);
  assert.equal(sqLevel(149, null), 2);
  assert.equal(sqLevel(100, null), 2);
  assert.equal(sqLevel(99, null), 1);
  assert.equal(sqLevel(0, null), 1);
});
test("RSSI thresholds → 4 levels", () => {
  assert.equal(sqLevel(null, -60), 4);
  assert.equal(sqLevel(null, -61), 3);
  assert.equal(sqLevel(null, -70), 3);
  assert.equal(sqLevel(null, -71), 2);
  assert.equal(sqLevel(null, -80), 2);
  assert.equal(sqLevel(null, -81), 1);
});
test("LQI wins over RSSI when both are present", () => {
  assert.equal(sqLevel(120, -38), 2);
});
test("no numeric value → level 0", () => {
  assert.equal(sqLevel(null, null), 0);
  assert.equal(sqLevel(NaN, undefined), 0);
});

// ── device resolution ──
test("sqResolve finds the device's ZHA lqi + rssi whatever their prefix", () => {
  const hass = {
    states: { "switch.sonoff_swv_zf2_switch": st("off"), "sensor.sonoff_swv_zf2_lqi": st("144"), "sensor.sonoff_swv_zf2_rssi": st("-64") },
    entities: reg({ "switch.sonoff_swv_zf2_switch": "d1", "sensor.sonoff_swv_zf2_lqi": "d1", "sensor.sonoff_swv_zf2_rssi": "d1" }),
  };
  assert.deepEqual(plain(sqResolve(hass, "d1")), { lqi: "sensor.sonoff_swv_zf2_lqi", rssi: "sensor.sonoff_swv_zf2_rssi" });
});
test("sqResolve ignores other devices and registry entries without a state (disabled)", () => {
  const hass = {
    states: { "sensor.other_lqi": st("200") },
    entities: reg({ "sensor.other_lqi": "d2", "sensor.mine_lqi": "d1", "sensor.mine_rssi": "d1" }),
  };
  assert.deepEqual(plain(sqResolve(hass, "d1")), {});
});
test("sqResolve only takes sensors", () => {
  const hass = {
    states: { "binary_sensor.x_rssi": st("on") },
    entities: reg({ "binary_sensor.x_rssi": "d1" }),
  };
  assert.deepEqual(plain(sqResolve(hass, "d1")), {});
});
test("sqResolve tolerates a missing registry or device id", () => {
  assert.deepEqual(plain(sqResolve({ states: {} }, "d1")), {});
  assert.deepEqual(plain(sqResolve({ states: {}, entities: reg({ "sensor.a_lqi": "d1" }) }, "")), {});
  assert.deepEqual(plain(sqResolve(null, "d1")), {});
});

// ── reading ──
test("no signal entity → not present, level 0", () => {
  const r = sqRead({ states: {} }, {});
  assert.equal(r.present, false);
  assert.equal(r.level, 0);
});
test("ZHA lqi + rssi → present, LQI decides the level", () => {
  const r = sqRead({ states: { "sensor.v_lqi": st("144"), "sensor.v_rssi": st("-64") } }, { lqi: "sensor.v_lqi", rssi: "sensor.v_rssi" });
  assert.equal(r.present, true);
  assert.equal(r.lqi, 144);
  assert.equal(r.rssi, -64);
  assert.equal(r.level, 2);
});
test("Z2M linkquality only", () => {
  const r = sqRead({ states: { "sensor.v_linkquality": st("248") } }, { linkquality: "sensor.v_linkquality" });
  assert.equal(r.present, true);
  assert.equal(r.level, 4);
});
test("rssi only (lqi disabled) falls back to RSSI", () => {
  const r = sqRead({ states: { "sensor.v_rssi": st("-85") } }, { rssi: "sensor.v_rssi" });
  assert.equal(r.present, true);
  assert.equal(r.level, 1);
});
test("unknown value right after a restart → present but level 0", () => {
  const r = sqRead({ states: { "sensor.v_lqi": st("unknown") } }, { lqi: "sensor.v_lqi" });
  assert.equal(r.present, true);
  assert.equal(r.lqi, null);
  assert.equal(r.level, 0);
});

// ── title / markup ──
test("title lists the raw values that exist", () => {
  assert.equal(sqTitle({ lqi: 120, rssi: -70 }), "LQI 120 · RSSI -70 dBm");
  assert.equal(sqTitle({ lqi: 248, rssi: null }), "LQI 248");
  assert.equal(sqTitle({ lqi: null, rssi: -70 }), "RSSI -70 dBm");
  assert.equal(sqTitle({ lqi: null, rssi: null }), "");
});
test("svg carries four level classes a1..a4", () => {
  const s = sqSvg();
  for (const c of ["a1", "a2", "a3", "a4"]) assert.ok(s.includes(`class="${c}"`), `missing ${c}`);
  assert.ok(s.startsWith("<svg"));
});

console.log(failures ? `\n${failures} failing` : "\nall passing");
process.exit(failures ? 1 : 0);
