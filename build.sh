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
DO_SMOKE=0

for arg in "$@"; do
  case "$arg" in
    test) DO_TEST=1; DO_TESTFLIGHT=0;;
    testflight) DO_TEST=1; DO_TESTFLIGHT=1;;
    quick) DO_TEST=0;;
    smoke) DO_SMOKE=1;;
  esac
done

# Secrets (provided by the Mac builder env; see memory: golf-build keychain)
# Defaults mirror the proven CityDoku values; ASC key file lives at
# $HOME/.appstoreconnect/private_keys/ on the builder (same account/key).
ASC_KEY_ID="${ASC_KEY_ID:-MA894X726H}"
ASC_ISSUER_ID="${ASC_ISSUER_ID:-69a6de98-0f77-47e3-e053-5b8c7c11a4d1}"
ASC_AUTH_KEY_PATH="${ASC_AUTH_KEY_PATH:-$HOME/.appstoreconnect/private_keys/AuthKey_${ASC_KEY_ID}.p8}"
TEAM_ID="${TEAM_ID:-827WYA3YJJ}"

# Manual-signing override (the headless TestFlight recipe): when
# PROVISIONING_PROFILE_SPECIFIER_OVERRIDE is set, archive signs manually with
# the golf-build keychain's Apple Distribution cert + the named on-disk
# profile — no Apple-ID session, no ASC roundtrip needed at archive time.
PROVISIONING_PROFILE_SPECIFIER="${PROVISIONING_PROFILE_SPECIFIER_OVERRIDE:-}"
CODE_SIGN_IDENTITY="${CODE_SIGN_IDENTITY_OVERRIDE:-}"

# ── Headless codesign keychain unlock (ported from CityDoku build.sh) ──────
# The Apple Development identity's PRIVATE KEY lives in the golf-build
# keychain; if this env is empty, source the host's own file. The unlock
# must use ABSOLUTE keychain paths (security resolves bare names against
# cwd — the daemon/SSH cwd is /) and codesign in a non-GUI session
# resolves through the DEFAULT keychain, not just the search list.
CODESIGN_KEYCHAIN="${CODESIGN_KEYCHAIN:-golf-build.keychain}"
KEYCHAIN_RESTORE_NEEDED=0
KEYCHAIN_RESOLVED=""
if [[ -z "${CODESIGN_KEYCHAIN_PASSWORD:-}" && -f "${HOME}/.golf-publish.env" ]]; then
    # shellcheck disable=SC1090
    . "${HOME}/.golf-publish.env" 2>/dev/null || true
    CODESIGN_KEYCHAIN_PASSWORD="${CODESIGN_KEYCHAIN_PASSWORD:-${GOLF_BUILD_KEYCHAIN_PW:-}}"
fi
restore_keychain_search_list() {
    if [[ "$KEYCHAIN_RESTORE_NEEDED" == "1" ]] && command -v security >/dev/null 2>&1; then
        security list-keychains -d user -s "${HOME}/Library/Keychains/login.keychain-db" "$KEYCHAIN_RESOLVED" >/dev/null 2>&1 || true
        security default-keychain -d user -s "${HOME}/Library/Keychains/login.keychain-db" >/dev/null 2>&1 || true
    fi
}
trap restore_keychain_search_list EXIT
unlock_codesign_keychain() {
    command -v security >/dev/null 2>&1 || return 0
    if [[ -z "${CODESIGN_KEYCHAIN_PASSWORD:-}" ]]; then
        echo "    CODESIGN_KEYCHAIN_PASSWORD not set; assuming $CODESIGN_KEYCHAIN already unlocked"
        return 0
    fi
    local chain="$CODESIGN_KEYCHAIN"
    local chain_dir="${HOME}/Library/Keychains"
    if [[ -f "${chain_dir}/${chain}" ]]; then
        chain="${chain_dir}/${chain}"
    elif [[ -f "${chain_dir}/${chain}-db" ]]; then
        chain="${chain_dir}/${chain}-db"
    fi
    echo "==> Unlocking codesign keychain ($chain)"
    security list-keychains -d user -s "${HOME}/Library/Keychains/login.keychain-db" "$chain" >/dev/null
    security default-keychain -d user -s "$chain" >/dev/null 2>&1 || true
    KEYCHAIN_RESTORE_NEEDED=1
    KEYCHAIN_RESOLVED="$chain"
    security unlock-keychain -p "$CODESIGN_KEYCHAIN_PASSWORD" "$chain"
    security set-key-partition-list -S apple-tool:,apple:,codesign: -s \
        -k "$CODESIGN_KEYCHAIN_PASSWORD" "$chain" >/dev/null
    echo "    ✓ Codesign keychain ready"
}

