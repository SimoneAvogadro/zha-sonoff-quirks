/**
 * Sonoff Valve Card (Irrigation) for Home Assistant
 * Custom Lovelace card for the SONOFF SWV-ZF2 dual-channel Zigbee water valve,
 * paired with the zha_sonoff_quirks integration (quirk + irrigation services).
 * v0.5.1 — Run history: expandable list of past irrigations fed by the
 *          integration's per-channel history sensors (runs attribute), merged
 *          across the two channels. History entities resolve lazily at
 *          runtime too, so configs saved with 0.4.0 pick them up without
 *          reopening the editor.
 * v0.4.0 — Initial release. Visual language and interaction patterns are shared
 *          with the Tuya irrigation card (tuya-cards-for-ha):
 *          - build DOM once, patch values in place (no innerHTML on hass push);
 *          - never overwrite an input that is shadowRoot.activeElement;
 *          - editor updates in-memory config on `input`, fires config-changed
 *            only on `change` (blur/Enter);
 *          - start AND stop overlays with a 10s watchdog, cleared when the
 *            target switch confirms the new state (double-tap race guard);
 *          - offline = switch state unavailable/unknown ONLY (ZHA does not
 *            refresh last_updated, so no staleness heuristics);
 *          - 1s render tick only while running, cleared in disconnectedCallback;
 *            progress is derived from device truth (session_* sensors) so the
 *            bar self-corrects and survives a browser refresh.
 *          SWV-ZF2 specifics: two independent OnOff channels (endpoint 1 and 2)
 *          sharing ONE global irrigation config (mode/duration/volume live only
 *          on endpoint 1), on-device auto-close, ~6s progress feed
 *          (session_volume / session_elapsed / session_target) interpolated
 *          client-side between reports.
 */

// ── i18n ──
const I18N = {
  it: {
    irrigating: "Irrigando", off: "Spento", offline: "Offline",
    starting: "Avvio…", startFailed: "Avvio fallito",
    stopping: "Arresto…", stopFailed: "Arresto fallito",
    liters: "Litri", time: "Tempo", remaining: "rimanente",
    line1: "Linea 1", line2: "Linea 2",
    lastSession: "Ultima sessione", duration: "Durata", none: "nessuna",
    history: "Storico irrigazioni",
    configError: "Seleziona una valvola Sonoff nella configurazione",
    defaultName: "Irrigazione",
    integrationMissing: "Installa l'integrazione ZHA Sonoff Quirks per abilitare il controllo",
    offlineMsg: "Valvola non raggiungibile — controllare batteria e segnale Zigbee",
    editorDevice: "Valvola Sonoff", editorSelect: "— Seleziona —",
    editorHint: "Mostra solo le valvole SONOFF SWV-ZF2 compatibili",
    editorNoDevice: "Nessuna valvola compatibile trovata",
    editorName: "Nome (opzionale)", editorNamePh: "Nome personalizzato",
    editorNameHint: "Lascia vuoto per usare il nome del dispositivo",
    editorName1: "Nome Linea 1 (opzionale)", editorName2: "Nome Linea 2 (opzionale)",
    editorResolveFail: "Registro entità non leggibile — risoluzione euristica sugli entity_id",
    editorUnresolved: "Entità non risolte:",
    cardDesc: "Card per la valvola SONOFF SWV-ZF2 a due linee con avvio a litri o a tempo",
  },
  en: {
    irrigating: "Irrigating", off: "Off", offline: "Offline",
    starting: "Starting…", startFailed: "Start failed",
    stopping: "Stopping…", stopFailed: "Stop failed",
    liters: "Liters", time: "Time", remaining: "remaining",
    line1: "Line 1", line2: "Line 2",
    lastSession: "Last session", duration: "Duration", none: "none",
    history: "Irrigation history",
    configError: "Select a Sonoff valve in the configuration",
    defaultName: "Irrigation",
    integrationMissing: "Install the ZHA Sonoff Quirks integration to enable control",
    offlineMsg: "Valve unreachable — check battery and Zigbee signal",
    editorDevice: "Sonoff valve", editorSelect: "— Select —",
    editorHint: "Shows only compatible SONOFF SWV-ZF2 valves",
    editorNoDevice: "No compatible valve found",
    editorName: "Name (optional)", editorNamePh: "Custom name",
    editorNameHint: "Leave empty to use device name",
    editorName1: "Line 1 name (optional)", editorName2: "Line 2 name (optional)",
    editorResolveFail: "Entity registry not readable — falling back to entity_id heuristics",
    editorUnresolved: "Unresolved entities:",
    cardDesc: "Card for the dual-line SONOFF SWV-ZF2 valve with liters or time based runs",
  },
  zh: {
    irrigating: "灌溉中", off: "关闭", offline: "离线",
    starting: "启动中…", startFailed: "启动失败",
    stopping: "停止中…", stopFailed: "停止失败",
    liters: "升量", time: "时长", remaining: "剩余",
    line1: "线路 1", line2: "线路 2",
    lastSession: "上次灌溉", duration: "持续时间", none: "无",
    history: "灌溉历史",
    configError: "请在配置中选择 Sonoff 水阀",
    defaultName: "灌溉",
    integrationMissing: "请安装 ZHA Sonoff Quirks 集成以启用控制",
    offlineMsg: "阀门无法连接 — 请检查电池和 Zigbee 信号",
    editorDevice: "Sonoff 水阀", editorSelect: "— 选择 —",
    editorHint: "仅显示兼容的 SONOFF SWV-ZF2 水阀",
    editorNoDevice: "未找到兼容的水阀",
    editorName: "名称（可选）", editorNamePh: "自定义名称",
    editorNameHint: "留空使用设备名称",
    editorName1: "线路 1 名称（可选）", editorName2: "线路 2 名称（可选）",
    editorResolveFail: "无法读取实体注册表 — 回退到 entity_id 启发式匹配",
    editorUnresolved: "未解析的实体：",
    cardDesc: "适用于双线路 SONOFF SWV-ZF2 水阀的卡片，支持按升量或时长灌溉",
  },
};
function _i18nLang(hass) {
  const lang = hass?.language?.split("-")[0] || "en";
  return I18N[lang] ? lang : "en";
}
function _t(hass, key) { return (I18N[_i18nLang(hass)] || I18N.en)[key] || I18N.en[key] || key; }
function _numLocale(hass) { const l = hass?.language; return l || "en"; }

const COMPAT_MODELS = ["SWV-ZF2", "SWV-ZF2U", "SWV-ZF2E"];
// Every key the editor tries to resolve (battery is optional, resolved separately).
const ENTITY_KEYS = ["mode", "duration", "volume", "fail_safe", "irrigating",
  "session_volume", "session_elapsed", "session_target", "switch_1", "switch_2",
  "history_1", "history_2"];
// Optional keys: resolved when present but never worth a warning banner — the
// history sensors only exist from integration 0.5.0 on.
const OPTIONAL_KEYS = ["history_1", "history_2"];
// Keys the card runtime actually reads — missing ones are surfaced in the config banner.
const RUNTIME_KEYS = ["mode", "duration", "volume", "session_volume",
  "session_elapsed", "session_target", "switch_1", "switch_2"];
