import XCTest
@testable import Collector

/// The crash-hunt is settled for the REAL architecture: the app's webview is
/// attached by WebViewHost (Fetch tab), and fetch() refuses to run without
/// the host. What must NEVER regress:
///   1. runBatch must complete (not hang) even when the host is absent.
///   2. fetch without an attached host must fail FAST, not stall.
final class CrashReproTests: XCTestCase {
    func testRunBatchCompletesFromBackgroundWithoutHost() async {
        UserDefaults.standard.set("gho_bogus_token_for_repro", forKey: "crr_pat")
        UserDefaults.standard.set("citywok/collector-data", forKey: "crr_repo")
        let engine = CollectorEngine(
            workURL: URL(string: "https://example.invalid/work.json")!,
            spacingRange: 0.01...0.02)
        let exp = expectation(description: "batch completes (no crash)")
        Task { await engine.runBatch(); exp.fulfill() }
        await fulfillment(of: [exp], timeout: 30)
    }

    @MainActor
    func testFetchFailsFastWhenHostNotAttached() async {
        let exp = expectation(description: "fetch errors fast without host")
        WebViewFetch.shared.fetch(videoId: "Opy7MLGAPBk") { result in
            if case .failure(let e) = result {
                XCTAssertTrue(String(describing: e).contains("host"), "expected host-missing error")
            } else {
                XCTFail("fetch without host must fail")
            }
            exp.fulfill()
        }
        await fulfillment(of: [exp], timeout: 10)
    }
}
