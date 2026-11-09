# Sentiwatt — Product Specification (v0.1)

> **Working name.** "Sentiwatt" (sentinel + sentient + watt) is a placeholder until trademark checks are done. Replace it everywhere when the final name is chosen.
>
> **One line:** Sentiwatt is the cybersecurity layer for AI-enabled home energy management — from the solar roof to the car in the driveway. It runs **alongside** the home's energy management system (HEMS), never instead of it.

---

## 1. What we are building — and what we are not

| We build | We do not build |
|---|---|
| A security product that **watches** the AI energy manager and every device it controls | An energy management system or optimiser (that is our partners' product) |
| Detection, correlation, exposure tracing and incident coordination | Autonomous device control or automatic "kill switches" |
| Evidence that **supports** CRA reporting | Automatic CRA compliance or automatic filing |
| Integrations into tools customers already use | A replacement for their HEMS, SIEM or ticketing system |

**Scope of protected assets:** PV inverters, home batteries, heat pumps, EV chargers (one-way and bidirectional / V2H), electric vehicles (via the charger), smart meters (P1), the HEMS gateway, and the **AI energy manager's decisions**.
**Lead use case:** V2H — the highest-stakes command type (one command moves large amounts of energy and affects mobility, battery health and grid export).

---

## 2. Why this product

1. **Nobody owns the cross-brand incident.** A Dutch home mixes an EV, charger, inverter, battery and heat pump from different brands. Each vendor (Samsung SmartThings, LG ThinQ, Hyundai/Kia, charger and inverter OEMs) secures only its own ecosystem.
2. **Security tools don't understand energy physics.** A SIEM sees "valid authenticated command". Sentiwatt asks "does this command make physical and economic sense for this home right now?" — which separates **attack vs fault vs misconfiguration**.
3. **The AI energy manager is a new attack surface.** A correctly coded AI can be tricked by manipulated price, forecast or grid-signal inputs into harmful but authenticated actions.
4. **Companies won't pool raw data.** Hierarchical federated learning (the founder's PhD work) trains detectors at each vendor/HEMS and shares only model updates.
5. **Regulation now demands speed.** The EU CRA requires manufacturers to give early warning of actively exploited vulnerabilities within 24 hours — which requires knowing *which deployed homes are affected* immediately.
6. **Evidence of real-world impact:** supermarket refrigeration hacks across Europe (Oct–Nov 2026), coordinated attack on Polish wind/solar sites (Dec 2025), AI-orchestrated intrusions (2025–2026). See deck/business plan references.

---

## 3. Users (personas)

| Persona | Organisation | Job to be done | Main screens |
|---|---|---|---|
| **Ops / SOC analyst** | HEMS platform, MSSP | Triage alerts, investigate incidents across vendors, decide action | Alert queue, Case view, Timeline, Home view |
| **Product-security (PSIRT) engineer** | Charger / inverter / battery OEM | Know which deployed devices an advisory affects; coordinate patching; prepare CRA evidence | Exposure view, Advisory view, Case collaboration, CRA export |
| **Security / platform admin** | Any customer | Connect data sources, set policies, manage users and sharing | Integrations, Policies, Users & roles |
| **Manager / CISO** | Any customer | See risk posture and KPIs | Dashboard, Reports |
| **Homeowner** (indirect) | Via partner app | Understand an alert in plain language and confirm/deny an unusual action | Partner app notification (white-label API) |

---

## 4. Product components

```
┌──────────────────────────────────────────────────────────────────────┐
│  SENTIWATT CONSOLE (web app)  · Alerts · Cases · Exposure · Reports   │
├──────────────────────────────────────────────────────────────────────┤
│  SENTIWATT CLOUD (multi-tenant, EU)                                   │
│  Asset graph · Detection engine · Correlation engine · Trace engine   │
│  Case management · Copilot · FedWatt FL server · Policy engine         │
├──────────────────────────────────────────────────────────────────────┤
│  SENTIWATT CONNECT  · REST/GraphQL API · Webhooks · SIEM/ticket export │
├──────────────────────────────────────────────────────────────────────┤
│  SENTIWATT EDGE AGENT (container on HEMS gateway or in vendor cloud)  │
│  Protocol collectors · Command risk scoring · FL client · Signed logs  │
├──────────────────────────────────────────────────────────────────────┤
│  DEVICES & DATA: OCPP · ISO 15118 · EEBus · SunSpec Modbus · P1 · MQTT │
│  External: ENTSO-E prices · weather · NVD · CISA KEV · vendor CSAF     │
└──────────────────────────────────────────────────────────────────────┘
```

