"""Lambda v2: the collector queue.

GET  /work     -> next batch (up to 8 pending video ids, popped from S3
                  collector/v1/queue/pending.json, mark-served with a served
                  timestamp so retry logic stays crude-but-obvious)
POST /submit   -> JSON body; validates <= 2MB, writes to collector/v1/results/
                  and removes the item from pending.json on success

No presigned slots, no anonymous S3 reads. CORS open (PWA/Shortcuts can call).
"""
import json
import os
from datetime import datetime, timezone

import boto3

BUCKET = "llm-chat-artifacts"
QUEUE_KEY = "collector/v1/queue/pending.json"
RESULTS_PREFIX = "collector/v1/results"
MAX_BATCH = 8
MAX_BODY = 2_000_000

s3 = boto3.client("s3")


def _now():
    return datetime.now(timezone.utc)


def _serve_batch():
    try:
        obj = s3.get_object(Bucket=BUCKET, Key=QUEUE_KEY)
        pending = json.loads(obj["Body"].read().decode())
    except s3.exceptions.NoSuchKey:
        pending = []
    if isinstance(pending, dict):
        items = pending.get("items", [])
    else:
        items = pending
    served, rest = items[:MAX_BATCH], items[MAX_BATCH:]
    if served:
        now = _now().isoformat()
        for it in served:
            it["servedAt"] = now
        body = json.dumps({"updatedAt": now, "items": rest}).encode()
        s3.put_object(Bucket=BUCKET, Key=QUEUE_KEY, Body=body, ContentType="application/json")
    return served


def _submit(body):
    if not body or len(body) > MAX_BODY:
        return 400, {"error": "empty or oversized body"}
    try:
        data = json.loads(body)
    except Exception:
        return 400, {"error": "invalid json"}
    vid = data.get("video_id") or "unknown"
    if not isinstance(data.get("lines"), list) or len(data["lines"]) > 200_000:
        return 400, {"error": "missing/oversized lines"}
    ts = _now().strftime("%Y%m%dT%H%M%SZ")
    key = f"{RESULTS_PREFIX}/{ts[:8]}/{ts}_{vid[:80].replace('/', '_')}.json"
    s3.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(data).encode(), ContentType="application/json")
    return 200, {"ok": True, "stored": key}


def handler(event, context):
    method = (event.get("requestContext") or {}).get("http", {}).get("method", "GET")
    if method == "GET":
        return _response(200, {"items": _serve_batch()})
    if method == "POST":
        code, payload = _submit(event.get("body") or "")
        return _response(code, payload)
    return _response(405, {"error": "method"})


def _response(code, obj):
    return {
        "statusCode": code,
        "headers": {
            "content-type": "application/json",
            "access-control-allow-origin": "*",
            "access-control-allow-methods": "GET,POST,OPTIONS",
            "access-control-allow-headers": "content-type",
        },
        "body": json.dumps(obj),
    }
