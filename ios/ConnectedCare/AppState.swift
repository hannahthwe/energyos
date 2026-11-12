import Foundation
import SwiftUI

@MainActor
final class AppState: ObservableObject {
    @AppStorage("serverURL") var serverURL: String = APIClient.defaultBaseURL

    @Published var demo: DemoState?
    @Published var overview: Overview?
    @Published var chat: [ChatEntry] = []
    @Published var cases: [HomeCase] = []
    @Published var savings: Savings?
    @Published var consents: [DataConsent] = []
    @Published var isThinking = false
    @Published var isLoading = false
    @Published var errorMessage: String?
    @Published var selectedTab: Tab = .home
    @Published var supportRunId: String?

    enum Tab: Hashable { case home, assistant, cases }

    let suggestions = [
        "Why didn't my EV discharge last night?",
        "Why did my home battery pause last night?",
        "Why was my charging so expensive last night?",
        "Was a setting changed?",
    ]

    var api: APIClient { APIClient(baseURL: serverURL) }

    var latestDiagnosis: HomeDiagnosis? {
        for entry in chat.reversed() {
            if case .assistant(let d) = entry { return d }
        }
        return nil
    }

    func refresh() async {
        isLoading = true
        defer { isLoading = false }
        do {
            async let d = api.demo()
            async let o = api.overview()
            async let c = api.cases()
            demo = try await d
            overview = try await o
            cases = try await c
            savings = try? await api.savings()
            errorMessage = nil
        } catch {
            errorMessage = "Can't reach ConnectedCare at \(serverURL). \(error.localizedDescription)"
        }
    }

    func switchScenario(_ code: String) async {
        do {
            try await api.setScenario(code)
            chat.removeAll()
            await refresh()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func ask(_ question: String) async {
        let q = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty else { return }
        chat.append(.user(id: UUID(), text: q))
        isThinking = true
        defer { isThinking = false }
        do {
            let d = try await api.ask(q)
            chat.append(.assistant(d))
            overview = try? await api.overview()
        } catch {
            chat.append(.failure(id: UUID(), text: error.localizedDescription))
        }
    }

    func loadConsents() async {
        do {
            consents = try await api.consents()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// On -> grant a new consent; off -> revoke the active one. Answers then report that source as unavailable.
    func setConsent(_ c: DataConsent, on: Bool) async {
        do {
            if on {
                try await api.grantConsent(source: c.sourceSystem)
            } else if let id = c.consentId {
                try await api.revokeConsent(id: id)
            }
            await loadConsents()
            savings = try? await api.savings()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func caseCreated() async {
        cases = (try? await api.cases()) ?? cases
    }
}
