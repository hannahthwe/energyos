import Foundation

// Decoded with .convertFromSnakeCase: `soc_pct` -> `socPct`, etc.

struct ScenarioInfo: Codable, Identifiable, Hashable {
    let code: String
    let name: String
    let summary: String
    var id: String { code }
}

struct DemoState: Codable {
    let scenario: String
    let scenarios: [ScenarioInfo]
    let aiMode: String
    let model: String?
    let syntheticData: Bool
    let demoMode: Bool?
}

struct Overview: Codable {
    struct EV: Codable {
        let socPct: Double?
        let reservePct: Double?
        let pluggedIn: Bool
        let usableForHomeKwh: Double
    }
    struct V2H: Codable {
        let available: Bool
        let reason: String
        let nextWindow: String?
        let lastNightDelivered: Bool
    }
    struct Charger: Codable {
        let status: String
        let model: String
    }
    struct LoadPoint: Codable, Identifiable {
        let t: String
        let kw: Double
        var id: String { t }
    }
    struct Home: Codable {
        let loadKw: Double?
        let solarKw: Double
        let loadSeries: [LoadPoint]
    }
    struct Finding: Codable, Identifiable {
        let runId: String
        let headline: String
        let createdAt: String
        let supportRecommended: Bool
        var id: String { runId }
    }

    let customerFirstName: String
    let address: String
    let ev: EV
    let v2h: V2H
    let charger: Charger
    let home: Home
    let recentFindings: [Finding]
}

struct Evidence: Codable, Identifiable, Hashable {
    let evidenceId: String
    let sourceSystem: String
    let sourceLabel: String
    let sourceRecordId: String
    let timestampUtc: String?
    let evidenceType: String
    let summary: String
    let visibilityLevel: String
    var id: String { evidenceId }
}

struct TimelineItem: Codable, Identifiable, Hashable {
    let timestampUtc: String
    let time: String
    let label: String
    let evidenceId: String
    let sourceSystem: String
    let kind: String
    let visibilityLevel: String
    var id: String { timestampUtc + evidenceId }
}

struct ScenarioRef: Codable, Hashable {
    let code: String
    let name: String
}

/// Homeowner-safe diagnosis. The server strips internal and restricted data
/// before it ever reaches the app.
struct HomeDiagnosis: Codable, Identifiable, Hashable {
    let runId: String
    let question: String
    let mode: String
    let modeNote: String?
    let scenario: ScenarioRef
    let likelyCause: String
    let headline: String
    let whatHappened: String
    let why: [String]
    let unknown: [String]
    let nextSteps: [String]
    let supportRecommended: Bool
    let evidence: [Evidence]
    let checksRun: [String]
    let unavailableSources: [String]?
    let timeline: [TimelineItem]
    let createdAt: String
    var id: String { runId }
}

struct SharePreview: Codable {
    let runId: String
    let question: String
    let summary: String
    let whatHappened: String
    let why: [String]
    let evidenceToShare: [Evidence]
    let alsoAvailableToSupport: String
}

struct HandoffResult: Codable {
    let caseId: String
    let status: String
    let title: String
    let escalationRequired: Bool
    let sharedEvidenceIds: [String]
}

struct HomeCase: Codable, Identifiable {
    let caseId: String
    let title: String
    let status: String
    let createdAt: String
    var id: String { caseId }
}

enum ChatEntry: Identifiable {
    case user(id: UUID, text: String)
    case assistant(HomeDiagnosis)
    case failure(id: UUID, text: String)

    var id: String {
        switch self {
        case .user(let id, _): return id.uuidString
        case .assistant(let d): return d.runId
        case .failure(let id, _): return id.uuidString
        }
    }
}

/// GET /api/home/savings: actual EV charging cost vs the cheapest hours (deterministic, server-side).
struct Savings: Codable {
    struct Session: Codable, Identifiable {
        let date: String
        let start: String
        let end: String
        let energyKwh: Double
        let actualEur: Double?
        let cheapestEur: Double?
        let savingEur: Double?
        let departure: String?
        let smartChargingOn: Bool?
        let cheapestHours: [String]
        var id: String { date + start }
    }
    let window: String
    let contractType: String?
    let complete: Bool
    let energyKwh: Double
    let actualEur: Double?
    let cheapestEur: Double?
    let savingEur: Double?
    let sessions: [Session]
    let tip: String
    let unavailableSources: [String]
    let evidenceCount: Int
    let priceNight: PriceNight?
}

/// Hourly prices for the last charging night, with the hours charged and the cheapest hours marked.
struct PriceNight: Codable {
    struct Hour: Codable, Identifiable {
        let time: String
        let marketEurKwh: Double
        let priceEurKwh: Double?
        let chargedKwh: Double
        let cheapest: Bool
        var id: String { time }
    }
    let date: String
    let from: String
    let to: String
    let contractType: String?
    let fixedPriceEurKwh: Double?
    let peak: Hour?
    let lowest: Hour?
    let hours: [Hour]
}

/// One row of the Data & privacy screen (GET /api/home/consents).
struct DataConsent: Codable, Identifiable, Hashable {
    let sourceSystem: String
    let label: String
    let sourceLabel: String
    let scope: String
    let legalBasis: String
    let toggleable: Bool
    let active: Bool
    let consentId: String?
    let approvedAt: String?
    let expiresAt: String?
    let revokedAt: String?
    var id: String { sourceSystem }
}