// Quirk entity unique_ids are "<ieee>-1-<suffix>" (endpoint 1, no cluster id).
// Always anchor the FULL suffix with the leading "-1-": "-duration" alone would
// collide with session_target_duration / water_usage_duration_ch1.
const UID_RULES = [
  ["mode",            "select",        "-1-irrigation_mode"],
  ["duration",        "number",        "-1-irrigation_duration"],
  ["volume",          "number",        "-1-irrigation_volume"],
  ["fail_safe",       "number",        "-1-fail_safe"],
  ["irrigating",      "binary_sensor", "-1-irrigating"],
  ["session_volume",  "sensor",        "-1-session_volume"],
  ["session_elapsed", "sensor",        "-1-session_elapsed"],
  ["session_target",  "sensor",        "-1-session_target_duration"],
];
// The integration's own history sensors embed the channel FIRST in their
// unique_id ("zha_sonoff_quirks_history_ch1_<switch entity_id>"), so they are
// matched by PREFIX, unlike the quirk entities above.
const UID_PREFIX_RULES = [
  ["history_1", "sensor", "zha_sonoff_quirks_history_ch1"],
  ["history_2", "sensor", "zha_sonoff_quirks_history_ch2"],
];
// entity_id-suffix fallback used only when the registry WS API is unavailable.
const EID_RULES = [
  ["mode",            "select.",        "_irrigation_mode"],
  ["duration",        "number.",        "_irrigation_duration"],
  ["volume",          "number.",        "_irrigation_volume"],
  ["fail_safe",       "number.",        "_fail_safe"],
  ["irrigating",      "binary_sensor.", "_irrigating"],
  ["session_volume",  "sensor.",        "_session_volume"],
  ["session_elapsed", "sensor.",        "_session_elapsed"],
  ["session_target",  "sensor.",        "_session_target_duration"],
  ["history_1",       "sensor.",        "_irrigation_history_ch1"],
  ["history_2",       "sensor.",        "_irrigation_history_ch2"],
];
const ICON_PLAY = `<svg width="18" height="18" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" fill="white"/></svg>`;
const ICON_STOP = `<svg width="16" height="16" viewBox="0 0 24 24" fill="white"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>`;
const BAD_STATES = ["unavailable", "unknown", "none", ""];

