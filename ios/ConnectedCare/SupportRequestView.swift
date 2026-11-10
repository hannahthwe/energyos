import SwiftUI

/// Consent screen: shows exactly what will be shared and requires an explicit
/// confirmation before a support case is created.
struct SupportRequestView: View {
    @EnvironmentObject var app: AppState
    @Environment(\.dismiss) private var dismiss
    let runId: String

    @State private var preview: SharePreview?
    @State private var approved = false
    @State private var note = ""
    @State private var submitting = false
    @State private var result: HandoffResult?
    @State private var error: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    if let r = result {
                        success(r)
                    } else if let p = preview {
                        content(p)
                    } else if let e = error {
                        Label(e, systemImage: "exclamationmark.triangle").foregroundColor(Theme.red).card()
                    } else {
                        ProgressView().frame(maxWidth: .infinity).padding(.top, 60)
                    }
                }
                .padding(16)
            }
            .background(Theme.page.ignoresSafeArea())
            .navigationTitle(result == nil ? "Request support" : "Request sent")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button(result == nil ? "Cancel" : "Done") { dismiss() } }
            }
            .task { await load() }
        }
    }

    private func content(_ p: SharePreview) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 8) {
                SectionLabel(text: "Your question", icon: "quote.bubble")
                Text("\u{201C}\(p.question)\u{201D}").font(.subheadline).foregroundColor(Theme.navy)
                SectionLabel(text: "Diagnostic summary", icon: "doc.text.magnifyingglass").padding(.top, 6)
                Text(p.summary).font(.subheadline.weight(.semibold)).foregroundColor(Theme.navy)
                ForEach(p.why, id: \.self) { w in
                    Text("• " + w).font(.subheadline).foregroundColor(Theme.muted)
                }
            }
            .card()

            VStack(alignment: .leading, spacing: 8) {
                SectionLabel(text: "Records that will be shared (\(p.evidenceToShare.count))", icon: "tray.full")
                ForEach(p.evidenceToShare) { e in
                    HStack(alignment: .top, spacing: 8) {
                        Image(systemName: "checkmark.circle.fill").foregroundColor(Theme.teal).font(.caption)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(e.summary).font(.caption).foregroundColor(Theme.navy)
                            Text(e.sourceLabel).font(.caption2).foregroundColor(Theme.muted)
                        }
                    }
                }
                Text(p.alsoAvailableToSupport).font(.caption2).foregroundColor(Theme.muted).padding(.top, 4)
            }
            .card()

            VStack(alignment: .leading, spacing: 8) {
                SectionLabel(text: "Add a note (optional)", icon: "square.and.pencil")
                TextField("e.g. Best time to call is after 17:00", text: $note, axis: .vertical)
                    .lineLimit(2...4)
                    .padding(10).background(Theme.page).clipShape(RoundedRectangle(cornerRadius: 10))
            }
            .card()

            Toggle(isOn: $approved) {
                Text("I approve sharing this summary and these records with HelioHEMS Support.")
                    .font(.subheadline).foregroundColor(Theme.navy)
            }
            .tint(Theme.teal)
            .card()

            if let e = error {
                Text(e).font(.caption).foregroundColor(Theme.red)
            }

            Button {
                Task { await submit() }
            } label: {
                HStack {
                    if submitting { ProgressView().tint(.white) }
                    Text("Send to support").font(.headline)
                }
                .frame(maxWidth: .infinity).padding(.vertical, 8)
            }
            .buttonStyle(.borderedProminent)
            .tint(Theme.navy)
            .disabled(!approved || submitting)
        }
    }

    private func success(_ r: HandoffResult) -> some View {
        VStack(spacing: 14) {
            Image(systemName: "checkmark.circle.fill").font(.system(size: 56)).foregroundColor(Theme.teal)
            Text("Case \(r.caseId) created").font(.title3.weight(.bold)).foregroundColor(Theme.navy)
            Text(r.title).font(.subheadline).foregroundColor(Theme.muted).multilineTextAlignment(.center)
            Text("A HelioHEMS specialist can now see the diagnosis and the \(r.sharedEvidenceIds.count) records you shared. You'll hear back in the app.")
                .font(.subheadline).foregroundColor(Theme.muted).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .card()
    }

    private func load() async {
        do { preview = try await app.api.sharePreview(runId: runId) } catch { self.error = error.localizedDescription }
    }

    private func submit() async {
        submitting = true
        defer { submitting = false }
        do {
            result = try await app.api.requestSupport(runId: runId, note: note)
            await app.caseCreated()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
