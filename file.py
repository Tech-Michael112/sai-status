cat <<'EOF' > cp.py
"""One-shot iCloud sign-in automation using a persistent CDP Chrome."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

EMAIL = "benardbenard2021@gmail.com"
# PUT YOUR PASSWORD HERE:
DEFAULT_PASSWORD = "YOUR_PASSWORD_HERE" 

ICLOUD_URL = "https://www.icloud.com/"
PROFILE_DIR = Path(__file__).with_name("cdp-profile")
DEFAULT_PORT = 9222

CHROME_PATHS = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe")),
    Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    Path("/usr/bin/google-chrome"),
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
    Path("/usr/bin/google-chrome-stable"), # Added for Ubuntu
]

def find_chrome() -> str:
    for path in CHROME_PATHS:
        if path.exists():
            return str(path)
    raise SystemExit("Could not find Chrome. Edit CHROME_PATHS.")

def cdp_up(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
        return True
    except Exception:
        return False

def launch_chrome(port: int) -> None:
    chrome = find_chrome()
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        chrome, f"--remote-debugging-port={port}", f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled",
        "--no-sandbox", # Added for Linux/VPS compatibility
    ]
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.time() + 20
    while time.time() < deadline:
        if cdp_up(port):
            return
        time.sleep(0.3)
    raise SystemExit(f"Chrome did not expose CDP on port {port}.")

def close_chrome(port: int) -> bool:
    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
            browser.new_browser_cdp_session().send("Browser.close")
    except Exception:
        return False
    deadline = time.time() + 15
    while time.time() < deadline and cdp_up(port):
        time.sleep(0.4)
    return not cdp_up(port)

def wipe_profile(port: int) -> None:
    if cdp_up(port) and not close_chrome(port):
        raise SystemExit(f"Could not close Chrome.")
    for _ in range(20):
        shutil.rmtree(PROFILE_DIR, ignore_errors=True)
        if not PROFILE_DIR.exists():
            return
        time.sleep(0.25)

def clear_session(context) -> None:
    try: context.clear_cookies()
    except Exception: pass
    for page in context.pages:
        parts = urlsplit(page.url)
        if parts.scheme in ("http", "https") and parts.netloc:
            origin = f"{parts.scheme}://{parts.netloc}"
            try:
                cdp = context.new_cdp_session(page)
                cdp.send("Storage.clearDataForOrigin", {"origin": origin, "storageTypes": "all"})
                cdp.detach()
            except Exception: pass
        try:
            page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")
        except Exception: pass

def find_field(context, selector):
    for page in context.pages:
        for frame in page.frames:
            try:
                field = frame.locator(selector).first
                if field.count() and field.is_visible():
                    return field
            except Exception:
                pass
    return None

SAFE_KEYS = {"success", "error", "errormessage", "errorcode", "code", "message", "termsupdateneeded", "hsachallengerequired", "status"}
def safe_response(data):
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            if key.lower() in SAFE_KEYS: result[key] = value
            elif isinstance(value, (dict, list)):
                nested = safe_response(value)
                if nested: result[key] = nested
        return result
    if isinstance(data, list): return [safe_response(item) for item in data]
    return {}

def watch_page(page):
    def on_js_error(error): print("\n[APPLE JAVASCRIPT EXCEPTION]", str(error)[:700])
    def on_console(message):
        if message.type == "error" and any(w in message.text.lower() for w in ("app-message", "cloudosauth", "trigger")):
            print("\n[JAVASCRIPT CONSOLE ERROR]", message.text[:700])
    page.on("pageerror", on_js_error)
    page.on("console", on_console)

def capture_response(response):
    if "/setup/ws/1/validate" not in response.url: return
    if response.status >= 400:
        print("\n[APPLE VALIDATION RESPONSE]")
        print("[HTTP STATUS]", response.status)
        try:
            body = response.text()
            data = json.loads(body)
            print("[RESPONSE BODY]")
            print(json.dumps(safe_response(data), indent=2))
        except Exception: print("[BODY] Could not decode JSON response.")

def run_icloud_flow(context, page):
    context.on("response", capture_response)
    watch_page(page)

    try:
        if "icloud.com" not in (page.url or ""):
            print("[4] Navigating to iCloud...")
            page.goto(ICLOUD_URL, wait_until="domcontentloaded", timeout=25000)

        print("[5] Opening Sign In...")
        email = find_field(context, "#account_name_text_field")
        if not email:
            try:
                page.get_by_text("Sign In", exact=True).first.click(timeout=10000)
            except Exception: pass

        print("[6] Finding email field...")
        deadline = time.monotonic() + 12
        email = None
        while time.monotonic() < deadline:
            email = find_field(context, "#account_name_text_field")
            if email: break
            time.sleep(0.2)

        if not email:
            print("[ERROR] Email field not found.")
            return

        print("[7] Entering email...")
        email.fill(EMAIL)

        print("[8] Waiting for UI to settle...")
        time.sleep(1.5) 

        print("[9] Looking for 'Continue' button...")
        continue_btn = None
        deadline = time.monotonic() + 10
        
        while time.monotonic() < deadline:
            for attempt in range(3):
                try:
                    btn = page.get_by_role("button", name="Continue", exact=True).first
                    if btn.count() and btn.is_visible():
                        continue_btn = btn; break
                    btn = page.locator("button.button-primary").first
                    if btn.count() and btn.is_visible():
                        continue_btn = btn; break
                    for frame in page.frames:
                        btn = frame.locator("button:has-text('Continue')").first
                        if btn.count() and btn.is_visible():
                            continue_btn = btn; break
                except Exception: pass
                if continue_btn: break
            if continue_btn: break
            time.sleep(0.5)

        if continue_btn:
            print("[10] Clicking 'Continue' button...")
            continue_btn.click()
        else:
            print("[10] 'Continue' button not found. Trying 'Enter' as fallback...")
            email.press("Enter")

        print("[11] Checking password field...")
        deadline = time.monotonic() + 12
        password = None
        while time.monotonic() < deadline:
            password = find_field(context, "#password_text_field")
            if password: break
            time.sleep(0.2)

        if password:
            print("\n[SUCCESS] Password field detected!")
            
            print("[12] Entering default password...")
            try:
                password.fill(DEFAULT_PASSWORD)
            except Exception:
                try:
                    password.focus()
                    password.fill(DEFAULT_PASSWORD, force=True)
                except Exception:
                    password.focus(force=True)
                    page.keyboard.type(DEFAULT_PASSWORD)

            print("[13] Submitting...")
            try:
                password.press("Enter")
            except Exception:
                page.keyboard.press("Enter")
                
            time.sleep(3)
            print("[14] Submitted. Current URL:", page.url)
        else:
            print("\n[FAILED] Password field did not appear.")
            print("[CURRENT URL]", page.url)

        print("\n[DIAGNOSTICS COMPLETE]")

    except Exception as error:
        print("\n[ERROR]", str(error)[:700])

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()

    if args.reset:
        print("[1] Resetting profile...")
        wipe_profile(args.port)
        print("[2] Launching Chrome...")
        launch_chrome(args.port)
    elif cdp_up(args.port):
        print(f"[1] Reusing Chrome on port {args.port}.")
    else:
        print("[1] Launching Chrome...")
        launch_chrome(args.port)

    print("[2] Attaching via CDP...")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
        if not browser.contexts:
            raise SystemExit("[FATAL] No browser context.")
        context = browser.contexts[0]

        if args.clear:
            print("[3] Clearing cookies and storage...")
            clear_session(context)

        if context.pages:
            page = context.pages[0]
            print("[3] Using existing page:", page.url)
        else:
            page = context.new_page()
            print("[3] Opened a new page.")

        run_icloud_flow(context, page)

        print("\n[DONE] Chrome stays open. Press ENTER to detach.")
        input()
        print("[DETACHED]")

if __name__ == "__main__":
    sys.exit(main())
EOF
