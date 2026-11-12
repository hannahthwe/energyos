import Charts
import SwiftUI

struct HomeView: View {
    @EnvironmentObject var app: AppState
    @State private var showPrivacy = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 14) {
                    if let err = app.errorMessage {
                        ErrorBanner(text: err) { Task { await app.refresh() } }
                    }
                    if let o = app.overview {
                        header(o)
                        evCard(o)
                        v2hCard(o)
                        if let s = app.savings { SavingsCard(savings: s) { showPrivacy = true } }
                        HStack(spacing: 14) {
                            chargerCard(o)
                            loadCard(o)
                        }
                        loadChart(o)
                        findingsCard(o)
                    } else if app.errorMessage == nil {
                        ProgressView().padding(.top, 80)
                    }
                }
                .padding(16)
            }
            .background(Theme.page.ignoresSafeArea())
            .navigationTitle("My Home")
            .toolbar {
                ToolbarItem(placement: .primaryAction) { ScenarioMenu() }
                ToolbarItem(placement: .topBarLeading) {
                    Button { showPrivacy = true } label: { Image(systemName: "lock.shield") }
                        .accessibilityLabel("Data & privacy")
                }
            }
            .sheet(isPresented: $showPrivacy) { PrivacyView().environmentObject(app) }
            .refreshable { await app.refresh() }
        }
    }

    private func header(_ o: Overview) -> some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Good morning, \(o.customerFirstName)").font(.title3.weight(.semibold)).foregroundColor(Theme.navy)
                Text(o.address).font(.caption).foregroundColor(Theme.muted)
            }
            Spacer()
            SyntheticBadge()
        }
    }

    private func evCard(_ o: Overview) -> some View {
        HStack(spacing: 18) {
            SocRing(soc: o.ev.socPct ?? 0, reserve: o.ev.reservePct ?? 0)
                .frame(width: 110, height: 110)
            VStack(alignment: .leading, spacing: 8) {
                SectionLabel(text: "Electric vehicle", icon: "car.fill")
                Text("\(Int(o.ev.socPct ?? 0))% charged").font(.title2.weight(.bold)).foregroundColor(Theme.navy)
                Text("Minimum reserve \(Int(o.ev.reservePct ?? 0))%").font(.subheadline).foregroundColor(Theme.muted)
                Label(o.ev.pluggedIn ? "Plugged in" : "Not plugged in", systemImage: "powerplug.fill")
                    .font(.caption.weight(.medium)).foregroundColor(Theme.sky)
            }
            Spacer(minLength: 0)
        }
        .card()
    }

    private func v2hCard(_ o: Overview) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel(text: "Vehicle-to-Home", icon: "house.and.flag.fill")
            HStack {
                Circle().fill(o.v2h.available ? Theme.sky : Theme.amber).frame(width: 10, height: 10)
                Text(o.v2h.available ? "Available" : "Not available").font(.headline).foregroundColor(Theme.navy)
                Spacer()
                if let w = o.v2h.nextWindow {
                    Text("Window \(w)").font(.caption).foregroundColor(Theme.muted)
                }
            }
            Text(o.v2h.reason).font(.subheadline).foregroundColor(Theme.muted)
            if !o.v2h.lastNightDelivered {
                Button {
                    app.selectedTab = .assistant
                    Task { await app.ask("Why didn't my EV discharge last night?") }
                } label: {
                    Label("Ask why V2H didn't run last night", systemImage: "sparkles")
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 10)
                        .background(Theme.skySoft)
                        .foregroundColor(Theme.sky)
                        .clipShape(RoundedRectangle(cornerRadius: 10))
                }
            }
        }
        .card()
    }

    private func chargerCard(_ o: Overview) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(text: "Charger", icon: "bolt.car.fill")
            Text(o.charger.status.capitalized).font(.headline)
                .foregroundColor(o.charger.status == "online" ? Theme.sky : Theme.red)
            Text(o.charger.model).font(.caption).foregroundColor(Theme.muted)
        }
        .card()
    }

    private func loadCard(_ o: Overview) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(text: "Home now", icon: "house.fill")
            Text(String(format: "%.1f kW", o.home.loadKw ?? 0)).font(.headline).foregroundColor(Theme.navy)
            Text(String(format: "Solar %.1f kW", o.home.solarKw)).font(.caption).foregroundColor(Theme.muted)
        }
        .card()
    }

    private func loadChart(_ o: Overview) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(text: "Household use last evening", icon: "chart.xyaxis.line")
            Chart(o.home.loadSeries) { p in
                AreaMark(x: .value("Time", p.t), y: .value("kW", p.kw))
                    .foregroundStyle(Theme.sky.opacity(0.18))
                LineMark(x: .value("Time", p.t), y: .value("kW", p.kw))
                    .foregroundStyle(Theme.sky)
            }
            .frame(height: 120)
        }
        .card()
    }

    private func findingsCard(_ o: Overview) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel(text: "Recent diagnostic findings", icon: "stethoscope")
            if o.recentFindings.isEmpty {
                Text("No findings yet. Ask the assistant about your system.").font(.subheadline).foregroundColor(Theme.muted)
            }
            ForEach(o.recentFindings) { f in
                VStack(alignment: .leading, spacing: 8) {
                    Text(f.headline).font(.subheadline).foregroundColor(Theme.navy)
                    if f.supportRecommended {
                        Button {
                            app.supportRunId = f.runId
                        } label: {
                            Label("Request support", systemImage: "person.crop.circle.badge.questionmark")
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(Theme.peachInk)
                        }
                        .buttonStyle(.borderedProminent)
                        .buttonBorderShape(.capsule)
                        .tint(Theme.peach)
                    }
                }
            }
        }
        .card()
    }
}