if [[ "$GENERATE" == 1 ]]; then
  # brew lives in /opt/homebrew/bin on this Mac (daemon/SSH PATH misses it).
  export PATH="/opt/homebrew/bin:/opt/homebrew/sbin:$PATH"
  which xcodegen >/dev/null || brew install xcodegen
  xcodegen generate
fi

if [[ "$DO_SMOKE" == 1 ]]; then
  echo "==> LIVE smoke on simulator (real YouTube + GitHub)"
  # transport token: Mac-local file, 600 perms, never committed
  export CRR_COLLECTOR_TOKEN="${CRR_COLLECTOR_TOKEN:-$(cat ${HOME}/.collector-smoke-token 2>/dev/null)}"
  export CRR_LIVE_SMOKE=1
  # XCTest env passthrough: TEST_RUNNER_<VAR> reaches the sim runner process.
  export TEST_RUNNER_CRR_LIVE_SMOKE=1
  export TEST_RUNNER_CRR_COLLECTOR_TOKEN="$CRR_COLLECTOR_TOKEN"
  xcodebuild test \
    -scheme "$SCHEME" \
    -destination "platform=iOS Simulator,name=iPhone 17,OS=latest" \
    -only-testing:CollectorTests/LiveSmokeTests \
    -resultBundlePath "$BUILD_DIR/smoke-results.xcresult"
  echo "    ✓ live smoke passed"
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
  unlock_codesign_keychain
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
  sign_args=()
  if [[ -n "${PROVISIONING_PROFILE_SPECIFIER_OVERRIDE:-}" ]]; then
    # Headless manual signing: golf-build keychain Distribution cert + named
    # on-disk profile (the CityDoku OTA recipe — no Apple-ID session needed).
    sign_args+=(CODE_SIGN_STYLE=Manual)
    sign_args+=(CODE_SIGN_IDENTITY="${CODE_SIGN_IDENTITY_SHA1_OVERRIDE:-D8B689C480523741E998064EF8A0856DE65DD115}")
    sign_args+=(PROVISIONING_PROFILE_SPECIFIER="$PROVISIONING_PROFILE_SPECIFIER_OVERRIDE")
    sign_args+=(OTHER_CODE_SIGN_FLAGS="--keychain=${HOME}/Library/Keychains/golf-build.keychain-db")
    sign_args+=(EXPANDED_CODE_SIGN_ALLOW_ENTITLEMENTS_MODIFICATION=YES)
  else
    sign_args+=(CODE_SIGN_STYLE=Automatic)
    sign_args+=(DEVELOPMENT_TEAM="$TEAM_ID")
  fi
  xcodebuild archive \
    -scheme "$SCHEME" \
    -archivePath "$ARCHIVE_PATH" \
    -destination "generic/platform=iOS" \
    "${sign_args[@]}" \
    MARKETING_VERSION=0.1.0 CURRENT_PROJECT_VERSION="$BUILD_NUMBER" \
    -allowProvisioningUpdates \
    -authenticationKeyPath "$ASC_AUTH_KEY_PATH" \
    -authenticationKeyID "$ASC_KEY_ID" \
    -authenticationKeyIssuerID "$ASC_ISSUER_ID"

  echo "==> Exporting App Store IPA"
  if [[ -n "${PROVISIONING_PROFILE_SPECIFIER_OVERRIDE:-}" ]]; then
    # Manual export: profile map + distribution cert pins (headless, no
    # Apple-ID session — CityDoku OTA recipe).
    cat > "$EXPORT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>method</key><string>${EXPORT_METHOD_OVERRIDE:-app-store-connect}</string>
  <key>teamID</key><string>${TEAM_ID}</string>
  <key>signingStyle</key><string>manual</string>
  <key>provisioningProfiles</key><dict>
    <key>${BUNDLE_ID}</key><string>${PROVISIONING_PROFILE_SPECIFIER_OVERRIDE}</string>
  </dict>
  <key>signingCertificate</key><string>${CODE_SIGN_IDENTITY_OVERRIDE:-Apple Distribution: Andrew Parisio (${TEAM_ID})}</string>
  <key>uploadSymbols</key><false/>
</dict></plist>
PLIST
  else
    cat > "$EXPORT_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>method</key><string>app-store-connect</string>
  <key>teamID</key><string>${TEAM_ID}</string>
  <key>uploadSymbols</key><true/>
</dict></plist>
PLIST
  fi
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
