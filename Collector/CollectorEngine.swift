import Foundation

/// The whole collection engine: get a work bundle, fetch captions natively
/// (so requests leave from the phone's current IP), upload results to the
/// workstation's presigned slots, report status.
final class CollectorEngine: ObservableObject {
    @Published var lastStatus: String = "idle"
    @Published var fetchedThisSession: Int = 0

    let workURL: URL
    private let session: URLSession
    private let spacingRange: ClosedRange<Double>   // seconds between fetches

    init(workURL: URL = URL(string: Config.workURL)!,
         spacingRange: ClosedRange<Double> = Config.spacingSeconds) {
        self.workURL = workURL
        self.spacingRange = spacingRange
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 45
        cfg.httpAdditionalHeaders = ["User-Agent": Config.userAgent]
        self.session = URLSession(configuration: cfg)
    }

    enum Config {
        // The workstation publishes this (public-read) each cycle.
        static var workURL: String {
            ProcessInfo.processInfo.environment["CRR_WORK_URL"]
                ?? "https://llm-chat-artifacts.s3.amazonaws.com/collector/v1/work.json"
        }
        static let spacingSeconds: ClosedRange<Double> = 45.0...75.0
        static let userAgent = "Collector/0.1 (personal research corpus; polite)"
        static let innertubePlayerURL = URL(string:
            "https://www.youtube.com/youtubei/v1/player?prettyPrint=false")!
        static let playerBodyBase: [String: Any] = [
            "context": [
                "client": [
                    "clientName": "WEB",
                    "clientVersion": "2.20240701.00.00",
                    "hl": "en", "gl": "US",
                ]
            ]
        ]
    }

    // MARK: - Work bundle

