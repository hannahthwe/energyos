# ConnectedCare AI

AI technical-support copilot for connected home energy (HEMS, bidirectional EV charging, vehicle-to-home).
Pitch MVP for the fictional HEMS manufacturer **HelioHEMS**. **All data is synthetic.**

| Part | What it is | Where |
|---|---|---|
| Homeowner app | Native **iOS app** (SwiftUI): home energy overview, Savings card, AI assistant, Data & privacy, consent-based support handoff | `ios/` |
| Homeowner app (web) | The same homeowner app in the browser, for demos without Xcode or on any phone | `web/` → `/app` |
| Support dashboard | Web console for HelioHEMS support staff: case queue, AI investigation, copilot, workflow | `web/` → `/support` |
| Backend | FastAPI + SQLite: unified data model, 5 specialist agents (18 deterministic tools), orchestrator, Claude integration, Data Act consent manager | `backend/` |

How the apps connect, the full API and the backend internals: **[docs/TECHNICAL.md](docs/TECHNICAL.md)**.

## Demo day: quick start

You need macOS (or Linux) with **Python 3.10+** and **Node.js 18+**. Everything runs from one script:

```bash
cd connectedcare
./start-demo.sh
```

The first run sets up Python and the web packages (a few minutes). After that it starts in seconds. The script:
1. creates `backend/.venv` and installs the Python packages, if missing;
2. installs `web/node_modules`, if missing;
3. builds the web app, if the code changed;
4. resets the demo data and starts the backend on port 8000, then prints the links.

| Open this | What it is |
|---|---|
| http://localhost:8000/support | Support dashboard (for HelioHEMS staff) |
| http://localhost:8000/app | Homeowner app in the browser |
| http://localhost:8000/docs | Interactive API docs |
| `http://<your Mac's Wi-Fi IP>:8000/app` | Homeowner app in Safari on your iPhone (same Wi-Fi). The script prints the address |

- **Stop:** press `Ctrl+C` in that terminal.
- **Keep earlier support cases** instead of starting fresh: `./start-demo.sh --keep`
- **Presentation mode** (hides agent/tool panels, raw evidence IDs, the data-model panel, the model name and `/docs`): `CONNECTEDCARE_DEMO_MODE=1 ./start-demo.sh`
- **Use Claude** instead of offline rules: run `export ANTHROPIC_API_KEY=sk-ant-...` first, then `./start-demo.sh`. The dashboard header shows which mode is on.
- **Phone shows "can't connect":** check that the phone and Mac are on the same Wi-Fi, the script is still running, and the macOS firewall isn't blocking Python (System Settings → Network → Firewall).
- **"Address already in use":** something is already on port 8000. Stop it, or run `lsof -ti :8000 | xargs kill`.

