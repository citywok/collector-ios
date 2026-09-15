import Foundation
import WebKit

/// Browser-context caption resolver: loads the REAL YouTube watch page in a
/// hidden WKWebView — a genuine player runtime, so YouTube mints real PO
/// tokens for it, per its own 2024-25 anti-scraping regime. Extraction runs
/// in the page's own JS (same origin, same cookies, sanctioned client), and
/// results come back over the webkit.messageHandlers channel.
final class WebViewFetch: NSObject, WKScriptMessageHandler, WKNavigationDelegate {
    static let shared = WebViewFetch()
    private var webView: WKWebView?
    private var jobs: [String: (Result<[String], Error>) -> Void] = [:]

    struct FetchError: Error, LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    func fetch(videoId: String, completion: @escaping (Result<[String], Error>) -> Void) {
        let cfg = WKWebViewConfiguration()
        let uc = cfg.userContentController
        uc.add(self, name: "collector")
        // Injected at document start: waits for ytInitialPlayerResponse, then
        // resolves caption tracks in-page and messages home.
        let bootstrap = """
        (function(){
          const V='%(VID)s';
          const post=(m)=>webkit.messageHandlers.collector.postMessage(Object.assign({videoId:V},m));
          const tryRead=(tries)=>{
            const pr=document.getElementById('movie_player') &&
                     (document.querySelector('ytd-watch-flexy')||{}).playerResponse;
            // Modern pages store the response on the player element/data:
            let resp=null;
            try { resp = window.ytInitialPlayerResponse; } catch(e){}
            if (!resp && document.querySelector('#movie_player')) {
              try { resp = document.getElementById('movie_player').getPlayerResponse(); } catch(e){}
            }
            if (resp) {
              const ps = resp.playabilityStatus || {};
              const tracks = ((resp.captions||{}).playerCaptionsTracklistRenderer||{}).captionTracks||[];
              if (tracks.length) {
                const en = tracks.filter(t=>(t.languageCode||'').startsWith('en'));
                const pool = en.length? en: tracks;
                const manual = pool.filter(t=>t.kind!=='asr');
                const track = (manual[0]||pool[0]);
                fetch(track.baseUrl + '&fmt=json3', {credentials:'include'})
                  .then(r=>r.text())
                  .then(t=>{
                    let lines=[];
                    try {
                      const j=JSON.parse(t);
                      for (const ev of (j.events||[])) {
                        const txt=(ev.segs||[]).map(s=>s.utf8||'').join('').trim();
                        if (txt && txt!=='\\n') lines.push(txt);
                      }
                    } catch(err){ post({error:'track parse: '+err}); return; }
                    post({lines: lines});
                  })
                  .catch(e=>post({error:'track fetch: '+e}));
              } else {
                post({playability: ps.status||'', reason: ps.reason||'', tracks: 0});
              }
            } else if (tries > 0) {
              setTimeout(()=>tryRead(tries-1), 1500);
            } else {
              post({error: 'no playerResponse found on page'});
            }
          };
          tryRead(10);
        })();
        """.replacingOccurrences(of: "%(VID)s", with: videoId)
        let us = WKUserScript(source: bootstrap, injectionTime: .atDocumentEnd, forMainFrameOnly: true)
        uc.addUserScript(us)
        let wv = WKWebView(frame: CGRect(x: 0, y: 0, width: 390, height: 844), configuration: cfg)
        wv.customUserAgent = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
        wv.isHidden = true
        wv.navigationDelegate = self
        self.webView = wv
        jobs[videoId] = completion
        wv.load(URLRequest(url: URL(string: "https://www.youtube.com/watch?v=\(videoId)")!))
        DebugLog.shared.record(videoId: videoId, transport: "wkwebview", httpStatus: 0,
                               playability: "loading", reason: "page load started")
    }

    // MARK: message from page JS

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard message.name == "collector",
              let body = message.body as? [String: Any],
              let videoId = body["videoId"] as? String else { return }
        let lines = (body["lines"] as? [String]) ?? []
        if !lines.isEmpty {
            DebugLog.shared.record(videoId: videoId, transport: "wkwebview",
                                   httpStatus: 200, playability: "OK",
                                   trackCount: lines.count)
            jobs[videoId]?(.success(lines))
            jobs.removeValue(forKey: videoId)
            return
        }
        let err = (body["error"] as? String) ?? "unknown"
        DebugLog.shared.record(videoId: videoId, transport: "wkwebview",
                               httpStatus: -1, playability: (body["playability"] as? String) ?? "",
                               reason: (body["reason"] as? String) ?? "", errorText: err)
        jobs[videoId]?(.failure(FetchError(message: err)))
        jobs.removeValue(forKey: videoId)
    }

    // MARK: navigation failures (network-level)

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        DebugLog.shared.record(videoId: "nav", transport: "wkwebview",
                               httpStatus: -1, errorText: error.localizedDescription)
    }
}