// ── Editor ──
// Build the DOM once, then update values in place. HA pushes a fresh `hass`
// object every few seconds; replacing shadowRoot.innerHTML on every push would
// destroy the focused <input> and steal the caret mid-typing. Focused inputs
// are never overwritten. Labels are baked at first build.
class SonoffValveCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._domBuilt = false;
    this._el = {};
    this._lastDevKey = "";
    this._warnText = "";
    this._seq = 0;
    this._resolvedFor = "";   // device_id an auto-resolution was attempted for
  }
  set hass(h) { this._hass = h; this._update(); }
  setConfig(c) { this._config = { ...c }; this._update(); }

  _buildDom() {
    const t = (k) => _t(this._hass, k);
    this.shadowRoot.innerHTML = `
<style>
.editor{padding:16px;font-family:var(--paper-font-body1_-_font-family,sans-serif)}
.row{margin-bottom:16px}
label{display:block;font-size:12px;font-weight:500;color:var(--secondary-text-color);margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em}
select,input[type="text"],input[type="number"]{width:100%;padding:10px 12px;border-radius:8px;border:1px solid var(--divider-color,rgba(255,255,255,.06));background:var(--card-background-color,#232640);color:var(--primary-text-color);font-size:14px;font-family:monospace;outline:none;box-sizing:border-box}
select:focus,input:focus{border-color:#4a90d9}
.hint{font-size:11px;color:var(--disabled-text-color,#5c5e76);margin-top:4px}
.empty{font-size:13px;color:var(--disabled-text-color);padding:12px;text-align:center;background:var(--divider-color,rgba(255,255,255,.06));border-radius:8px}
.warn{font-size:12px;color:#e25555;background:rgba(226,85,85,.12);border:1px solid rgba(226,85,85,.3);border-radius:8px;padding:10px 12px;white-space:pre-line}
[hidden]{display:none!important}
</style>
<div class="editor">
  <div class="row">
    <label>${t("editorDevice")}</label>
    <div id="dev-wrap">
      <select id="dev"></select>
      <div class="hint">${t("editorHint")}</div>
    </div>
    <div id="dev-empty" class="empty" hidden>${t("editorNoDevice")}</div>
  </div>
  <div class="row warn" id="warn" hidden></div>
  <div class="row">
    <label>${t("editorName")}</label>
    <input type="text" id="nm" placeholder="${t("editorNamePh")}">
    <div class="hint">${t("editorNameHint")}</div>
  </div>
  <div class="row">
    <label>${t("editorName1")}</label>
    <input type="text" id="n1" placeholder="${t("line1")}">
  </div>
  <div class="row">
    <label>${t("editorName2")}</label>
    <input type="text" id="n2" placeholder="${t("line2")}">
  </div>
</div>`;
    const r = this.shadowRoot;
    this._el = {
      dev: r.getElementById("dev"),
      devWrap: r.getElementById("dev-wrap"),
      devEmpty: r.getElementById("dev-empty"),
      warn: r.getElementById("warn"),
      nm: r.getElementById("nm"),
      n1: r.getElementById("n1"),
      n2: r.getElementById("n2"),
    };
    this._el.dev.addEventListener("change", (ev) => {
      const v = ev.target.value;
      if (!v) {
        const { device_id, entities, ...rest } = this._config;
        this._config = rest;
        this._warnText = "";
        this._fire();
        this._update();
        return;
      }
      this._resolveDevice(v).catch((err) => {
        console.warn("[sonoff-valve-card] device resolution failed", err);
      });
    });
    // Update the in-memory config on every keystroke so a Save click that
    // doesn't blur the input first still captures the typed value — but only
    // fire config-changed on `change` (blur/Enter). Firing per keystroke
    // round-trips through HA's hui-card-editor and blurs the input mid-typing.
    const bindName = (inputEl, key) => {
      inputEl.addEventListener("input", (e2) => {
        if (e2.target.value) this._config = { ...this._config, [key]: e2.target.value };
        else { const c = { ...this._config }; delete c[key]; this._config = c; }
      });
      inputEl.addEventListener("change", () => this._fire());
    };
    bindName(this._el.nm, "name");
    bindName(this._el.n1, "name_1");
    bindName(this._el.n2, "name_2");
    this._domBuilt = true;
  }

  // Compatible devices: model match first, entity heuristic as fallback.
  _compatDevices() {
    const devs = this._hass?.devices || {};
    const out = [];
    for (const [id, d] of Object.entries(devs)) {
      if (d && COMPAT_MODELS.includes(d.model)) {
        out.push({ id, label: d.name_by_user || d.name || id });
      }
    }
    if (!out.length) {
      const seen = new Set();
      for (const [eid, ent] of Object.entries(this._hass?.entities || {})) {
        if (!ent?.device_id || seen.has(ent.device_id)) continue;
        if (eid.startsWith("sensor.") && eid.endsWith("_session_volume")) {
          seen.add(ent.device_id);
          const d = devs[ent.device_id];
          out.push({ id: ent.device_id, label: d?.name_by_user || d?.name || ent.device_id });
        }
      }
    }
    out.sort((a, b) => a.label.localeCompare(b.label));
    return out;
  }

  // Resolve the entities map for a device. Primary path: entity registry over
  // WebSocket (unique_id rules — robust against renamed entity_ids). Fallback:
  // entity_id-suffix heuristics via hass.entities, with a visible warning.
  async _resolveDevice(deviceId) {
    const seq = ++this._seq;
    let wsFailed = false;
    let found = {};
    if (typeof this._hass?.callWS === "function") {
      try {
        const all = await this._hass.callWS({ type: "config/entity_registry/list" });
        if (seq !== this._seq) return;
        const domains = ["switch", "sensor", "select", "number", "binary_sensor"];
        const cands = (all || []).filter((e) =>
          e.device_id === deviceId && domains.includes(e.entity_id.split(".")[0]));
        const uids = {};
        await Promise.all(cands.map(async (e) => {
          try {
            const full = await this._hass.callWS({ type: "config/entity_registry/get", entity_id: e.entity_id });
            if (full && full.unique_id != null) uids[e.entity_id] = String(full.unique_id).toLowerCase();
          } catch (_) { /* per-entity failure: leave unresolved */ }
        }));
        if (seq !== this._seq) return;
        found = this._resolveFromRegistry(cands, uids);
      } catch (err) {
        wsFailed = true;
      }
    } else {
      wsFailed = true;
    }
    if (seq !== this._seq) return;
    if (wsFailed) found = this._resolveHeuristic(deviceId);
    // Battery: the device sensor whose device_class is "battery" (its unique_id
    // is the default-discovery "<ieee>-1-1" form, so device_class is the safest
    // client-side signal). Optional — no warning when absent.
    if (!found.battery) {
      const states = this._hass?.states || {};
      for (const [eid, ent] of Object.entries(this._hass?.entities || {})) {
        if (ent?.device_id !== deviceId || !eid.startsWith("sensor.")) continue;
        if (states[eid]?.attributes?.device_class === "battery") { found.battery = eid; break; }
      }
    }
    const unresolved = ENTITY_KEYS.filter(
      (k) => !found[k] && !OPTIONAL_KEYS.includes(k)
    );
    const t = (k) => _t(this._hass, k);
    let warn = "";
    if (wsFailed) warn += t("editorResolveFail");
    if (unresolved.length) warn += (warn ? "\n" : "") + t("editorUnresolved") + " " + unresolved.join(", ");
    this._warnText = warn;
    this._config = { ...this._config, device_id: deviceId, entities: found };
    this._fire();
    this._update();
  }

  _resolveFromRegistry(cands, uids) {
    const found = {};
    for (const [key, domain, suffix] of UID_RULES) {
      for (const c of cands) {
        if (!c.entity_id.startsWith(domain + ".")) continue;
        const uid = uids[c.entity_id];
        if (uid && uid.endsWith(suffix)) { found[key] = c.entity_id; break; }
      }
    }
    for (const [key, domain, prefix] of UID_PREFIX_RULES) {
      for (const c of cands) {
        if (!c.entity_id.startsWith(domain + ".")) continue;
        const uid = uids[c.entity_id];
        if (uid && uid.startsWith(prefix)) { found[key] = c.entity_id; break; }
      }
    }
    // Switch unique_ids are "<ieee>-<ep>" or "<ieee>-<ep>-6" depending on the
    // endpoint's Zigbee device_type — accept both. Restricted to the switch
    // domain, so the battery sensor's "…-1-1" can never match.
    for (const c of cands) {
      if (!c.entity_id.startsWith("switch.")) continue;
      const uid = uids[c.entity_id] || "";
      if (!found.switch_1 && /-1(-6)?$/.test(uid)) { found.switch_1 = c.entity_id; continue; }
      if (!found.switch_2 && /-2(-6)?$/.test(uid)) { found.switch_2 = c.entity_id; }
    }
    return found;
  }

  _resolveHeuristic(deviceId) {
    const found = {};
    const switches = [];
    for (const [eid, ent] of Object.entries(this._hass?.entities || {})) {
      if (!ent || ent.device_id !== deviceId) continue;
      if (eid.startsWith("switch.")) { switches.push(eid); continue; }
      for (const [key, domPrefix, suffix] of EID_RULES) {
        if (found[key]) continue;
        if (eid.startsWith(domPrefix) && eid.endsWith(suffix)) { found[key] = eid; break; }
      }
      if (!found.session_target && eid.startsWith("sensor.") && eid.endsWith("_session_target")) {
        found.session_target = eid;
      }
    }
    switches.sort();
    if (switches[0]) found.switch_1 = switches[0];
    if (switches[1]) found.switch_2 = switches[1];
    return found;
  }

  _update() {
    if (!this._hass) return;
    if (!this._domBuilt) this._buildDom();
    const devices = this._compatDevices();
    const cur = this._config.device_id || "";
    const ae = this.shadowRoot.activeElement;
    const hasDev = devices.length > 0;

    this._el.devWrap.hidden = !hasDev;
    this._el.devEmpty.hidden = hasDev;

    if (hasDev) {
      // Only rebuild <option>s when the device set actually changed — avoids
      // clobbering an open dropdown on every HA state push.
      const key = devices.map((d) => d.id + ":" + d.label).join("|");
      if (key !== this._lastDevKey) {
        const t = (k) => _t(this._hass, k);
        // Device names are user text: escape them (a device renamed to
        // "Valvola <giardino>" would otherwise truncate the option markup).
        const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
          .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
        const opts = [`<option value="">${t("editorSelect")}</option>`];
        for (const d of devices) opts.push(`<option value="${esc(d.id)}">${esc(d.label)}</option>`);
        this._el.dev.innerHTML = opts.join("");
        this._lastDevKey = key;
      }
      if (ae !== this._el.dev && this._el.dev.value !== cur) this._el.dev.value = cur;
    }

    // Reopening the editor on a config with device_id but an absent/partial
    // entities map (YAML-authored, or entities renamed since first resolution)
    // must re-resolve without forcing the user to deselect and reselect the
    // device: the dropdown already equals device_id, so `change` never fires.
    // One attempt per device_id; _seq guards against overlapping resolutions.
    if (cur && this._resolvedFor !== cur) {
      const ents = this._config.entities || {};
      if (ENTITY_KEYS.some((k) => !ents[k])) {
        this._resolvedFor = cur;
        this._resolveDevice(cur).catch((err) => {
          console.warn("[sonoff-valve-card] device resolution failed", err);
        });
      } else {
        this._resolvedFor = cur;
      }
    }

    this._el.warn.hidden = !this._warnText;
    if (this._warnText && this._el.warn.textContent !== this._warnText) {
      this._el.warn.textContent = this._warnText;
    }

    // Never overwrite an input the user is currently editing.
    const nm = this._config.name || "";
    const n1 = this._config.name_1 || "";
    const n2 = this._config.name_2 || "";
    if (ae !== this._el.nm && this._el.nm.value !== nm) this._el.nm.value = nm;
    if (ae !== this._el.n1 && this._el.n1.value !== n1) this._el.n1.value = n1;
    if (ae !== this._el.n2 && this._el.n2.value !== n2) this._el.n2.value = n2;
  }

  _fire() { this.dispatchEvent(new CustomEvent("config-changed", { detail: { config: this._config }, bubbles: true, composed: true })); }
}

