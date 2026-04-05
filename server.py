#!/usr/bin/env python3
"""
CharmPass local server — serves the HTML and fetches the real daily security code.
Usage: python server.py
Then open: http://localhost:8080
"""
import http.server
import json
import os
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

SERVE_DIR = Path(__file__).parent
PORT = 8080

MTA_URL   = "https://mobility-app-east.transitsherpa.com/rider/graphql/"
SCOPE     = "baltimore-mta-prod"
CACHE_FILE = SERVE_DIR / "daily_code.json"

QUERY = (
    "query GetSecurityCodes($scope:String!, $pastDayCount:Int!, $futureDayCount:Int!){"
    "content(scope: $scope) {"
    "securityCodes(pastDayCount: $pastDayCount, futureDayCount: $futureDayCount) {"
    "code id expirationTime}}}"
)

def fetch_code_from_mta():
    now_s  = int(time.time())
    now_ms = now_s * 1000

    payload = json.dumps({
        "query": QUERY,
        "operationName": "GetSecurityCodes",
        "variables": {"scope": SCOPE, "pastDayCount": 1, "futureDayCount": 30}
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Accept":        "application/json",
        "x-mv-app-version":     "3.21.7451",
        "x-mv-os-version":      "14",
        "x-mv-os-type":         "android",
        "x-mv-device-platform": "arm64-v8a",
        "x-mv-device-model":    "Pixel 8",
        "x-mv-device-name":     "Google",
        "x-mv-timestamp":       str(now_ms),
        "x-mv-transaction":     str(uuid.uuid4()),
        "x-mv-appid":           str(uuid.uuid4()),
    }

    req = urllib.request.Request(MTA_URL, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    codes = data["data"]["content"]["securityCodes"]
    # Find the first code whose expiration is >= now
    current = next((c for c in codes if c["expirationTime"] >= now_s), None)
    if not current:
        return None

    result = {
        "code":      current["code"],
        "expiresAt": current["expirationTime"] * 1000,   # convert to ms for JS
        "fetchedAt": now_ms,
    }
    with open(CACHE_FILE, "w") as f:
        json.dump(result, f)
        
    js_content = f"const DAILY_CODE = {json.dumps(result)};\n"
    with open(SERVE_DIR / "daily_code.js", "w") as f:
        f.write(js_content)
        
    print(f"[{_ts()}] Fetched code: {result['code']}  "
          f"expires {datetime.fromtimestamp(current['expirationTime']).strftime('%Y-%m-%d %H:%M')}")
    return result


def get_code(force=False):
    """Return valid cached code, or fetch a fresh one."""
    if not force and CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text())
            # still valid if expiresAt > now+60s (60s buffer)
            if cached.get("expiresAt", 0) > (time.time() + 60) * 1000:
                # Ensure JS file is also written if it's missing or we just loaded from cache
                js_content = f"const DAILY_CODE = {json.dumps(cached)};\n"
                with open(SERVE_DIR / "daily_code.js", "w") as f:
                    f.write(js_content)
                return cached
        except Exception:
            pass
    return fetch_code_from_mta()


def _ts():
    return datetime.now().strftime("%H:%M:%S")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(SERVE_DIR), **kwargs)

    def do_GET(self):
        if self.path.rstrip("/") == "/api/daily-code":
            try:
                result = get_code()
                if result:
                    body = json.dumps(result).encode()
                    self.send_response(200)
                    self.send_header("Content-Type",  "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.send_header("Cache-Control",  "no-cache")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._json_error(503, "Could not fetch security code from MTA")
            except Exception as e:
                print(f"[{_ts()}] ERROR: {e}")
                self._json_error(502, str(e))
        else:
            super().do_GET()

    def _json_error(self, code, msg):
        body = json.dumps({"error": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type",  "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Suppress static file noise; only print API calls
        if "/api/" in (args[0] if args else ""):
            print(f"[{_ts()}] {fmt % args}")


if __name__ == "__main__":
    import sys
    is_fetch_only = "--fetch-only" in sys.argv
    # Pre-fetch on startup
    try:
        r = get_code(force=is_fetch_only)
        if r:
            print(f"  Today's code: {r['code']}")
    except Exception as e:
        print(f"  Warning: initial fetch failed ({e})")

    if is_fetch_only:
        print("  Fetch only mode: exiting.")
        sys.exit(0)

    print("=" * 50)
    print("  CharmPass Server")
    print(f"  http://localhost:{PORT}")
    print("=" * 50)
    print("  Press Ctrl+C to stop.\n")
    httpd = http.server.HTTPServer(("", PORT), Handler)
    httpd.serve_forever()
