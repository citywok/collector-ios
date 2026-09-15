import XCTest
@testable import Collector

final class CollectorEngineTests: XCTestCase {
    // Fixture: player response shape (real structure, trimmed).
    let playerFixture = """
    {"captions":{"playerCaptionsTracklistRenderer":{"captionTracks":[
      {"baseUrl":"https://www.youtube.com/api/timedtext?lang=en&v=XYZ","languageCode":"en"},
      {"baseUrl":"https://www.youtube.com/api/timedtext?lang=en&v=XYZ&kind=asr","languageCode":"en","kind":"asr"},
      {"baseUrl":"https://www.youtube.com/api/timedtext?lang=es&v=XYZ","languageCode":"es"}
    ]}}}
    """.data(using: .utf8)!

    func testCaptionTracksParse() throws {
        let tracks = try PlayerParser.captionTracks(from: playerFixture)
        XCTAssertEqual(tracks.count, 3)
        XCTAssertEqual(tracks[1].kind, "asr")
        XCTAssertEqual(tracks[0].languageCode, "en")
    }

    func testManualPreferredOverASR() throws {
        let tracks = try PlayerParser.captionTracks(from: playerFixture)
        let manual = tracks.first { $0.kind == nil && ($0.languageCode?.hasPrefix("en") ?? false) }
        XCTAssertEqual(manual?.kind, nil) // the first (manual en) wins
    }

    func testNoCaptionsReturnsEmpty() throws {
        let empty = Data(#"{"playabilityStatus":{"status":"LOGIN_REQUIRED"}}"#.utf8)
        let tracks = try PlayerParser.captionTracks(from: empty)
        XCTAssertTrue(tracks.isEmpty)
    }

    func testJson3ToText() {
        let json3 = Data(#"
        {"events":[
          {"segs":[{"utf8":"THE U.S. OPEN WRAPPED "},{"utf8":"UP AT ARTHUR ASHE"}]},
          {"segs":[{"utf8":"\n"}]},
          {"segs":[{"utf8":"STADIUM IN QUEENS."}]}
        ]}
        ""#.utf8)
        let lines = CaptionText.fromJson3(json3)
        XCTAssertEqual(lines, ["THE U.S. OPEN WRAPPED UP AT ARTHUR ASHE", "STADIUM IN QUEENS."])
    }

    func testWorkBundleDecode() throws {
        let json = """
        {"generatedAt":"2026-09-15T12:00:00Z","deviceIdHint":"32A",
         "items":[{"videoId":"abc123","title":"Hannity 9/15","slot":0}],
         "uploadSlots":["https://s3/put/0"],"statusSlot":"https://s3/put/status"}
        """
        let b = try JSONDecoder().decode(WorkBundle.self, from: Data(json.utf8))
        XCTAssertEqual(b.items.count, 1)
        XCTAssertEqual(b.items[0].slot, 0)
        XCTAssertEqual(b.uploadSlots.count, 1)
    }
}
