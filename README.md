# EnergyOS Secure — pitch prototype

AI-assisted cyber-physical security and multi-vendor incident coordination for connected home energy
(EV/V2H chargers, solar, batteries, HEMS gateways, smart meters).

**Detect → Correlate → Trace → Assess impact → Human review → Coordinate → Audit**

All vendors, devices and incidents are simulated. `HYPO-*` vulnerabilities are hypothetical.

## Run it

**No install (pitch):** open `dist/energyos-pitch.html` in a browser. The whole engine runs in the page.

**Python backend:**
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python run.py          # http://localhost:8000
```
**Docker:** `docker build -t energyos . && docker run -p 8000:8000 energyos`

**Tests:** `.venv/bin/pip install pytest && .venv/bin/python -m pytest -q tests`

## Demo flow (2 minutes)
1. As **May (analyst)**, click *Run scenario* on the Overview (suspicious V2H discharge).
2. *Live monitoring*: gateway brute-force → −9 kW discharge command → charger and independent meter both show the discharge.
3. Open the incident: competing hypotheses, cyber→physical link, impact, firmware exposure.
4. Tick evidence items → *Assign task + share selected* to VoltCharge.
5. Switch identity to **Andy (VoltCharge)**: Andy sees only the shared items; acknowledge the task.
6. Back as May: confirm attack, move to resolved, close. Check the *Audit log* (hash-chained).

## What each feature does
| Feature | What it does |
|---|---|
| Fleet simulator | 4 homes × 5 devices from 5 fictitious vendors, physically consistent power/SOC (sign: + draws from home bus, − injects). |
| Scenarios | 7 repeatable scenarios: normal, legit schedule change, suspicious V2H, comm failure, meter mismatch, sensor fault, fleet probe on vulnerable firmware. |
| Detection | Explainable rules (brute force, unknown client, unexpected discharge, gateway bypass, API probe, meter mismatch, implausible values, silence) + rolling-baseline statistics. Every finding names its rule and evidence. |
| Correlation | Groups findings by installation and time window, links commands to measured power changes, merges fleet patterns that share firmware, ranks competing hypotheses (attack, legit use, comm fault, sensor fault, firmware defect…). |
| Supply chain | Parses CycloneDX SBOMs, maps components to vulnerabilities by version, shows deployed exposure; OEMs confirm or rule out impact. |
| Incidents | Evidence timeline, impact, recommendations, status workflow, notes, export. Humans confirm attacks and close incidents. No automated device control. |
| Coordination | Assign tasks to OEMs/operators, share selected evidence only, acknowledgements, due dates, overdue flags. |
| Roles | Analyst, HEMS operator, OEM (×2), executive. Access enforced in the backend; switch identity in the top bar. |
| Audit | Tamper-evident SHA-256 hash chain of every human and system action. |
| Simulation lab | Expected vs actual per run: rules fired, incidents, top hypothesis, match with ground truth. |

## Layout
- `energyos/` – Python engine (simulator, scenarios, detection, correlation, supply chain, workflow, API router)
- `web/index.html` – dashboard; `web/engine.js` – same engine in JavaScript for the no-backend build
- `scripts/build_pitch.py` – builds `dist/energyos-pitch.html`
- `tests/` – end-to-end acceptance tests

Prototype limits: in-memory state (resets on restart), demo identity switcher instead of real login, rules + statistics (no trained ML model yet).