**Before you present:** start the script, open `/support` on the laptop and `/app` on the phone (or the iOS app), open `http://localhost:8000/support?scenario=A` once to start on scenario **A**, and follow the [demo script](#4-demo-script-35-minutes) below. Use two screens: the phone is the homeowner, the laptop is support.

**Switching scenarios (presenter only):** there is no scenario selector on screen, so the apps look like a real product. Switch with **Shift+Alt+A … E** on any page (web), or open any page with `?scenario=D` added to the link. In the iOS app it's under the gear icon → *Presenter: scenario*.

The sections below describe each part on its own, for development.

```
iOS app ──┐                          ┌─ Technical Diagnostics Agent ─┐
          ├─► FastAPI ─► Orchestrator ├─ Energy Behaviour Agent ──────┤─► deterministic tools ─► canonical model ◄─ adapters ◄─ CRM
Web  ─────┘   (role       (Claude or  ├─ Security Correlation Agent ──┤   (consent-filtered)                           ◄─ HEMS backend
              enforced)    offline)   ├─ Support Resolution Agent ────┤                                                ◄─ Charger API
                                      └─ Grid & Tariff Agent ─────────┘                                                ◄─ KB + audit log
                                                                                                                        ◄─ Energy supplier
                                                                                                                        ◄─ Market prices
```

## 1. Run the backend (needed by both apps)

```bash
cd connectedcare/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` lets an iPhone on the same Wi-Fi reach it. Add `CONNECTEDCARE_RESET=1` in front to wipe cases and reseed.

**AI mode.** With no API key it runs fully offline with deterministic rules. To use Claude:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Claude (`claude-opus-5-5`, override with `CONNECTEDCARE_MODEL`) then picks which agents to run, through tool calling, and writes the explanation. Both modes use the same agents, tools and data. Every Claude answer is checked: evidence IDs it didn't receive from an agent are dropped, a homeowner answer that leaks internal detail is rejected, and on any error the app falls back to offline mode. The refusal fallback (`fallbacks: "default"`) is enabled.

## 2. Support dashboard (web)

```bash
cd connectedcare/web
npm install
npm run build      # the backend then serves it at http://localhost:8000/support
# or, for development with hot reload: npm run dev  ->  http://localhost:5173/support
```

## 3. iOS homeowner app

Requires a Mac with **Xcode 15 or later** (free from the Mac App Store). iOS 16+.

**Simulator**
1. Open `connectedcare/ios/ConnectedCare.xcodeproj` in Xcode.
2. Pick an iPhone simulator and press **Run** (⌘R). The app connects to `http://localhost:8000`.

**Your own iPhone**
1. In Xcode go to *Settings → Accounts* and sign in with your Apple ID (a free account works).
2. Select the **ConnectedCare** target → *Signing & Capabilities* → tick *Automatically manage signing* and pick your *Team*. If Xcode reports that the bundle ID is taken, change `com.example.connectedcare` to something unique, such as `com.yourname.connectedcare`.
3. Connect the iPhone by cable and unlock it. Tap *Trust* on the phone. On iOS 16+, turn on *Settings → Privacy & Security → Developer Mode* and restart the phone.
4. Choose your iPhone as the run destination and press **Run**.
5. The first time, the phone may block the app: go to *Settings → General → VPN & Device Management*, tap your Apple ID and choose *Trust*.
6. In the app, tap **Demo → Server settings…** and enter your Mac's Wi-Fi address, e.g. `http://192.168.1.20:8000`. To find it, go to *System Settings → Wi-Fi → Details → IP address*. Allow *Local Network* access when iOS asks.

With a free Apple ID the app stays installed for 7 days; run it from Xcode again to renew. A paid Apple Developer account ($99/year) gives 1-year signing and TestFlight for sharing with others.

## 4. Demo script (3–5 minutes)

1. **iPhone → Home** (iOS app, or `/app` in Safari). SOC ring, reserve marker, V2H status, charger online. Make sure scenario **A** is active (Shift+Alt+A on the laptop, or the gear icon in iOS).
2. **Assistant:** "Why didn't my EV discharge last night?" The answer explains that the car was below the 40% reserve and that nothing is broken. The answer comes from the home's records. No support needed.
3. Switch to scenario **B** and ask again. The charger rejected the request, and the app says plainly that the reason isn't visible. Tap **Request support**. The consent screen lists exactly which records will be shared. Approve it.
4. **Web `/support`:** the new case appears in the queue with its CRM profile, installation, devices and history. Open the **AI investigation**.
5. Walk through the **agent cards**: what each one checked, its finding, its evidence and what it couldn't see. Note that Security was *not used*, and why.
6. Show the **unified timeline** (HEMS + charger API on one UTC clock) and the expected-vs-measured power chart.
7. Show the **Data sources → unified data model** panel: CRM customer → commissioning scan → HEMS asset → charger ID. A neighbour's charger with a near-identical nickname was **rejected** as a match.
8. Ask the **copilot**: "Which system rejected the command?", then "Was there a configuration change?" (this runs the Security agent live).
9. Click **Prepare escalation**: a Voltara package with no restricted records. Then **Draft response** and set the status.
10. Optional: scenario **C**. An unregistered API client raised the reserve 13 minutes before the discharge window. The AI flags it for human review and does **not** call it an attack.
11. Optional: scenario **D**. Ask "Why did my home battery pause last night?" The battery was paused 18:00–19:00 because the grid operator asked the supplier to limit it (grid congestion). The app says it's grid steering, not a fault, and that the household is compensated. In `/support` the steering start and end are on the timeline.
12. Optional: scenario **E**. The **Savings** card shows €13.65 paid this week vs €7.53 possible. Ask "Why was my charging so expensive last night?": €8.15 paid at the 18:00 peak, €4.54 in the cheapest hours (02:00, 03:00, 04:00), car still ready by 07:00.
13. **Data & privacy** (lock icon): turn off *Energy supplier* and ask again. The answer now says the supplier data isn't available. Turn it back on. The support case page shows which sources are consented.

| Scenario | What happened | Likely cause |
|---|---|---|
| A | EV at 35%, reserve 40% | `soc_reserve` (no fault) |
| B | Charger rejected the V2H session | `charger_rejection` (escalate to Voltara) |
| C | Reserve raised by an unregistered API client | `config_change_raised_reserve` (human review) |
| D | Home battery paused 18:00–19:00 by grid steering | `grid_steering` (no fault, compensated) |
| E | Dynamic tariff, EV charged at the evening peak | `peak_charging` (€ paid vs € possible) |

## Tests

```bash
cd connectedcare/backend && .venv/bin/python -m pytest -q
cd connectedcare/web && npm run typecheck
```

36 backend tests cover: canonical mapping, units and signs (incl. supplier ct/kWh, market EUR/MWh, local time → UTC); device and supply-point identity via verified CRM records (rejecting the decoy and serial mismatches); diagnoses for all 5 scenarios, offline and with a scripted Claude; the savings calculation; consent revoke → "unavailable" answers; that no tool writes to any table; demo mode; evidence references; disclosure of missing third-party data; homeowner visibility (in the orchestrator and the API); consent-gated case creation; persistence across sessions and scenario switches; and the Claude tool loop with a scripted client (evidence validation, leak rejection, fallback).

## Key design points

- **Unified data model** (`backend/app/canonical.py`): Customer, Installation, Device, SourceDeviceMapping, EnergyMeasurement, EnergySchedule, OperatingConstraint, DeviceCommand, ChargingSession, DiagnosticEvent, TariffContract, PriceSeries, GridSteeringEvent, EvidenceRecord, SupportCase, SupportMessage, ConfigurationChange, ConsentRecord. Every record keeps `source_system` and `source_record_id`.
- **Source-native schemas** (`backend/app/sources.py`): the CRM uses `crm_customer_ref`/`site_ref`; the HEMS uses `home_uid`/`asset_key`/`requested_watts` and epoch times; the charger API uses `chargepoint_ref`/`session_state`/`power_kw` with + = export. Adapters (`adapters.py`) normalise units (kW), signs (+ = draws, − = supplies the home) and time (UTC).
- **Identity** comes only from the installer's commissioning scan, with serial numbers cross-checked across all sources. Names are never used.
- **Visibility** (`visibility.py`) is enforced on the server. Evidence is `homeowner_safe`, `support_internal` or `restricted` (security audit). The homeowner API only ever returns homeowner-safe data, and Claude in homeowner mode only receives filtered agent output.
- **Consent** (`consents.py`): each source has a data-access consent (EU Data Act user-designated recipient, or contract). Without active consent, tools return no rows and answers say the data is unavailable.
- **Savings are calculated, not generated:** `compare_actual_vs_cheapest` works out € paid vs the cheapest hours before departure from meter values, the contract and day-ahead prices. Claude only explains the result.
- **Charger limits:** HelioHEMS only sees what Voltara's authorised integration exposes. The system says so instead of guessing.

## Not built (by design)

Multi-tenant OEMs, SSO (a demo role header is used instead), billing, real device control or cloud connections (nothing sends commands to devices, including smart-charging changes), B2C app, analytics. iOS signing needs your own Apple ID.