// ── Main Card ──
class SonoffValveCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null; this._config = null; this._entities = null;
    this._deviceId = ""; this._configName = ""; this._name1 = ""; this._name2 = "";
    this._mode = null;               // open panel: "litri" | "tempo" | null
    // Render tick: while any channel is open we re-render once a second so the
    // device-derived countdown/progress bar advances. Values come from device
    // truth (session_* sensors) on every render, so the bar never drifts and
    // self-corrects on each hass update / browser refresh.
    this._tick = null;
    // Pending overlay: { action: "start"|"stop", channel: "1"|"2", failed }.
    // Shown between pressing a button and the target switch confirming the new
    // state; a 10s watchdog surfaces failure. Guards double-tap races.
    this._pending = null; this._pendingTimer = null; this._pendingHold = null;
    this._inputLitri = 10; this._inputMin = 5;
    this._userEditedLitri = false; this._userEditedTempo = false;
    // Run-history state: panel open flag, render key (skip innerHTML churn on
    // every hass push), throttle stamp for the lazy entity resolution.
    this._histOpen = false; this._histKey = ""; this._histResolveAt = 0;
    this._histWsTried = false;
    this._cfgProblems = []; this._cfgFatal = true;
    this._domCreated = false;
    this._el = {};
  }

  static getConfigElement() { return document.createElement("sonoff-valve-card-editor"); }
  static getStubConfig() { return { device_id: "", name: "", entities: {} }; }

  setConfig(config) {
    if (!config || typeof config !== "object") throw new Error("Invalid configuration");
    this._config = config;
    this._entities = config.entities || {};
    this._deviceId = config.device_id || "";
    this._configName = config.name || "";
    this._name1 = config.name_1 || "";
    this._name2 = config.name_2 || "";
    // Validate the entities map (raw YAML configs without the editor are fine
    // as long as they provide the map). Problems are surfaced in an in-card
    // banner instead of throwing, so the preview and partial configs degrade
    // gracefully; missing switches disable the action panel entirely.
    const probs = [];
    if (!this._deviceId) probs.push("device_id");
    for (const k of RUNTIME_KEYS) if (!this._entities[k]) probs.push("entities." + k);
    this._cfgProblems = probs;
    this._cfgFatal = !this._entities.switch_1 || !this._entities.switch_2;
    this._domCreated = false;
    if (this._hass) this._render();
  }

  getCardSize() { return 5; }

  set hass(hass) {
    const old = this._hass; this._hass = hass;
    if (!this._config) return;
    const wasOn = old ? this._anyOn(old) : false;
    const isOn = this._anyOn(hass);
    // Clear the pending overlay as soon as the target switch confirms.
    if (this._pending) {
      const sw = this._pending.channel === "2" ? this._entities.switch_2 : this._entities.switch_1;
      const st = hass?.states?.[sw]?.state;
      if (this._pending.action === "start" && st === "on") this._clearPending();
      else if (this._pending.action === "stop" && st !== undefined && st !== "on") this._clearPending();
    }
    if (isOn) {
      // A channel is open (by us, by an automation, or already running on
      // (re)load). The switches are the single source of truth. Reflect the
      // device's run mode in the open panel only on the rising edge / first
      // load, so the user can still close a panel mid-run.
      if (!wasOn || old === null) this._reflectRunMode();
      this._startTick();
    } else {
      if (wasOn) { this._userEditedLitri = false; this._userEditedTempo = false; }
      this._stopTick();
      // Only sync inputs from the device when idle and not mid-start, so a
      // transient unknown/stale value can't disturb the inputs.
      if (!this._pending) this._syncFromEntities();
    }
    this._lazyResolveHistory();
    this._render();
  }

  // Configs saved with card/integration 0.4.0 have no history entities. Fill
  // them in at runtime so the history list appears without the user reopening
  // the editor. Two passes: the cheap entity_id-suffix scan first, then (if
  // that finds nothing, e.g. renamed entity_ids) a one-shot registry lookup
  // over WebSocket by unique_id prefix — the same authority the editor uses.
  // Retried at most once a minute until both channels resolve (the sensors
  // may be created only after the integration updates).
  _lazyResolveHistory() {
    if (!this._deviceId || !this._entities) return;
    if (this._entities.history_1 && this._entities.history_2) return;
    const now = Date.now();
    if (now - this._histResolveAt < 60000) return;
    this._histResolveAt = now;
    const ents = { ...this._entities };
    let changed = false;
    for (const [eid, ent] of Object.entries(this._hass?.entities || {})) {
      if (ent?.device_id !== this._deviceId || !eid.startsWith("sensor.")) continue;
      if (!ents.history_1 && eid.endsWith("_irrigation_history_ch1")) {
        ents.history_1 = eid; changed = true;
      } else if (!ents.history_2 && eid.endsWith("_irrigation_history_ch2")) {
        ents.history_2 = eid; changed = true;
      }
    }
    if (changed) this._entities = ents;
    else if (!this._entities.history_1 && !this._entities.history_2) {
      this._lazyResolveHistoryWS().catch(() => { /* stay on the suffix path */ });
    }
  }

  async _lazyResolveHistoryWS() {
    if (this._histWsTried || typeof this._hass?.callWS !== "function") return;
    this._histWsTried = true;
    const all = await this._hass.callWS({ type: "config/entity_registry/list" });
    const cands = (all || []).filter((e) =>
      e.device_id === this._deviceId && e.entity_id.startsWith("sensor."));
    const ents = { ...this._entities };
    let changed = false;
    for (const c of cands) {
      if (ents.history_1 && ents.history_2) break;
      let uid = "";
      try {
        const full = await this._hass.callWS({ type: "config/entity_registry/get", entity_id: c.entity_id });
        uid = String(full?.unique_id || "").toLowerCase();
      } catch (_) { continue; }
      for (const [key, , prefix] of UID_PREFIX_RULES) {
        if (!ents[key] && uid.startsWith(prefix)) { ents[key] = c.entity_id; changed = true; break; }
      }
    }
    if (changed) { this._entities = ents; this._render(); }
  }

  // The duration/volume number entities are separate device attrs (minutes /
  // liters), so each input syncs from its own entity — no cross-contamination.
  _syncFromEntities() {
    const v = this._nv(this._entities.volume);
    if (!this._userEditedLitri && v > 0) this._inputLitri = Math.max(1, Math.min(10000, Math.round(v)));
    const m = this._nv(this._entities.duration);
    if (!this._userEditedTempo && m > 0) this._inputMin = Math.max(1, Math.min(719, Math.round(m)));
  }

  // ── State helpers ──
  _sv(eid) { if (!eid || !this._hass?.states[eid]) return "unavailable"; return this._hass.states[eid].state; }
  _nv(eid) { const v = parseFloat(this._sv(eid)); return isNaN(v) ? 0 : v; }
  _chSwitch(ch) { return ch === "2" ? this._entities.switch_2 : this._entities.switch_1; }
  _chOn(ch, h) { return (h || this._hass)?.states?.[this._chSwitch(ch)]?.state === "on"; }
  _anyOn(h) { return this._chOn("1", h) || this._chOn("2", h); }
  // Trust HA's authoritative availability signal ONLY. ZHA flips the switch to
  // "unavailable" when the device stops responding; we deliberately do NOT add
  // a last_updated staleness check (ZHA doesn't refresh last_updated for
  // unchanged values, so a quiet healthy valve would false-positive).
  _chOffline(ch) {
    const s = this._hass?.states[this._chSwitch(ch)];
    if (!s) return true;
    return s.state === "unavailable" || s.state === "unknown" || s.state === "none";
  }
  _isOffline() { return this._chOffline("1") && this._chOffline("2"); }
  _chName(ch) {
    if (ch === "2") return this._name2 || _t(this._hass, "line2");
    return this._name1 || _t(this._hass, "line1");
  }
  _getName() {
    if (this._configName) return this._configName;
    const d = this._hass?.devices?.[this._deviceId];
    return (d && (d.name_by_user || d.name)) || _t(this._hass, "defaultName");
  }
  _integrationAvailable() {
    return !!(this._hass?.services?.zha_sonoff_quirks?.irrigation_by_liters);
  }
  async _svc(d, s, data) { await this._hass.callService(d, s, data); }

  // ── DOM helpers ──
  _txt(el, v) { if (el && el.textContent !== v) el.textContent = v; }
  _setInput(el, v) { const s = String(v); if (el && el.value !== s) el.value = s; }
  _cls(el, cls, on) { if (el) el.classList.toggle(cls, !!on); }
  _isEditingGroup(group) {
    const ae = this.shadowRoot.activeElement;
    if (!ae || ae.tagName !== "INPUT") return false;
    switch (group) {
      case "litri": return ae.id === "vl";
      case "tempo": return ae.id === "tmin";
      default: return false;
    }
  }

  _selectMode(m) {
    this._mode = this._mode === m ? null : m;
    if (!this._mode) { this._userEditedLitri = false; this._userEditedTempo = false; }
    this._render();
  }

  // Which kind of run is active, from the device's global irrigation mode
  // (select entity; options are "duration", "capacity", "duration with interval").
  _runKind() {
    const m = this._sv(this._entities.mode);
    if (m === "capacity") return "litri";
    if (typeof m === "string" && m.startsWith("duration")) return "tempo";
    return null;
  }
  _reflectRunMode() {
    if (this._mode !== null) return;   // don't yank a panel the user opened
    const kind = this._runKind();
    if (kind) this._mode = kind;
  }

  // ── Pending (start/stop) overlay lifecycle ──
  _beginPending(action, channel) {
    this._pending = { action, channel, failed: false };
    if (this._pendingTimer) clearTimeout(this._pendingTimer);
    if (this._pendingHold) { clearTimeout(this._pendingHold); this._pendingHold = null; }
    this._pendingTimer = setTimeout(() => this._pendingTimedOut(), 10000);
    this._render();
  }
  _pendingTimedOut() { this._pendingTimer = null; this._pendingFail(); }
  _pendingFail() {
    if (this._pendingTimer) { clearTimeout(this._pendingTimer); this._pendingTimer = null; }
    if (!this._pending) return;
    // Switch never confirmed within 10s — surface "failed" briefly, then clear.
    this._pending.failed = true;
    this._render();
    this._pendingHold = setTimeout(() => {
      this._pending = null; this._pendingHold = null; this._render();
    }, 1800);
  }
  _clearPending() {
    if (this._pendingTimer) { clearTimeout(this._pendingTimer); this._pendingTimer = null; }
    if (this._pendingHold) { clearTimeout(this._pendingHold); this._pendingHold = null; }
    this._pending = null;
  }

  // ── Actions ──
  // The go buttons are switch-driven (single source of truth): channel on →
  // stop it, off → start it with the panel's kind. Both channels can run at
  // once; each button reflects only its own switch.
  async _onGo(kind, ch) {
    if (this._pending) return;
    if (this._isOffline() || this._chOffline(ch)) return;
    if (this._chOn(ch)) {
      this._beginPending("stop", ch);
      try {
        await this._svc("switch", "turn_off", { entity_id: this._chSwitch(ch) });
      } catch (err) {
        console.error("[sonoff-valve-card] turn_off failed", err);
        this._pendingFail();
      }
      return;
    }
    if (!this._integrationAvailable()) { console.warn("[sonoff-valve-card] zha_sonoff_quirks services not available"); return; }
    if (!this._deviceId) return;
    let service, data;
    if (kind === "litri") {
      const v = Math.round(this._inputLitri);
      if (!(v >= 1)) return;
      service = "irrigation_by_liters";
      data = { device_id: this._deviceId, channel: ch, liters: Math.min(10000, v) };
    } else {
      const m = Math.round(this._inputMin);
      if (!(m >= 1)) return;
      service = "irrigation_by_minutes";
      data = { device_id: this._deviceId, channel: ch, minutes: Math.min(719, m) };
    }
    this._beginPending("start", ch);
    try {
      await this._svc("zha_sonoff_quirks", service, data);
    } catch (err) {
      console.error("[sonoff-valve-card] " + service + " failed", err);
      this._pendingFail();
    }
  }

  // ── Render tick (device-truth progress) ──
  _startTick() { if (this._tick) return; this._tick = setInterval(() => this._render(), 1000); }
  _stopTick() { if (this._tick) { clearInterval(this._tick); this._tick = null; } }

  // The session sensors deliberately persist the PREVIOUS run's totals (that
  // is what feeds the "last session" row), and the device's progress feed only
  // starts ~6s after the valve opens. A sensor whose last_changed predates the
  // most recent switch-on is therefore stale for progress purposes: consuming
  // it would show the old run's volume/elapsed at 100% for the first seconds
  // of a new run. 2s tolerance covers HA clock jitter between state writes.
  _sessionFresh(st) {
    let newestOn = 0;
    for (const sw of [this._entities.switch_1, this._entities.switch_2]) {
      const s = this._hass?.states[sw];
      if (s && s.state === "on") {
        const t = new Date(s.last_changed).getTime();
        if (!isNaN(t) && t > newestOn) newestOn = t;
      }
    }
    if (!newestOn) return false;
    const lc = new Date(st.last_changed).getTime();
    return !isNaN(lc) && lc >= newestOn - 2000;
  }

  // Progress from device truth while any channel is open. The quirk's session
  // feed reports every ~6s; the duration path interpolates elapsed between
  // reports using the sensor's last_changed, clamped to the target.
  _progress() {
    const none = { tp: null, lp: null };
    if (!this._anyOn()) return none;
    const kind = this._runKind();
    if (kind === "litri") {
      const st = this._hass?.states[this._entities.session_volume];
      if (!st || BAD_STATES.includes(st.state)) return none;
      if (!this._sessionFresh(st)) return none;   // pre-first-report window
      const delivered = parseFloat(st.state);
      if (isNaN(delivered)) return none;
      let target = this._nv(this._entities.volume);
      if (!(target > 0)) target = this._inputLitri;
      if (!(target > 0)) return none;
      const pct = Math.max(0, Math.min(100, Math.round((delivered / target) * 100)));
      return { tp: null, lp: { delivered: Math.max(0, delivered), target, pct } };
    }
    if (kind === "tempo") {
      const target = this._nv(this._entities.session_target);   // seconds
      if (!(target > 0)) return none;
      const st = this._hass?.states[this._entities.session_elapsed];
      if (!st || BAD_STATES.includes(st.state)) return none;
      if (!this._sessionFresh(st)) return none;   // pre-first-report window
      const base = parseFloat(st.state);                        // seconds
      if (isNaN(base)) return none;
      const lc = new Date(st.last_changed).getTime();
      let elapsed = base;
      if (!isNaN(lc)) elapsed += Math.max(0, (Date.now() - lc) / 1000);
      elapsed = Math.max(0, Math.min(target, elapsed));
      const remaining = Math.max(0, Math.round(target - elapsed));
      const pct = Math.max(0, Math.min(100, Math.round((remaining / target) * 100)));
      return { tp: { remaining, total: target, pct }, lp: null };
    }
    return none;
  }

  // Merged run history from the integration's per-channel history sensors
  // (each carries a `runs` attribute, most recent first). The device itself
  // has no run log — this is the server-side record kept by zha_sonoff_quirks.
  _historyRuns() {
    const out = [];
    for (const [key, ch] of [["history_1", "1"], ["history_2", "2"]]) {
      const st = this._hass?.states[this._entities[key]];
      const runs = st?.attributes?.runs;
      if (!Array.isArray(runs)) continue;
      for (const r of runs) {
        if (r && r.end) out.push({ ...r, _ch: ch });
      }
    }
    out.sort((a, b) => new Date(b.end) - new Date(a.end));
    return out.slice(0, 8);
  }

  _histRowHtml(r) {
    const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    const loc = _numLocale(this._hass);
    const d = new Date(r.end);
    const when = isNaN(d.getTime()) ? "—"
      : d.toLocaleDateString(loc, { day: "2-digit", month: "2-digit" })
        + " " + d.toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit" });
    const liters = (r.liters === null || r.liters === undefined) ? "—"
      : `${this._fmtVolShortNum(r.liters)} L`;
    return `<div class="hr-row">`
      + `<span class="hr-when">${esc(when)}</span>`
      + `<span class="hr-ch">${esc(this._chName(r._ch))}</span>`
      + `<span class="hr-dur">${esc(this._fd(r.duration_s || 0))}</span>`
      + `<span class="hr-l">${esc(liters)}</span>`
      + `</div>`;
  }

  // Idle summary: the session sensors keep the last run's totals (device
  // truth), so when nothing is running we show them as "last session".
  _lastSession() {
    const vs = this._sv(this._entities.session_volume);
    const ds = this._sv(this._entities.session_elapsed);
    const v = BAD_STATES.includes(vs) ? NaN : parseFloat(vs);
    const d = BAD_STATES.includes(ds) ? NaN : parseFloat(ds);
    const hasV = isFinite(v) && v > 0;
    const hasD = isFinite(d) && d > 0;
    if (!hasV && !hasD) return null;
    return { vol: hasV ? v : 0, dur: hasD ? d : 0 };
  }

  // ── Formatters ──
  _fd(s) { s = Math.round(s); if (s < 60) return `${s} s`; const m = Math.floor(s / 60), r = s % 60; if (m < 60) return r > 0 ? `${m}m ${r}s` : `${m} min`; return `${Math.floor(m / 60)}h ${m % 60}m`; }
  _fmtVolShortNum(v) {
    const n = Number(v) || 0;
    return n.toLocaleString(_numLocale(this._hass), { minimumFractionDigits: 0, maximumFractionDigits: 1 });
  }
  _p2(n) { return String(Math.round(n)).padStart(2, "0"); }
  // h:mm:ss above one hour: duration runs go up to 719 minutes, and a raw
  // "719:00" reads as broken formatting rather than minutes:seconds.
  _mmss(sec) {
    sec = Math.max(0, Math.round(sec));
    const h = Math.floor(sec / 3600);
    const rest = `${this._p2(Math.floor((sec % 3600) / 60))}:${this._p2(sec % 60)}`;
    return h > 0 ? `${h}:${rest}` : rest;
  }

  // ── Render dispatcher ──
  _render() {
    if (!this._hass || !this._config) return;
    if (!this._domCreated) {
      this._createDOM();
      this._domCreated = true;
    } else {
      this._update();
    }
  }

  // ── Initial DOM creation (runs once; values are patched by _update) ──
  _createDOM() {
    const t = (k) => _t(this._hass, k);
    const hasBatt = !!this._entities.battery;
    this.shadowRoot.innerHTML = `
<style>
:host{--accent:#2ecc8b;--accent-dim:rgba(46,204,139,.12);--accent-hover:#27b67a;--blue:#4a90d9;--blue-dim:rgba(74,144,217,.12);--blue-text:#6aabf0;--danger:#e25555;--tm:var(--primary-text-color,#e8e8f0);--ts:var(--secondary-text-color,#8b8da5);--th:var(--disabled-text-color,#5c5e76);--bd:var(--divider-color,rgba(255,255,255,.06))}
ha-card{overflow:hidden}
.ch{display:flex;align-items:center;justify-content:space-between;padding:12px 16px 6px}
.hl{display:flex;align-items:center;gap:10px}
.di{width:32px;height:32px;border-radius:8px;background:var(--accent-dim);display:flex;align-items:center;justify-content:center}
.tt{font-size:15px;font-weight:600;color:var(--tm)}
.hr{display:flex;align-items:center;gap:10px}
.bt{display:flex;align-items:center;gap:4px;font-size:11px;color:var(--th);font-family:monospace}
.bs{width:18px;height:10px;border:1.2px solid var(--th);border-radius:2px;position:relative;overflow:hidden}
.bf{position:absolute;inset:1px;background:var(--accent);border-radius:1px}
.bp{width:2px;height:5px;background:var(--th);border-radius:0 1px 1px 0;margin-left:-1px}
.badge{font-size:11px;font-weight:500;padding:3px 10px;border-radius:20px;transition:all .3s}
.badge.off{background:var(--bd);color:var(--th)}
.badge.active{background:var(--accent-dim);color:var(--accent)}
.badge.offline{background:rgba(226,85,85,.15);color:var(--danger);font-weight:600}
.cb{padding:6px 16px 14px}
.sc{margin-bottom:16px}.sc:last-child{margin-bottom:0}
.dv{height:1px;background:var(--bd);margin:0 0 16px;display:none}
.dv.vi{display:block}
.ar{display:flex;gap:8px}
.ab{flex:1;display:flex;align-items:center;justify-content:center;gap:7px;padding:11px 12px;border-radius:8px;border:1px solid var(--bd);background:transparent;cursor:pointer;font-size:13px;font-weight:500;color:var(--ts);font-family:inherit;transition:all .15s}
.ab:hover{background:var(--bd);color:var(--tm)}.ab.ac{border-color:rgba(74,144,217,.4);background:var(--blue-dim);color:var(--blue-text)}
.ip{display:grid;grid-template-rows:0fr;transition:grid-template-rows .25s ease,margin-top .2s;margin-top:0}.ip>*{overflow:hidden}.ip.vi{grid-template-rows:1fr;margin-top:8px}
.ir{display:flex;gap:8px;align-items:center;padding-top:2px}
.nw{flex:1;display:flex;align-items:center;border:1px solid var(--bd);border-radius:8px;overflow:hidden;transition:border-color .15s}.nw:focus-within{border-color:rgba(74,144,217,.5)}
.ni{flex:1;min-width:0;padding:10px 12px;border:none;background:transparent;font-size:20px;font-weight:500;color:var(--tm);text-align:center;outline:none;font-family:monospace}
.ut{padding:0 14px;font-size:13px;font-weight:600;color:var(--th);background:var(--bd);align-self:stretch;display:flex;align-items:center;border-left:1px solid var(--bd)}
.fh{font-size:10px;color:var(--th);text-align:center;min-width:50px;margin-top:6px}
.gb{width:44px;height:44px;border-radius:50%;flex-shrink:0;border:none;background:var(--accent);cursor:pointer;display:flex;align-items:center;justify-content:center;transition:all .15s;box-shadow:0 2px 12px rgba(46,204,139,.25)}.gb:hover{background:var(--accent-hover)}.gb:active{transform:scale(.93)}
@keyframes pg{0%,100%{box-shadow:0 0 0 0 rgba(226,85,85,.3)}50%{box-shadow:0 0 0 6px rgba(226,85,85,0)}}
.gb.rn{animation:pg 1.2s infinite;background:var(--danger);box-shadow:0 2px 12px rgba(226,85,85,.3)}
.gb.dis{opacity:.35;pointer-events:none;box-shadow:none;animation:none}
.chc{display:flex;flex-direction:column;align-items:center;gap:3px;flex-shrink:0}
.chl{font-size:9px;color:var(--th);max-width:56px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:center}
.pw{height:3px;border-radius:2px;background:var(--bd);margin-top:6px;overflow:hidden;opacity:0;transition:opacity .2s}.pw.vi{opacity:1}
.pb{height:100%;border-radius:2px;background:var(--accent);transition:width .3s linear}
.hist-compact{display:flex;align-items:center;gap:8px;padding:2px 0;min-height:24px}
.hist-compact-label{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--th);flex-shrink:0}
.hist-when{flex:1;min-width:0;font-size:12px;color:var(--tm);font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.hist-when.none{color:var(--ts);font-weight:400;font-style:italic}
.hist-vol{font-size:12px;color:var(--tm);font-weight:500;white-space:nowrap;flex-shrink:0}
.hist-vol-label{color:var(--th);font-weight:400}
.hist-toggle{background:none;border:none;color:var(--th);cursor:pointer;padding:2px 4px;flex-shrink:0;display:none;line-height:0;transition:transform .2s}
.hist-toggle.open{transform:rotate(90deg)}
.hist-toggle svg{display:block}
.hist-list{margin-top:6px;border-top:1px solid var(--bd);padding-top:6px;display:none;flex-direction:column;gap:4px}
.hist-list.vi{display:flex}
.hr-row{display:flex;align-items:center;gap:10px;font-size:11px;color:var(--ts);font-family:monospace}
.hr-when{color:var(--tm);flex-shrink:0}
.hr-ch{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:90px}
.hr-dur{margin-left:auto;flex-shrink:0}
.hr-l{color:var(--tm);min-width:48px;text-align:right;flex-shrink:0}
input[type=number]::-webkit-inner-spin-button,input[type=number]::-webkit-outer-spin-button{-webkit-appearance:none;margin:0}
input[type=number]{-moz-appearance:textfield}
.intg-missing{background:rgba(226,85,85,.12);color:var(--danger);border:1px solid rgba(226,85,85,.3);border-radius:8px;padding:10px 12px;font-size:12px;margin-bottom:12px;text-align:center;display:none}
.intg-missing.vi{display:block}
#action-sec{position:relative}
.start-ov{position:absolute;inset:0;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.55);backdrop-filter:blur(1.5px);border-radius:10px;z-index:5;font-size:14px;font-weight:600;color:#fff;letter-spacing:.3px}
.start-ov.vi{display:flex}
.start-ov.failed{color:var(--danger)}
.off-banner{background:rgba(226,85,85,.12);color:var(--danger);border:1px solid rgba(226,85,85,.3);border-radius:8px;padding:10px 12px;font-size:12px;margin-bottom:12px;display:none;align-items:center;gap:8px}
.off-banner.vi{display:flex}
.off-banner svg{flex-shrink:0}
/* Hide action panel entirely when offline: the buttons can't do anything. */
.sc.disabled{display:none}
</style>
<ha-card>
  <div class="ch">
    <div class="hl">
      <div class="di"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" stroke-width="2.2" stroke-linecap="round"><path d="M12 2C12 2 5 9 5 14a7 7 0 0014 0c0-5-7-12-7-12z"/></svg></div>
      <span class="tt"></span>
    </div>
    <div class="hr">
      <span class="badge off"></span>
      ${hasBatt ? `<div class="bt" id="bt-wrap" style="display:none"><div class="bs"><div class="bf" style="width:0%"></div></div><div class="bp"></div><span class="batt-pct"></span></div>` : ""}
    </div>
  </div>
  <div class="cb">
    <div class="intg-missing" id="cfg-banner"></div>
    <div class="intg-missing" id="intg-missing">${t("integrationMissing")}</div>
    <div class="off-banner" id="off-banner">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 8.82a15 15 0 0 1 20 0"/><path d="M5 12.86a10 10 0 0 1 14 0"/><path d="M8.5 16.43a5 5 0 0 1 7 0"/><line x1="2" y1="2" x2="22" y2="22"/></svg>
      <span>${t("offlineMsg")}</span>
    </div>
    <div class="sc" id="action-sec">
      <div class="ar">
        <button class="ab" id="bl"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M12 2C12 2 5 9 5 14a7 7 0 0014 0c0-5-7-12-7-12z"/></svg>${t("liters")}</button>
        <button class="ab" id="bt"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>${t("time")}</button>
      </div>
      <div class="ip" id="ip-litri"><div>
        <div class="ir">
          <div class="nw"><input type="number" inputmode="numeric" pattern="[0-9]*" class="ni" id="vl" min="1" max="10000"><div class="ut">L</div></div>
          <div class="chc"><button class="gb" id="gl1">${ICON_PLAY}</button><span class="chl" id="chl-l1"></span></div>
          <div class="chc"><button class="gb" id="gl2">${ICON_PLAY}</button><span class="chl" id="chl-l2"></span></div>
        </div>
        <div class="fh" id="litri-fh" style="display:none"></div>
        <div class="pw" id="litri-pw"><div class="pb" id="litri-bar" style="width:0%"></div></div>
      </div></div>
      <div class="ip" id="ip-tempo"><div>
        <div class="ir">
          <div class="nw"><input type="number" inputmode="numeric" pattern="[0-9]*" class="ni" id="tmin" min="1" max="719"><div class="ut">min</div></div>
          <div class="chc"><button class="gb" id="gt1">${ICON_PLAY}</button><span class="chl" id="chl-t1"></span></div>
          <div class="chc"><button class="gb" id="gt2">${ICON_PLAY}</button><span class="chl" id="chl-t2"></span></div>
        </div>
        <div class="fh" id="tempo-fh" style="display:none"></div>
        <div class="pw" id="tempo-pw"><div class="pb" id="tempo-bar" style="width:0%"></div></div>
      </div></div>
      <div class="start-ov" id="start-ov"><span id="start-ov-txt"></span></div>
    </div>
    <div class="dv" id="divider"></div>
    <div class="sc" style="margin-bottom:0">
      <div class="hist-compact" id="last-row">
        <span class="hist-compact-label">${t("lastSession")}</span>
        <span class="hist-when" id="ls-when"></span>
        <span class="hist-vol" id="ls-vol" style="display:none"><span class="hist-vol-label">${t("liters")}:</span> <span id="ls-vol-val"></span></span>
        <button class="hist-toggle" id="hist-toggle" title="${t("history")}"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6"/></svg></button>
      </div>
      <div class="hist-list" id="hist-list"></div>
    </div>
  </div>
</ha-card>`;
    this._cacheEls();
    this._bindEvents();
    this._update();
  }

  _cacheEls() {
    const r = this.shadowRoot;
    const $ = (id) => r.getElementById(id);
    const q = (sel) => r.querySelector(sel);
    this._el = {
      tt: q(".tt"), badge: q(".badge"),
      battWrap: $("bt-wrap"), bf: q(".bf"), battPct: q(".batt-pct"),
      cfgBanner: $("cfg-banner"), intgMissing: $("intg-missing"), offBanner: $("off-banner"),
      actionSec: $("action-sec"),
      bl: $("bl"), bt: $("bt"),
      ipLitri: $("ip-litri"), ipTempo: $("ip-tempo"),
      vl: $("vl"), tmin: $("tmin"),
      gl1: $("gl1"), gl2: $("gl2"), gt1: $("gt1"), gt2: $("gt2"),
      chlL1: $("chl-l1"), chlL2: $("chl-l2"), chlT1: $("chl-t1"), chlT2: $("chl-t2"),
      litriFh: $("litri-fh"), litriPw: $("litri-pw"), litriBar: $("litri-bar"),
      tempoFh: $("tempo-fh"), tempoPw: $("tempo-pw"), tempoBar: $("tempo-bar"),
      startOv: $("start-ov"), startOvTxt: $("start-ov-txt"),
      divider: $("divider"),
      lastRow: $("last-row"), lsWhen: $("ls-when"), lsVol: $("ls-vol"), lsVolVal: $("ls-vol-val"),
      histToggle: $("hist-toggle"), histList: $("hist-list"),
    };
  }

  _bindEvents() {
    const el = this._el;
    el.bl?.addEventListener("click", () => this._selectMode("litri"));
    el.bt?.addEventListener("click", () => this._selectMode("tempo"));
    el.gl1?.addEventListener("click", () => this._onGo("litri", "1"));
    el.gl2?.addEventListener("click", () => this._onGo("litri", "2"));
    el.gt1?.addEventListener("click", () => this._onGo("tempo", "1"));
    el.gt2?.addEventListener("click", () => this._onGo("tempo", "2"));
    el.vl?.addEventListener("change", (ev) => {
      this._inputLitri = Math.max(1, Math.min(10000, parseInt(ev.target.value) || 1));
      this._userEditedLitri = true;
    });
    el.tmin?.addEventListener("change", (ev) => {
      this._inputMin = Math.max(1, Math.min(719, parseInt(ev.target.value) || 1));
      this._userEditedTempo = true;
    });
    el.histToggle?.addEventListener("click", () => {
      this._histOpen = !this._histOpen;
      this._render();
    });
  }

  // ── Selective DOM update (runs on every subsequent hass update / tick) ──
  _update() {
    const t = (k) => _t(this._hass, k);
    const el = this._el;
    const fatal = this._cfgFatal;
    const isOn = !fatal && this._anyOn();
    const offline = !fatal && this._isOffline();

    // ── Header ──
    this._txt(el.tt, this._getName());
    if (el.battWrap) {
      const showBatt = !offline && !fatal;
      el.battWrap.style.display = showBatt ? "flex" : "none";
      if (showBatt) {
        const batt = this._nv(this._entities.battery);
        if (el.bf) el.bf.style.width = Math.min(100, batt) + "%";
        this._txt(el.battPct, Math.round(batt) + "%");
      }
    }
    let bTxt, bCls;
    if (offline) { bTxt = t("offline"); bCls = "offline"; }
    else if (isOn) { bTxt = t("irrigating"); bCls = "active"; }
    else { bTxt = t("off"); bCls = "off"; }
    if (el.badge) { this._txt(el.badge, bTxt); el.badge.className = "badge " + bCls; }

    // ── Banners ──
    const cfgTxt = this._cfgProblems.length
      ? t("configError") + " — " + this._cfgProblems.join(", ")
      : "";
    this._cls(el.cfgBanner, "vi", !!cfgTxt);
    if (cfgTxt) this._txt(el.cfgBanner, cfgTxt);
    this._cls(el.intgMissing, "vi", !fatal && !this._integrationAvailable());
    this._cls(el.offBanner, "vi", offline);

    // ── Action panel gating ──
    this._cls(el.actionSec, "disabled", offline || fatal);

    // ── Mode buttons / panels ──
    this._cls(el.bl, "ac", this._mode === "litri");
    this._cls(el.bt, "ac", this._mode === "tempo");
    this._cls(el.ipLitri, "vi", this._mode === "litri");
    this._cls(el.ipTempo, "vi", this._mode === "tempo");
    this._cls(el.divider, "vi", this._mode !== null);

    // ── Inputs (never overwrite while focused) ──
    if (!this._isEditingGroup("litri")) this._setInput(el.vl, Math.round(this._inputLitri));
    if (!this._isEditingGroup("tempo")) this._setInput(el.tmin, Math.round(this._inputMin));

    // ── Channel labels + go buttons (each reflects only its own switch) ──
    this._txt(el.chlL1, this._chName("1")); this._txt(el.chlT1, this._chName("1"));
    this._txt(el.chlL2, this._chName("2")); this._txt(el.chlT2, this._chName("2"));
    for (const [ch, btns] of [["1", [el.gl1, el.gt1]], ["2", [el.gl2, el.gt2]]]) {
      const on = this._chOn(ch);
      const dis = !offline && !fatal && this._chOffline(ch);
      for (const b of btns) {
        if (!b) continue;
        this._cls(b, "rn", on);
        this._cls(b, "dis", dis);
        const want = on ? "stop" : "play";
        if (b.dataset.icon !== want) { b.innerHTML = on ? ICON_STOP : ICON_PLAY; b.dataset.icon = want; }
      }
    }

    // ── Progress (device truth) ──
    const { tp, lp } = fatal ? { tp: null, lp: null } : this._progress();
    const litriActive = !!lp, tempoActive = !!tp;
    this._cls(el.litriPw, "vi", litriActive);
    if (el.litriBar) el.litriBar.style.width = (lp ? lp.pct : 0) + "%";
    if (el.litriFh) {
      el.litriFh.style.display = litriActive ? "block" : "none";
      if (lp) this._txt(el.litriFh, `${this._fmtVolShortNum(lp.delivered)} / ${this._fmtVolShortNum(lp.target)} L`);
    }
    this._cls(el.tempoPw, "vi", tempoActive);
    if (el.tempoBar) el.tempoBar.style.width = (tp ? tp.pct : 0) + "%";
    if (el.tempoFh) {
      el.tempoFh.style.display = tempoActive ? "block" : "none";
      if (tp) this._txt(el.tempoFh, `${this._mmss(tp.remaining)} ${t("remaining")}`);
    }

    // ── Pending overlay ("Avvio… (Linea N)" / "Arresto… (Linea N)") ──
    const p = this._pending;
    this._cls(el.startOv, "vi", !!p);
    this._cls(el.startOv, "failed", !!(p && p.failed));
    if (p && el.startOvTxt) {
      const base = p.action === "stop"
        ? (p.failed ? t("stopFailed") : t("stopping"))
        : (p.failed ? t("startFailed") : t("starting"));
      this._txt(el.startOvTxt, `${base} (${this._chName(p.channel)})`);
    }

    // ── Idle summary (last session, from the persistent session sensors) ──
    const ls = (fatal || isOn) ? null : this._lastSession();
    const showRow = !fatal && !isOn;
    if (el.lastRow) el.lastRow.style.display = showRow ? "flex" : "none";
    if (showRow) {
      if (ls) {
        this._txt(el.lsWhen, `${t("duration")}: ${this._fd(ls.dur)}`);
        this._cls(el.lsWhen, "none", false);
        if (el.lsVol) el.lsVol.style.display = "inline";
        this._txt(el.lsVolVal, this._fmtVolShortNum(ls.vol));
      } else {
        this._txt(el.lsWhen, ": " + t("none"));
        this._cls(el.lsWhen, "none", true);
        if (el.lsVol) el.lsVol.style.display = "none";
      }
    }

    // ── Run history (expandable list behind the chevron on the idle row) ──
    const runs = (fatal || isOn) ? [] : this._historyRuns();
    const hasHist = runs.length > 0;
    if (el.histToggle) el.histToggle.style.display = hasHist ? "block" : "none";
    const histOpen = hasHist && this._histOpen;
    this._cls(el.histToggle, "open", histOpen);
    this._cls(el.histList, "vi", histOpen);
    if (histOpen && el.histList) {
      // Rebuild the rows only when the underlying data actually changed —
      // innerHTML churn on every hass push would be wasted work.
      const key = runs.map((r) => r.end + r._ch).join("|");
      if (key !== this._histKey) {
        this._histKey = key;
        el.histList.innerHTML = runs.map((r) => this._histRowHtml(r)).join("");
      }
    }
  }

  disconnectedCallback() {
    this._stopTick();
    if (this._pendingTimer) { clearTimeout(this._pendingTimer); this._pendingTimer = null; }
    if (this._pendingHold) { clearTimeout(this._pendingHold); this._pendingHold = null; }
    // Also drop the pending overlay state itself: with the watchdog cleared, a
    // card that re-attaches (view switch, dashboard edit) would otherwise show
    // the click-intercepting "Avvio…/Arresto…" overlay forever if the switch
    // never confirmed. The next hass update repaints from device truth anyway.
    this._pending = null;
  }
}

