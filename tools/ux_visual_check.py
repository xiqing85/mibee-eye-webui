#!/usr/bin/env python3
"""Deep visual QA of the redesigned mibee-webui frontend against the mock
server (:8090). Exercises every view and interaction pattern — auth flows,
live-view controls, multi-camera select, camera tiles + confirm dialog,
settings editing/validation/save, PTZ panel, status pills, devices, toasts,
both themes, both languages, desktop + mobile viewports — and captures a
screenshot of each state so they can be reviewed visually.

Usage: .venv/bin/python tools/ux_visual_check.py   (mock server must run)
Output: tmp/ux-qa/*.png + PASS/FAIL summary per check.
"""
import atexit
import pathlib
import subprocess
import sys

from playwright.sync_api import sync_playwright

# Self-contained: the suite spawns its own throwaway mock on a spare port so
# every run starts from first-boot state (setup flow, exactly one camera) —
# repeatable and independent of the long-lived dev mock on :8090.
PORT = 8091
BASE = f"http://127.0.0.1:{PORT}"
_mock = subprocess.Popen(
    [sys.executable, str(pathlib.Path(__file__).parent / "mock_server.py"), str(PORT)],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
atexit.register(lambda: _mock.poll() is None and _mock.terminate())

import time
for _ in range(50):
    try:
        import urllib.request
        urllib.request.urlopen(f"{BASE}/api/health", timeout=1)
        break
    except Exception:
        time.sleep(0.2)
OUT = pathlib.Path(__file__).resolve().parent.parent / "tmp" / "ux-qa"
OUT.mkdir(parents=True, exist_ok=True)

results = []


def check(name, ok, detail="", note_skip=False):
    if note_skip and ok:
        print("SKIP " + name)
        return
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail and not ok else ""))


def shot(pg, name, full=False):
    pg.screenshot(path=str(OUT / f"{name}.png"), full_page=full)


