import Foundation
import WebKit

/// Browser-context caption resolver for the REAL app runtime.
/// The WKWebView lives in the app's view hierarchy (WebViewHost); fetches
/// load the watch page into it and run the extraction in the page's own JS.
/// A background-queue watchdog guarantees completion even if a page stalls.
final class WebViewFetch: NSObject, WKScriptMessageHandler {
    static let shared = WebViewFetch()
    private weak var webView: WKWebView?
    private var jobs: [String: (Result<[String], Error>) -> Void] = [:]

    struct FetchError: Error, LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    /// Called by the SwiftUI host with the live, foreground webview.
    func attach(_ wv: WKWebView) {
        self.webView = wv
    }

    func fetch(videoId: String, completion: @escaping (Result<[String], Error>) -> Void) {
        if !Thread.isMainThread {
            DispatchQueue.main.async { self.fetch(videoId: videoId, completion: completion) }
            return
        }
        guard let wv = webView, wv.window != nil else {
            completion(.failure(FetchError(message: "browser host not attached — open the Fetch tab first")))
            return
        }
        jobs[videoId] = completion

        // Extraction runs inside the page (same-origin, real cookies/PO tokens).
        let inject = """
        (function(){
          const post=(m)=>webkit.messageHandlers.collector.postMessage(Object.assign({videoId:'\(videoId)'},m));
          const tryRead=(tries)=>{
            let resp=null;
            try { resp = window.ytInitialPlayerResponse; } catch(e){}
            if (!resp) { try { resp = document.getElementById('movie_player').getPlayerResponse(); } catch(e){} }
            if (resp) {
              const ps = resp.playabilityStatus || {};
              const tracks = ((resp.captions||{}).playerCaptionsTracklistRenderer||{}).captionTracks||[];
              if (tracks.length) {
                const en = tracks.filter(t=>(t.languageCode||'').startsWith('en'));
                const pool = en.length? en: tracks;
                const manual = pool.filter(t=>t.kind!=='asr');
                const track = (manual[0]||pool[0]);
                post({playability: ps.status||'', tracks: tracks.length});
                // Start playback (muted) so the player mints the caption URL
                // with the real PO token — fetching before playing yields an
                // empty body (Unexpected EOF seen 2026-09-16).
                const mp=document.getElementById('movie_player');
                // Play FIRST, then RE-READ the player's captionTracks: the
                // minted (pot-bearing) URL only exists in the player's
                // UPDATED response after playback starts — the pre-play
                // URL yields an empty json3 (the 20-attempt phone bundle
                // proved it 2026-09-16).
                const startTry=(n)=>{
                  try {
                    if (mp && mp.playVideo) { mp.mute && mp.mute(); mp.playVideo(); }
                    const v=document.querySelector('video');
                    if (v) { v.muted=true; v.play().catch(()=>{}); }
                  } catch(e){}
                  setTimeout(()=>{
                    let mintedBase=null;
                    try {
                      const fr=mp && mp.getPlayerResponse && mp.getPlayerResponse();
                      const frTracks=((fr.captions||{}).playerCaptionsTracklistRenderer||{}).captionTracks||[];
                      if (frTracks.length) {
                        const en2=frTracks.filter(t=>(t.languageCode||'').startsWith('en'));
                        const pool2=en2.length? en2: frTracks;
                        const man2=pool2.filter(t=>t.kind!=='asr');
                        const t2=(man2[0]||pool2[0]);
                        mintedBase=t2.baseUrl;
                      }
                    } catch(e){}
                    const useBase = mintedBase || track.baseUrl;
                    if (mintedBase) post({playability:'MINTED', reason:'player re-emitted track post-play', tracks:1});
                    else post({playability:'NO-MINT', reason:'player kept same track URL', tracks:1});
                    fetch(useBase + '&fmt=json3', {credentials:'include'})
                      .then(r=>r.text())
                      .then(t=>{
                        post({http:0, reason:'fetch len='+t.length+' head='+(t||'').slice(0,90)});
                        if (!t || t.length < 50) {
                          if (n>0) { startTry(n-1); return; }
                          post({error:'track empty after play '+ (t?t.length:0)});
                          return;
                        }
                        let lines=[];
                        try {
                          const j=JSON.parse(t);
                          for (const ev of (j.events||[])) {
                            const txt=(ev.segs||[]).map(s=>s.utf8||'').join('').trim();
                            if (txt && txt!=='\\n') lines.push(txt);
                          }
                        } catch(err){ post({error:'track parse: '+err}); return; }
                        post({lines: lines});
                      }).catch(e=>post({error:'track fetch: '+e}));
                  }, 6000);
                };
                startTry(3);
              } else if (tries > 0) { setTimeout(()=>tryRead(tries-1), 2000); }
              else { post({playability: ps.status||'', reason: ps.reason||'', tracks: 0, error: 'no caption tracks'}); }
            } else if (tries > 0) {
              setTimeout(()=>tryRead(tries-1), 2000);
            } else { post({error: 'no playerResponse on page after 10 probes'}); }
          };
          tryRead(10);
        })();
        """
        wv.configuration.userContentController.removeAllUserScripts()
        wv.configuration.userContentController.addUserScript(
            WKUserScript(source: inject, injectionTime: .atDocumentEnd, forMainFrameOnly: true))

        // Background-queue watchdog: 60s cap per video, immune to page stalls.
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + 60) { [weak self] in
            guard let self, let job = self.jobs.removeValue(forKey: videoId) else { return }
            DebugLog.shared.record(videoId: videoId, transport: "wkwebview-app",
                                   httpStatus: 0, playability: "TIMEOUT",
                                   reason: "page never produced captions in 60s",
                                   errorText: "watchdog fired")
            DispatchQueue.main.async {
                self.webView?.stopLoading()
                job(.failure(FetchError(message: "watchdog: no captions in 60s")))
            }
        }

        DebugLog.shared.record(videoId: videoId, transport: "wkwebview-app",
                               httpStatus: 0, playability: "loading",
                               reason: "driving app-owned browser")
        wv.load(URLRequest(url: URL(string: "https://www.youtube.com/watch?v=\(videoId)")!))
    }

    // MARK: page → app messages

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        guard message.name == "collector",
              let body = message.body as? [String: Any],
              let videoId = body["videoId"] as? String else { return }
        let lines = (body["lines"] as? [String]) ?? []
        if !lines.isEmpty {
            DebugLog.shared.record(videoId: videoId, transport: "wkwebview-app",
                                   httpStatus: 200, playability: "OK", trackCount: lines.count)
            jobs[videoId]?(.success(lines))
            jobs.removeValue(forKey: videoId)
            return
        }
        let err = (body["error"] as? String) ?? "no lines"
        DebugLog.shared.record(videoId: videoId, transport: "wkwebview-app",
                               httpStatus: (body["http"] as? Int) ?? -1,
                               playability: (body["playability"] as? String) ?? "",
                               reason: (body["reason"] as? String) ?? "",
                               trackCount: (body["tracks"] as? Int) ?? 0,
                               errorText: err)
        jobs[videoId]?(.failure(FetchError(message: err)))
        jobs.removeValue(forKey: videoId)
    }
}
