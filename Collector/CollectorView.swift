import SwiftUI

struct CollectorView: View {
    @StateObject private var engine: CollectorEngine
    @State private var auto = false
    @State private var timer: Timer?

    init() { _engine = StateObject(wrappedValue: CollectorEngine()) }

    var body: some View {
        NavigationStack {
            List {
                Section("Device") {
                    LabeledContent("ID", value: UIDeviceIdentifiers.stableId().prefix(8))
                    LabeledContent("Fetched this session", value: "\(engine.fetchedThisSession)")
                }
                Section("Collection") {
                    Button("Fetch next batch now") {
                        Task { await engine.runBatch() }
                    }
                    Toggle("Auto (while app open)", isOn: $auto)
                    if auto { LabeledContent("Pacing", value: "8–15 min randomized") }
                }
                Section("Status") {
                    Text(engine.lastStatus).font(.footnote).foregroundStyle(.secondary)
                    Text("Requests leave from this device's current network — no server proxies, no credentials on device beyond short-lived S3 PUT slots.")
                        .font(.caption2).foregroundStyle(.tertiary)
                }
            }
            .navigationTitle("Speech Collector")
            .onChange(of: auto) { on in
                timer?.invalidate()
                if on {
                    timer = Timer.scheduledTimer(withTimeInterval: 10 * 60, repeats: true) { _ in
                        Task { await engine.runBatch() }
                    }
                    Task { await engine.runBatch() }
                }
            }
        }
    }
}