    func loadWorkBundle() async throws -> WorkBundle {
        let (data, resp) = try await session.data(from: workURL)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
            throw CaptionError.playerRequestFailed((resp as? HTTPURLResponse)?.statusCode ?? -1)
        }
        return try JSONDecoder().decode(WorkBundle.self, from: data)
    }

    // MARK: - Fetch one video's captions

    func fetchCaptions(videoId: String) async throws -> [String] {
        var body = Config.playerBodyBase
        body["videoId"] = videoId
        var req = URLRequest(url: Config.innertubePlayerURL)
        req.httpMethod = "POST"
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let (pdata, presp) = try await session.data(for: req)
        let httpStatus = (presp as? HTTPURLResponse)?.statusCode ?? -1
        var playability = "", reason = "", trackCount = 0, firstParams = ""
        var tracks: [CaptionTrack] = []
        do {
            tracks = try PlayerParser.captionTracks(from: pdata)
            trackCount = tracks.count
            if let obj = try? JSONSerialization.jsonObject(with: pdata) as? [String: Any] {
                let ps = obj["playabilityStatus"] as? [String: Any] ?? [:]
                playability = (ps["status"] as? String) ?? ""
                reason = (ps["reason"] as? String) ?? ""
            }
            if let t0 = tracks.first,
               let comps = URLComponents(string: t0.baseUrl),
               let q = comps.query {
                firstParams = String(q.prefix(140))
            }
        } catch {
            DebugLog.shared.record(videoId: videoId, transport: "innertube-v1",
                                   httpStatus: httpStatus, errorText: "parse: \(error)")
            throw error
        }
        DebugLog.shared.record(videoId: videoId, transport: "innertube-v1",
                               httpStatus: httpStatus, playability: playability,
                               reason: reason, trackCount: trackCount,
                               firstTrackParams: firstParams,
                               bytesPreview: String(data: pdata.prefix(200), encoding: .utf8) ?? "")
        guard httpStatus == 200 else {
            throw CaptionError.playerRequestFailed(httpStatus)
        }
        let manual = tracks.first { $0.kind == nil && ($0.languageCode?.hasPrefix("en") ?? false) }
        let enAny = tracks.first { $0.languageCode?.hasPrefix("en") ?? false }
        let asr = tracks.first { $0.kind == "asr" }
        guard let track = manual ?? enAny ?? asr ?? tracks.first else {
            throw CaptionError.noCaptions
        }
        var treq = URLRequest(url: URL(string: track.baseUrl + "&fmt=json3")!)
        treq.setValue(Config.userAgent, forHTTPHeaderField: "User-Agent")
        let (tdata, tresp) = try await session.data(for: treq)
        let tstatus = (tresp as? HTTPURLResponse)?.statusCode ?? -1
        DebugLog.shared.record(videoId: videoId, transport: "timedtext",
                               httpStatus: tstatus,
                               bytesPreview: String(data: tdata.prefix(200), encoding: .utf8) ?? "")
        guard tstatus == 200 else {
            throw CaptionError.trackDownloadFailed(tstatus)
        }
        return CaptionText.fromJson3(tdata)
    }

    // MARK: - Upload one result

    func upload(item: WorkItem, lines: [String], bundle: WorkBundle) async throws {
        guard item.slot < bundle.uploadSlots.count else { return }
        var req = URLRequest(url: URL(string: bundle.uploadSlots[item.slot])!)
        req.httpMethod = "PUT"
        let payload: [String: Any] = [
            "video_id": item.videoId,
            "title": item.title ?? "",
            "captured_at": ISO8601DateFormatter().string(from: Date()),
            "lines": lines,
        ]
        req.httpBody = try JSONSerialization.data(withJSONObject: payload)
        let (_, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? -1
        guard (200..<300).contains(code) else { throw CaptionError.trackDownloadFailed(code) }
    }

    func reportStatus(bundle: WorkBundle, results: [[String: Any]]) async {
        guard let slotURL = URL(string: bundle.statusSlot) else { return }
        var req = URLRequest(url: slotURL)
        req.httpMethod = "PUT"
        req.httpBody = try? JSONSerialization.data(withJSONObject: [
            "device": UIDeviceIdentifiers.stableId(),
            "finished_at": ISO8601DateFormatter().string(from: Date()),
            "results": results,
        ])
        _ = try? await session.data(for: req)
    }

    // MARK: - One full batch (GitHub transport; WKWebView resolver)

    func runBatch() async {
        var results: [[String: Any]] = []
        do {
            let queue = try await GH.fetchPending(session: session)
            guard !queue.isEmpty else {
                lastStatus = "queue empty — workstation has not published new work"
                return
            }
            for item in queue.prefix(2) {
                let lines: [String]
                do {
                    // Browser-context fetch (real player runtime: PO tokens mint
                    // for real; the raw-innertube path died to YouTube's wall).
                    lines = try await withCheckedThrowingContinuation { cont in
                        WebViewFetch.shared.fetch(videoId: item.videoId) { result in
                            cont.resume(with: result)
                        }
                    }
                    results.append(["video_id": item.videoId,
                                    "title": item.title ?? "",
                                    "status": "ok",
                                    "captured_at": ISO8601DateFormatter().string(from: Date()),
                                    "lines": lines])
                    fetchedThisSession += 1
                    lastStatus = "ok: \(item.title ?? item.videoId) (\(lines.count) lines)"
                } catch {
                    results.append(["video_id": item.videoId, "title": item.title ?? "",
                                    "status": "error:\(error)", "lines": []])
                    lastStatus = "miss: \(item.title ?? item.videoId) — \(error)"
                }
                let pause = Double.random(in: spacingRange.lowerBound...spacingRange.upperBound)
                try await Task.sleep(nanoseconds: UInt64(pause * 1_000_000_000))
            }
            try await GH.postResults(results: results, session: session)
            try await GH.removeConsumed(ids: results.map { ($0["video_id"] as? String) ?? "" }, session: session)
            try await GH.postDebugLog(session: session)
            lastStatus = "batch uploaded: \(results.count) results"
        } catch {
            // STILL post: a failed batch must not go silent — record + ship home
            results.append(["batch_error": "\(error)", "partial": true])
            try? await GH.postResults(results: results, session: session)
            try? await GH.postDebugLog(session: session)
            lastStatus = "batch partial/failed — posted anyway: \(error)"
        }
    }
}

enum UIDeviceIdentifiers {
    static func stableId() -> String {
        if let s = UserDefaults.standard.string(forKey: "crr_device_id") { return s }
        let s = UUID().uuidString
        UserDefaults.standard.set(s, forKey: "crr_device_id")
        return s
    }
}
