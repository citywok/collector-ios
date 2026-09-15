#!/usr/bin/env python3
"""Register bundle id + app record + mint an App Store profile for the
Collector app, headless (ASC API key auth; adapted from CityDoku's
mint_adhoc_profile.py — same certificate/keychain contract).

Steps:
  1. GET /v1/bundleIds?filter[identifier]=… — POST to register if missing.
  2. GET /v1/apps?filter[bundleId]=…        — POST to create app if missing.
  3. POST /v1/profiles (IOS_APP_STORE, DISTRIBUTION cert embedded).
  4. Download profile + verify it covers a golf-build-keyed identity.
  5. Install to ~/Library/MobileDevice/"Provisioning Profiles"/ and print
     its Name (the PROVISIONING_PROFILE_SPECIFIER for manual signing).
"""
import base64
import json
import sys
import time
from pathlib import Path

import jwt

ASC_KEY_ID = "MA894X726H"
ASC_ISSUER_ID = "69a6de98-0f77-47e3-e053-5b8c7c11a4d1"
ASC_AUTH_KEY_PATH = f"{Path.home()}/.appstoreconnect/private_keys/AuthKey_{ASC_KEY_ID}.p8"
API = "https://api.appstoreconnect.apple.com"
BUNDLE = "me.citywok.speechcollector"
APP_NAME = "Speech Collector"
TEAM = "827WYA3YJJ"
KEYED_IDENTITY_SHA1S = {
    "A82A8DD6490F113A0DD07D435B94A0DAADD7E4EC",
    "069761B4516D10FF6260938BB5C6E2C75F6632E2",
    "D8B689C480523741E998064EF8A0856DE65DD115",
}


def token():
    now = int(time.time())
    return jwt.encode({"iss": ASC_ISSUER_ID, "iat": now, "exp": now + 600,
                       "aud": "appstoreconnect-v1"},
                      open(ASC_AUTH_KEY_PATH).read(),
                      algorithm="ES256", headers={"kid": ASC_KEY_ID, "typ": "JWT"})


def call(method, path, payload=None):
    import urllib.request, urllib.error
    req = __import__("urllib.request", fromlist=["urlopen"]).Request(
        API + path, method=method,
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"},
        data=json.dumps(payload).encode() if payload else None)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.load(r) if r.status != 204 else {}
    except urllib.error.HTTPError as e:
        if e.code in (409, 422):
            raise Conflict(e.code, e.read().decode()[:300])
        raise


class Conflict(Exception):
    pass


def get_or_register_bundle():
    got = call("GET", f"/v1/bundleIds?filter[identifier]={BUNDLE}").get("data") or []
    if got:
        return got[0]
    try:
        return call("POST", "/v1/bundleIds", {
            "data": {"type": "bundleIds",
                     "attributes": {"identifier": BUNDLE, "name": APP_NAME,
                                    "platform": "IOS"}}})["data"]
    except Conflict:
        # already registered upstream (race/ASC auto-registration) — re-fetch
        got = call("GET", f"/v1/bundleIds?filter[identifier]={BUNDLE}").get("data") or []
        if not got:
            raise
        return got[0]


def get_or_create_app():
    got = call("GET", f"/v1/apps?filter[bundleId]={BUNDLE}").get("data") or []
    if got:
        return got[0]
    try:
        return call("POST", "/v1/apps", {
            "data": {"type": "apps",
                     "attributes": {"bundleId": BUNDLE, "name": APP_NAME,
                                    "sku": "speechcollector",
                                    "primaryLocale": "en-US"}}})["data"]
    except Conflict:
        got = call("GET", f"/v1/apps?filter[bundleId]={BUNDLE}").get("data") or []
        if not got:
            raise
        return got[0]



def certs_of():
    return call("GET", "/v1/certificates?filter%5BcertificateType%5D=DISTRIBUTION&limit=200").get("data") or []


def create_profile(bundle):
    certs = certs_of()
    keyed = [c for c in certs if c.get("attributes", {}).get("serialNumber")]
    name = f"Collector Headless AppStore {time.strftime('%Y%m%d-%H%M%S')}"
    payload = {
        "data": {"type": "profiles",
                 "attributes": {"name": name, "profileType": "IOS_APP_STORE"},
                 "relationships": {
                     "bundleId": {"data": {"id": bundle["id"], "type": "bundleIds"}},
                     "certificates": {"data": [{"id": c["id"], "type": "certificates"} for c in certs]}}}}
    return call("POST", "/v1/profiles", payload)["data"]


def download_profile(profile):
    # Two shapes in the wild: (a) GET /profiles/{id} returning base64 in
    # attributes.profileContent; (b) GET /profiles/{id}/profileContent
    # returning the raw DER bytes. Try (a) then (b).
    import urllib.request, urllib.error
    req = __import__("urllib.request", fromlist=["urlopen"]).Request(
        f"{API}/v1/profiles/{profile['id']}",
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.load(r)
        content = (data.get("data", {}).get("attributes") or {}).get("profileContent")
        if content:
            return base64.b64decode(content)
    except urllib.error.HTTPError:
        pass
    req2 = urllib.request.Request(
        f"{API}/v1/profiles/{profile['id']}/profileContent",
        headers={"Authorization": f"Bearer {token()}"})
    with urllib.request.urlopen(req2, timeout=60) as r:
        return r.read()


def profile_facts(raw):
    import plistlib
    body = plistlib.loads(raw)
    ents = body.get("Entitlements") or {}
    import hashlib
    der = body.get("DeveloperCertificates") or []
    sha1s = []
    from cryptography import x509
    for item in der:
        if isinstance(item, bytes):
            try:
                sha1s.append(hashlib.sha1(x509.load_der_x509_certificate(item).fingerprint).hexdigest().upper())
            except Exception:
                pass
    return body, ents.get("application-identifier", ""), sha1s


def main():
    bundle = get_or_register_bundle()
    app = get_or_create_app()
    print(f"bundle ok: {bundle['id']}")
    print(f"app record ok: {app['id']}")
    profile = create_profile(bundle)
    raw = download_profile(profile)
    body, app_id, sha1s = profile_facts(raw)
    usable = sorted(set(sha1s) & KEYED_IDENTITY_SHA1S)
    print(json.dumps({"Name": body.get("Name"), "UUID": body.get("UUID"),
                      "Expires": str(body.get("ExpirationDate")),
                      "app_id": app_id, "keyed_overlap": usable}, indent=2))
    if not usable:
        print("ERROR: minted profile embeds no golf-build-keyed identity", file=sys.stderr)
        return 1
    dest = Path.home() / "Library/MobileDevice/Provisioning Profiles"
    dest.mkdir(exist_ok=True)
    out = dest / f"{body['UUID']}.mobileprovision"
    out.write_bytes(raw)
    print(f"INSTALLED: {out}")
    print(f"PROFILE_NAME: {body['Name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
