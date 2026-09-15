#!/usr/bin/env python3
"""CREC corpus downloader with magic-byte verification.

Downloads daily Congressional Record PDFs from govinfo, verifying each
response is a real PDF (magic bytes %PDF), because govinfo returns its
HTML error page with HTTP 200 on transient failures. Resumable: skips
files already valid, retries invalid/missing ones with backoff.

Usage: python3 crec_download.py [start_date end_date]
Writes: /tmp/crec_pdfs/CREC-YYYY-MM-DD.pdf (valid files only)
        /tmp/crec_failed_final.txt (dates that never validated)
"""
import datetime, os, subprocess, sys, time

PDF_DIR = os.environ.get("CRR_PDF_DIR", "/tmp/crec_pdfs")
os.makedirs(PDF_DIR, exist_ok=True)
start = datetime.date.fromisoformat(sys.argv[1] if len(sys.argv) > 1 else "2025-01-03")
end = datetime.date.fromisoformat(sys.argv[2] if len(sys.argv) > 2 else "2026-09-12")


def head_is_pdf(path):
    with open(path, "rb") as f:
        return f.read(8).startswith(b"%PDF")


def download(date):
    ds = date.isoformat()
    fn = f"{PDF_DIR}/CREC-{ds}.pdf"
    if os.path.exists(fn) and head_is_pdf(fn):
        return "present"
    if os.path.exists(fn):
        os.remove(fn)
    url = f"https://www.govinfo.gov/content/pkg/CREC-{ds}/pdf/CREC-{ds}.pdf"
    for attempt in range(3):
        # absent editions hang on 302-follow error loops: short first-probe
        timeout = 12 if attempt == 0 else 90
        r = subprocess.run(
            ["curl", "-sL", "--max-time", str(timeout), "-A", "Mozilla/5.0",
             "-o", fn, "-w", "%{http_code}", url],
            capture_output=True, text=True)
        code = r.stdout.strip()
        if code == "200" and os.path.exists(fn) and head_is_pdf(fn):
            return "downloaded"
        if code.startswith("4") or code in ("404", "400") or code == "000":
            # 4xx = definitive absence; 000 = the absent-edition 302-follow
            # hang (no readable response within the probe window). Neither
            # deserves a retry — measured wall cost was 120s+ per absent date.
            return "absent"
        time.sleep(2 + attempt)
    return "failed"


def main():
    done, failed = [], []
    day = start
    while day <= end:
        if day.weekday() >= 5:  # Sat/Sun: no daily editions (rare exceptions rescannable)
            failed.append(day.isoformat() + " (weekend)")
            day += datetime.timedelta(days=1)
            continue
        outcome = download(day)
        (done if outcome != "failed" else failed).append(day.isoformat())
        day += datetime.timedelta(days=1)
        time.sleep(1.2)
    print(f"valid: {len(done)}  absent: {len([1 for _ in failed])}  failed: {len(failed)}")
    with open("/tmp/crec_failed_final.txt", "w") as f:
        f.write("\n".join(failed))


if __name__ == "__main__":
    main()
