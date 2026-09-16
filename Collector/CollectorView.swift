import SwiftUI

struct CollectorView: View {
    @StateObject private var engine: CollectorEngine
    @State private var pat: String = ""
    @State private var repo: String = "citywok/collector-data"

    init() { _engine = StateObject(wrappedValue: CollectorEngine()) }

    var body: some View {
        TabView {
            FetchTab(engine: engine)
                .tabItem { Label("Fetch", systemImage: "globe") }
            BrowserTab()
                .tabItem { Label("Browser", systemImage: "safari") }
            SetupTab(engine: engine, pat: $pat, repo: $repo)
                .tabItem { Label("Setup", systemImage: "gear") }
        }
    }
}

// MARK: - Fetch tab (drive the app-owned browser)

struct FetchTab: View {
    @ObservedObject var engine: CollectorEngine
    @State private var activity: String = "browser host initializing"

    var body: some View {
        VStack(spacing: 0) {
            WebViewHost(activity: $activity)
                .frame(height: 220)
                .cornerRadius(10)
                .padding(.horizontal)
            List {
                Section("Status") {
                    LabeledContent("Session fetches", value: "\(engine.fetchedThisSession)")
                    LabeledContent("Browser", value: activity)
                    Text(engine.lastStatus).font(.footnote).foregroundStyle(.secondary)
                }
                Section("Collection") {
                    Button("Fetch next batch now") {
                        Task { await engine.runBatch() }
                    }
                    Text("Keep this tab open while fetching — the app's own browser loads each video and harvests captions. Works from any IP; 8–15 min pacing between videos.")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }
        }
    }
}

// MARK: - Browser tab (visible YouTube; same webview session)

struct BrowserTab: View {
    var body: some View {
        VStack {
            Text("Optional: keep the video visible — harvesting runs in the Fetch tab's browser either way.")
                .font(.footnote).foregroundStyle(.secondary).padding(.horizontal)
            WebViewHost(activity: .constant("visible browser"))
        }
    }
}

// MARK: - Setup tab (token/repo/device)

struct SetupTab: View {
    @ObservedObject var engine: CollectorEngine
    @Binding var pat: String
    @Binding var repo: String

    var body: some View {
        Form {
            Section("GitHub transport") {
                SecureField("Fine-grained PAT (contents R/W on collector-data)", text: $pat)
                Button("Save token") {
                    UserDefaults.standard.set(pat, forKey: "crr_pat")
                    engine.lastStatus = "token saved (\(pat.count) chars)"
                    pat = ""
                }
                .disabled(pat.count < 20)
                TextField("Repo (owner/name)", text: $repo)
                Button("Save repo") {
                    UserDefaults.standard.set(repo, forKey: "crr_repo")
                    engine.lastStatus = "repo saved: \(repo)"
                }
                .disabled(!repo.contains("/"))
            }
            Section("Token state") {
                let has = (UserDefaults.standard.string(forKey: "crr_pat") ?? "").isEmpty == false
                LabeledContent("Token saved", value: has ? "yes (\(UserDefaults.standard.string(forKey: "crr_pat")!.count) chars)" : "NO — paste above and Save")
                LabeledContent("Repo saved", value: UserDefaults.standard.string(forKey: "crr_repo") ?? "(default)")
            }
            Section("Diagnostics") {
                Button("Send debug bundle now") {
                    Task {
                        try? await GH.postDebugLog(session: URLSession(configuration: .default))
                        engine.lastStatus = "debug bundle sent: \(DebugLog.shared.attempts.count) attempts"
                    }
                }
                LabeledContent("Attempts logged", value: "\(DebugLog.shared.attempts.count)")
            }
            Section("Device") {
                LabeledContent("ID", value: String(UIDeviceIdentifiers.stableId().prefix(8)))
            }
        }
    }
}