if (!customElements.get("sonoff-valve-card-editor")) {
  customElements.define("sonoff-valve-card-editor", SonoffValveCardEditor);
}
if (!customElements.get("sonoff-valve-card")) {
  customElements.define("sonoff-valve-card", SonoffValveCard);
}

window.customCards = window.customCards || [];
// Localized picker name based on the stored HA language, with the English term
// alongside so searches in either language match.
(function () {
  if (window.customCards.some((c) => c.type === "sonoff-valve-card")) return;
  const raw = (function () {
    try { return localStorage.getItem("selectedLanguage"); } catch (_) { return null; }
  })() || navigator.language || "en";
  const lang = raw.replace(/^"|"$/g, "").split("-")[0];
  const pickerName = {
    it: "Valvola Sonoff (Irrigazione)",
    zh: "Sonoff 水阀（灌溉）",
    en: "Sonoff Valve (Irrigation)",
  }[lang] || "Sonoff Valve (Irrigation)";
  const pickerDesc = (I18N[lang] || I18N.en).cardDesc;
  window.customCards.push({ type: "sonoff-valve-card", name: pickerName, description: pickerDesc, preview: true });
})();
console.info("%c SONOFF-VALVE-CARD %c v0.5.1 ", "color:white;background:#2ecc8b;font-weight:bold;padding:2px 6px;border-radius:4px 0 0 4px;", "color:#2ecc8b;background:#1a1c2e;font-weight:bold;padding:2px 6px;border-radius:0 4px 4px 0;");
