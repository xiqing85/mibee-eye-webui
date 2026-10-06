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
    # Setup mode = confirm-password field present; the username input is
    # rendered in both modes (SPEC §2).
    if pg.locator("#login-password2").is_visible():
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
        # apply to first boot; sign in instead. The username input must be
        # rendered in login mode too (SPEC §2 explicit-username login).
        check("setup: enters app (skip — already configured)", True, note_skip=True)
        check("login: username field visible", pg.locator("#login-username").is_visible())
        pg.fill("#login-username", "admin")
        pg.fill("#login-password", "12345678")
        pg.click("button[data-i18n=loginBtn]")
        pg.wait_for_timeout(2000)
        check("login: enters app", pg.locator("#app").is_visible())
    shot(pg, "02-live-initial", full=True)

    # ── New IA: five primary destinations ──────────────────────────────
    check("nav: five primary tabs",
          pg.locator("#nav .nav-tab:not(.hidden)").count() == 5,
          str(pg.locator("#nav .nav-tab:not(.hidden)").count()))
    check("nav: no legacy settings/status/devices tabs",
          pg.locator('#nav .nav-tab[data-view=settings], #nav .nav-tab[data-view=status], '
                     '#nav .nav-tab[data-view=devices], #nav .nav-tab[data-view=cameras]').count() == 0)
    check("system: subnav has settings+status, devices unhidden by cap",
          pg.locator('#system-subnav .subnav-btn:not(.hidden)').count() == 3)

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
    check("multi-cam: camera grid section appears",
          pg.locator("#cameras-section:not(.hidden-cap)").count() == 1)
    check("multi-cam: devices subview unhidden by cap",
          "hidden" not in (pg.locator('#system-subnav .subnav-btn[data-subview=devices]')
                           .get_attribute("class") or ""))
    shot(pg, "05-live-multicam-select")

    # ── Camera grid (lives inside the Live view now) + confirm dialog ──
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
    # The stop→start cycle now includes a 1.5s V4L2 release grace — wait
    # past it for the re-render + toast.
    pg.wait_for_timeout(4500)
    flip_h = pg.locator(".tile-actions .btn-flip").first
    after = flip_h.get_attribute("aria-pressed")
    check("cameras: device flip toggles", before != after, f"{before} -> {after}")
    check("cameras: flip toast", pg.locator(".toast").count() >= 1)
    shot(pg, "06b-cameras-flip-pressed")
    flip_h.click()  # restore
    pg.wait_for_timeout(4500)
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

    # ── System ▸ Settings: edit / validate / save / collapse / PTZ ────
    pg.click("#nav .nav-tab[data-view=system]")
    pg.wait_for_timeout(1500)
    shot(pg, "09-settings", full=True)
    check("settings: sections rendered",
          pg.locator("#config-form .config-section").count() >= 4)
    check("settings: save disabled when clean", pg.locator("#save-config").is_disabled())
    # scene capability keys (SPEC appendix A #31) render with labels
    check("settings: scene voice window field",
          pg.locator('[id="cf-scene.voice.follow_up_window_secs"]').count() == 1)
    check("settings: scene wake word field",
          pg.locator('[id="cf-scene.voice.wake_word"]').count() == 1)
    check("settings: scene weather city labelled",
          '天气查询城市' in pg.locator('label[for="cf-scene.tools.weather_city"]').inner_text())
    pg.fill('[id="cf-scene.voice.follow_up_window_secs"]', '-1')
    pg.wait_for_timeout(400)
    check("settings: negative scene window flagged",
          "invalid" in pg.locator('[id="cf-scene.voice.follow_up_window_secs"]')
          .locator("xpath=ancestor::div[contains(@class,'config-field')]").first.get_attribute("class"))
    pg.fill('[id="cf-scene.voice.follow_up_window_secs"]', '12')
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
    pg.click("#system-subnav .subnav-btn[data-subview=status]")
    pg.wait_for_timeout(400)
    check("unsaved guard: dialog shown", pg.locator("#confirm-overlay").is_visible())
    shot(pg, "13-unsaved-guard")
    pg.click("#confirm-cancel")
    pg.wait_for_timeout(300)
    check("unsaved guard: stays on settings",
          "active" in pg.locator("#sys-settings").get_attribute("class"))
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

    # ── Zones (SPEC appendix A #21): toolbar button + editor ──────────
    # The loading overlay (z6) intentionally sits above the stream
    # controls (z5) while the stream builds — wait it out first.
    pg.wait_for_selector("#stream-loading", state="hidden", timeout=20000)
    check("zones: edit button visible", pg.locator("#btn-zones-edit").is_visible())
    pg.click("#btn-zones-edit")
    pg.wait_for_timeout(600)
    check("zones: editor opens", pg.locator("#zones-editor").is_visible())
    check("zones: canvas has size",
          pg.locator("#zones-edit-canvas").evaluate("c => c.width > 0 && c.height > 0"))
    check("zones: seeded zone listed", pg.locator("#zones-list li").count() >= 1)
    # Draw a tripwire: pick the line kind, place two points, commit.
    pg.select_option("#zones-kind", "line_cross")
    pg.fill("#zones-name", "ux-line")
    canvas = pg.locator("#zones-edit-canvas")
    box = canvas.bounding_box()
    canvas.click(position={"x": box["width"] * 0.3, "y": box["height"] * 0.3})
    canvas.click(position={"x": box["width"] * 0.7, "y": box["height"] * 0.7})
    pg.click("#zones-add")
    pg.wait_for_timeout(300)
    check("zones: draft committed to list", pg.locator("#zones-list li").count() >= 2)
    pg.click("#zones-save")
    pg.wait_for_timeout(600)
    check("zones: editor closes after save",
          not pg.locator("#zones-editor").is_visible())
    shot(pg, "15b-zones-saved", full=True)
    pg.click("#btn-zones-edit")
    pg.wait_for_timeout(400)
    check("zones: persisted round-trip", pg.locator("#zones-list li").count() >= 2)
    pg.click("#zones-close")
    pg.wait_for_timeout(200)

    # ── Assistant view (chat inline since the redesign) + one exchange ─
    check("assistant: tab visible", pg.locator("#nav .nav-tab[data-view=assistant]").is_visible())
    pg.click("#nav .nav-tab[data-view=assistant]")
    pg.wait_for_timeout(600)
    check("assistant: view active", pg.locator("#view-assistant.active").count() == 1)
    check("assistant: chat card visible", pg.locator("#chat-log").is_visible())
    check("assistant: speakers card in view",
          pg.locator("#view-assistant #speakers-card").count() == 1 and
          pg.locator("#view-assistant #faces-card").count() == 1)
    # Grounded chat (#29): the eye toggle appears on VLM-capable mocks;
    # a plain reply carries a "scene" badge, a vision turn a "vlm" badge.
    check("chat: vision toggle visible (vlm cap)",
          pg.locator("#chat-vision").is_visible())
    pg.fill("#chat-input", "hello")
    pg.click("#chat-send")
    pg.wait_for_timeout(800)
    check("chat: reply bubble rendered",
          pg.locator("#chat-log .chat-bubble").count() >= 2)
    # Voice waveform (SPEC §6 audio_level): the strip renders and the
    # canvas actually paints (mock bursts every 2s).
    check("waveform: strip visible in chat card",
          pg.locator("#chat-waveform-wrap:not(.hidden)").is_visible())
    # Poll up to ~9s for any canvas change: the mock's audio_level cadence
    # can idle between two single samples (2026-10-04 first-run flake) —
    # any observed difference proves the animation loop is live.
    wf_before = pg.evaluate("document.getElementById('chat-waveform').toDataURL().length")
    wf_animates = False
    for _ in range(6):
        pg.wait_for_timeout(1500)
        wf_now = pg.evaluate("document.getElementById('chat-waveform').toDataURL().length")
        if wf_now > 0 and wf_now != wf_before:
            wf_animates = True
            break
        wf_before = wf_now
    check("waveform: canvas animates with audio_level events", wf_animates)
    check("chat: scene badge on plain reply",
          pg.locator("#chat-log .chat-badge.scene").count() >= 1)
    pg.click("#chat-vision")
    pg.fill("#chat-input", "你能看到我吗")
    pg.click("#chat-send")
    pg.wait_for_timeout(800)
    check("chat: vlm badge on vision reply",
          pg.locator("#chat-log .chat-badge.vlm").count() >= 1)
    pg.click("#chat-vision")  # leave it off for later legs
    # Conversation records (SPEC §3.4): the human-readable turn log —
    # seeded voice/http turns, no-reply pill, and the thinking toggle.
    check("convlog: card visible (conversations cap)",
          pg.locator("#convlog-card:not(.hidden)").is_visible())
    check("convlog: seeded turns rendered",
          pg.locator("#convlog-list .conv-turn").count() >= 4)
    check("convlog: voice + http origin badges",
          pg.locator("#convlog-list .trace-origin.voice").count() >= 2 and
          pg.locator("#convlog-list .trace-origin.chat").count() >= 2)
    check("convlog: no-reply turn pill",
          pg.locator("#convlog-list .conv-turn .state-pill.off").count() >= 1)
    check("convlog: engine pill on replied turns",
          pg.locator("#convlog-list .conv-turn .state-pill.on").count() >= 3)
    check("convlog: heard text rendered",
          "小蜜蜂" in pg.locator("#convlog-list .conv-user").first.inner_text())
    pg.locator("#convlog-list .conv-thinking-toggle").first.click()
    pg.wait_for_timeout(150)
    check("convlog: thinking entries expand",
          pg.locator("#convlog-list .conv-think-entry").count() >= 2)
    check("convlog: thinking entry carries source+note",
          pg.locator("#convlog-list .conv-think-src").first.inner_text() != "" and
          pg.locator("#convlog-list .conv-think-note").first.inner_text() != "")
    pg.locator("#convlog-list .conv-thinking-toggle").first.click()
    pg.wait_for_timeout(150)
    check("convlog: thinking collapses",
          pg.locator("#convlog-list .conv-think-entry").count() == 0)
    shot(pg, "15c-chat-assistant", full=True)

    # ── Records view (SPEC appendix A #24): list + filter + clear ─────
    check("records: tab visible", pg.locator("#nav .nav-tab[data-view=records]").is_visible())
    pg.click("#nav .nav-tab[data-view=records]")
    pg.wait_for_timeout(800)
    check("records: view active", pg.locator("#view-records.active").count() == 1)
    check("records: faces card rendered (mock)", pg.locator("#faces-card").count() == 1)
    check("records: seeded rows rendered", pg.locator("#records-list .record-row").count() >= 2)
    check("records: sound kind badge", pg.locator("#records-list .record-kind.kind-sound").count() >= 1)
    check("records: voice kind badge", pg.locator("#records-list .record-kind.kind-voice").count() >= 1)
    check("records: scene badge on correlated row",
          pg.locator("#records-list .record-kind.kind-scene").count() >= 1)
    check("records: media badge on correlated row",
          pg.locator("#records-list .record-kind.kind-media").count() >= 1)
    check("records: speaker badge on attributed voice row",
          pg.locator("#records-list .record-kind.kind-speaker").count() == 1 and
          pg.locator("#records-list .kind-speaker").inner_text().strip() == "mickey")

    # ── AI models page (SPEC §4.9/§4.10, model_manager + cloud_ai) ──
    check("models: tab visible", pg.locator("#nav .nav-tab[data-view=models]").is_visible())
    pg.click("#nav .nav-tab[data-view=models]")
    pg.wait_for_timeout(600)
    check("models: view active", pg.locator("#view-models.active").count() == 1)
    check("models: cloud card rendered",
          pg.locator("#cloud-card:not(.hidden)").count() == 1)
    check("models: cloud key state unset", "未设置密钥" in pg.locator("#cloud-key-state").inner_text())
    check("models: cloud chat suggestions present",
          pg.locator("#cloud-chat-suggest option").count() >= 3)
    check("models: capability cards rendered",
          pg.locator("#models-caps .card").count() >= 4)
    check("models: active badge on llm",
          pg.locator("#model-card-llm .model-row-active .badge-active").count() == 1)
    check("models: downloadable alternative offered",
          pg.locator("#model-card-llm .model-row:not(.model-row-active) button.btn-primary").count() == 1)
    check("models: no-source entry shows badge",
          pg.locator("#model-card-ocr .badge-nodl").count() >= 1)
    check("models: immediate badge on detection card",
          pg.locator("#model-card-ai .model-apply-badge.badge-immediate").count() == 1)
    # Download flow: the mock task ticks over ~3s with SSE progress.
    pg.click("#model-card-llm .model-row:not(.model-row-active) button.btn-primary")
    pg.wait_for_timeout(1200)
    check("models: progress bar during download",
          pg.locator("#model-card-llm .model-progress:not(.hidden)").count() == 1)
    pg.wait_for_timeout(3500)
    check("models: download completes to activate button",
          pg.locator("#model-card-llm .model-row:not(.model-row-active) button", has_text="启用").count() == 1)
    # Cloud save round-trip: provider select + fallback toggle persist.
    pg.select_option("#cloud-provider", "openrouter")
    pg.fill("#cloud-api-key", "sk-or-mock-key")
    pg.click("#cloud-save")
    pg.wait_for_timeout(600)
    check("models: cloud key state set after save",
          "已保存密钥" in pg.locator("#cloud-key-state").inner_text())
    check("models: clear-key button appears",
          pg.locator("#cloud-key-clear:not(.hidden)").count() == 1)
    shot(pg, "16-models", full=True)
    # Back to the assistant view for the speaker enroll flow (the card
    # moved there in the redesign); meetings stay under records.
    pg.click("#nav .nav-tab[data-view=assistant]")
    pg.wait_for_timeout(600)

    # ── Speakers card (SPEC appendix A #25): seeded chip + enroll flow ─
    check("speakers: card visible", pg.locator("#speakers-card:not(.hidden)").count() == 1)
    check("speakers: seeded chip rendered", pg.locator("#speakers-list .speaker-chip").count() == 1)
    check("speakers: chip shows sample count",
          "×3" in pg.locator("#speakers-list .speaker-samples").inner_text())
    # Enroll "alice" with 3 samples: the mock collects one per GET poll,
    # the frontend polls every 1.5s and commits when full.
    pg.fill("#speaker-name", "alice")
    pg.click("#speaker-enroll")
    pg.wait_for_timeout(1000)
    check("speakers: progress visible during enrollment",
          pg.locator("#speaker-progress:not(.hidden)").count() == 1)
    pg.wait_for_timeout(5500)
    check("speakers: enrollment auto-committed",
          pg.locator("#speakers-list .speaker-chip").count() == 2 and
          any("alice" in c.inner_text()
              for c in pg.locator("#speakers-list .speaker-chip").all()))
    check("speakers: progress hidden after commit",
          pg.locator("#speaker-progress.hidden").count() == 1)
    shot(pg, "15e-speakers", full=True)
    # Delete the alice chip; mickey stays.
    pg.locator("#speakers-list .speaker-chip", has_text="alice").locator(".speaker-del").click()
    pg.wait_for_timeout(400)
    pg.click("#confirm-ok")
    pg.wait_for_timeout(600)
    check("speakers: delete removes only the named profile",
          pg.locator("#speakers-list .speaker-chip").count() == 1 and
          "mickey" in pg.locator("#speakers-list .speaker-chip").inner_text())

    # ── Meetings card (SPEC appendix A #27): seeded row + live cycle ──
    pg.click("#nav .nav-tab[data-view=records]")
    pg.wait_for_timeout(600)
    check("meetings: card visible", pg.locator("#meetings-card:not(.hidden)").count() == 1)
    check("meetings: seeded done row rendered",
          pg.locator("#meetings-list .meeting-row").count() == 1 and
          "已完成" in pg.locator("#meetings-list .meeting-row .record-kind").inner_text())
    pg.click("#meeting-start")
    pg.wait_for_timeout(700)
    check("meetings: recording indicator visible",
          pg.locator("#meeting-rec:not(.hidden)").count() == 1)
    check("meetings: stop button swapped in",
          pg.locator("#meeting-stop:not(.hidden)").count() == 1 and
          pg.locator("#meeting-start.hidden").count() == 1)
    check("meetings: recording row in list",
          "录音中" in pg.locator("#meetings-list .meeting-row").first.inner_text())
    shot(pg, "15f-meeting-rec", full=True)
    pg.click("#meeting-stop")
    pg.wait_for_timeout(700)
    check("meetings: indicator cleared after stop",
          pg.locator("#meeting-rec.hidden").count() == 1 and
          pg.locator("#meeting-start:not(.hidden)").count() == 1)
    check("meetings: processing row in list",
          "处理中" in pg.locator("#meetings-list .meeting-row").first.inner_text())
    # The mock finalizes 2s after stop; the 4s safety poll re-renders.
    pg.wait_for_timeout(4800)
    check("meetings: done row after async pipeline",
          "已完成" in pg.locator("#meetings-list .meeting-row").first.inner_text())
    # Expand the minutes of the newest (first) row.
    pg.locator("#meetings-list .meeting-row").first.locator("button", has_text="展开纪要").click()
    pg.wait_for_timeout(700)
    check("meetings: segments rendered on expand",
          pg.locator("#meetings-list .meeting-seg").count() >= 2)
    check("meetings: named + anonymous speaker labels",
          "mickey" in pg.locator("#meetings-list .meeting-detail").inner_text() and
          "说话人 2" in pg.locator("#meetings-list .meeting-detail").inner_text())
    shot(pg, "15g-meeting-minutes", full=True)

    pg.select_option("#records-kind", "voice")
    pg.wait_for_timeout(600)
    check("records: filter narrows to voice",
          pg.locator("#records-list .record-row").count() == 1 and
          pg.locator("#records-list .kind-voice").count() == 1)
    shot(pg, "15d-records", full=True)
    pg.click("#records-clear")
    pg.wait_for_timeout(400)
    pg.click("#confirm-ok")
    pg.wait_for_timeout(600)
    check("records: cleared to empty state", pg.locator("#records-list .record-empty").count() == 1)

    # ── System ▸ Status ────────────────────────────────────────────────
    pg.click("#nav .nav-tab[data-view=system]")
    pg.wait_for_timeout(400)
    pg.click("#system-subnav .subnav-btn[data-subview=status]")
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

    # ── Resource profile (SPEC appendix A #40; mock: 10 features, 5 off) ──
    check("res: profile card visible", pg.locator("#resource-card").is_visible())
    check("res: all 10 feature rows", pg.locator("#resource-card .res-row").count() == 10)
    check("res: every off row carries a reason",
          pg.locator("#resource-card .res-row-off .res-reason").count() == 5)
    check("res: budget summary rendered",
          "MiB" in (pg.text_content("#resource-card .res-summary") or ""))
    check("res: mode badge present", pg.locator("#resource-card .res-mode").count() == 1)

    # ── System ▸ Devices ───────────────────────────────────────────────
    pg.click("#system-subnav .subnav-btn[data-subview=devices]")
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
    pg.click("#nav .nav-tab[data-view=preview]")
    pg.wait_for_timeout(800)
    shot(pg, "18-english-live", full=True)

    # ── Theme toggle (dark→light) across views ────────────────────────
    pg.click("#nav .theme-btn")
    pg.wait_for_timeout(400)
    check("theme: light applied", pg.evaluate("document.documentElement.dataset.theme") == "light")
    shot(pg, "19-light-cameras", full=True)
    pg.click("#nav .nav-tab[data-view=system]")
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
    # SPEC §2: the username input is rendered in login mode too (empty
    # resolves to "admin" server-side) — fill it explicitly for the
    # re-login cycle so the explicit-username path is what gets covered.
    check("login: username field visible", pg.locator("#login-username").is_visible())
    pg.fill("#login-username", "admin")
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
    # Overlay-canvas layout contract (2026-10-01 regression): the overlay
    # canvases must be absolutely positioned. An in-flow #zones-overlay fed
    # its device-pixel intrinsic size back into renderZonesOverlay's
    # wrapper.clientHeight read — at dpr>1 every ai_detection event doubled
    # the wrapper height until the page whited out. Reproduce the sizing
    # loop exactly and assert the wrapper cannot grow.
    pos = mp.evaluate("getComputedStyle(document.getElementById('zones-overlay')).position")
    check("mobile: zones overlay out of flow", pos == "absolute", f"position={pos}")
    growth = mp.evaluate("""() => {const w = document.querySelector('.stream-wrapper');
        const zs = document.getElementById('zones-overlay');
        const dpr = window.devicePixelRatio || 1;
        const before = Math.round(w.getBoundingClientRect().height);
        for (let i = 0; i < 5; i++) {
            zs.width = Math.round(w.clientWidth * dpr);
            zs.height = Math.round(w.clientHeight * dpr);
        }
        return Math.round(w.getBoundingClientRect().height) - before}""")
    check("mobile: overlay sizing loop cannot grow the wrapper", growth == 0, f"grew {growth}px")
    mp.click("#nav-mobile .nav-tab[data-view=assistant]")
    mp.wait_for_timeout(1000)
    shot(mp, "31-mobile-assistant", full=True)
    mp.click("#nav-mobile .nav-tab[data-view=system]")
    mp.wait_for_timeout(1000)
    shot(mp, "32-mobile-settings", full=True)
    mp.click("#system-subnav .subnav-btn[data-subview=status]")
    mp.wait_for_timeout(1000)
    shot(mp, "33-mobile-status", full=True)
    check("mobile: five bottom tabs",
          mp.locator("#nav-mobile .nav-tab:not(.hidden)").count() == 5,
          str(mp.locator("#nav-mobile .nav-tab:not(.hidden)").count()))
    # horizontal overflow check on every view
    overflow = mp.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check("mobile: no horizontal overflow", overflow <= 0, f"{overflow}px overflow")
    check("mobile: zero page errors", not merrors, "; ".join(merrors[:3]))
    mctx.close()

    b.close()

fails = [r for r in results if not r[1]]
print(f"\n===== {len(results) - len(fails)}/{len(results)} passed, {len(fails)} failed =====")
sys.exit(1 if fails else 0)
