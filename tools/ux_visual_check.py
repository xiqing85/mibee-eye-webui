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
import pathlib
import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8090"
OUT = pathlib.Path(__file__).resolve().parent.parent / "tmp" / "ux-qa"
OUT.mkdir(parents=True, exist_ok=True)

results = []


def check(name, ok, detail=""):
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
    check("setup: username field visible", pg.locator("#login-username").is_visible())
    check("setup: hint visible", pg.locator("#setup-hint").is_visible())
    check("setup: confirm field visible", pg.locator("#login-password2").is_visible())

    # Setup validation: mismatched passwords
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
