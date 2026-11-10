# ConnectedCare AI

AI technical-support copilot for connected home energy (HEMS, bidirectional EV charging, vehicle-to-home).
Pitch MVP for the fictional HEMS manufacturer **HelioHEMS**. **All data is synthetic.**

| Part | What it is | Where |
|---|---|---|
| Homeowner app | Native **iOS app** (SwiftUI): home energy overview, AI assistant, consent-based support handoff | `ios/` |
| Support dashboard | Web console for HelioHEMS support staff: case queue, AI investigation, copilot, workflow | `web/` → `/support` |
| Backend | FastAPI + SQLite: unified data model, 4 specialist agents, orchestrator, Claude integration | `backend/` |

```
iOS app ──┐                          ┌─ Technical Diagnostics Agent ─┐
          ├─► FastAPI ─► Orchestrator ├─ Energy Behaviour Agent ──────┤─► deterministic tools ─► canonical model ◄─ adapters ◄─ CRM
Web  ─────┘   (role       (Claude or  ├─ Security Correlation Agent ──┤                                                ◄─ HEMS backend
              enforced)    offline)   └─ Support Resolution Agent ────┘                                                ◄─ Charger API
                                                                                                                        ◄─ KB + audit log
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

1. **iPhone → Home.** SOC ring, reserve marker, V2H status, charger online. Tap *Demo* and pick scenario **A**.
2. **Assistant:** "Why didn't my EV discharge last night?" The answer explains that the car was below the 40% reserve and that nothing is broken. The answer comes from the home's records. No support needed.
3. Switch to scenario **B** and ask again. The charger rejected the request, and the app says plainly that the reason isn't visible. Tap **Request support**. The consent screen lists exactly which records will be shared. Approve it.
4. **Web `/support`:** the new case appears in the queue with its CRM profile, installation, devices and history. Open the **AI investigation**.
5. Walk through the **four agent cards**: what each one checked, its finding, its evidence and what it couldn't see. Note that Security was *not used*, and why.
6. Show the **unified timeline** (HEMS + charger API on one UTC clock) and the expected-vs-measured power chart.
7. Show the **Data sources → unified data model** panel: CRM customer → commissioning scan → HEMS asset → charger ID. A neighbour's charger with a near-identical nickname was **rejected** as a match.
8. Ask the **copilot**: "Which system rejected the command?", then "Was there a configuration change?" (this runs the Security agent live).
9. Click **Prepare escalation**: a Voltara package with no restricted records. Then **Draft response** and set the status.
10. Optional: scenario **C**. An unregistered API client raised the reserve 13 minutes before the discharge window. The AI flags it for human review and does **not** call it an attack.

## Tests

```bash
cd connectedcare/backend && .venv/bin/python -m pytest -q
cd connectedcare/web && npm run typecheck
```

19 backend tests cover: canonical mapping, units and signs; device identity via commissioning (rejecting the decoy and serial mismatches); diagnoses for all 3 scenarios; evidence references; disclosure of missing third-party data; homeowner visibility (in the orchestrator and the API); consent-gated case creation; persistence across sessions and scenario switches; and the Claude tool loop with a scripted client (evidence validation, leak rejection, fallback).

## Key design points

- **Unified data model** (`backend/app/canonical.py`): Customer, Installation, Device, SourceDeviceMapping, EnergyMeasurement, EnergySchedule, OperatingConstraint, DeviceCommand, ChargingSession, DiagnosticEvent, EvidenceRecord, SupportCase, SupportMessage, ConfigurationChange, ConsentRecord. Every record keeps `source_system` and `source_record_id`.
- **Source-native schemas** (`backend/app/sources.py`): the CRM uses `crm_customer_ref`/`site_ref`; the HEMS uses `home_uid`/`asset_key`/`requested_watts` and epoch times; the charger API uses `chargepoint_ref`/`session_state`/`power_kw` with + = export. Adapters (`adapters.py`) normalise units (kW), signs (+ = draws, − = supplies the home) and time (UTC).
- **Identity** comes only from the installer's commissioning scan, with serial numbers cross-checked across all sources. Names are never used.
- **Visibility** (`visibility.py`) is enforced on the server. Evidence is `homeowner_safe`, `support_internal` or `restricted` (security audit). The homeowner API only ever returns homeowner-safe data, and Claude in homeowner mode only receives filtered agent output.
- **Charger limits:** HelioHEMS only sees what Voltara's authorised integration exposes. The system says so instead of guessing.

## Not built (by design)

Multi-tenant OEMs, SSO (a demo role header is used instead), billing, real device control or cloud connections, B2C app, analytics. iOS signing needs your own Apple ID.
