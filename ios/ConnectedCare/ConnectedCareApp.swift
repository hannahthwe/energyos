import SwiftUI

@main
struct ConnectedCareApp: App {
    @StateObject private var app = AppState()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(app)
                .tint(Theme.teal)
        }
    }
}

struct RootView: View {
    @EnvironmentObject var app: AppState

    var body: some View {
        TabView(selection: $app.selectedTab) {
            HomeView()
                .tabItem { Label("Home", systemImage: "house.fill") }
                .tag(AppState.Tab.home)
            AssistantView()
                .tabItem { Label("Assistant", systemImage: "sparkles") }
                .tag(AppState.Tab.assistant)
            CasesView()
                .tabItem { Label("Support", systemImage: "lifepreserver") }
                .tag(AppState.Tab.cases)
        }
        .task { await app.refresh() }
        .sheet(item: Binding(
            get: { app.supportRunId.map { RunRef(id: $0) } },
            set: { app.supportRunId = $0?.id }
        )) { ref in
            SupportRequestView(runId: ref.id).environmentObject(app)
        }
    }
}

struct RunRef: Identifiable {
    let id: String
}
