import Foundation

/// One unit of collection work, handed to the device by the workstation.
struct WorkItem: Codable, Identifiable {
    var videoId: String
    var title: String?
    var slot: Int               // index of the presigned-PUT slot reserved for this result
    var id: String { videoId }
}

/// The workstation-generated bundle: which videos to fetch this session and
/// where (presigned S3 PUT) to upload each result. The app holds no S3
/// credentials; the workstation mints short-lived PUT slots per batch.
struct WorkBundle: Codable {
    var generatedAt: String
    var deviceIdHint: String?
    var items: [WorkItem]
    var uploadSlots: [String]   // presigned PUT URLs, indexed by item.slot
    var statusSlot: String      // presigned PUT for the per-batch status report
}

struct CaptionTrack: Codable {
    var baseUrl: String
    var languageCode: String?
    var kind: String?           // "asr" for auto-generated, nil for manual
}

enum CaptionError: Error, CustomStringConvertible {
    case noCaptions
    case playerRequestFailed(Int)
    case trackDownloadFailed(Int)
    case invalidPlayerPayload

    var description: String {
        switch self {
        case .noCaptions: return "no captions"
        case .playerRequestFailed(let s): return "player HTTP \(s)"
        case .trackDownloadFailed(let s): return "track HTTP \(s)"
        case .invalidPlayerPayload: return "invalid player payload"
        }
    }
}

/// Extract the caption tracks from a youtubei/v1/player response.
/// Pure function — unit-tested with real-ish fixture payloads.
enum PlayerParser {
    static func captionTracks(from data: Data) throws -> [CaptionTrack] {
        guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let captions = obj["captions"] as? [String: Any],
              let renderer = captions["playerCaptionsTracklistRenderer"] as? [String: Any],
              let tracks = renderer["captionTracks"] as? [[String: Any]] else {
            return []
        }
        return tracks.compactMap { t in
            guard let url = t["baseUrl"] as? String else { return nil }
            return CaptionTrack(baseUrl: url,
                                languageCode: t["languageCode"] as? String,
                                kind: t["kind"] as? String)
        }
    }
}

/// Convert a json3 caption payload into plain text.
/// Pure function — unit-tested.
enum CaptionText {
    static func fromJson3(_ data: Data) -> [String] {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let events = obj["events"] as? [[String: Any]] else { return [] }
        var lines: [String] = []
        for e in events {
            guard let segs = e["segs"] as? [[String: Any]] else { continue }
            let line = segs.compactMap { $0["utf8"] as? String }.joined()
            let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmed.isEmpty, trimmed != "\n" { lines.append(trimmed) }
        }
        return lines
    }
}