struct SocRing: View {
    let soc: Double
    let reserve: Double

    var body: some View {
        ZStack {
            Circle().stroke(Theme.border, lineWidth: 12)
            Circle()
                .trim(from: 0, to: CGFloat(min(soc, 100) / 100))
                .stroke(soc > reserve ? Theme.sky : Theme.amber, style: StrokeStyle(lineWidth: 12, lineCap: .round))
                .rotationEffect(.degrees(-90))
            // Reserve marker
            Rectangle()
                .fill(Theme.navy)
                .frame(width: 3, height: 18)
                .offset(y: -55)
                .rotationEffect(.degrees(reserve / 100 * 360))
            VStack(spacing: 0) {
                Text("\(Int(soc))%").font(.title3.weight(.bold)).foregroundColor(Theme.navy)
                Text("SOC").font(.caption2).foregroundColor(Theme.muted)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Battery \(Int(soc)) percent, reserve \(Int(reserve)) percent")
    }
}

struct ErrorBanner: View {
    let text: String
    let retry: () -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Connection problem", systemImage: "wifi.exclamationmark").font(.headline).foregroundColor(Theme.red)
            Text(text).font(.caption).foregroundColor(Theme.muted)
            Button("Retry", action: retry).buttonStyle(.bordered)
        }
        .card()
    }
}

struct ScenarioMenu: View {
    @EnvironmentObject var app: AppState
    @State private var showSettings = false

    var body: some View {
        Menu {
            if let demo = app.demo {
                Section("Demo scenario") {
                    ForEach(demo.scenarios) { s in
                        Button {
                            Task { await app.switchScenario(s.code) }
                        } label: {
                            if s.code == demo.scenario {
                                Label("\(s.code): \(s.name)", systemImage: "checkmark")
                            } else {
                                Text("\(s.code): \(s.name)")
                            }
                        }
                    }
                }
                Text("AI mode: \(demo.aiMode)")
            }
            Button("Server settings…") { showSettings = true }
        } label: {
            Label("Demo", systemImage: "slider.horizontal.3")
        }
        .sheet(isPresented: $showSettings) { SettingsView() }
    }
}

// MARK: - Savings

struct SavingsCard: View {
    let savings: Savings
    var openPrivacy: () -> Void

    private func eur(_ v: Double?) -> String { v.map { String(format: "€%.2f", $0) } ?? "–" }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                SectionLabel(text: "Savings · last 7 days", icon: "eurosign.circle")
                Spacer()
                if let c = savings.contractType {
                    Text("\(c) price").font(.caption2.weight(.medium)).foregroundColor(Theme.muted)
                }
            }
            if savings.contractType != nil && !savings.sessions.isEmpty {
                HStack(spacing: 8) {
                    stat("You paid", eur(savings.actualEur), accent: false)
                    stat("Cheapest", eur(savings.cheapestEur), accent: false)
                    stat("Could save", eur(savings.savingEur), accent: true)
                }
                if let last = savings.sessions.last, !last.cheapestHours.isEmpty, (savings.savingEur ?? 0) > 0 {
                    Text("Last charge \(last.start)–\(last.end): \(eur(last.actualEur)). Cheaper hours before your \(last.departure ?? "") departure: \(last.cheapestHours.joined(separator: ", ")).")
                        .font(.caption).foregroundColor(Theme.muted)
                }
            }
            Text(savings.tip).font(.subheadline).foregroundColor(Theme.navy)
            if !savings.unavailableSources.isEmpty {
                Button("Open Data & privacy", action: openPrivacy).font(.caption.weight(.semibold))
            }
        }
        .card()
    }

    private func stat(_ label: String, _ value: String, accent: Bool) -> some View {
        VStack(spacing: 2) {
            Text(label).font(.caption2).foregroundColor(Theme.muted)
            Text(value).font(.headline).foregroundColor(accent ? Theme.sky : Theme.navy)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(accent ? Theme.skySoft : Theme.page)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

// MARK: - Data & privacy

struct PrivacyView: View {
    @EnvironmentObject var app: AppState
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(app.consents) { c in
                        if c.toggleable {
                            Toggle(isOn: Binding(get: { c.active }, set: { on in Task { await app.setConsent(c, on: on) } })) {
                                row(c)
                            }
                            .tint(Theme.sky)
                        } else {
                            HStack {
                                row(c)
                                Spacer()
                                Text("Always on").font(.caption).foregroundColor(Theme.muted)
                            }
                        }
                    }
                } footer: {
                    Text("If you turn a source off, the assistant will tell you that data is unavailable instead of using it.")
                }
            }
            .navigationTitle("Data & privacy")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .task { await app.loadConsents() }
        }
    }

    private func row(_ c: DataConsent) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(c.label).font(.body.weight(.medium))
            Text(c.scope).font(.caption).foregroundColor(Theme.muted)
            Text(c.legalBasis.hasPrefix("EU Data Act") ? "EU Data Act: you chose HelioHEMS to receive this data"
                 : c.legalBasis == "contract" ? "Needed for your HelioHEMS service contract" : c.legalBasis)
                .font(.caption2).foregroundColor(Theme.muted)
        }
    }
}