1. **Edge Agent** — collects device/command data locally, scores commands in real time, buffers when offline, runs the federated-learning client, signs logs.
2. **Cloud** — the brain: asset graph, detection, correlation, trace, cases.
3. **Console** — the web app for analysts, PSIRT engineers and admins.
4. **Connect** — APIs and integrations into existing tools.
5. **FedWatt** — the federated-learning engine (can be marketed as "powered by FedWatt").
6. **Lab** (optional product) — attack-simulation testbed for OEMs to test their devices and train detectors.

---

## 5. Features and functions

Priority: **P0** = MVP / course prototype · **P1** = first paid pilot · **P2** = product after pilot.

### 5.0 Foundation — assets and data

| ID | Feature | Functions | Priority |
|---|---|---|---|
| F-01 | **Asset inventory & digital twin** | Register homes, devices (type, vendor, model, serial, firmware, protocol), the AI EMS, and owners; auto-discover from connectors | P0 |
| F-02 | **Asset graph** | Relationships: home → gateway → devices; device → firmware → components (SBOM); device → responsible vendor | P0 |
| F-03 | **Data connectors** | OCPP 2.0.1 / 2.1 (incl. `SecurityEventNotification`, `SetChargingProfile`, transactions), SunSpec Modbus, P1 smart meter, MQTT, HEMS REST APIs, syslog/auth logs; later EEBus, ISO 15118-20 session data | P0 (OCPP, Modbus, P1, MQTT) / P1 (others) |
| F-04 | **Normalised event schema** | One schema for 4 event classes: *cyber* (login, config change, firmware update), *physical* (power, SOC, temperature), *command* (who sent what to which device), *context* (price, forecast, grid limit, user schedule) | P0 |
| F-05 | **External context feeds** | ENTSO-E day-ahead prices, weather/solar forecast, grid-operator limits | P0 (prices) / P1 |
| F-06 | **Tamper-evident logging** | Signed, time-synchronised event logs at the edge for forensic use | P1 |

### 5.1 Detect

| ID | Feature | Functions | Priority |
|---|---|---|---|
| D-01 | **Physics consistency checks** | Energy balance per home (PV + import + battery + V2H = load + export); SOC change vs reported power; impossible values; device state vs command | P0 |
| D-02 | **Command risk scoring** | Score every control command on 4 checks: authentic? within policy? explainable by the AI EMS? seen across the fleet? → Low / Elevated / High | P0 |
| D-03 | **AI EMS decision monitoring** | Expected-behaviour model replays the EMS objective (tariff, SOC, departure time) and flags deviations | P1 |
| D-04 | **Input-integrity checks** | Cross-check price/forecast/grid signals against independent sources; detect manipulated or spoofed inputs | P1 |
| D-05 | **Cyber detections** | New location/IP login, brute force, off-hours admin action, firmware change outside window, config drift, OCPP security events, protocol anomalies | P0 (5 rules) / P1 |
| D-06 | **Behavioural anomaly models** | Per-home and per-device baselines; unsupervised models (e.g. isolation forest, autoencoder) | P1 |
| D-07 | **Benign-fault classifier** | Recognise communication loss, sensor faults, misconfiguration to cut false alarms | P1 |
| D-08 | **Alert management** | Severity + confidence, deduplication, suppression rules, feedback ("true / false positive") to improve models | P0 |
| D-09 | **Policy engine** | Customer-defined rules: min SOC, departure times, max export, allowed command sources and hours | P0 |

### 5.2 Correlate (core differentiator)

| ID | Feature | Functions | Priority |
|---|---|---|---|
| C-01 | **Case builder** | Group related alerts across devices, vendors and time windows into one case | P0 |
| C-02 | **Timeline reconstruction** | Ordered cross-vendor timeline of cyber, physical, command and context events | P0 |
| C-03 | **Hypothesis ranking** | Rank *attack / misconfiguration / fault* with evidence for and against each | P0 (rule-based) / P1 (ML) |
| C-04 | **Fleet campaign detection** | Same pattern across N homes, the same firmware or the same vendor → fleet-level case | P1 |
| C-05 | **FedWatt federated learning** | Hierarchical FL: local training at edge/vendor, aggregation per HEMS/region, global model; only model updates leave each party; secure aggregation | P1 (2 silos) / P2 |
| C-06 | **Explainability** | For every score: which signals contributed and why, in plain language | P0 |

### 5.3 Trace (supply-chain exposure)

