import XCTest
@testable import Collector

/// Reproduces the device crash path in the simulator: the real runBatch()
/// flow (queue fetch → WebViewFetch instantiation) — if WKWebView-off-main
/// or any other main-thread violation exists, this crashes the runner.
final class CrashReproTests: XCTestCase {
    func testRunBatchDoesNotCrashFromBackground() async {
        // Use a bogus token: the queue GET will fail fast and runBatch will
        // hit its catch arm — the crash we're hunting was in the fetch path
        // BEFORE any network result, on webview construction.
        UserDefaults.standard.set("gho_bogus_token_for_crash_repro", forKey: "crr_pat")
        UserDefaults.standard.set("citywok/collector-data", forKey: "crr_repo")
        let engine = CollectorEngine(
            workURL: URL(string: "https://example.invalid/work.json")!,
            spacingRange: 0.01...0.02)
        _ = engine  // construction isolation
        // exercise the actual fetch path the button drives:
        let exp1 = expectation(description: "engine batch returns without crashing")
        Task {
            await engine.runBatch()
            exp1.fulfill()
        }
        await fulfillment(of: [exp1], timeout: 30)
        // if we reach here, no thread-assertion crash occurred
    }

    func testWebViewFetchConstructionFromBackground() async {
        // The exact pre-fix crash: WKWebView init off-main.
        UserDefaults.standard.set("citywok/collector-data", forKey: "crr_repo")
        let exp = expectation(description: "fetch call completes or errors, no crash")
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            DispatchQueue.global(qos: .userInitiated).async {
                WebViewFetch.shared.fetch(videoId: "Opy7MLGAPBk") { _ in
                    cont.resume()
                }
            }
        }
        await fulfillment(of: [exp], timeout: 45)
    }
}
