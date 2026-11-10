import SwiftUI

struct CasesView: View {
    @EnvironmentObject var app: AppState

    var body: some View {
        NavigationStack {
            List {
                if app.cases.isEmpty {
                    Text("No support requests yet.").foregroundColor(Theme.muted)
                }
                ForEach(app.cases) { c in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text(c.caseId).font(.caption.monospaced()).foregroundColor(Theme.muted)
                            Spacer()
                            StatusPill(status: c.status)
                        }
                        Text(c.title).font(.subheadline.weight(.semibold)).foregroundColor(Theme.navy)
                        Text(c.createdAt.replacingOccurrences(of: "T", with: " ").replacingOccurrences(of: "Z", with: " UTC"))
                            .font(.caption2).foregroundColor(Theme.muted)
                    }
                    .padding(.vertical, 4)
                }
            }
            .navigationTitle("Support")
            .refreshable { await app.caseCreated() }
            .task { await app.caseCreated() }
        }
    }
}

struct StatusPill: View {
    let status: String
    var color: Color {
        switch status {
        case "New": return Theme.teal
        case "Investigating": return Theme.amber
        case "Escalated": return Theme.red
        default: return Theme.muted
        }
    }
    var body: some View {
        Text(status).font(.caption2.weight(.bold)).padding(.horizontal, 8).padding(.vertical, 3)
            .background(color.opacity(0.14)).foregroundColor(color).clipShape(Capsule())
    }
}

struct SettingsView: View {
    @EnvironmentObject var app: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var url = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("http://192.168.1.20:8000", text: $url)
                        .autocorrectionDisabled()
                        .urlField()
                } header: {
                    Text("ConnectedCare server")
                } footer: {
                    Text("Simulator: http://localhost:8000. On a real iPhone, use your Mac's Wi-Fi address, e.g. http://192.168.1.20:8000, and start the backend with --host 0.0.0.0.")
                }
                if let d = app.demo {
                    Section("Status") {
                        LabeledContent("Scenario", value: d.scenario)
                        LabeledContent("AI mode", value: d.aiMode == "claude" ? "Claude (\(d.model ?? ""))" : "Offline demo")
                    }
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        app.serverURL = url
                        dismiss()
                        Task { await app.refresh() }
                    }
                }
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } }
            }
            .onAppear { url = app.serverURL }
        }
    }
}

extension View {
    @ViewBuilder
    func urlField() -> some View {
        #if os(iOS)
        self.keyboardType(.URL).textInputAutocapitalization(.never)
        #else
        self
        #endif
    }
}
