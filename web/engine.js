/* EnergyOS Secure — in-browser engine.
 * JavaScript port of the Python package in /energyos (simulator, detection, correlation,
 * supply chain, workflow, audit, API router). Used when the dashboard is opened as a static
 * page with no backend (e.g. the hosted pitch link). Same rules, thresholds and permissions. */
(function () {
  "use strict";

  // ---------------------------------------------------------------- deterministic RNG
  function mulberry32(a) {
    return function () {
      a |= 0; a = (a + 0x6d2b79f5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function makeRng(seed) {
    const r = mulberry32(seed);
    return {
      random: r,
      uniform: (a, b) => a + (b - a) * r(),
      gauss: (mu, sd) => mu + sd * Math.sqrt(-2 * Math.log(r() || 1e-12)) * Math.cos(2 * Math.PI * r()),
      choice: (arr) => arr[Math.floor(r() * arr.length)],
      id: () => Array.from({ length: 4 }, () => Math.floor(r() * 0xffffffff).toString(16).padStart(8, "0")).join("-"),
    };
  }
  const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
  const nowIso = () => new Date().toISOString().slice(0, 19) + "Z";

  // ---------------------------------------------------------------- fleet (fictitious)
  const VENDORS = {
    voltcharge: { name: "VoltCharge Systems", type: "EV charger OEM" },
    sunpeak: { name: "SunPeak Inverters", type: "Solar inverter OEM" },
    storacell: { name: "StoraCell Batteries", type: "Battery OEM" },
    homegrid: { name: "HomeGrid HEMS", type: "HEMS provider" },
    meterwise: { name: "MeterWise Metering", type: "Smart meter OEM" },
  };
  const MODELS = {
    charger: { vendor: "voltcharge", model: "VC-Bidi 11 (V2H)", rated: 11000 },
    inverter: { vendor: "sunpeak", model: "SP-5000", rated: 5000 },
    battery: { vendor: "storacell", model: "SC-10", rated: 5000 },
    gateway: { vendor: "homegrid", model: "HG-Hub 2", rated: 0 },
    meter: { vendor: "meterwise", model: "MW-1", rated: 0 },
  };
  const INSTALLATIONS = {
    "inst-001": { name: "Residence 001 (Leeds)", charger_fw: "3.2.1" },
    "inst-002": { name: "Residence 002 (York)", charger_fw: "3.2.1" },
    "inst-003": { name: "Residence 003 (Bristol)", charger_fw: "3.2.1" },
    "inst-004": { name: "Residence 004 (Cardiff)", charger_fw: "3.3.0" },
  };
  const OTHER_FW = { inverter: "1.9.2", battery: "4.0.3", gateway: "5.1.0", meter: "2.0.1" };
  const KNOWN_CLIENTS = new Set(["hems-optimiser", "homegrid-cloud", ...Object.keys(INSTALLATIONS).map((i) => "app-user-" + i.slice(-3))]);
  const EV_CAP = 60000, BAT_CAP = 10000, EFF = 0.93;

  // ---------------------------------------------------------------- supply chain fixtures (CycloneDX-derived)
  const FW_COMPONENTS = {
    "VC-Bidi 11 (V2H)|3.2.1": [["vc-ocpp-stack", "1.4.2", "ev_charging,v2h_discharge"], ["vc-v2h-controller", "2.1.0", "v2h_discharge"], ["openssl", "1.1.1k", "device_communications"], ["mosquitto", "2.0.11", "device_communications"], ["zlib", "1.2.11", ""]],
    "VC-Bidi 11 (V2H)|3.3.0": [["vc-ocpp-stack", "1.5.0", "ev_charging,v2h_discharge"], ["vc-v2h-controller", "2.2.0", "v2h_discharge"], ["openssl", "3.0.13", "device_communications"], ["mosquitto", "2.0.18", "device_communications"], ["zlib", "1.3.1", ""]],
    "HG-Hub 2|5.1.0": [["hg-auth-service", "3.0.4", "hems_command_integrity"], ["hg-device-broker", "5.1.0", "hems_command_integrity,device_communications"], ["openssl", "3.0.8", "device_communications"], ["nginx", "1.22.1", "device_communications"]],
    "SP-5000|1.9.2": [["sp-modbus-gw", "1.2.0", "solar_inverter_monitoring"], ["mbedtls", "2.28.0", "device_communications"]],
    "SC-10|4.0.3": [["sc-bms-core", "4.0.3", "battery_management"], ["mbedtls", "2.16.12", "device_communications"]],
    "MW-1|2.0.1": [["mw-dlms", "2.0.1", "metering"], ["mbedtls", "3.4.0", "device_communications"]],
  };
  const SUPPLIERS = { "VC-Bidi 11 (V2H)": "voltcharge", "HG-Hub 2": "homegrid", "SP-5000": "sunpeak", "SC-10": "storacell", "MW-1": "meterwise" };
  const VULNS = [
    { id: "HYPO-2026-0001", hypothetical: true, title: "HYPOTHETICAL: improper authorisation of OCPP SetChargingProfile", component: "vc-ocpp-stack", fixed_in: "1.5.0", cvss: 8.1,
      description: "Clearly labelled hypothetical vulnerability for demonstration. An authenticated but unauthorised client could submit charging profiles, including V2H discharge setpoints.", functions: ["ev_charging", "v2h_discharge"] },
    { id: "HYPO-2026-0002", hypothetical: true, title: "HYPOTHETICAL: missing brute-force throttling on gateway local API", component: "hg-auth-service", fixed_in: "3.1.0", cvss: 7.3,
      description: "Clearly labelled hypothetical vulnerability for demonstration. Local API password authentication lacks rate limiting.", functions: ["hems_command_integrity"] },
  ];
  function versionKey(v) { return (v.match(/\d+|[a-zA-Z]+/g) || []).map((p) => (/^\d+$/.test(p) ? +p : p)); }
  function versionLt(a, b) {
    const x = versionKey(a), y = versionKey(b);
    for (let i = 0; i < Math.max(x.length, y.length); i++) {
      if (x[i] === undefined) return true;
      if (y[i] === undefined) return false;
      if (x[i] === y[i]) continue;
      if (typeof x[i] === typeof y[i]) return x[i] < y[i];
      return typeof x[i] === "number";
    }
    return false;
  }

  class SupplyChain {
    constructor() {
      this.sboms = {};
      this.decisions = {};
      for (const [k, comps] of Object.entries(FW_COMPONENTS)) {
        const [model, ver] = k.split("|");
        this.sboms[k] = { firmware_name: model, firmware_version: ver, spec_version: "1.5", supplier: SUPPLIERS[model],
          filename: `${model.split(" ")[0].toLowerCase()}-${ver}.cdx.json`,
          components: comps.map(([n, v, f]) => ({ name: n, version: v, purl: `pkg:generic/${n}@${v}`, functions: f ? f.split(",") : [] })) };
      }
    }
    addSbom(raw, filename) {
      if (raw.length > 2000000) throw new Error("SBOM too large");
      const doc = JSON.parse(raw);
      if (doc.bomFormat !== "CycloneDX") throw new Error("Not a CycloneDX document");
      const root = (doc.metadata || {}).component || {};
      const comps = (doc.components || []).slice(0, 5000).filter((c) => c && c.name).map((c) => ({
        name: String(c.name).slice(0, 200), version: String(c.version || "").slice(0, 64), purl: c.purl,
        functions: (c.properties || []).filter((p) => p.name === "energyos:function" && p.value).flatMap((p) => p.value.split(",")) }));
      const p = { firmware_name: root.name, firmware_version: root.version, spec_version: doc.specVersion,
        supplier: ((doc.metadata || {}).supplier || {}).name, filename, components: comps };
      this.sboms[`${root.name}|${root.version}`] = p;
      return p;
    }
    applicability(model, fw) {
      const sbom = this.sboms[`${model}|${fw}`];
      const out = [];
      if (!sbom) return out;
      for (const v of VULNS) for (const c of sbom.components) {
        if (c.name !== v.component) continue;
        let status, method;
        if (!c.version) { status = "under_investigation"; method = "name match, version unknown"; }
        else if (versionLt(c.version, v.fixed_in)) { status = "potentially_affected"; method = `version ${c.version} < fixed ${v.fixed_in}`; }
        else { status = "not_affected"; method = `version ${c.version} >= fixed ${v.fixed_in}`; }
        const d = this.decisions[`${v.id}|${model}|${fw}`];
        if (d) { status = d.status; method = `human decision by ${d.by}: ${d.note}`; }
        out.push({ vulnerability: v.id, title: v.title, hypothetical: v.hypothetical, cvss: v.cvss, component: `${c.name}@${c.version}`,
          status, method, functions: v.functions, firmware: `${model} ${fw}` });
      }
      return out;
    }
    exposure(devices) {
      return VULNS.map((v) => {
        const affected = [];
        for (const d of devices) for (const a of this.applicability(d.model, d.firmware))
          if (a.vulnerability === v.id && ["potentially_affected", "confirmed_affected"].includes(a.status))
            affected.push({ device_id: d.id, installation_id: d.installation_id, firmware: `${d.model} ${d.firmware}`, status: a.status });
        return { ...v, affected_devices: affected };
      });
    }
  }

  // ---------------------------------------------------------------- simulator
  class Device {
    constructor(id, inst, kind, firmware) {
      Object.assign(this, { id, installation_id: inst, kind, firmware, true_power_w: 0, setpoint_w: 0, soc_pct: null,
        temperature_c: 25, online: true, silent_until: 0, report_override_w: null, sensor_fault: false });
    }
    get vendor() { return MODELS[this.kind].vendor; }
    get model() { return MODELS[this.kind].model; }
    get rated_w() { return MODELS[this.kind].rated; }
  }

  class Simulator {
    constructor(seed) {
      this.rng = makeRng(seed);
      this.t = 0;
      this.devices = {};
      for (const [inst, cfg] of Object.entries(INSTALLATIONS)) {
        const n = inst.slice(-3);
        for (const kind of ["gateway", "meter", "charger", "inverter", "battery"]) {
          const d = new Device(`${kind.slice(0, 3)}-${n}`, inst, kind, kind === "charger" ? cfg.charger_fw : OTHER_FW[kind]);
          if (kind === "charger") { d.soc_pct = this.rng.uniform(40, 70); d.setpoint_w = this.rng.choice([3700, 7400]); }
          if (kind === "battery") d.soc_pct = this.rng.uniform(40, 70);
          this.devices[d.id] = d;
        }
      }
      this.house = {}; this.cloud = {};
      for (const i of Object.keys(INSTALLATIONS)) { this.house[i] = this.rng.uniform(350, 600); this.cloud[i] = this.rng.uniform(0.6, 1); }
      this.scenario = null;
      this.pending = [];
    }
    dev(inst, kind) { return this.devices[`${kind.slice(0, 3)}-${inst.slice(-3)}`]; }
    activeScenario() { return this.scenario && !this.scenario.done ? this.scenario.key : null; }
    event(d, type, severity = "info", extra = {}) {
      const { client_id = null, ...details } = extra;
      const ev = { id: this.rng.id(), kind: "event", t: this.t, installation_id: d.installation_id, device_id: d.id, device_kind: d.kind,
        vendor: d.vendor, event_type: type, severity, client_id, provenance: "simulated", scenario: this.activeScenario(), details };
      this.pending.push(ev);
      return ev;
    }
    issueCommand(inst, kind, value, client, bypass = false) {
      const gw = this.dev(inst, "gateway"), target = this.dev(inst, kind);
      const cmd = this.rng.id().slice(0, 8);
      if (!bypass) this.event(gw, "command_issued", "info", { client_id: client, target: target.id, command: "set_power", value_w: value, command_id: cmd });
      if (value < 0 && kind === "charger" && target.soc_pct < 20) { this.event(target, "command_rejected", "medium", { client_id: client, reason: "soc_limit" }); return; }
      target.setpoint_w = value;
      this.event(target, "command_received", "info", { client_id: client, command: "set_power", value_w: value, command_id: cmd, via: bypass ? "local_api" : "gateway" });
    }
    startScenario(key) {
      const sc = SCENARIOS[key];
      const run = { key, started_at: this.t, steps: [], duration: sc.duration, ground_truth: {}, done: false };
      this.scenario = run;
      sc.setup(this, run);
      return { key, ground_truth: run.ground_truth };
    }
    runSteps() {
      const run = this.scenario;
      if (!run || run.done) return;
      const el = this.t - run.started_at;
      const rem = [];
      for (const [off, fn] of run.steps) { if (el >= off) fn(this); else rem.push([off, fn]); }
      run.steps = rem;
      if (el >= run.duration && !rem.length) run.done = true;
    }
    tick(dt = 1) {
      this.t += dt;
      this.runSteps();
      const rng = this.rng, tel = [];
      for (const inst of Object.keys(INSTALLATIONS)) {
        const gw = this.dev(inst, "gateway");
        if (rng.random() < 0.02) this.event(gw, "auth_success", "info", { client_id: "app-user-" + inst.slice(-3), method: "oauth_token" });
        if (rng.random() < 0.03) this.event(gw, "api_access", "info", { client_id: "homegrid-cloud", endpoint: "/api/v1/status" });
        if (Math.floor(this.t) % 30 === 0) {
          const pv = -this.dev(inst, "inverter").true_power_w;
          const surplus = pv - this.house[inst] - Math.max(this.dev(inst, "charger").true_power_w, 0);
          const bat = this.dev(inst, "battery");
          const sp = clamp(surplus, -bat.rated_w, bat.rated_w);
          if (Math.abs(sp - bat.setpoint_w) > 200) this.issueCommand(inst, "battery", Math.round(sp / 10) * 10, "hems-optimiser");
        }
      }
      for (const d of Object.values(this.devices)) {
        if (d.kind === "inverter") {
          const c = (this.cloud[d.installation_id] = clamp(this.cloud[d.installation_id] + rng.gauss(0, 0.01), 0.3, 1));
          d.true_power_w = -(d.rated_w * 0.75 * c * (0.9 + 0.1 * Math.sin(this.t / 300)));
          d.temperature_c = 35 + 0.004 * -d.true_power_w + rng.gauss(0, 0.2);
        } else if (d.kind === "charger" || d.kind === "battery") {
          const cap = d.kind === "charger" ? EV_CAP : BAT_CAP;
          let target = d.setpoint_w;
          if (target > 0 && d.soc_pct >= 99.5) target = 0;
          if (target < 0 && d.soc_pct <= 10) target = 0;
          if (!d.online) target = 0;
          d.true_power_w += (target - d.true_power_w) * (1 - Math.exp(-dt / 3));
          const p = d.true_power_w;
          const wh = ((p > 0 ? p * EFF : p / EFF) * dt) / 3600;
          d.soc_pct = clamp(d.soc_pct + (wh / cap) * 100, 0, 100);
          d.temperature_c += (25 + (Math.abs(p) / 1000) * 1.8 - d.temperature_c) * 0.05;
        }
      }
      for (const inst of Object.keys(INSTALLATIONS)) {
        const others = ["charger", "inverter", "battery"].reduce((s, k) => s + this.dev(inst, k).true_power_w, 0);
        this.dev(inst, "meter").true_power_w = this.house[inst] + rng.gauss(0, 25) + others;
      }
      for (const d of Object.values(this.devices)) {
        if (this.t < d.silent_until) continue;
        let m = {};
        let reported = d.true_power_w * (1 + rng.gauss(0, 0.005));
        if (d.report_override_w !== null) reported = d.report_override_w + rng.gauss(0, 20);
        if (d.kind === "meter") {
          const ch = this.dev(d.installation_id, "charger");
          m = { grid_power_w: +reported.toFixed(1), ev_circuit_power_w: +(ch.true_power_w * (1 + rng.gauss(0, 0.01))).toFixed(1) };
        } else if (d.kind === "gateway") {
          m = { cpu_pct: +(12 + rng.gauss(0, 2)).toFixed(1), connected_devices: 4 };
        } else {
          const p = +reported.toFixed(1), v = +(230 + rng.gauss(0, 1.2)).toFixed(1);
          m = { active_power_w: p, commanded_power_w: d.kind !== "inverter" ? d.setpoint_w : null, voltage_v: v, current_a: +(Math.abs(p) / v).toFixed(2) };
        }
        let soc = d.soc_pct, temp = d.temperature_c;
        if (d.sensor_fault) { temp = rng.choice([412, -88, temp]); if (soc !== null) soc = rng.choice([3, 99, soc]); }
        if (soc !== null) m[d.kind === "battery" ? "soc_pct" : "soc_estimate_pct"] = +soc.toFixed(2);
        m.temperature_c = +temp.toFixed(1);
        let state = "online";
        if (d.kind === "charger" || d.kind === "battery") state = d.true_power_w > 150 ? "charging" : d.true_power_w < -150 ? "discharging" : "idle";
        tel.push({ kind: "telemetry", t: this.t, installation_id: d.installation_id, device_id: d.id, device_kind: d.kind, vendor: d.vendor,
          state, measurements: m, provenance: "simulated", scenario: this.activeScenario() });
      }
      const evs = this.pending; this.pending = [];
      return [tel, evs];
    }
  }

  // ---------------------------------------------------------------- scenarios
  const SCENARIOS = {
    normal: { name: "Normal operation", duration: 30, description: "All devices operate normally.",
      expected: "No incidents. Background commands from known clients only.",
      setup: (s, run) => { run.ground_truth = { label: "benign", devices: [] }; } },
    legit_change: { name: "Legitimate EV charging change", duration: 30, description: "Resident boosts charging via the app (inst-002).",
      expected: "Context observation only; top hypothesis: legitimate user activity. No incident.",
      setup: (s, run) => {
        const gw = s.dev("inst-002", "gateway"), c = "app-user-002";
        run.steps = [[3, (s) => { s.event(gw, "auth_success", "info", { client_id: c, method: "oauth_token", user: "resident-002" });
          s.event(gw, "schedule_changed", "info", { client_id: c, user: "resident-002", change: "boost charging to 11 kW until 07:00" });
          s.issueCommand("inst-002", "charger", 11000, c); }]];
        run.ground_truth = { label: "benign", devices: ["cha-002"], expected_hypothesis: "legitimate_user_activity" };
      } },
    suspicious_v2h: { name: "Suspicious V2H discharge command", duration: 40,
      description: "Unknown client brute-forces the gateway, then commands a -9 kW V2H discharge (inst-001).",
      expected: "High-severity cyber-physical incident; hypothesis: unauthorised command; charger firmware exposure attached.",
      setup: (s, run) => {
        const gw = s.dev("inst-001", "gateway"), bad = "unknown-7f3a91";
        const fails = (s) => { for (let i = 0; i < 3; i++) s.event(gw, "auth_failure", "low", { client_id: bad, method: "local_api_password", source_ip: "10.0.8.77" }); };
        run.steps = [[3, fails], [6, fails], [9, (s) => s.event(gw, "auth_success", "info", { client_id: bad, method: "local_api_password", source_ip: "10.0.8.77" })],
          [12, (s) => s.issueCommand("inst-001", "charger", -9000, bad)]];
        run.ground_truth = { label: "malicious", devices: ["gat-001", "cha-001"], expected_hypothesis: "unauthorised_command" };
      } },
    comm_failure: { name: "Communication failure", duration: 35, description: "EV charger stops reporting for 25 s (inst-003).",
      expected: "Incident with communication-fault hypothesis; no cyber indicators.",
      setup: (s, run) => {
        const ch = s.dev("inst-003", "charger"), gw = s.dev("inst-003", "gateway");
        run.steps = [[3, (s) => { ch.silent_until = s.t + 25; s.event(gw, "communication_lost", "medium", { target: ch.id, reason: "no heartbeat" }); }],
          [29, (s) => s.event(gw, "communication_restored", "info", { target: ch.id })]];
        run.ground_truth = { label: "fault", devices: ["cha-003"], expected_hypothesis: "communication_fault" };
      } },
    telemetry_mismatch: { name: "Telemetry inconsistency", duration: 40,
      description: "Charger reports 7.4 kW while the meter's EV circuit sees ~0.3 kW (inst-004).",
      expected: "Cross-device mismatch incident; sensor fault vs integrity issue for human review.",
      setup: (s, run) => {
        const ch = s.dev("inst-004", "charger");
        run.steps = [[3, () => { ch.report_override_w = 7400; ch.setpoint_w = 300; }], [35, () => { ch.report_override_w = null; }]];
        run.ground_truth = { label: "integrity_anomaly", devices: ["cha-004", "met-004"], expected_hypothesis: "sensor_fault" };
      } },
    sensor_fault: { name: "Battery sensor fault", duration: 30,
      description: "Battery reports physically implausible temperature and SOC jumps (inst-002).",
      expected: "Incident with sensor-fault hypothesis; no cyber indicators.",
      setup: (s, run) => {
        const b = s.dev("inst-002", "battery");
        run.steps = [[3, () => { b.sensor_fault = true; }], [25, () => { b.sensor_fault = false; }]];
        run.ground_truth = { label: "fault", devices: ["bat-002"], expected_hypothesis: "sensor_fault" };
      } },
    firmware_fleet: { name: "Fleet probe on vulnerable firmware", duration: 30,
      description: "Unknown clients probe the OCPP endpoint on every charger running firmware 3.2.1.",
      expected: "Single fleet-level incident across installations sharing firmware 3.2.1, enriched with HYPO-2026-0001.",
      setup: (s, run) => {
        const targets = Object.values(s.devices).filter((d) => d.kind === "charger" && d.firmware === "3.2.1").map((d) => d.installation_id);
        run.steps = targets.map((inst, i) => [3 + i * 4, (s) => { const gw = s.dev(inst, "gateway");
          for (let k = 0; k < 2; k++) s.event(gw, "api_access", "low", { client_id: "scanner-" + i, endpoint: "/ocpp/v16/SetChargingProfile", target: s.dev(inst, "charger").id, response: "200" }); }]);
        run.ground_truth = { label: "suspicious_probe", devices: targets.map((i) => "cha-" + i.slice(-3)), expected_hypothesis: "cybersecurity_compromise" };
      } },
  };

  // ---------------------------------------------------------------- detection
  const isUnknown = (c) => c != null && !KNOWN_CLIENTS.has(c) && !c.startsWith("app-user-");
  class DetectionEngine {
    constructor() {
      this.ids = 1; this.authFail = {}; this.sched = {}; this.lastCmd = {}; this.lastSeen = {}; this.lastLabel = {};
      this.offline = new Set(); this.hist = {}; this.repCharger = {}; this.streak = {}; this.prevSoc = {}; this.cool = new Map();
    }
    emit(out, t, rec, rule, title, category, severity, explanation, evidence, o = {}) {
      const { method = "rule", score = null, client = null, cooldown = 60 } = o;
      const key = `${rule}|${rec.device_id}|${client}`;
      const prev = this.cool.get(key);
      if (prev && t - prev.last_t < cooldown) { prev.occurrences++; prev.last_t = t; prev.evidence = prev.evidence.concat(evidence).slice(-20); return; }
      const d = { id: this.ids++, rule, rule_version: "1.0", title, category, method, severity, score, t, last_t: t,
        installation_id: rec.installation_id, device_id: rec.device_id, device_kind: rec.device_kind, vendor: rec.vendor, client_id: client,
        explanation, evidence, occurrences: 1, provenance: rec.provenance, scenario: rec.scenario };
      this.cool.set(key, d);
      out.push(d);
    }
    onEvent(ev, out) {
      const { t, installation_id: inst, event_type: et, client_id: client, details: det } = ev;
      const ref = [{ type: "event", id: ev.id, summary: `${et} client=${client}` }];
      const unknown = isUnknown(client);
      if (et === "auth_failure") {
        const q = (this.authFail[inst] = this.authFail[inst] || []);
        q.push([t, client, ev.id]);
        while (q.length && t - q[0][0] > 60) q.shift();
        if (q.length >= 5) this.emit(out, t, ev, "AUTH_BRUTE_FORCE", "Repeated authentication failures", "cyber", "high",
          `${q.length} failed logins at the gateway within 60 s.`, q.map((e) => ({ type: "event", id: e[2], summary: `auth_failure client=${e[1]}` })), { client });
      }
      if (et === "auth_success") {
        const f = (this.authFail[inst] || []).filter((e) => e[1] === client && t - e[0] <= 120);
        if (f.length >= 3) this.emit(out, t, ev, "AUTH_SUCCESS_AFTER_FAILURES", "Login succeeded after repeated failures", "cyber", "high",
          `Client ${client} authenticated after ${f.length} failures in 120 s (possible credential guessing).`, ref, { client });
      }
      if (unknown && ["auth_success", "command_issued", "command_received", "api_access"].includes(et))
        this.emit(out, t, ev, "UNRECOGNISED_CLIENT", "Unrecognised client identity", "cyber", "high",
          `Client '${client}' is not in the installation's authorised client registry.`, ref, { client, cooldown: 300 });
      if (et === "schedule_changed" && !unknown) {
        this.sched[inst] = t;
        this.emit(out, t, ev, "AUTHORISED_SCHEDULE_CHANGE", "Authorised schedule change (context)", "context", "info",
          `Authenticated known client ${client} changed the charging schedule: ${det.change}.`, ref, { client });
      }
      if (et === "command_received") {
        this.lastCmd[ev.device_id] = [t, det.value_w, client, ev.id];
        const v = det.value_w || 0;
        if (ev.device_kind === "charger" && v < 0 && t - (this.sched[inst] ?? -1e9) > 300)
          this.emit(out, t, ev, "UNEXPECTED_V2H_DISCHARGE", "Unexpected V2H discharge command", "cyber", "high",
            `Charger received a ${(v / 1000).toFixed(1)} kW discharge setpoint with no authorised schedule change in the previous 5 minutes.`, ref, { client });
        if (det.via === "local_api") this.emit(out, t, ev, "GATEWAY_BYPASS", "Command bypassed the HEMS gateway", "cyber", "high",
          "Device accepted a command that the gateway never issued.", ref, { client });
      }
      if (et === "command_rejected") this.emit(out, t, ev, "COMMAND_REJECTED", "Device rejected a command", "cyber", "medium",
        `Device rejected command from ${client}: ${det.reason}.`, ref, { client });
      if (et === "api_access" && unknown && String(det.endpoint || "").includes("/ocpp/"))
        this.emit(out, t, ev, "SENSITIVE_API_PROBE", "Unrecognised client accessed charging-control API", "cyber", "medium",
          `${client} called ${det.endpoint} targeting ${det.target}.`, ref, { client });
      if (et === "communication_lost") this.emit(out, t, ev, "GATEWAY_REPORTED_COMM_LOSS", "Gateway reports device unreachable", "physical", "medium",
        `Gateway lost heartbeat with ${det.target}.`, ref);
    }
    onTelemetry(tm, out) {
      const { t, device_id: dev, measurements: m, device_kind: kind } = tm;
      this.lastSeen[dev] = t; this.lastLabel[dev] = tm.scenario; this.offline.delete(dev);
      const ref = [{ type: "telemetry", device: dev, t, summary: Object.entries(m).map(([k, v]) => `${k}=${v}`).join(", ") }];
      const temp = m.temperature_c;
      if (temp != null && !(temp >= -40 && temp <= 120)) this.emit(out, t, tm, "IMPLAUSIBLE_TEMPERATURE", "Physically implausible temperature", "physical", "medium",
        `Reported ${temp} °C is outside the plausible range -40…120 °C.`, ref);
      const soc = m.soc_pct ?? m.soc_estimate_pct;
      if (soc != null) {
        const prev = this.prevSoc[dev];
        if (prev != null && Math.abs(soc - prev) > 10) this.emit(out, t, tm, "IMPLAUSIBLE_SOC_JUMP", "Implausible state-of-charge jump", "physical", "medium",
          `SOC moved ${prev.toFixed(1)}% → ${soc.toFixed(1)}% in one sample; at rated power this requires minutes, not seconds.`, ref);
        this.prevSoc[dev] = soc;
      }
      const p = m.active_power_w;
      if (p != null && (kind === "charger" || kind === "battery")) {
        const h = (this.hist[dev] = this.hist[dev] || []);
        if (h.length >= 10) {
          const ago = h[h.length - 6];
          if (Math.abs(p - ago) > 3000) {
            const c = this.lastCmd[dev];
            const linked = c && t - c[0] <= 20;
            const known = linked && (KNOWN_CLIENTS.has(c[2]) || c[2] === "app-user-" + tm.installation_id.slice(-3));
            this.emit(out, t, tm, "POWER_STEP_CHANGE", "Large power-flow change", "physical", known ? "low" : "medium",
              `Active power changed ${(ago / 1000).toFixed(1)} → ${(p / 1000).toFixed(1)} kW in 5 s` +
              (linked ? `; follows command ${c[1]} W from ${c[2]} (${(t - c[0]).toFixed(0)} s earlier).` : "; no recent command explains it."),
              ref, { client: linked ? c[2] : null });
          }
        }
        if (h.length >= 30) {
          const mu = h.reduce((a, b) => a + b, 0) / h.length;
          const sd = Math.sqrt(h.reduce((a, b) => a + (b - mu) ** 2, 0) / h.length);
          const z = Math.abs(p - mu) / Math.max(sd, 200);
          if (z > 6 && Math.abs(p - mu) > 2000) this.emit(out, t, tm, "BASELINE_DEVIATION", "Deviation from device baseline", "physical", "low",
            `Power ${p.toFixed(0)} W vs rolling mean ${mu.toFixed(0)} W (z=${z.toFixed(1)}, 60-sample window).`, ref, { method: "statistical", score: +z.toFixed(2) });
        }
        h.push(p); if (h.length > 60) h.shift();
        if (kind === "charger") this.repCharger[tm.installation_id] = p;
      }
      if (kind === "meter") {
        const inst = tm.installation_id, rep = this.repCharger[inst], evc = m.ev_circuit_power_w;
        if (rep != null && evc != null) {
          const gap = Math.abs(rep - evc);
          this.streak[inst] = gap > Math.max(1000, 0.15 * Math.abs(evc)) ? (this.streak[inst] || 0) + 1 : 0;
          if (this.streak[inst] >= 3) this.emit(out, t, { ...tm, device_id: "cha-" + inst.slice(-3), device_kind: "charger", vendor: "voltcharge" },
            "METER_CHARGER_MISMATCH", "Charger telemetry disagrees with meter", "physical", "high",
            `Charger reports ${rep.toFixed(0)} W; independent meter EV circuit measures ${evc.toFixed(0)} W (gap ${gap.toFixed(0)} W for 3+ samples).`, ref);
        }
      }
    }
    liveness(t, devices, out) {
      for (const d of Object.values(devices)) {
        const last = this.lastSeen[d.id];
        if (last != null && t - last > 5 && !this.offline.has(d.id)) {
          this.offline.add(d.id);
          this.emit(out, t, { installation_id: d.installation_id, device_id: d.id, device_kind: d.kind, vendor: d.vendor, provenance: "simulated", scenario: this.lastLabel[d.id] },
            "TELEMETRY_SILENCE", "Device stopped reporting", "physical", "medium", `No telemetry from ${d.id} for ${(t - last).toFixed(0)} s (expected every 1 s).`,
            [{ type: "telemetry", device: d.id, t: last, summary: "last sample received" }]);
        }
      }
    }
    process(t, tel, evs, devices) {
      const out = [];
      for (const e of evs) this.onEvent(e, out);
      for (const tm of [...tel].sort((a, b) => (a.device_kind === "meter") - (b.device_kind === "meter"))) this.onTelemetry(tm, out);
      this.liveness(t, devices, out);
      return out;
    }
  }

  // ---------------------------------------------------------------- correlation
  const SEV_W = { info: 0, low: 1, medium: 3, high: 5, critical: 8 };
  const SEV = ["info", "low", "medium", "high", "critical"];
  const FLEET_RULES = new Set(["SENSITIVE_API_PROBE", "COMMAND_REJECTED", "METER_CHARGER_MISMATCH", "TELEMETRY_SILENCE"]);
  const WINDOW = 180;
  const HLABEL = { unauthorised_command: "Possible unauthorised command", cybersecurity_compromise: "Possible cybersecurity compromise",
    legitimate_user_activity: "Legitimate user activity", communication_fault: "Communication fault", sensor_fault: "Sensor / metering fault",
    firmware_defect: "Firmware defect", integration_failure: "Integration failure" };

  class Correlator {
    constructor(fw) { this.groups = []; this.fw = fw; this.gid = 1; }
    open() { return this.groups.filter((g) => g.status === "open" && g.merged_into == null); }
    add(dets, now) {
      const touched = new Set();
      for (const d of dets) {
        let g = this.open().reverse().find((g) => g.installations.has(d.installation_id) && d.t - g.last_t <= WINDOW);
        if (!g) { g = { id: this.gid++, kind: "installation", status: "open", first_t: d.t, last_t: d.t, installations: new Set(), devices: new Set(),
          vendors: new Set(), clients: new Set(), detections: [], severity: "info", score: 0, hypotheses: [], links: [], incident_id: null, merged_into: null }; this.groups.push(g); }
        this.attach(g, d); touched.add(g.id);
      }
      for (const id of this.fleetMerge()) touched.add(id);
      for (const g of this.groups) if (g.status === "open" && now - g.last_t > WINDOW) g.status = "closed_window";
      const out = this.groups.filter((g) => touched.has(g.id) && g.merged_into == null);
      out.forEach((g) => this.assess(g));
      return out;
    }
    attach(g, d) {
      g.detections.push(d); d.group_id = g.id;
      g.installations.add(d.installation_id); g.devices.add(d.device_id); g.vendors.add(d.vendor);
      if (d.client_id) g.clients.add(d.client_id);
      g.first_t = Math.min(g.first_t, d.t); g.last_t = Math.max(g.last_t, d.t);
    }
    fleetMerge() {
      const touched = [];
      const gs = this.open();
      for (const a of gs) for (const b of gs) {
        if (a === b || a.merged_into || b.merged_into || a.id > b.id) continue;
        if (Math.abs(a.last_t - b.last_t) > WINDOW || [...a.installations].some((i) => b.installations.has(i))) continue;
        const ra = new Set(a.detections.map((d) => d.rule).filter((r) => FLEET_RULES.has(r)));
        const common = [...new Set(b.detections.map((d) => d.rule).filter((r) => ra.has(r)))];
        const fwa = new Set([...a.installations].map(this.fw));
        const fwc = [...new Set([...b.installations].map(this.fw))].filter((f) => fwa.has(f));
        if (common.length && fwc.length) {
          b.detections.forEach((d) => this.attach(a, d));
          b.merged_into = a.id; a.kind = "fleet"; a.fleet_key = { rules: common.sort(), charger_firmware: fwc.sort() };
          touched.push(a.id);
        }
      }
      return touched;
    }
    assess(g) {
      const dets = g.detections, rules = new Set(dets.map((d) => d.rule));
      const cyber = dets.filter((d) => d.category === "cyber");
      const links = [];
      for (const p of dets) if (p.rule === "POWER_STEP_CHANGE" && p.client_id) {
        const prior = cyber.filter((c) => c.client_id === p.client_id && c.t <= p.t);
        if (prior.length) { const rank = (c) => c.t + (["UNEXPECTED_V2H_DISCHARGE", "GATEWAY_BYPASS"].includes(c.rule) ? 0.5 : 0); const c = prior.reduce((a, b) => (rank(b) > rank(a) ? b : a));
          links.push({ cyber: c.id, physical: p.id, lag_s: +(p.t - c.t).toFixed(1), text: `${c.title} (${c.client_id}) → ${p.title} on ${p.device_id} ${(p.t - c.t).toFixed(0)} s later` }); }
      }
      g.links = links;
      const has = (...r) => r.some((x) => rules.has(x));
      const f = { unknown: has("UNRECOGNISED_CLIENT"), auth: has("AUTH_BRUTE_FORCE", "AUTH_SUCCESS_AFTER_FAILURES"), unexpected: has("UNEXPECTED_V2H_DISCHARGE", "GATEWAY_BYPASS"),
        effect: links.length > 0, legit: has("AUTHORISED_SCHEDULE_CHANGE"), comm: has("TELEMETRY_SILENCE", "GATEWAY_REPORTED_COMM_LOSS"),
        implausible: has("IMPLAUSIBLE_TEMPERATURE", "IMPLAUSIBLE_SOC_JUMP"), mismatch: has("METER_CHARGER_MISMATCH"), probe: has("SENSITIVE_API_PROBE"),
        rejected: has("COMMAND_REJECTED"), fleet: g.installations.size >= 2, cyber: cyber.length > 0 };
      const n = (b) => (b ? 1 : 0);
      const raw = {
        unauthorised_command: 2 * n(f.unexpected) + 1.5 * n(f.unknown) + n(f.auth) + 1.5 * n(f.effect && f.unexpected) - 2 * n(f.legit),
        cybersecurity_compromise: n(f.auth) + n(f.unknown) + 1.5 * n(f.probe) + n(f.fleet && f.probe) + 0.5 * n(f.unexpected) + 0.5 * n(f.mismatch),
        legitimate_user_activity: 2.5 * n(f.legit) - 2 * n(f.unknown) - 1.5 * n(f.auth) + 0.5 * n(f.legit && !f.cyber),
        communication_fault: 2 * n(f.comm) - n(f.cyber),
        sensor_fault: 2 * n(f.implausible) + 1.5 * n(f.mismatch) - n(f.cyber),
        firmware_defect: n(f.fleet && (f.rejected || f.mismatch || f.comm)) + 0.5 * n(f.rejected),
        integration_failure: n(f.rejected) + 0.5 * n(f.comm && f.fleet),
      };
      const why = {
        unauthorised_command: [["unexpected", "command not preceded by an authorised schedule change"], ["unknown", "issuing client is not in the authorised registry"], ["effect", "command followed by a measured power-flow change"]],
        cybersecurity_compromise: [["auth", "authentication anomalies"], ["unknown", "unrecognised client"], ["probe", "charging-control API probed"], ["fleet", "pattern repeats across installations"]],
        legitimate_user_activity: [["legit", "authenticated resident changed the schedule"]],
        communication_fault: [["comm", "device stopped reporting / gateway lost heartbeat"]],
        sensor_fault: [["implausible", "physically implausible readings"], ["mismatch", "device telemetry disagrees with independent meter"]],
        firmware_defect: [["fleet", "same behaviour on devices sharing firmware"]],
        integration_failure: [["rejected", "devices rejected forwarded commands"]],
      };
      const against = { legitimate_user_activity: [["unknown", "unrecognised client involved"], ["auth", "authentication anomalies"]],
        communication_fault: [["cyber", "cyber indicators present"]], sensor_fault: [["cyber", "cyber indicators present"]],
        unauthorised_command: [["legit", "authorised schedule change on record"]] };
      const pos = Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, Math.max(0, v)]));
      const total = Object.values(pos).reduce((a, b) => a + b, 0) || 1;
      g.hypotheses = Object.entries(pos).sort((a, b) => b[1] - a[1]).filter(([, v]) => v > 0).map(([k, v]) => ({ key: k, label: HLABEL[k],
        support: Math.round((v / total) * 100) / 100, for: (why[k] || []).filter(([fl]) => f[fl]).map((x) => x[1]), against: (against[k] || []).filter(([fl]) => f[fl]).map((x) => x[1]) }));
      g.features = f;
      let sev = dets.reduce((a, d) => (SEV.indexOf(d.severity) > SEV.indexOf(a) ? d.severity : a), "info");
      if (f.unexpected && f.effect) sev = "critical";
      g.severity = sev;
      g.score = dets.reduce((a, d) => a + SEV_W[d.severity], 0);
      const h = g.hypotheses;
      g.confidence = h.length >= 2 && h[0].support - h[1].support < 0.15 ? "low — competing explanations remain plausible"
        : h.length && h[0].support >= 0.5 ? "moderate — evidence favours the top hypothesis" : "low";
      g.promote = SEV.indexOf(sev) >= 3 || g.score >= 5;
    }
  }

  // ---------------------------------------------------------------- platform / workflow
  const ORGS = {
    aegis: { name: "Aegis Grid Security Operations", type: "security_provider" },
    homegrid: { name: "HomeGrid HEMS", type: "hems_operator", vendor: "homegrid" },
    voltcharge: { name: "VoltCharge Systems", type: "oem", vendor: "voltcharge" },
    storacell: { name: "StoraCell Batteries", type: "oem", vendor: "storacell" },
    sunpeak: { name: "SunPeak Inverters", type: "oem", vendor: "sunpeak" },
  };
  const USERS = {
    "u-analyst": { name: "Dana Reyes", role: "security_analyst", org: "aegis" },
    "u-operator": { name: "Sam Okafor", role: "fleet_operator", org: "homegrid" },
    "u-voltcharge": { name: "Lena Brandt", role: "oem_security", org: "voltcharge" },
    "u-storacell": { name: "Kenji Mori", role: "oem_security", org: "storacell" },
    "u-exec": { name: "Priya Shah", role: "executive", org: "homegrid" },
  };
  const PERMS = {
    security_analyst: ["view_fleet", "view_incidents_all", "triage", "decide", "assign", "share", "simulate", "audit", "comment"],
    fleet_operator: ["view_fleet", "view_incidents_all", "comment", "task_respond"],
    oem_security: ["view_shared", "task_respond", "vex", "comment"],
    executive: ["overview"],
  };
  const TRANSITIONS = { new: ["triaged", "investigating", "awaiting_oem"], triaged: ["investigating", "awaiting_oem", "resolved"],
    investigating: ["awaiting_oem", "mitigation_planned", "monitoring", "resolved"], awaiting_oem: ["investigating", "mitigation_planned"],
    mitigation_planned: ["monitoring", "resolved"], monitoring: ["resolved", "investigating"], resolved: ["closed", "reopened"],
    closed: ["reopened"], reopened: ["investigating", "triaged"], duplicate: ["reopened"] };
  const FUNCTIONS = { charger: ["EV charging", "V2H discharge"], battery: ["Battery management"], inverter: ["Solar inverter monitoring"],
    gateway: ["HEMS command integrity", "Device communications"], meter: ["Metering"] };
  class HttpErr extends Error { constructor(status, msg) { super(msg); this.status = status; } }
  const deny = (p) => new HttpErr(403, `permission denied (${p})`);
  const notFound = () => new HttpErr(404, "not found");
  const bad = (m) => new HttpErr(400, m);

  async function sha256(s) {
    if (globalThis.crypto && crypto.subtle) {
      const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
      return [...new Uint8Array(b)].map((x) => x.toString(16).padStart(2, "0")).join("");
    }
    let h = 0; for (let i = 0; i < s.length; i++) h = (Math.imul(31, h) + s.charCodeAt(i)) | 0; return (h >>> 0).toString(16).padStart(64, "0");
  }

  class Platform {
    constructor(seed = 42) { this.seed = seed; this.audit = []; this.auditQ = Promise.resolve(); this.resetState(); }
    resetState() {
      this.wall0 = Date.now();
      this.sim = new Simulator(this.seed);
      this.engine = new DetectionEngine();
      this.corr = new Correlator((inst) => this.sim.dev(inst, "charger").firmware);
      this.sc = new SupplyChain();
      this.hist = {}; this.events = []; this.detections = []; this.incidents = {}; this.tasks = {}; this.runs = [];
      this.incId = 1; this.taskId = 1;
    }
    ts(t) { return new Date(this.wall0 + t * 1000).toISOString().slice(0, 19) + "Z"; }
    step(n = 1) {
      for (let i = 0; i < n; i++) {
        const [tel, evs] = this.sim.tick(1);
        for (const r of [...tel, ...evs]) r.ts = this.ts(r.t);
        for (const tm of tel) { const h = (this.hist[tm.device_id] = this.hist[tm.device_id] || []); h.push(tm); if (h.length > 300) h.shift(); }
        this.events.push(...evs); if (this.events.length > 3000) this.events.splice(0, this.events.length - 3000);
        const dets = this.engine.process(this.sim.t, tel, evs, this.sim.devices);
        dets.forEach((d) => (d.ts = this.ts(d.t)));
        this.detections.push(...dets);
        for (const g of this.corr.add(dets, this.sim.t)) this.syncIncident(g);
      }
    }
    addAudit(actor, action, rtype, rid, details) {
      const rec = { seq: this.audit.length + 1, at: nowIso(), actor: actor || "system", actor_name: (USERS[actor] || {}).name || "EnergyOS engine",
        action, resource: `${rtype}:${rid}`, details, prev_hash: null, hash: null };
      this.audit.push(rec);
      this.auditQ = this.auditQ.then(async () => {
        const i = this.audit.indexOf(rec);
        rec.prev_hash = i > 0 ? this.audit[i - 1].hash : "0".repeat(64);
        const { hash, ...body } = rec;
        rec.hash = await sha256(rec.prev_hash + JSON.stringify(body));
      });
    }
    async verifyAudit() {
      await this.auditQ;
      let prev = "0".repeat(64);
      for (const r of this.audit) { const { hash, ...body } = r; if (r.prev_hash !== prev || (await sha256(prev + JSON.stringify(body))) !== hash) return false; prev = hash; }
      return true;
    }
    can(a, perm) { const u = USERS[a]; return !!(u && PERMS[u.role].includes(perm)); }
    req(a, perm) { if (!this.can(a, perm)) throw deny(perm); return USERS[a]; }

    syncIncident(g) {
      let inc;
      if (g.incident_id) inc = this.incidents[g.incident_id];
      else if (g.promote) {
        const id = this.incId++;
        inc = { id, code: `INC-${new Date().getFullYear()}-${String(id).padStart(4, "0")}`, status: "new", group_id: g.id, created_at: this.ts(g.first_t),
          notes: [], decisions: [], history: [], shares: {}, attack_confirmation: "unconfirmed", owner: null, merged_into: null };
        g.incident_id = id; this.incidents[id] = inc;
        this.addAudit(null, "incident.created", "incident", inc.code, { group: g.id, severity: g.severity, reason: "correlation threshold met" });
      } else return;
      for (const o of this.corr.groups) if (o.merged_into === g.id && o.incident_id && o.incident_id !== inc.id) {
        const dup = this.incidents[o.incident_id];
        if (dup.status !== "duplicate") { dup.status = "duplicate"; dup.merged_into = inc.code; this.addAudit(null, "incident.deduplicated", "incident", dup.code, { merged_into: inc.code }); }
      }
    }
    group(inc) { return this.corr.groups.find((g) => g.id === inc.group_id); }
    title(g) {
      const top = g.hypotheses.length ? g.hypotheses[0].label : "Anomaly";
      const where = g.kind === "fleet" ? `${g.installations.size} installations` : [...g.installations][0];
      return `${top} — ${where} (${g.devices.size} devices, ${g.vendors.size} vendors)`;
    }
    impact(g) {
      return [...g.devices].sort().map((dev) => {
        const d = this.sim.devices[dev]; if (!d) return null;
        const s = (this.hist[dev] || []).filter((x) => x.t >= g.first_t - 10 && x.t <= g.last_t + 60);
        const pw = s.map((x) => x.measurements.active_power_w).filter((x) => x != null);
        const e = { device: dev, kind: d.kind, vendor: VENDORS[d.vendor].name, functions_potentially_affected: FUNCTIONS[d.kind] };
        const parts = [];
        if (pw.length) {
          parts.push(`power range ${(Math.min(...pw) / 1000).toFixed(1)}…${(Math.max(...pw) / 1000).toFixed(1)} kW`);
          const exp = pw.filter((p) => p < 0).reduce((a, p) => a - p, 0) / 3600;
          if (exp > 1 && d.kind === "charger") parts.push(`${(exp / 1000).toFixed(2)} kWh discharged/exported in window`);
        }
        const socs = s.map((x) => x.measurements.soc_estimate_pct ?? x.measurements.soc_pct).filter((x) => x != null);
        if (socs.length && d.kind === "charger") parts.push(`EV SOC estimate ${socs[0].toFixed(1)}% → ${socs[socs.length - 1].toFixed(1)}%`);
        if (this.engine.offline.has(dev) || g.detections.some((x) => x.rule === "TELEMETRY_SILENCE" && x.device_id === dev)) parts.push("telemetry gap observed");
        if (parts.length) e.observed = parts.join("; ");
        return e;
      }).filter(Boolean);
    }
    exposure(g) {
      const out = [];
      for (const dev of [...g.devices].sort()) { const d = this.sim.devices[dev]; if (!d) continue;
        for (const a of this.sc.applicability(d.model, d.firmware)) if (["potentially_affected", "confirmed_affected", "under_investigation"].includes(a.status))
          out.push({ ...a, device: dev, note: "Possible relevance only — not evidence that this vulnerability caused the anomaly." }); }
      for (const inst of g.installations) { const ch = this.sim.dev(inst, "charger"); if (g.devices.has(ch.id)) continue;
        for (const a of this.sc.applicability(ch.model, ch.firmware)) if (["potentially_affected", "confirmed_affected"].includes(a.status))
          out.push({ ...a, device: ch.id, note: "Installation context — possible relevance only." }); }
      return out;
    }
    evidence(inc, g) {
      const ev = g.detections.map((d) => ({ id: `det-${d.id}`, type: "detection", ts: d.ts, device: d.device_id, vendor: d.vendor, category: d.category,
        severity: d.severity, title: d.title, rule: `${d.rule} v${d.rule_version}`, method: d.method, explanation: d.explanation, occurrences: d.occurrences,
        raw: d.evidence.slice(-6), provenance: d.provenance, kind: "model/rule output" }));
      for (const x of this.exposure(g)) ev.push({ id: `exp-${x.vulnerability}-${x.device}`, type: "exposure", device: x.device, vendor: this.sim.devices[x.device].vendor,
        title: `${x.vulnerability}: ${x.title}`, explanation: `${x.component} — ${x.status} (${x.method}). ${x.note}`, severity: "info", category: "supply_chain", kind: "supply-chain context" });
      return ev;
    }
    recommended(g) {
      const top = g.hypotheses.length ? g.hypotheses[0].key : null;
      const rec = {
        unauthorised_command: ["Contact resident to verify whether the discharge was requested", "Request VoltCharge review of charger command/OCPP logs (evidence share)",
          "Consider revoking the unrecognised client's gateway credentials (human-approved, executed by operator)", "Assess exposure to HYPO-2026-0001 / HYPO-2026-0002 on affected firmware"],
        cybersecurity_compromise: ["Request OEM investigation of probed endpoint on shared firmware", "Prioritise firmware update planning for exposed devices", "Review gateway access logs across affected installations"],
        communication_fault: ["Ask operator to check installation connectivity", "Monitor for recovery"],
        sensor_fault: ["Request OEM diagnostics of the reporting sensor / metering", "Compare against meter before trusting device telemetry"],
        legitimate_user_activity: ["Confirm change with resident; close as benign if confirmed"],
        firmware_defect: ["Request OEM root-cause analysis across firmware cohort"], integration_failure: ["Review HEMS ↔ device integration logs with operator"],
      }[top] || ["Review evidence"];
      return { actions: rec, responsible_vendors: [...g.vendors].map((v) => VENDORS[v].name).sort(), note: "Recommendations only. EnergyOS never executes device control; humans approve and act." };
    }
    visible(a) {
      const u = USERS[a];
      if (this.can(a, "view_incidents_all")) return Object.values(this.incidents);
      if (this.can(a, "view_shared")) return Object.values(this.incidents).filter((i) =>
        Object.values(this.tasks).some((t) => t.incident_id === i.id && t.org === u.org) || (i.shares[u.org] || []).length);
      throw deny("incidents");
    }
    incFor(a, id) { const inc = this.incidents[id]; if (!inc || !this.visible(a).includes(inc)) throw notFound(); return inc; }
    listIncidents(a) {
      const full = this.can(a, "view_incidents_all");
      return this.visible(a).map((inc) => { const g = this.group(inc); return { id: inc.id, code: inc.code, status: inc.status, severity: g.severity, kind: g.kind,
        title: full ? this.title(g) : "Shared investigation", installations: full ? [...g.installations].sort() : [],
        top_hypothesis: full && g.hypotheses.length ? g.hypotheses[0].label : null, created_at: inc.created_at, last_activity: this.ts(g.last_t),
        vendors: [...g.vendors].sort(), merged_into: inc.merged_into, attack_confirmation: inc.attack_confirmation }; }).sort((x, y) => y.id - x.id);
    }
    series(g) {
      const out = {};
      const win = (x) => x.t >= g.first_t - 20 && x.t <= g.last_t + 60;
      for (const dev of [...g.devices].sort()) { const d = this.sim.devices[dev];
        if (d && (d.kind === "charger" || d.kind === "battery")) out[dev] = (this.hist[dev] || []).filter(win).map((s) => ({ ts: s.ts.slice(11, 19), power_w: s.measurements.active_power_w })); }
      for (const inst of g.installations) { const mid = "met-" + inst.slice(-3);
        out[mid + " (EV circuit)"] = (this.hist[mid] || []).filter(win).map((s) => ({ ts: s.ts.slice(11, 19), power_w: s.measurements.ev_circuit_power_w })); }
      return out;
    }
    getIncident(a, id) {
      const inc = this.incFor(a, id), g = this.group(inc), evidence = this.evidence(inc, g);
      const tasks = Object.values(this.tasks).filter((t) => t.incident_id === id);
      if (this.can(a, "view_incidents_all")) {
        const summary = `${g.detections.length} findings across ${g.devices.size} devices from ${g.vendors.size} vendors in ${[...g.installations].sort().join(", ")}. ` + g.links.map((l) => l.text + ".").join(" ");
        return { id, code: inc.code, status: inc.status, severity: g.severity, kind: g.kind, title: this.title(g), summary, fleet_key: g.fleet_key || null,
          merged_into: inc.merged_into, installations: [...g.installations].sort(), devices: [...g.devices].sort(),
          vendors: [...g.vendors].map((v) => VENDORS[v].name).sort(), clients: [...g.clients].sort(), first_seen: this.ts(g.first_t), last_seen: this.ts(g.last_t),
          hypotheses: g.hypotheses, confidence: g.confidence, links: g.links, impact: this.impact(g), evidence, recommended: this.recommended(g),
          notes: inc.notes, decisions: inc.decisions, history: inc.history, tasks, attack_confirmation: inc.attack_confirmation, owner: inc.owner,
          shares: Object.fromEntries(Object.entries(inc.shares).map(([o, s]) => [o, [...s].sort()])), allowed_transitions: [...(TRANSITIONS[inc.status] || [])].sort(),
          telemetry: this.series(g), audit: this.audit.filter((r) => r.resource === `incident:${inc.code}`) };
      }
      const org = USERS[a].org, allowed = new Set(inc.shares[org] || []);
      return { id, code: inc.code, status: inc.status, severity: g.severity, title: "Shared investigation", restricted_view: true,
        summary: "You can see only evidence explicitly shared with your organisation.", evidence: evidence.filter((e) => allowed.has(e.id)),
        tasks: tasks.filter((t) => t.org === org), notes: inc.notes.filter((n) => n.visibility === "shared" || n.org === org) };
    }
    transition(a, id, to, reason) {
      this.req(a, "triage");
      const inc = this.incFor(a, id);
      if (!(TRANSITIONS[inc.status] || []).includes(to)) throw bad(`Transition ${inc.status} → ${to} not allowed`);
      const g = this.group(inc);
      if (to === "closed" && ["high", "critical"].includes(g.severity) && !reason) throw bad("Closing a high/critical incident requires a recorded rationale");
      const e = { from: inc.status, to, by: USERS[a].name, reason, at: nowIso() };
      inc.history.push(e); inc.status = to; if (!inc.owner) inc.owner = USERS[a].name;
      this.addAudit(a, "incident.status_changed", "incident", inc.code, e);
      return e;
    }
    addNote(a, id, body, visibility = "internal") {
      const u = this.req(a, "comment"), inc = this.incFor(a, id);
      if (this.can(a, "view_shared")) visibility = "shared";
      const n = { by: u.name, org: u.org, body: String(body).slice(0, 4000), visibility, at: nowIso() };
      inc.notes.push(n); this.addAudit(a, "incident.note_added", "incident", inc.code, { visibility });
      return n;
    }
    decide(a, id, decision, rationale) {
      const u = this.req(a, "decide"), inc = this.incFor(a, id);
      if (!["confirm_attack", "reject_attack", "approve_remediation", "reportability_review_required", "not_reportable"].includes(decision)) throw bad("unknown decision");
      if (!rationale) throw bad("rationale required");
      const r = { decision, by: u.name, rationale: rationale.slice(0, 2000), at: nowIso() };
      inc.decisions.push(r);
      if (decision === "confirm_attack") inc.attack_confirmation = "confirmed by human";
      if (decision === "reject_attack") inc.attack_confirmation = "rejected by human";
      this.addAudit(a, `incident.decision.${decision}`, "incident", inc.code, { rationale: rationale.slice(0, 200) });
      return r;
    }
    share(a, id, org, ids, purpose) {
      this.req(a, "share");
      const inc = this.incFor(a, id);
      if (!ORGS[org] || org === USERS[a].org) throw bad("unknown organisation");
      const valid = new Set(this.evidence(inc, this.group(inc)).map((e) => e.id));
      const sel = (ids || []).filter((e) => valid.has(e));
      if (!sel.length) throw bad("no valid evidence selected");
      inc.shares[org] = [...new Set([...(inc.shares[org] || []), ...sel])];
      this.addAudit(a, "evidence.shared", "incident", inc.code, { grantee: ORGS[org].name, evidence: sel, purpose });
      return { org, evidence: inc.shares[org] };
    }
    createTask(a, id, org, title, dueHours, ids, type = "investigation") {
      const u = this.req(a, "assign"), inc = this.incFor(a, id);
      if (!ORGS[org]) throw bad("unknown organisation");
      const tid = this.taskId++;
      const t = { id: tid, incident_id: id, incident_code: inc.code, org, org_name: ORGS[org].name, title: String(title).slice(0, 300), type, status: "open",
        created_by: u.name, created_at: nowIso(), due_at: new Date(Date.now() + (+dueHours || 48) * 3600e3).toISOString().slice(0, 16) + "Z", acknowledged_at: null, comments: [] };
      this.tasks[tid] = t;
      this.addAudit(a, "task.created", "incident", inc.code, { task: tid, org: ORGS[org].name, title });
      if (ids && ids.length) this.share(a, id, org, ids, `task ${tid}: ${title}`);
      if (["new", "triaged", "investigating"].includes(inc.status) && TRANSITIONS[inc.status].includes("awaiting_oem") && ORGS[org].type === "oem")
        this.transition(a, id, "awaiting_oem", `Task ${tid} assigned to ${ORGS[org].name}`);
      return t;
    }
    listTasks(a) {
      const u = USERS[a]; if (!u) throw new HttpErr(401, "not signed in");
      return Object.values(this.tasks).filter((t) => this.can(a, "view_incidents_all") || t.org === u.org)
        .map((t) => ({ ...t, overdue: !["completed", "cancelled"].includes(t.status) && new Date(t.due_at) < new Date() })).sort((x, y) => y.id - x.id);
    }
    updateTask(a, tid, status, comment) {
      const u = this.can(a, "assign") ? USERS[a] : this.req(a, "task_respond");
      const t = this.tasks[tid];
      if (!t || (!this.can(a, "assign") && t.org !== u.org)) throw notFound();
      if (![undefined, null, "", "acknowledged", "in_progress", "blocked", "completed", "cancelled"].includes(status)) throw bad("bad status");
      if (status) { if (status === "acknowledged" && !t.acknowledged_at) t.acknowledged_at = nowIso(); t.status = status; }
      if (comment) t.comments.push({ by: u.name, org: u.org, body: String(comment).slice(0, 4000), at: nowIso() });
      this.addAudit(a, "task.updated", "incident", t.incident_code, { task: tid, status, comment: !!comment });
      return t;
    }
    vex(a, vuln, model, fw, status, note) {
      const u = this.req(a, "vex");
      const dev = Object.values(this.sim.devices).find((d) => d.model === model);
      if (!dev || dev.vendor !== ORGS[u.org].vendor) throw deny("not your product");
      if (!["confirmed_affected", "not_affected", "under_investigation"].includes(status)) throw bad("bad status");
      this.sc.decisions[`${vuln}|${model}|${fw}`] = { status, by: u.name, note: String(note || "").slice(0, 500) };
      this.addAudit(a, "vulnerability.applicability_decided", "vulnerability", vuln, { firmware: `${model} ${fw}`, status, note: String(note || "").slice(0, 200) });
    }
    startScenario(a, key) {
      this.req(a, "simulate");
      if (!SCENARIOS[key]) throw notFound();
      const info = this.sim.startScenario(key);
      const run = { id: this.runs.length + 1, key, name: SCENARIOS[key].name, start_t: this.sim.t, started_at: this.ts(this.sim.t),
        ground_truth: info.ground_truth, expected: SCENARIOS[key].expected };
      this.runs.push(run);
      this.addAudit(a, "simulation.started", "scenario", key, { run: run.id });
      return run;
    }
    runResults() {
      return this.runs.map((r) => {
        const end = r.start_t + SCENARIOS[r.key].duration;
        const dets = this.detections.filter((d) => d.scenario === r.key && d.t >= r.start_t && d.t <= end + 5);
        const gids = new Set(dets.map((d) => d.group_id));
        const groups = this.corr.groups.filter((g) => gids.has(g.id) && g.merged_into == null);
        const incs = groups.filter((g) => g.incident_id).map((g) => this.incidents[g.incident_id].code);
        let tops = groups.filter((g) => g.hypotheses.length && g.incident_id).map((g) => g.hypotheses[0].key);
        if (!tops.length) tops = groups.filter((g) => g.hypotheses.length).map((g) => g.hypotheses[0].key);
        const exp = r.ground_truth.expected_hypothesis;
        const first = dets.length ? Math.min(...dets.map((d) => d.t)) : null;
        return { ...r, status: this.sim.t < end ? "running" : "completed",
          detections: dets.map((d) => ({ rule: d.rule, severity: d.severity, device: d.device_id, at_s: +(d.t - r.start_t).toFixed(1) })),
          incidents: incs, top_hypotheses: tops, hypothesis_match: exp ? tops[0] === exp : !incs.length,
          first_detection_s: first != null ? +(first - r.start_t).toFixed(1) : null };
      }).reverse();
    }
  }

  // ---------------------------------------------------------------- API router (mirrors energyos/api_core.py)
  const TOKENS = {};
  const det = (d) => { const o = {}; for (const k of ["id", "ts", "rule", "title", "category", "method", "severity", "score", "device_id", "vendor", "client_id", "explanation", "occurrences", "group_id", "provenance"]) o[k] = d[k] ?? null; return o; };
  const ROUTES = [
    ["GET", /^\/api\/users$/, (p) => Object.entries(USERS).map(([id, u]) => ({ id, ...u, org_name: ORGS[u.org].name })), true],
    ["POST", /^\/api\/login$/, (p, a, q, b) => { if (!USERS[b.user_id]) throw new HttpErr(401, "unknown user");
      const tok = Math.random().toString(36).slice(2) + Date.now().toString(36); TOKENS[tok] = b.user_id;
      p.addAudit(b.user_id, "auth.login", "user", b.user_id, { method: "demo identity selector" }); return { token: tok }; }, true],
    ["GET", /^\/api\/me$/, (p, a) => ({ id: a, ...USERS[a], org_name: ORGS[USERS[a].org].name, permissions: [...PERMS[USERS[a].role]].sort() })],
    ["GET", /^\/api\/overview$/, (p) => {
      const incs = Object.values(p.incidents).filter((i) => i.status !== "duplicate");
      const active = incs.filter((i) => !["resolved", "closed"].includes(i.status));
      const sev = {}; active.forEach((i) => { const s = p.group(i).severity; sev[s] = (sev[s] || 0) + 1; });
      const exposure = p.sc.exposure(Object.values(p.sim.devices));
      const buckets = {};
      for (const d of p.detections) { const k = p.ts(Math.floor(d.t / 30) * 30).slice(11, 19);
        buckets[k] = buckets[k] || { time: k, cyber: 0, physical: 0 }; if (d.category === "cyber" || d.category === "physical") buckets[k][d.category]++; }
      const devs = Object.values(p.sim.devices);
      return { installations: Object.keys(INSTALLATIONS).length, devices: devs.length, devices_online: devs.filter((d) => !p.engine.offline.has(d.id)).length,
        vendors: Object.keys(VENDORS).length, active_incidents: active.length, by_severity: sev, awaiting_review: incs.filter((i) => i.status === "new").length,
        awaiting_oem: incs.filter((i) => i.status === "awaiting_oem").length, open_tasks: Object.values(p.tasks).filter((t) => !["completed", "cancelled"].includes(t.status)).length,
        detections_total: p.detections.length, exposed_devices: new Set(exposure.flatMap((v) => v.affected_devices.map((x) => x.device_id))).size,
        trend: Object.values(buckets).slice(-20), sim_time_s: p.sim.t,
        recent_audit: p.audit.slice(-8).reverse().map((r) => ({ at: r.at, who: r.actor_name, action: r.action, resource: r.resource })) };
    }],
    ["GET", /^\/api\/live$/, (p, a, q) => {
      p.req(a, "view_fleet");
      const inst = q.installation || "inst-001", n = inst.slice(-3);
      const rows = {};
      for (const [name, dev, key] of [["charger_reported_w", "cha-" + n, "active_power_w"], ["meter_ev_circuit_w", "met-" + n, "ev_circuit_power_w"],
        ["battery_w", "bat-" + n, "active_power_w"], ["pv_w", "inv-" + n, "active_power_w"], ["grid_w", "met-" + n, "grid_power_w"],
        ["ev_soc", "cha-" + n, "soc_estimate_pct"], ["battery_soc", "bat-" + n, "soc_pct"]])
        for (const s of (p.hist[dev] || []).slice(-120)) { const ts = s.ts.slice(11, 19); (rows[ts] = rows[ts] || { ts })[name] = s.measurements[key] ?? null; }
      return { installation: inst, series: Object.values(rows).sort((x, y) => (x.ts < y.ts ? -1 : 1)),
        events: p.events.filter((e) => e.installation_id === inst).slice(-40).reverse().map((e) => ({ ts: e.ts, device_id: e.device_id, vendor: e.vendor, event_type: e.event_type, severity: e.severity, client_id: e.client_id, details: e.details })),
        detections: p.detections.filter((d) => d.installation_id === inst).slice(-30).reverse().map(det),
        devices: Object.values(p.sim.devices).filter((d) => d.installation_id === inst).map((d) => { const h = p.hist[d.id] || [];
          return { id: d.id, kind: d.kind, vendor: VENDORS[d.vendor].name, model: d.model, firmware: d.firmware, online: !p.engine.offline.has(d.id),
            last: h.length ? h[h.length - 1].measurements : {}, state: h.length ? h[h.length - 1].state : "unknown" }; }),
        installations: Object.entries(INSTALLATIONS).map(([id, v]) => ({ id, ...v })) };
    }],
    ["GET", /^\/api\/correlations$/, (p, a) => { p.req(a, "view_incidents_all");
      return p.corr.groups.slice(-60).reverse().filter((g) => !g.merged_into).map((g) => ({ id: g.id, kind: g.kind, status: g.status, severity: g.severity, score: g.score,
        installations: [...g.installations].sort(), devices: [...g.devices].sort(), vendors: [...g.vendors].sort(), hypotheses: g.hypotheses.slice(0, 3), links: g.links,
        confidence: g.confidence, incident: g.incident_id ? p.incidents[g.incident_id].code : null, promoted: !!g.incident_id,
        detections: g.detections.slice(-12).map(det), first_seen: p.ts(g.first_t), last_seen: p.ts(g.last_t) })); }],
    ["GET", /^\/api\/fleet$/, (p, a) => {
      const u = USERS[a]; let devs;
      if (p.can(a, "view_fleet")) devs = Object.values(p.sim.devices);
      else if (p.can(a, "vex")) devs = Object.values(p.sim.devices).filter((d) => d.vendor === ORGS[u.org].vendor);
      else throw deny("fleet");
      const full = p.can(a, "view_fleet");
      return { devices: devs.map((d) => ({ id: full ? d.id : "(withheld)", installation: full ? d.installation_id : "(withheld)", kind: d.kind, vendor: VENDORS[d.vendor].name,
        model: d.model, firmware: d.firmware, online: !p.engine.offline.has(d.id) })), vendors: Object.entries(VENDORS).map(([id, v]) => ({ id, ...v })) };
    }],
    ["GET", /^\/api\/supply-chain$/, (p, a) => {
      const u = USERS[a]; const own = p.can(a, "vex") ? ORGS[u.org].vendor : null;
      const sboms = [];
      for (const s of Object.values(p.sc.sboms)) {
        const vendor = (Object.values(p.sim.devices).find((d) => d.model === s.firmware_name) || {}).vendor;
        if (own && vendor !== own) continue;
        sboms.push({ firmware: s.firmware_name, version: s.firmware_version, file: s.filename, spec: s.spec_version, supplier: s.supplier,
          installed_devices: Object.values(p.sim.devices).filter((d) => d.model === s.firmware_name && d.firmware === s.firmware_version).length,
          components: s.components, applicability: p.sc.applicability(s.firmware_name, s.firmware_version) });
      }
      const exposure = p.sc.exposure(Object.values(p.sim.devices));
      if (!p.can(a, "view_fleet")) for (const v of exposure) v.affected_devices = v.affected_devices.filter((x) => own && sboms.some((s) => x.firmware.includes(s.firmware))).map((x) => ({ firmware: x.firmware, status: x.status }));
      return { sboms, vulnerabilities: exposure, can_decide: p.can(a, "vex") };
    }],
    ["POST", /^\/api\/supply-chain\/decision$/, (p, a, q, b) => { p.vex(a, b.vulnerability, b.model, b.version, b.status, b.note); return { ok: true }; }],
    ["POST", /^\/api\/supply-chain\/sbom$/, (p, a, q, b) => { if (!(p.can(a, "vex") || p.can(a, "triage"))) throw deny("sbom upload");
      const s = p.sc.addSbom(String(b.content || ""), String(b.filename || "upload.json").slice(0, 100));
      p.addAudit(a, "sbom.uploaded", "sbom", `${s.firmware_name} ${s.firmware_version}`, { components: s.components.length });
      return { firmware: s.firmware_name, version: s.firmware_version, components: s.components.length }; }],
    ["GET", /^\/api\/incidents$/, (p, a) => p.listIncidents(a)],
    ["GET", /^\/api\/incidents\/(\d+)$/, (p, a, q, b, id) => p.getIncident(a, +id)],
    ["POST", /^\/api\/incidents\/(\d+)\/status$/, (p, a, q, b, id) => p.transition(a, +id, b.status, b.reason || "")],
    ["POST", /^\/api\/incidents\/(\d+)\/notes$/, (p, a, q, b, id) => p.addNote(a, +id, b.body, b.visibility)],
    ["POST", /^\/api\/incidents\/(\d+)\/decision$/, (p, a, q, b, id) => p.decide(a, +id, b.decision, b.rationale || "")],
    ["POST", /^\/api\/incidents\/(\d+)\/tasks$/, (p, a, q, b, id) => p.createTask(a, +id, b.org, b.title, b.due_hours, b.evidence_ids || [], b.type)],
    ["POST", /^\/api\/incidents\/(\d+)\/share$/, (p, a, q, b, id) => p.share(a, +id, b.org, b.evidence_ids || [], b.purpose || "")],
    ["GET", /^\/api\/incidents\/(\d+)\/export$/, (p, a, q, b, id) => { const inc = p.getIncident(a, +id); p.addAudit(a, "incident.exported", "incident", inc.code, { format: "json" });
      return { export_note: "Observed facts = telemetry/events; detections = rule/model output; hypotheses = machine-ranked explanations; decisions = human records. Simulated data.", ...inc }; }],
    ["GET", /^\/api\/tasks$/, (p, a) => p.listTasks(a)],
    ["POST", /^\/api\/tasks\/(\d+)$/, (p, a, q, b, id) => p.updateTask(a, +id, b.status, b.comment)],
    ["GET", /^\/api\/orgs$/, () => Object.entries(ORGS).map(([id, o]) => ({ id, ...o }))],
    ["GET", /^\/api\/scenarios$/, (p) => ({ scenarios: Object.entries(SCENARIOS).map(([key, v]) => ({ key, name: v.name, description: v.description, expected: v.expected, duration: v.duration })),
      runs: p.runResults(), sim_time_s: p.sim.t })],
    ["POST", /^\/api\/scenarios\/(\w+)\/start$/, (p, a, q, b, key) => p.startScenario(a, key)],
    ["POST", /^\/api\/simulation\/reset$/, (p, a) => { p.req(a, "simulate"); p.resetState(); p.addAudit(a, "simulation.reset", "platform", "-", {}); return { ok: true }; }],
    ["GET", /^\/api\/audit$/, async (p, a) => { p.req(a, "audit"); return { chain_valid: await p.verifyAudit(), records: p.audit.slice(-200).reverse() }; }],
  ];

  const platform = new Platform(42);
  platform.step(90); // warm-up so charts have history on first view
  async function request(method, path, query, body, token) {
    const actor = TOKENS[token || ""];
    for (const [m, rx, fn, open] of ROUTES) {
      const mt = path.match(rx);
      if (m !== method || !mt) continue;
      try {
        if (!open && !actor) throw new HttpErr(401, "not signed in");
        const data = await fn(platform, actor, query || {}, body || {}, ...mt.slice(1));
        return { status: 200, data: JSON.parse(JSON.stringify(data)) };
      } catch (e) {
        return { status: e.status || 400, data: { error: e.message } };
      }
    }
    return { status: 404, data: { error: "no such route" } };
  }
  setInterval(() => { try { platform.step(1); } catch (e) { console.error(e); } }, 1000);
  globalThis.EnergyOSEngine = { request, platform };
})();