with sync_playwright() as p:
    b = p.chromium.launch(headless=True)

    # ══ Desktop, first-boot setup flow ═══════════════════════════════
    ctx = b.new_context(viewport={"width": 1280, "height": 800})
    # Force MJPEG path: the mock has no real media, so skip MSE attempts.
    # Pin theme/lang so the toggle checks are deterministic (otherwise
    # Playwright's light system preference flips the start theme).
    ctx.add_init_script("window.MediaSource = undefined;"
                        "localStorage.setItem('mibee_theme','dark');"
                        "localStorage.setItem('mibee_lang','zh');")
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)[:200]))
    pg.on("console", lambda m: errors.append("console: " + m.text[:200])
          if m.type == "error" and "Failed to load resource" not in m.text else None)

    pg.goto(BASE, wait_until="domcontentloaded")
    pg.wait_for_timeout(1200)
    shot(pg, "01-setup-mode")
    if pg.locator("#login-username").is_visible():
        # First boot against a fresh mock: full SPEC §2 setup flow.
        check("setup: username field visible", True)
        check("setup: hint visible", pg.locator("#setup-hint").is_visible())
        check("setup: confirm field visible", pg.locator("#login-password2").is_visible())
        pg.fill("#login-username", "admin")
        pg.fill("#login-password", "12345678")
        pg.fill("#login-password2", "12345679")
        pg.click("button[data-i18n=loginBtn]")
        pg.wait_for_timeout(400)
        check("setup: mismatch error shown", pg.locator("#login-error").is_visible())
        pg.fill("#login-password2", "12345678")
        pg.click("button[data-i18n=loginBtn]")
        pg.wait_for_timeout(2000)
        check("setup: enters app", pg.locator("#app").is_visible())
    else:
        # Mock already set up by a previous run — the setup assertions only
        # apply to first boot; sign in instead.
        check("setup: enters app (skip — already configured)", True, note_skip=True)
        pg.fill("#login-password", "12345678")
        pg.click("button[data-i18n=loginBtn]")
        pg.wait_for_timeout(2000)
        check("login: enters app", pg.locator("#app").is_visible())
    shot(pg, "02-live-initial", full=True)

    # ── Live view chrome ──────────────────────────────────────────────
    check("live: HUD visible", pg.locator("#stream-live-dot").is_visible())
    check("live: mjpeg badge after fallback", pg.locator("#mjpeg-fallback-badge").is_visible())
    check("live: camera selector hidden (1 camera)",
          not pg.locator("#live-camera-field").is_visible())
    # hover state on snapshot button
    pg.hover("#btn-snapshot")
    pg.wait_for_timeout(250)
    shot(pg, "03-live-controls-hover")
    pg.click("#btn-hflip")
    pg.wait_for_timeout(250)
    check("live: hflip pressed state",
          pg.get_attribute("#btn-hflip", "aria-pressed") == "true")
    shot(pg, "04-live-hflip-active")
    pg.click("#btn-hflip")

    # ── Add 2nd camera via API → multi-camera UI ─────────────────────
    pg.evaluate("""async () => {
      const csrf = document.cookie.match(/csrf-token=([^;]+)/)?.[1] || '';
      const r = await fetch('/api/cameras', {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
        body: JSON.stringify({name: 'Backyard USB', camera_type: 'usb', config: {device_index: 0}})});
      return r.status;
    }""")
    pg.wait_for_timeout(800)
    check("multi-cam: selector appears", pg.locator("#live-camera-field").is_visible())
    check("multi-cam: cameras tab visible",
          pg.locator("#nav .nav-tab[data-view=cameras]").is_visible())
    check("multi-cam: devices tab visible",
          pg.locator("#nav .nav-tab[data-view=devices]").is_visible())
    shot(pg, "05-live-multicam-select")

    # ── Cameras view + confirm dialog ─────────────────────────────────
    pg.click("#nav .nav-tab[data-view=cameras]")
    pg.wait_for_timeout(1200)
    shot(pg, "06-cameras-grid", full=True)
    tiles = pg.locator("#cameras-grid .tile")
    check("cameras: 2 tiles", tiles.count() == 2)
    check("cameras: icon action buttons",
          pg.locator(".tile-actions button .icon").count() >= 4)
    # device-level flip buttons (camera_management): toggle + pressed state
    flip_h = pg.locator(".tile-actions .btn-flip").first
    before = flip_h.get_attribute("aria-pressed")
    flip_h.click()
    pg.wait_for_timeout(1200)
    flip_h = pg.locator(".tile-actions .btn-flip").first
    after = flip_h.get_attribute("aria-pressed")
    check("cameras: device flip toggles", before != after, f"{before} -> {after}")
    check("cameras: flip toast", pg.locator(".toast").count() >= 1)
    shot(pg, "06b-cameras-flip-pressed")
    flip_h.click()  # restore
    pg.wait_for_timeout(1000)
    # delete → in-app confirm dialog → cancel
    pg.locator(".tile-actions .btn-danger").first.click()
    pg.wait_for_timeout(500)
    check("confirm: dialog visible", pg.locator("#confirm-overlay").is_visible())
    shot(pg, "07-confirm-dialog")
    pg.click("#confirm-cancel")
    pg.wait_for_timeout(300)
    check("confirm: cancel keeps tile", pg.locator("#cameras-grid .tile").count() == 2)
    pg.locator(".tile-actions .btn-danger").first.click()
    pg.wait_for_timeout(400)
    pg.click("#confirm-ok")
    pg.wait_for_timeout(800)
    check("confirm: ok deletes tile", pg.locator("#cameras-grid .tile").count() == 1)
    shot(pg, "08-cameras-after-delete")

    # ── Settings: edit / validate / save / collapse / PTZ toggle ─────
    pg.click("#nav .nav-tab[data-view=settings]")
    pg.wait_for_timeout(1500)
    shot(pg, "09-settings", full=True)
    check("settings: sections rendered",
          pg.locator("#config-form .config-section").count() >= 4)
    check("settings: save disabled when clean", pg.locator("#save-config").is_disabled())
    # collapse first section
    pg.locator(".config-section-title").first.click()
    pg.wait_for_timeout(300)
    shot(pg, "10-settings-collapsed")
    check("settings: section collapsed",
          "collapsed" in pg.locator(".config-section").first.get_attribute("class"))
    pg.locator(".config-section-title").first.click()
    # invalid number → field red + save disabled
    pg.fill('[id="cf-camera.width"]', '99999')
    pg.wait_for_timeout(400)
    check("settings: invalid field flagged",
          "invalid" in pg.locator('[id="cf-camera.width"]').locator("xpath=ancestor::div[contains(@class,'config-field')]").first.get_attribute("class"))
    shot(pg, "11-settings-invalid")
    pg.fill('[id="cf-camera.width"]', '1280')
    # dirty → unsaved pill + save enabled
    pg.fill('[id="cf-camera.fps"]', '30')
    pg.wait_for_timeout(300)
    check("settings: unsaved pill visible", pg.locator("#unsaved-indicator").is_visible())
    check("settings: save enabled", not pg.locator("#save-config").is_disabled())
    shot(pg, "12-settings-dirty", full=True)
    # unsaved guard: navigate away → confirm dialog → stay
    pg.click("#nav .nav-tab[data-view=status]")
    pg.wait_for_timeout(400)
    check("unsaved guard: dialog shown", pg.locator("#confirm-overlay").is_visible())
    shot(pg, "13-unsaved-guard")
    pg.click("#confirm-cancel")
    pg.wait_for_timeout(300)
    check("unsaved guard: stays on settings",
          "active" in pg.locator("#view-settings").get_attribute("class"))
    # save → success toast
    pg.click("#save-config")
    pg.wait_for_timeout(900)
    check("settings: toast shown", pg.locator(".toast").count() >= 1)
    shot(pg, "14-settings-saved-toast")
    check("settings: fps persisted", pg.input_value('[id="cf-camera.fps"]') == "30")

    # Regression: 20-digit SIP IDs are strings in the config document. The
    # editor must round-trip them verbatim — converting to float both loses
    # precision and makes real devices reject the PUT (string expected).
    pg.fill('[id="cf-gb28181.device_id"]', "34020000001320000099")
    pg.wait_for_timeout(400)
    pg.click("#save-config")
    pg.wait_for_timeout(1000)
    check("settings: string-ID save accepted",
          pg.locator(".toast-error").count() == 0,
          "device rejected the PUT (string field sent as number)")
    got = pg.evaluate("""async () => {
      const csrf = document.cookie.match(/csrf-token=([^;]+)/)?.[1] || '';
      const r = await fetch('/api/config', {headers: {'X-CSRF-Token': csrf}});
      return (await r.json()).data.gb28181.device_id;
    }""")
    check("settings: 20-digit ID round-trips as string",
          got == "34020000001320000099", f"got: {got!r}")

    # ── Per-section apply badges + restart entry (SPEC §5/§5.1) ────────
    badges = pg.locator(".section-apply-badge").count()
    check("settings: section apply badges rendered", badges >= 4, f"{badges} badges")
    check("settings: restart badge class present",
          pg.locator(".section-apply-badge.apply-restart").count() >= 1)
    check("settings: immediate badge class present (imaging section)",
          pg.locator(".section-apply-badge.apply-immediate").count() >= 1)
    check("settings: restart button visible (caps.restart)",
          pg.locator("#btn-restart-device").is_visible())
    shot(pg, "12c-apply-badges", full=True)
    # Dialog flow — cancel must NOT restart (the POST path is exercised on
    # real hardware E2E; here a reload would derail the suite).
    pg.click("#btn-restart-device")
    pg.wait_for_timeout(400)
    check("restart: confirm dialog shown", pg.locator("#confirm-overlay").is_visible())
    shot(pg, "12d-restart-confirm")
    pg.click("#confirm-cancel")
    pg.wait_for_timeout(300)
    check("restart: cancel keeps page alive",
          pg.locator("#view-settings").is_visible() and
          pg.locator("#restart-overlay").count() == 0 or
          not pg.locator("#restart-overlay").is_visible())

    # ── PTZ: enable in settings, use panel ───────────────────────────
    # The toggle applies instantly (localStorage), outside the config form.
    pg.click("#ptz-toggle-row .switch")
    pg.wait_for_timeout(400)
    pg.click("#nav .nav-tab[data-view=preview]")
    pg.wait_for_timeout(1200)
    check("ptz: panel visible", pg.locator("#ptz-panel").is_visible())
    pan_before = pg.text_content("#ptz-pan")
    pg.click('.ptz-btn[data-dir="left"]')
    pg.wait_for_timeout(600)
    pan_after = pg.text_content("#ptz-pan")
    check("ptz: pan value changed", pan_before != pan_after, f"{pan_before} -> {pan_after}")
    shot(pg, "15-ptz-panel", full=True)

    # ── Status view ───────────────────────────────────────────────────
    pg.click("#nav .nav-tab[data-view=status]")
    pg.wait_for_timeout(1200)
    shot(pg, "16-status", full=True)
    check("status: device pills rendered", pg.locator("#device-info .state-pill").count() >= 1)
    check("status: api badge online",
          "online" in pg.get_attribute("#api-badge", "class"))
    check("status: readings leaders", pg.locator("#device-info .reading").count() >= 5)

    # ── Observability (SPEC §3.2): charts + logs + request traces ─────
    # Charts need ≥2 polls (2s apart) before lines render.
    pg.wait_for_timeout(5000)
    check("obs: resource card visible", pg.locator("#obs-card").is_visible())
    check("obs: 8 chart canvases", pg.locator("#obs-card canvas.obs-chart").count() == 8)
    check("obs: cpu value rendered", pg.text_content("#obs-cpu-val") != "-")
    check("obs: log rows rendered", pg.locator("#obs-logs .obs-log").count() >= 1)
    try:
        pg.wait_for_selector("#obs-requests tr", timeout=8000)
        req_rows = True
    except Exception:
        req_rows = False
    check("obs: request rows rendered", req_rows)
    check("obs: prometheus endpoint public",
          pg.evaluate("fetch('/metrics').then(r => r.status)") == 200)
    shot(pg, "16b-status-observability", full=True)

    # ── Devices view ──────────────────────────────────────────────────
    pg.click("#nav .nav-tab[data-view=devices]")
    pg.wait_for_timeout(1200)
    shot(pg, "17-devices", full=True)
    check("devices: rows with icons", pg.locator("#video-devices .device-ico").count() >= 1)
    # use-as-camera → toast
    pg.locator("#video-devices .btn-small").first.click()
    pg.wait_for_timeout(900)
    check("devices: use-as-camera toast", pg.locator(".toast-success").count() >= 1)

    # ── Language toggle (zh→en) ───────────────────────────────────────
    pg.click("#nav .lang-btn")
    pg.wait_for_timeout(500)
    check("lang: nav switches to EN",
          pg.text_content("#nav .nav-tab[data-view=preview] span").strip() == "Live")
    check("lang: save button EN", pg.text_content("#save-config").strip() == "Save")
    pg.click("#nav .nav-tab[data-view=cameras]")
    pg.wait_for_timeout(800)
    shot(pg, "18-english-cameras", full=True)

    # ── Theme toggle (dark→light) across views ────────────────────────
    pg.click("#nav .theme-btn")
    pg.wait_for_timeout(400)
    check("theme: light applied", pg.evaluate("document.documentElement.dataset.theme") == "light")
    shot(pg, "19-light-cameras", full=True)
    pg.click("#nav .nav-tab[data-view=settings]")
    pg.wait_for_timeout(800)
    shot(pg, "20-light-settings", full=True)
    pg.click("#nav .nav-tab[data-view=preview]")
    pg.wait_for_timeout(800)
    shot(pg, "21-light-live", full=True)
    pg.click("#nav .theme-btn")
    pg.wait_for_timeout(300)

    # ── Logout + wrong password error ─────────────────────────────────
    pg.click("#nav .logout-btn")
    pg.wait_for_timeout(1200)
    check("logout: back to login", pg.locator("#view-login").is_visible())
    shot(pg, "22-login")
    pg.fill("#login-password", "wrong-password")
    pg.click("button[data-i18n=loginBtn]")
    try:
        pg.wait_for_selector("#login-error:not(.hidden)", timeout=5000)
        ok_err = True
    except Exception:
        ok_err = False
    check("login: wrong pw error", ok_err)
    shot(pg, "23-login-error")
    # eye toggle
    pg.click(".password-toggle")
    pg.wait_for_timeout(200)
    check("login: eye toggle reveals", pg.get_attribute("#login-password", "type") == "text")
    pg.fill("#login-password", "12345678")
    pg.click("button[data-i18n=loginBtn]")
    pg.wait_for_timeout(2000)
    check("login: re-login works", pg.locator("#app").is_visible())

    check("desktop: zero page errors", not errors, "; ".join(errors[:3]))
    ctx.close()

    # ══ Mobile viewport ═══════════════════════════════════════════════
    mctx = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True,
                         has_touch=True, device_scale_factor=2)
    mctx.add_init_script("window.MediaSource = undefined;"
                         "localStorage.setItem('mibee_theme','dark');"
                         "localStorage.setItem('mibee_lang','zh');")
    mp = mctx.new_page()
    merrors = []
    mp.on("pageerror", lambda e: merrors.append(str(e)[:200]))
    mp.goto(BASE, wait_until="domcontentloaded")
    mp.wait_for_timeout(1500)
    # already set up; login
    mp.fill("#login-password", "12345678")
    mp.click("button[data-i18n=loginBtn]")
    mp.wait_for_timeout(2000)
    check("mobile: app boots", mp.locator("#app").is_visible())
    check("mobile: bottom tab bar visible", mp.locator("#nav-mobile").is_visible())
    check("mobile: tools stay in top bar", mp.locator("#nav .theme-btn").is_visible())
    shot(mp, "30-mobile-live", full=True)
    # Horizontal overflow on mobile widens the LAYOUT viewport, which pushes
    # the fixed bottom nav off-screen — the tab bar then becomes unclickable
    # and every later tab click reports an interception (#6). Assert early,
    # on the preview view with two cameras, where the live toolbar is widest.
    overflow_pv = mp.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("mobile: no horizontal overflow (preview)", overflow_pv <= 0, f"{overflow_pv}px overflow")
    mp.click("#nav-mobile .nav-tab[data-view=cameras]")
    mp.wait_for_timeout(1000)
    shot(mp, "31-mobile-cameras", full=True)
    mp.click("#nav-mobile .nav-tab[data-view=settings]")
    mp.wait_for_timeout(1000)
    shot(mp, "32-mobile-settings", full=True)
    mp.click("#nav-mobile .nav-tab[data-view=status]")
    mp.wait_for_timeout(1000)
    shot(mp, "33-mobile-status", full=True)
    # horizontal overflow check on every view
    overflow = mp.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("mobile: no horizontal overflow", overflow <= 0, f"{overflow}px overflow")
    check("mobile: zero page errors", not merrors, "; ".join(merrors[:3]))
    mctx.close()

    b.close()

fails = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(fails)}/{len(results)} passed, {len(fails)} failed =====")
sys.exit(1 if fails else 0)