| ID | Feature | Functions | Priority |
|---|---|---|---|
| T-01 | **SBOM & firmware registry** | Import CycloneDX / SPDX SBOMs per firmware version; link to deployed devices | P1 |
| T-02 | **Advisory ingestion** | NVD API, CISA KEV, vendor CSAF/VEX, national CERT feeds | P0 (NVD + KEV) / P1 |
| T-03 | **Applicability matching** | Advisory → component → firmware → deployed homes, with states: *affected / not affected / under investigation / unknown* | P1 |
| T-04 | **Blast-radius view** | Count and list affected homes, which functions are at risk (e.g. discharge control), which vendors must act | P1 |
| T-05 | **Prioritisation** | Rank by known exploitation (KEV), exposure, function criticality, fleet size | P1 |
| T-06 | **Patch rollout tracking** | Track % of affected homes updated over time | P2 |

### 5.4 Coordinate

| ID | Feature | Functions | Priority |
|---|---|---|---|
| K-01 | **Case management** | Owner, status, severity, SLA timers, comments, attachments | P0 |
| K-02 | **Vendor assignment & secure sharing** | Share a case (or a redacted part) with another organisation; role-based access per case | P1 |
| K-03 | **Playbooks** | Recommended human steps per incident type; checklists | P1 |
| K-04 | **Notifications** | Email, Slack/Teams, webhooks; homeowner message via partner app API | P0 (email/webhook) / P1 |
| K-05 | **CRA evidence package** | Draft 24-hour early warning, 72-hour notification and final report fields from case data; signed export; **human submits** | P1 |
| K-06 | **Audit trail** | Every view, edit, share and export logged | P0 |

### 5.5 Copilot (LLM assistant)

| ID | Feature | Functions | Priority |
|---|---|---|---|
| A-01 | **Case summary** | Plain-language narrative of what happened, with citations to events | P1 |
| A-02 | **Next-step suggestions** | "What to check next" based on hypotheses and playbooks | P2 |
| A-03 | **Natural-language queries** | "Which homes discharged between 01:00 and 03:00 with firmware 2.3?" | P2 |
| Rule | The LLM **never** sets risk scores or sends device commands; it only summarises evidence for humans | — |

### 5.6 Integrations (Connect)

| ID | Feature | Functions | Priority |
|---|---|---|---|
| I-01 | **Public API** | REST (+ later GraphQL) for assets, events, alerts, cases | P0 |
| I-02 | **Webhooks** | Push alerts and case updates | P0 |
| I-03 | **SIEM export** | Syslog/CEF, JSON; STIX/TAXII for threat intel; connectors for Splunk, Microsoft Sentinel, Elastic | P1 |
| I-04 | **Ticketing** | Jira, ServiceNow create/update with evidence | P1 |
| I-05 | **White-label homeowner alerts** | API for partner apps | P1 |

### 5.7 Dashboards and reports

| ID | Feature | Functions | Priority |
|---|---|---|---|
| R-01 | **Operations dashboard** | Open alerts/cases, severity mix, mean time to triage, top affected device types | P0 |
| R-02 | **Exposure dashboard** | Affected homes per advisory, patch progress | P1 |
| R-03 | **Pilot KPI report** | Triage time vs baseline, grouping accuracy, false-positive and miss rates | P0 |

### 5.8 Platform, security and privacy (non-negotiable for a security company)

| ID | Requirement |
|---|---|
| S-01 | Multi-tenant with strict tenant isolation; EU data residency |
| S-02 | SSO (OIDC/SAML), MFA, role-based access control |
| S-03 | Encryption in transit (TLS 1.3) and at rest; key management |
| S-04 | Data minimisation, configurable retention, GDPR consent tracking for household data |
| S-05 | Secure SDLC, dependency scanning, our own SBOM, external pen-test before production |
| S-06 | Deployment options: EU cloud SaaS, customer-hosted, edge container |

### 5.9 Non-functional targets (to validate)

| Metric | Target |
|---|---|
| Command risk score at edge | < 200 ms added latency |
| Alert visible in console | < 60 s from event |
| Scale (pilot → product) | 5,000 → 100,000 homes per tenant |
| Edge offline buffer | ≥ 72 h |
| Availability (production) | 99.9% |

---

## 6. Key user flows

**Flow A — Suspicious V2H discharge (HEMS analyst)**
1. Edge agent receives a discharge command at 02:13 → D-02 scores it **High** (outside policy, contradicts tariff and departure time, new login 4 min earlier).
2. Alert appears; C-01 groups it with the login alert and the inverter export spike into **one case**.
3. Analyst opens the case: C-02 timeline, C-03 hypotheses (attack 72% / misconfiguration 20% / fault 8%) with evidence.
4. T-03 shows the charger's firmware matches an advisory; T-04 shows 1,240 other homes on that firmware.
5. Analyst assigns the case to the charger OEM via K-02; homeowner is notified through the partner app (K-04).
6. OEM PSIRT exports a CRA evidence draft (K-05) and submits it themselves.

