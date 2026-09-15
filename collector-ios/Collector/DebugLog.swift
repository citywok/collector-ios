import Foundation

/// Rolling in-app diagnostic log of every caption-fetch attempt:
/// what was requested, what YouTube actually returned (anonymized enough
/// to post publicly but complete enough to debug remotely).
struct FetchAttempt: Codable {
    var ts: String
    var videoId: String
    var transport: String        // "innertube-v1" / "wkwebview" / "direct"
    var httpStatus: Int
    var playability: String      // status string from the player payload
    var reason: String           // playability reason (bot-wall, unplayable…)
    var trackCount: Int
    var firstTrackParams: String // query params of track[0] (no full URL; hides token)
    var bytesPreview: String     // first 200 chars of the raw response
    var errorText: String
}

final class DebugLog: ObservableObject {
    static let shared = DebugLog()
    @Published var attempts: [FetchAttempt] = []

    func record(videoId: String, transport: String, httpStatus: Int,
                playability: String = "", reason: String = "",
                trackCount: Int = 0, firstTrackParams: String = "",
                bytesPreview: String = "", errorText: String = "") {
        let a = FetchAttempt(ts: ISO8601DateFormatter().string(from: Date()),
                             videoId: videoId, transport: transport,
                             httpStatus: httpStatus, playability: playability,
                             reason: reason, trackCount: trackCount,
                             firstTrackParams: firstTrackParams,
                             bytesPreview: String(bytesPreview.prefix(200)),
                             errorText: String(errorText.prefix(300)))
        attempts.append(a)
        if attempts.count > 20 { attempts.removeFirst(attempts.count - 20) }
        UserDefaults.standard.set(try? JSONEncoder().encode(attempts), forKey: "crr_debug_log")
    }

    init() {  // restore across launches
        if let d = UserDefaults.standard.data(forKey: "crr_debug_log"),
           let a = try? JSONDecoder().decode([FetchAttempt].self, from: d) {
            attempts = a
        }
    }

    var summary: String {
        attempts.map { a in
            "[\(a.ts.prefix(19))] \(a.videoId) via \(a.transport): HTTP \(a.httpStatus) \(a.playability) '\(a.reason.prefix(60))' tracks=\(a.trackCount) \(a.errorText.isEmpty ? "" : "ERR: \(a.errorText.prefix(120))")"
        }.joined(separator: "\n")
    }
}
