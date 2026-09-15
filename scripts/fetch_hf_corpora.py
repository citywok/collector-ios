#!/usr/bin/env python3
"""Fetch public speech corpora for the thesis test (HF/GitHub, not archive.org).

Datasets:
  1. Eugleo/us-congressional-speeches   (17.4M attributed rows, 13.7GB) —
     the 1995/2015-2024 longitudinal axis for the gate-regime question.
  2. macleginn/stanford_congress_record_longer_speeches (27k speeches,
     party/state/gender) — small, instant smoke-test + metadata cross-check.
  3. yeeder/congressional-record-parquet — freshness check only (list files).

Stores under /tmp/hf_corpora. Uses huggingface_hub snapshot_download
(hf_transfer disabled, normal retry). Resumable by construction.
"""
import json
import os
import sys

OUT = "/tmp/hf_corpora"
os.makedirs(OUT, exist_ok=True)


def main():
    from huggingface_hub import snapshot_download
    results = {}
    # 1) the big one — parquet-only snapshot to keep it managebles
    try:
        p = snapshot_download(
            repo_id="Eugleo/us-congressional-speeches",
            repo_type="dataset",
            allow_patterns=["*.parquet", "*.json", "README*"],
            local_dir=f"{OUT}/eugleo",
            max_workers=4,
        )
        results["eugleo"] = {"path": p, "ok": True}
        print("EUGLEO_OK", p, flush=True)
    except Exception as e:
        results["eugleo"] = {"error": f"{type(e).__name__}: {e}"[:200]}
        print("EUGLEO_FAIL", results["eugleo"]["error"], flush=True)
    # 2) small smoke-test set
    try:
        p = snapshot_download(
            repo_id="macleginn/stanford_congress_record_longer_speeches",
            repo_type="dataset",
            local_dir=f"{OUT}/macleginn",
        )
        results["macleginn"] = {"path": p, "ok": True}
        print("MACLEGINN_OK", p, flush=True)
    except Exception as e:
        results["macleginn"] = {"error": f"{type(e).__name__}: {e}"[:200]}
        print("MACLEGINN_FAIL", results["macleginn"]["error"], flush=True)
    # 3) freshness inventory (file list only)
    try:
        from huggingface_hub import list_repo_files
        files = list_repo_files("yeeder/congressional-record-parquet", repo_type="dataset")
        results["yeeder"] = {"files": len(files), "sample": files[:6], "ok": True}
        print("YEEDER_INVENTORY", len(files), files[:3], flush=True)
    except Exception as e:
        results["yeeder"] = {"error": f"{type(e).__name__}: {e}"[:200]}
        print("YEEDER_FAIL", results["yeeder"]["error"], flush=True)
    json.dump(results, open(f"{OUT}/fetch_status.json", "w"), indent=1)
    print("HF_FETCH_DONE", flush=True)


if __name__ == "__main__":
    main()