**Flow B — New advisory (OEM PSIRT engineer)**
1. T-02 ingests a new advisory → T-03 matches it to firmware 2.3.x → T-04: 8,400 affected homes, function at risk: discharge control.
2. T-05 marks it urgent (listed in CISA KEV) → engineer opens a fleet case, tracks patch rollout (T-06).

**Flow C — Manipulated price feed (AI EMS attack)**
1. D-04 sees the price feed for one HEMS diverge from ENTSO-E → D-03 sees batteries and EVs across 300 homes discharging against expected behaviour.
2. C-04 raises a fleet campaign case; analyst switches the HEMS to a trusted price source (human action, outside Sentiwatt).

---

## 7. MVP — what to build first (course prototype → pilot demo)

**Goal:** show end-to-end *event → detection → one correlated case → affected assets → human action* on a simulated multi-vendor home fleet, with honest metrics.

**Testbed (simulated)**
- 20–100 simulated homes, each with: OCPP charger (V2H-capable), SunSpec inverter, battery, P1 meter, simple AI EMS (rule-based or MPC scheduler using ENTSO-E prices).
- Attack injector: (1) spoofed discharge command after credential misuse, (2) manipulated price feed, (3) firmware advisory affecting part of the fleet.
- Benign faults: (4) communication loss, (5) misconfigured schedule.

**MVP features (all P0):** F-01–F-05, D-01, D-02, D-05 (5 rules), D-08, D-09, C-01, C-02, C-03 (rules), C-06, T-02 (NVD + KEV), K-01, K-04 (email/webhook), K-06, I-01, I-02, R-01, R-03.

**Success metrics for the demo**
- All 3 attack scenarios grouped into the correct case; both benign faults not labelled as attacks.
- Time from first event to assembled case < 60 s.
- False-positive rate and missed detections reported honestly.
- Comparison: cyber-only rules vs cyber + physical (shows the value of D-01/D-02).

---

## 8. Suggested technology stack

| Layer | Suggestion | Why |
|---|---|---|
| Backend API | Python + FastAPI | Fast to build; same language as ML |
| Event streaming | MQTT broker (Mosquitto/EMQX) → Redpanda/Kafka | IoT-native ingest; scalable stream |
| Time-series + relational | PostgreSQL + TimescaleDB | One database for events and cases |
| Asset graph | PostgreSQL (recursive queries) → Neo4j later | Start simple |
| Protocols | `ocpp` (Python, Mobility House), `pymodbus`, DSMR P1 parser | Mature open-source libraries |
| ML | scikit-learn, PyTorch | Anomaly models, classifiers |
| Federated learning | Flower (`flwr`) | Supports hierarchical/custom strategies |
| SBOM / advisories | CycloneDX tools, NVD API 2.0, CISA KEV JSON | Standard formats |
| LLM copilot | Claude via API, retrieval over case data | Summaries with citations |
| Frontend | React + TypeScript; charts for timelines | Analyst console |
| Edge agent | Python or Go in a Docker container | Runs on gateway or in vendor cloud |
| Infra | Docker Compose (MVP) → Kubernetes, EU region | Simple first, scalable later |

---

## 9. Build roadmap (aligned with the business plan)

| Phase | Duration | Deliverable |
|---|---|---|
| 0 | 2–4 weeks | Testbed + ingestion + D-01/D-02/D-05 + case builder + basic console (course demo) |
| 1 | months 1–3 | Hypothesis ranking, policies, NVD/KEV matching, pilot KPI report; discovery interviews |
| 2 | months 3–6 | Paid pilot on real partner data: 2–3 device types, P1 features, first FedWatt training across 2 silos |
| 3 | months 6–12 | Trace module (SBOM, applicability, blast radius), secure case sharing, CRA evidence export, SIEM/Jira connectors |
| 4 | months 12–24 | Multi-tenant production, fleet campaigns, copilot, BE/DE expansion |

---

## 10. Open questions to resolve

1. Which design partner and which 2–3 device types first?
2. Where does the edge agent run in the first pilot — on the gateway or in the HEMS cloud?
3. What data-sharing agreement and consent model does the partner require?
4. Which incident classes matter most to the partner's support and security teams?
5. Final product name after trademark checks.
