import XCTest
@testable import Collector

/// Live end-to-end smoke: REAL network from the simulator environment.
/// Gated behind CRR_LIVE_SMOKE=1 so normal test runs stay offline/hermetic.
final class LiveSmokeTests: XCTestCase {
    private var live: Bool {
        ProcessInfo.processInfo.environment["CRR_LIVE_SMOKE"] == "1"
    }
    private var session: URLSession {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 45
        return URLSession(configuration: cfg)
    }

    /// The app's native Swift caption path against real YouTube.
    /// Video: known caption-bearing Fox upload (captions verified upstream).
    func testLiveInnertubeCaptionFetch() async throws {
        try XCTSkipUnless(live, "live smoke disabled")
        let engine = CollectorEngine()
        let lines = try await engine.fetchCaptions(videoId: "Opy7MLGAPBk")
        XCTAssertGreaterThan(lines.count, 30, "expected a real transcript, got \(lines.count) lines")
        XCTAssertTrue(lines.joined().contains("JANUARY"), "transcript content sanity")
    }

    /// GitHub transport GET against the real private queue file.
    func testLiveGitHubQueueGet() async throws {
        try XCTSkipUnless(live, "live smoke disabled")
        XCTAssertFalse(GH.token.isEmpty, "transport token must be present for live smoke")
        let read = await GH.readQueue(session: session)
        let items: [GH.PendingItem]
        switch read {
        case .items(let q): items = q
        case .failed(let http, let note): throw NSError(domain: "smoke", code: http, userInfo: [NSLocalizedDescriptionKey: note])
        }
        XCTAssertNotNil(items, "GET must resolve (200 with items, or 404 -> [])")
    }

    /// Two-writer append semantics against the real repo: two sequential
    /// 'device sessions' post results; each must see the other's writes.
    func testLiveTwoWriterAppend() async throws {
        try XCTSkipUnless(live, "live smoke disabled")
        let stub: [[String: Any]] = [
            ["video_id": "SMOKE_A", "device": "device1", "lines": ["one"]],
        ]
        try await GH.postResults(results: stub, session: session)
        let stub2: [[String: Any]] = [
            ["video_id": "SMOKE_B", "device": "device2", "lines": ["two"]],
        ]
        try await GH.postResults(results: stub2, session: session)
        // read back
        let req = GH.request(path: "/repos/\(GH.repo)/contents/results/\(day()).json", method: "GET")
        let (data, resp) = try await session.data(for: req)
        XCTAssertEqual((resp as? HTTPURLResponse)?.statusCode, 200)
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        let content = (obj?["content"] as? String ?? "")
            .replacingOccurrences(of: "\n", with: "")
        let decoded = try JSONSerialization.jsonObject(
            with: Data(base64Encoded: content) ?? Data()) as? [[String: Any]]
        let ids = decoded?.compactMap { $0["video_id"] as? String } ?? []
        XCTAssertTrue(ids.contains("SMOKE_A") && ids.contains("SMOKE_B"),
                      "both writers' results must coexist: \(ids)")
    }

    private func day() -> String {
        String(ISO8601DateFormatter().string(from: Date()).prefix(10))  // results/YYYY-MM-DD.json
    }
}
