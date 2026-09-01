#!/usr/bin/env python3
"""Automated browser walkthrough of the shared web UI against one device.

Covers: first-boot setup OR login (wrong password first), camera start
when the capability exists, live video (MSE playhead advancing / MJPEG
fallback), settings + status views, language + theme toggles, logout.
Screenshots land in tmp/walkthrough-<tag>/.

Usage:
  tools/browser_walkthrough.py <BASE_URL> <PASSWORD> [USERNAME] [TAG]

Examples:
  tools/browser_walkthrough.py http://<rs-device-ip>:8088 2022-02-22
  tools/browser_walkthrough.py https://127.0.0.1:8443 2022-02-22 admin nb

One-time setup (no node needed):
  python3 -m venv .venv && .venv/bin/pip install playwright
  .venv/bin/playwright install chromium
  .venv/bin/python tools/browser_walkthrough.py ...
"""
import json
import pathlib
import sys

from playwright.sync_api import sync_playwright

BASE = sys.argv[1].rstrip("/")
PASSWORD = sys.argv[2]
USERNAME = sys.argv[3] if len(sys.argv) > 3 else "admin"
TAG = sys.argv[4] if len(sys.argv) > 4 else BASE.split("//")[-1].replace(":", "-").replace("/", "")
OUT = pathlib.Path(__file__).resolve().parent.parent / "tmp" / f"walkthrough-{TAG}"
OUT.mkdir(parents=True, exist_ok=True)
TLS = BASE.startswith("https")

issues = []

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={"width": 1280, "height": 800}, ignore_https_errors=TLS)
    pg = ctx.new_page()
    reqs = {}

    def on_response(r):
        if "/api/" in r.url:
            reqs[r.request.method + " " + r.url.replace(BASE, "")] = r.status

    pg.on("response", on_response)
    # 401/403/503 console noise is expected (pre-auth /api/auth/me probe,
    # setup_required boot state) — only report other errors.
    pg.on("console", lambda m: issues.append(("console-error", m.text[:200]))
          if m.type == "error" and "401" not in m.text and "403" not in m.text
          and "503" not in m.text else None)
    pg.on("pageerror", lambda e: issues.append(("pageerror", str(e)[:300])))

    # -- open; boot decides between first-boot setup and login ------------
    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(2500)
    pg.screenshot(path=str(OUT / "01-entry.png"), full_page=True)

    if pg.locator("#login-username").is_visible():
        # First boot: SPEC §2 setup (username + password + confirm), then
        # the server signs us in directly.
        pg.fill("#login-username", USERNAME)
        pg.fill("#login-password", PASSWORD)
        if pg.locator("#login-password2").is_visible():
            pg.fill("#login-password2", PASSWORD)
        pg.click("button[data-i18n=loginBtn]")
        pg.wait_for_timeout(4000)
    else:
        # Wrong password must be rejected with a visible error.
        pg.fill("#login-password", "definitely-wrong")
        pg.click("button[data-i18n=loginBtn]")
        pg.wait_for_timeout(1500)
        if pg.locator("#app").is_visible():
            issues.append(("auth", "wrong password accepted"))
        pg.screenshot(path=str(OUT / "02-login-wrongpw.png"), full_page=True)
        pg.fill("#login-password", PASSWORD)
        pg.click("button[data-i18n=loginBtn]")
        pg.wait_for_timeout(3500)

    if not pg.locator("#app").is_visible():
        err = pg.locator("#login-error")
        issues.append(("auth", f"login failed: {err.inner_text() if err.is_visible() else 'no error shown'}"))
        pg.screenshot(path=str(OUT / "03-login-stuck.png"), full_page=True)
        print("API calls:", json.dumps(reqs, indent=1))
        print("ISSUES:", json.dumps(issues, ensure_ascii=False, indent=1))
        print(f"screenshots: {OUT}")
        sys.exit(1)
    pg.screenshot(path=str(OUT / "03-after-login.png"), full_page=True)

    # -- cameras tab first: start a stopped camera (fresh notebook boots --
    #    with the stream stopped; starting before the live check avoids a
    #    false "video not advancing" there) --------------------------------
    cam = pg.locator("#nav .nav-tab[data-view=cameras]")
    if cam.count() and cam.first.is_visible():
        cam.first.click()
        pg.wait_for_timeout(1500)
        start = pg.locator("#view-cameras button", has_text="启动").or_(
            pg.locator("#view-cameras button", has_text="Start"))
        if start.count():
            start.first.click()
            pg.wait_for_timeout(5000)
        pg.screenshot(path=str(OUT / "04-cameras.png"), full_page=True)
        pg.locator("#nav .nav-tab[data-view=preview]").first.click()
        pg.wait_for_timeout(4000)

    # -- live view: MSE playhead should advance (or MJPEG img present) ----
    pg.wait_for_timeout(4000)
    vid = pg.locator("video")
    if vid.count():
        t1 = pg.evaluate("()=>{const v=document.querySelector('video');return v?v.currentTime:null}")
        pg.wait_for_timeout(4000)
        t2 = pg.evaluate("()=>{const v=document.querySelector('video');return v?v.currentTime:null}")
        if t1 is None or t2 <= t1:
            issues.append(("live", f"video not advancing ({t1} -> {t2})"))
    else:
        issues.append(("live", "no <video> element (no MJPEG fallback either)"))
    pg.screenshot(path=str(OUT / "05-live.png"), full_page=True)

    # -- settings + status -------------------------------------------------
    for view, shot in (("settings", "06-settings"), ("status", "07-status")):
        tab = pg.locator(f"#nav .nav-tab[data-view={view}]")
        if tab.count() and tab.first.is_visible():
            tab.first.click()
            pg.wait_for_timeout(2000)
            if len(pg.inner_text("body")) < 80:
                issues.append((view, "view looks empty"))
            pg.screenshot(path=str(OUT / f"{shot}.png"), full_page=True)

    # -- language + theme toggles ------------------------------------------
    for btn, shot in (("#nav .lang-btn", "08-lang"), ("#nav .theme-btn", "09-theme")):
        b_ = pg.locator(btn)
        if b_.count() and b_.first.is_visible():
            b_.first.click()
            pg.wait_for_timeout(800)
            pg.screenshot(path=str(OUT / f"{shot}.png"), full_page=True)
            b_.first.click()

    # -- logout -------------------------------------------------------------
    lg = pg.locator("#nav .logout-btn")
    if lg.count() and lg.first.is_visible():
        lg.first.click()
        pg.wait_for_timeout(2000)
        if not pg.locator("#view-login").is_visible():
            issues.append(("auth", "login view not shown after logout"))
    pg.screenshot(path=str(OUT / "10-logout.png"), full_page=True)

    b.close()

print("API calls:")
for k, v in sorted(reqs.items()):
    print(f"  {v} {k}")
print("ISSUES:", json.dumps(issues, ensure_ascii=False, indent=1) if issues else "none")
print(f"screenshots: {OUT}")
sys.exit(1 if issues else 0)
