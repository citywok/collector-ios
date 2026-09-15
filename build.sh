#!/bin/bash
# AgentX-style lean build script for the Collector iOS app.
# Runs ON THE MAC (mac-builder daemon or local). Dependencies: xcodegen, xcbeautify (optional).
SIM_LOCK_PROTOCOL=2   # per-device-lock era; intake refuses builds without it
set -euo pipefail
cd "$(dirname "$0")"
APP_NAME=Collector
SCHEME=Collector
BUNDLE_ID=me.citywok.speechcollector
BUILD_DIR=build
ARCHIVE_PATH="$BUILD_DIR/$APP_NAME.xcarchive"
EXPORT_DIR="$BUILD_DIR/export"
EXPORT_PLIST=ExportOptions-AppStore.plist
GENERATE=1
DO_TEST=1
DO_TESTFLIGHT=0

for arg in "$@"; do
  case "$arg" in
    test) DO_TEST=1; DO_TESTFLIGHT=0;;
    testflight) DO_TEST=1; DO_TESTFLIGHT=1;;
    quick) DO_TEST=0;;
  esac
done

# Secrets (provided by the Mac builder env; see memory: golf-build keychain)
# Defaults mirror the proven CityDoku values; ASC key file lives at
# $HOME/.appstoreconnect/private_keys/ on the builder (same account/key).
ASC_KEY_ID="${ASC_KEY_ID:-MA894X726H}"
ASC_ISSUER_ID="${ASC_ISSUER_ID:-69a6de98-0f77-47e3-e053-5b8c7c11a4d1}"
ASC_AUTH_KEY_PATH="${ASC_AUTH_KEY_PATH:-$HOME/.appstoreconnect/private_keys/AuthKey_${ASC_KEY_ID}.p8}"
TEAM_ID="${TEAM_ID:-827WYA3YJJ}"

if [[ "$GENERATE" == 1 ]]; then
  which xcodegen >/dev/null || brew install xcodegen
  xcodegen generate
fi

if [[ "$DO_TEST" == 1 ]]; then
  echo "==> Unit tests on simulator"
  # Destination must be an existing device type on the build host; the host
  # has no iPhone 16 — its fleet is iPhone 17-era (proven in the intake log).
  DEST="platform=iOS Simulator,name=iPhone 17,OS=latest"
  xcodebuild test \
    -scheme "$SCHEME" \
    -destination "$DEST" \
    -resultBundlePath "$BUILD_DIR/test-results.xcresult"
  echo "    ✓ tests passed"
fi

if [[ "$DO_TESTFLIGHT" == 1 ]]; then
  if [[ -z "$ASC_KEY_ID" || -z "$ASC_ISSUER_ID" || ! -f "$ASC_AUTH_KEY_PATH" ]]; then
    echo "ERROR: ASC key env not set (ASC_KEY_ID / ASC_ISSUER_ID / ASC_AUTH_KEY_PATH)" >&2
    exit 2
  fi

  # Build-number: ASC max + 1, floored at git commit count (proven pattern).
  BUILD_NUMBER=$(/usr/bin/python3 - "$ASC_KEY_ID" "$ASC_ISSUER_ID" "$ASC_AUTH_KEY_PATH" "$BUNDLE_ID" <<PYEOF
import sys, json, urllib.request, urllib.parse, jwt, time
kid, iss, keyp, bundle_id = sys.argv[1:5]
key = open(keyp).read(); now = int(time.time())
tok = jwt.encode({"iss": iss, "iat": now, "exp": now+600, "aud": "appstoreconnect-v1"}, key, algorithm="ES256", headers={"kid": kid, "typ": "JWT"})
h = {"Authorization": f"Bearer {tok}"}
req = urllib.request.Request("https://api.appstoreconnect.apple.com/v1/apps?filter[bundleId]="+urllib.parse.quote(bundle_id), headers=h)
apps = json.load(urllib.request.urlopen(req, timeout=30)).get("data", [])
if not apps: print("0"); sys.exit(0)
app_id = apps[0]["id"]
req = urllib.request.Request(f"https://api.appstoreconnect.apple.com/v1/builds?filter[app]={app_id}&limit=200", headers=h)
mx = max([int(b["attributes"].get("version") or 0) for b in json.load(urllib.request.urlopen(req, timeout=30)).get("data", [])] or [0])
print(mx+1)
PYEOF
)
  GITCOUNT=$(git rev-list --count HEAD 2>/dev/null || echo 0)
  BUILD_NUMBER=$(( BUILD_NUMBER > GITCOUNT ? BUILD_NUMBER : GITCOUNT ))
  echo "    build number: $BUILD_NUMBER"

  echo "==> Archiving"
  xcodebuild archive \
    -scheme "$SCHEME" \
    -archivePath "$ARCHIVE_PATH" \
    -destination "generic/platform=iOS" \
    CODE_SIGN_STYLE=Automatic \
    DEVELOPMENT_TEAM="$TEAM_ID" \
    MARKETING_VERSION=0.1.0 CURRENT_PROJECT_VERSION="$BUILD_NUMBER" \
    -allowProvisioningUpdates \
    -authenticationKeyPath "$ASC_AUTH_KEY_PATH" \
    -authenticationKeyID "$ASC_KEY_ID" \
    -authenticationKeyIssuerID "$ASC_ISSUER_ID"

  echo "==> Exporting App Store IPA"
  cat > "$EXPORT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>method</key><string>app-store-connect</string>
  <key>teamID</key><string>${TEAM_ID:-9XBN64MC88}</string>
  <key>uploadSymbols</key><true/>
</dict></plist>
PLIST
  xcodebuild -exportArchive \
    -archivePath "$ARCHIVE_PATH" -exportPath "$EXPORT_DIR" \
    -exportOptionsPlist "$EXPORT_PLIST" -allowProvisioningUpdates \
    -authenticationKeyPath "$ASC_AUTH_KEY_PATH" -authenticationKeyID "$ASC_KEY_ID" \
    -authenticationKeyIssuerID "$ASC_ISSUER_ID"

  echo "==> Uploading to TestFlight"
  IPA=$(find "$EXPORT_DIR" -name '*.ipa' -print -quit)
  test -n "$IPA"
  xcrun altool --upload-app --type ios --file "$IPA" --apiKey "$ASC_KEY_ID" --apiIssuer "$ASC_ISSUER_ID"
  echo "    ✓ uploaded build $BUILD_NUMBER"
fi
echo "DONE"
