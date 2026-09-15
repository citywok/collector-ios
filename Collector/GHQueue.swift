/// GitHub-based queue transport (replaces the S3/Lambda plan — the personal
/// AWS account blocks public function URLs and public bucket policies).
///
/// The queue lives in a dedicated private repo (collector-data):
///   queue/pending.json    — workstation writes; phone GETs and consumes
///   results/<date>.jsonl  — phone POSTs captured captions (append-style per
///                            day, single device so no clobbering)
/// The app authenticates with a fine-grained PAT scoped to that one repo,
/// embedded at build time via CRR_COLLECTOR_TOKEN (build.sh → Info.plist).
import Foundation

enum GH {
    static var token: String {
        if let s = UserDefaults.standard.string(forKey: "crr_pat"), !s.isEmpty {
            return s  // device-pasted token (onboarding screen) — preferred
        }
        let s = Bundle.main.object(forInfoDictionaryKey: "CRRCollectorToken") as? String ?? ""
        return s.isEmpty ? (ProcessInfo.processInfo.environment["CRR_COLLECTOR_TOKEN"] ?? "") : s
    }
    static var repo: String {
        if let s = UserDefaults.standard.string(forKey: "crr_repo"), !s.isEmpty {
            return s
        }
        let s = Bundle.main.object(forInfoDictionaryKey: "CRRCollectorRepo") as? String ?? ""
        return s.isEmpty ? (ProcessInfo.processInfo.environment["CRR_COLLECTOR_REPO"] ?? "") : s
    }
    static var api: URLComponents { URLComponents(string: "https://api.github.com")! }

    struct PendingItem: Codable {
        let videoId: String
        let title: String?
    }

    static func request(path: String, method: String, json: [String: Any]? = nil) -> URLRequest {
        var c = api
        c.path = path
        var req = URLRequest(url: c.url!)
        req.httpMethod = method
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        req.setValue("Collector/0.1", forHTTPHeaderField: "User-Agent")
        if let json {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try? JSONSerialization.data(withJSONObject: json)
        }
        return req
    }

    /// Push the device's diagnostic log home (results/debug-<ts>.json) —
    /// no PAT needed beyond the repo contents grant; this is the
    /// "app tells me what it saw" channel. Auto-fires after every batch.
    static func postDebugLog(session: URLSession) async throws {
        let log = DebugLog.shared
        let payload: [[String: Any]] = log.attempts.map { a in
            ["ts": a.ts, "videoId": a.videoId, "transport": a.transport,
             "http": a.httpStatus, "playability": a.playability, "reason": a.reason,
             "tracks": a.trackCount, "params": a.firstTrackParams,
             "preview": a.bytesPreview, "err": a.errorText]
        }
        let day = String(ISO8601DateFormatter().string(from: Date()).prefix(10))
        let path = "results/debug-\(day).json"
        var prior: [[String: Any]] = []
        var sha: String?
        let get = request(path: "/repos/\(repo)/contents/\(path)", method: "GET")
        let (gdata, gresp) = try await session.data(for: get)
        if (gresp as? HTTPURLResponse)?.statusCode == 200,
           let obj = try? JSONSerialization.jsonObject(with: gdata) as? [String: Any],
           let content = obj["content"] as? String,
           let decoded = Data(base64Encoded: content.replacingOccurrences(of: "\n", with: "")),
           let priorObj = try? JSONSerialization.jsonObject(with: decoded) as? [[String: Any]] {
            prior = priorObj
            sha = obj["sha"] as? String
        }
        var all = prior
        all.append(contentsOf: payload)
        let put = request(path: "/repos/\(repo)/contents/\(path)", method: "PUT",
                          json: ["message": "debug log \(day) (\(payload.count) attempts)",
                                 "content": Data(try JSONSerialization.data(withJSONObject: all)).base64EncodedString(),
                                 "sha": sha ?? ""])
        _ = try await session.data(for: put)
    }

    /// GET the pending queue (single file on main).
    static func fetchPending(session: URLSession) async throws -> [PendingItem] {
        let req = request(path: "/repos/\(repo)/contents/queue/pending.json", method: "GET")
        let (data, resp) = try await session.data(for: req)
        guard (resp as? HTTPURLResponse)?.statusCode == 200 else { return [] }
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let content = obj["content"] as? String,
              let decoded = Data(base64Encoded: content.replacingOccurrences(of: "\n", with: "")) else {
            return []
        }
        struct Pending: Codable { let items: [PendingItem] }
        let pending = try JSONDecoder().decode(Pending.self, from: decoded)
        return pending.items
    }

    /// POST captured results: appends to results/YYYY-MM-DD.json (full-file
    /// PUT per batch — single device, no contention).
    static func postResults(results: [[String: Any]], session: URLSession) async throws {
        let day = ISO8601DateFormatter().string(from: Date()).prefix(10)
        let path = "results/\(day).json"
        var prior: [[String: Any]] = []
        var sha: String?
        let get = request(path: "/repos/\(repo)/contents/\(path)", method: "GET")
        let (gdata, gresp) = try await session.data(for: get)
        if (gresp as? HTTPURLResponse)?.statusCode == 200,
           let obj = try? JSONSerialization.jsonObject(with: gdata) as? [String: Any],
           let content = obj["content"] as? String,
           let decoded = Data(base64Encoded: content.replacingOccurrences(of: "\n", with: "")),
           let priorObj = try? JSONSerialization.jsonObject(with: decoded) as? [[String: Any]] {
            prior = priorObj
            sha = obj["sha"] as? String
        }
        var all = prior
        all.append(contentsOf: results)
        var put = request(path: "/repos/\(repo)/contents/\(path)", method: "PUT",
                          json: ["message": "results \(Date())",
                                 "content": Data(try JSONSerialization.data(withJSONObject: all)).base64EncodedString(),
                                 "sha": sha ?? ""])
        put.httpBody = try? JSONSerialization.data(withJSONObject: [
            "message": "results \(Date())",
            "content": Data(try JSONSerialization.data(withJSONObject: all)).base64EncodedString(),
            "sha": sha ?? "",
        ])
        _ = try await session.data(for: put)
    }

    /// Remove consumed items from pending.json.
    static func removeConsumed(ids: [String], session: URLSession) async throws {
        let get = request(path: "/repos/\(repo)/contents/queue/pending.json", method: "GET")
        let (data, resp) = try await session.data(for: get)
        guard (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let content = obj["content"] as? String,
              let decoded = Data(base64Encoded: content.replacingOccurrences(of: "\n", with: "")) else { return }
        struct Pending: Codable { var items: [PendingItem]; var updatedAt: String? }
        var pending = try JSONDecoder().decode(Pending.self, from: decoded)
        let drops = Set(ids)
        pending.items.removeAll { drops.contains($0.videoId) }
        let put = request(path: "/repos/\(repo)/contents/queue/pending.json", method: "PUT",
                          json: ["message": "consume \(ids.count)",
                                 "content": Data(try JSONEncoder().encode(pending)).base64EncodedString(),
                                 "sha": (obj["sha"] as? String) ?? ""])
        _ = try await session.data(for: put)
    }
}
