import Foundation

/// Talks to the ConnectedCare FastAPI backend. The homeowner app only uses
/// `/api/home/*` and `/api/demo/*`; the server always treats these calls as
/// the homeowner role.
struct APIClient {
    static let defaultBaseURL = "http://localhost:8000"

    var baseURL: String

    enum APIError: LocalizedError {
        case badURL
        case http(Int, String)

        var errorDescription: String? {
            switch self {
            case .badURL: return "The server address is not valid. Check Settings."
            case .http(let code, let body): return "Server returned \(code): \(body.prefix(160))"
            }
        }
    }

    private var decoder: JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }

    private func request(_ path: String, method: String = "GET", body: [String: Any]? = nil) async throws -> Data {
        let trimmed = baseURL.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        guard let url = URL(string: trimmed + path) else { throw APIError.badURL }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.timeoutInterval = 90
        req.setValue("homeowner", forHTTPHeaderField: "X-Demo-Role")
        if let body = body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(code) else {
            throw APIError.http(code, String(data: data, encoding: .utf8) ?? "")
        }
        return data
    }

    func demo() async throws -> DemoState {
        try decoder.decode(DemoState.self, from: await request("/api/demo"))
    }

    func setScenario(_ code: String) async throws {
        _ = try await request("/api/demo/scenario", method: "POST", body: ["scenario": code])
    }

    func overview() async throws -> Overview {
        try decoder.decode(Overview.self, from: await request("/api/home/overview"))
    }

    func ask(_ question: String) async throws -> HomeDiagnosis {
        try decoder.decode(HomeDiagnosis.self, from: await request("/api/home/chat", method: "POST", body: ["question": question]))
    }

    func sharePreview(runId: String) async throws -> SharePreview {
        try decoder.decode(SharePreview.self, from: await request("/api/home/support-request/\(runId)/preview"))
    }

    func requestSupport(runId: String, note: String?) async throws -> HandoffResult {
        var body: [String: Any] = ["run_id": runId, "approved": true]
        if let note = note, !note.isEmpty { body["note"] = note }
        return try decoder.decode(HandoffResult.self, from: await request("/api/home/support-request", method: "POST", body: body))
    }

    func savings(window: String = "7d") async throws -> Savings {
        try decoder.decode(Savings.self, from: await request("/api/home/savings?window=\(window)"))
    }

    func consents() async throws -> [DataConsent] {
        try decoder.decode([DataConsent].self, from: await request("/api/home/consents"))
    }

    func grantConsent(source: String) async throws {
        _ = try await request("/api/home/consents", method: "POST", body: ["source_system": source])
    }

    func revokeConsent(id: String) async throws {
        _ = try await request("/api/home/consents/\(id)/revoke", method: "POST", body: [:])
    }

    func cases() async throws -> [HomeCase] {
        try decoder.decode([HomeCase].self, from: await request("/api/home/cases"))
    }
}
