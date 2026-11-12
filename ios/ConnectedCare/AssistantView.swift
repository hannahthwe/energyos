import SwiftUI

struct AssistantView: View {
    @EnvironmentObject var app: AppState
    @State private var draft = ""

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                ScrollViewReader { proxy in
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 14) {
                            intro
                            ForEach(app.chat) { entry in
                                row(entry).id(entry.id)
                            }
                            if app.isThinking {
                                ThinkingRow().id("thinking")
                            }
                        }
                        .padding(16)
                    }
                    .onChange(of: app.chat.count) { _ in
                        withAnimation { proxy.scrollTo(app.isThinking ? "thinking" : app.chat.last?.id, anchor: .bottom) }
                    }
                    .onChange(of: app.isThinking) { thinking in
                        if thinking { withAnimation { proxy.scrollTo("thinking", anchor: .bottom) } }
                    }
                }
                composer
            }
            .background(Theme.page.ignoresSafeArea())
            .navigationTitle("Assistant")
            .toolbar { ToolbarItem(placement: .primaryAction) { ScenarioMenu() } }
        }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Image(systemName: "sparkles").foregroundColor(Theme.sky)
                Text("Helio Assistant").font(.headline).foregroundColor(Theme.navy)
                Spacer()
            }
            Text("I check your home's actual records (schedule, battery level, charger status) before answering.")
                .font(.subheadline).foregroundColor(Theme.muted)
            if app.chat.isEmpty {
                ForEach(app.suggestions, id: \.self) { s in
                    Button {
                        Task { await app.ask(s) }
                    } label: {
                        Text(s).font(.subheadline).frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12).background(Theme.skySoft).foregroundColor(Theme.navy)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                    }
                    .disabled(app.isThinking)
                }
            }
        }
        .card()
    }

    @ViewBuilder
    private func row(_ entry: ChatEntry) -> some View {
        switch entry {
        case .user(_, let text):
            HStack {
                Spacer(minLength: 40)
                Text(text).padding(12).background(Theme.navy).foregroundColor(.white)
                    .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            }
        case .assistant(let d):
            DiagnosisCard(d: d) { app.supportRunId = d.runId }
        case .failure(_, let text):
            Label(text, systemImage: "exclamationmark.triangle").font(.caption).foregroundColor(Theme.red).card()
        }
    }

    private var composer: some View {
        HStack(spacing: 10) {
            TextField("Ask about your energy system", text: $draft, axis: .vertical)
                .lineLimit(1...4)
                .padding(10)
                .background(Theme.page)
                .clipShape(RoundedRectangle(cornerRadius: 12))
                .onSubmit(send)
            Button(action: send) {
                Image(systemName: "arrow.up.circle.fill").font(.system(size: 32)).foregroundColor(Theme.sky)
            }
            .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty || app.isThinking)
        }
        .padding(12)
        .background(Theme.card.shadow(.drop(color: .black.opacity(0.05), radius: 6, y: -2)))
    }

    private func send() {
        let q = draft
        draft = ""
        Task { await app.ask(q) }
    }
}

struct ThinkingRow: View {
    var body: some View {
        HStack(spacing: 10) {
            ProgressView()
            Text("Checking schedule, battery, charger and settings…").font(.subheadline).foregroundColor(Theme.muted)
        }
        .card()
    }
}

struct DiagnosisCard: View {
    let d: HomeDiagnosis
    let requestSupport: () -> Void
    @State private var showEvidence = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                Image(systemName: d.supportRecommended ? "exclamationmark.circle.fill" : "checkmark.seal.fill")
                    .foregroundColor(d.supportRecommended ? Theme.amber : Theme.sky)
                    .font(.title3)
                Text(d.headline).font(.headline).foregroundColor(Theme.navy)
            }
            block("What happened", icon: "clock", items: [d.whatHappened])
            block("Why it probably happened", icon: "lightbulb", items: d.why)
            block("What we can't see", icon: "eye.slash", items: d.unknown)
            block("What you can safely do", icon: "hand.thumbsup", items: d.nextSteps, numbered: true)

            if !d.timeline.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    SectionLabel(text: "Last night", icon: "list.bullet.rectangle")
                    ForEach(d.timeline) { t in
                        HStack(alignment: .top, spacing: 10) {
                            Text(t.time).font(.caption.monospacedDigit().weight(.semibold)).foregroundColor(Theme.sky)
                                .frame(width: 42, alignment: .leading)
                            Text(t.label).font(.caption).foregroundColor(Theme.navy)
                        }
                    }
                }
            }

            DisclosureGroup(isExpanded: $showEvidence) {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(d.evidence) { e in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(e.summary).font(.caption).foregroundColor(Theme.navy)
                            Text(e.sourceLabel).font(.caption2).foregroundColor(Theme.muted)
                        }
                    }
                }
                .padding(.top, 6)
            } label: {
                Text("Based on \(d.evidence.count) records from your system").font(.caption.weight(.medium))
                    .foregroundColor(Theme.muted)
            }

            HStack {
                Text(d.checksRun.count == 1 ? "1 specialist check" : "\(d.checksRun.count) specialist checks")
                Text("·")
                Text(d.mode == "claude" ? "Claude AI" : "Offline demo mode")
            }
            .font(.caption2).foregroundColor(Theme.muted)

            Button(action: requestSupport) {
                Label(d.supportRecommended ? "Request support" : "Still need help? Contact support",
                      systemImage: "person.crop.circle.badge.questionmark")
                    .font(.subheadline.weight(.semibold))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 6)
                    .foregroundStyle(d.supportRecommended ? Theme.peachInk : Color.white)
            }
            .buttonStyle(.borderedProminent)
            .buttonBorderShape(.capsule)
            .tint(d.supportRecommended ? Theme.peach : Theme.muted)
        }
        .card()
    }

    @ViewBuilder
    private func block(_ title: String, icon: String, items: [String], numbered: Bool = false) -> some View {
        if !items.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                SectionLabel(text: title, icon: icon)
                ForEach(Array(items.enumerated()), id: \.offset) { i, text in
                    HStack(alignment: .top, spacing: 6) {
                        Text(numbered ? "\(i + 1)." : "•").font(.subheadline).foregroundColor(Theme.sky)
                        Text(text).font(.subheadline).foregroundColor(Theme.navy).fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }
    }
}
