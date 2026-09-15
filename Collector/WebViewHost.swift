import SwiftUI
import WebKit

/// The app IS the browser: a real, attached, foreground WKWebView that loads
/// YouTube watch pages. No unit-test runner sandbox, no frozen WebContent —
/// the same runtime a human phones use. The extraction JS harvests captions
/// from pages this webview genuinely loaded; results post home via the
/// engine's GitHub transport.
struct WebViewHost: UIViewRepresentable {
    // Keep the host alive across re-renders.
    @Binding var activity: String

    func makeUIView(context: Context) -> WKWebView {
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(WebViewFetch.shared, name: "collector")
        let wv = WKWebView(frame: .zero, configuration: cfg)
        wv.customUserAgent = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
        wv.allowsBackForwardNavigationGestures = false
        WebViewFetch.shared.attach(wv)
        activity = "browser host ready (attached)"
        return wv
    }

    func updateUIView(_ uiView: WKWebView, context: Context) {}
}
