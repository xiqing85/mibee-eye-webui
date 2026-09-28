# MiBee Eye — Web UI User Guide

This guide covers the web interface embedded in every **mibee-eye** camera
device (the Raspberry Pi builds, the notebook/PC build). One codebase serves
all of them: the UI reads the device's advertised `capabilities` and renders
exactly the features that device supports, so your device may hide some of
the panels described here. Device-specific extras (voice interaction, local
LLM chat, alarm descriptions) are documented in each device repo's user
guide — see [mibee-eye-notebook](https://github.com/xiqing85/mibee-eye-notebook).

Contents: [First visit](#first-visit--setup--login) ·
[Getting around](#getting-around) · [Live view](#live-view) ·
[Zones editor](#zones-editor) · [PTZ & imaging](#ptz--imaging) ·
[AI panel](#ai-panel) · [Chat](#chat) · [Alarms](#alarms) ·
[Cameras / Devices / Status](#cameras--devices--status) ·
[Settings](#settings) · [Language & theme](#language--theme) ·
[Troubleshooting](#troubleshooting)

## First visit — setup & login

1. Open the device address in a browser, e.g. `https://<device>:8443`. The
   certificate is self-signed unless the operator installed their own, so
   the browser shows a one-time warning — proceed (Advanced → Proceed).
2. **First visit only**: the device answers that it needs setup. Choose an
   admin username and a strong password — this account lives on the device.
3. **Logins after that**: enter your username and password. Leaving the
   username empty logs in as `admin` on single-user devices.
4. Wrong passwords rate-limit per IP and lock the account back off
   exponentially after repeated failures — wait and retry.

The session lasts 24 hours (or until you press **Log out**).

## Getting around

The top bar switches between the five views — **Live**, **Cameras**,
**Settings**, **Status**, **Devices** — and carries the language (zh/en)
and theme toggles plus the logout button. On narrow (phone) screens the
same views move to a bottom tab bar. The **Cameras** and **Devices** tabs
appear only on devices that support multiple cameras / device enumeration.

## Live view

The live view shows the camera stream (MSE/H.264) with overlays drawn on
top. The stream toolbar (top of the picture) carries:

- **Start / stop** — begin or end the capture stream.
- **Snapshot** — download a JPEG of the current frame.
- **Quality** — switch between the main stream and the low-resolution
  **sub** stream (bandwidth-saving; only on devices that advertise
  `substream`).
- **Rotate** — quarter-turn rotation on devices that expose the control.
- **Zones** — opens the zones editor (see below; capability `zones`).

Overlays, when the device supports them:

- **Detection boxes** (`ai`) — green boxes with class and score, live.
- **Zone overlay** (`zones`) — your saved zones, highlighted while firing.

### Zones editor

Zones are regions the device watches. **Intrusion** zones are polygons
(≥ 3 points) that alarm when a tracked object stays inside for the dwell
time; **tripwire** zones are two-point lines that alarm on crossing
(direction reported).

1. Press the **zones** toolbar button — the current frame freezes as the
   drawing backdrop.
2. Click points on the backdrop (poly), two for a tripwire. **Undo** /
   **Clear** fix mistakes.
3. Name the zone, choose the kind, set dwell seconds (intrusion only).
4. **Save** — applies immediately for that camera; no restart.

Zone crossings arrive as alarm toasts and `zone_event` events (requires the
device's AI tracking to be active).

## PTZ & imaging

Devices that advertise `ptz` show a pan/tilt pad with press-and-hold
movement, a zoom slider and keyboard-arrow control. Devices that advertise
`imaging` show an imaging panel — brightness / contrast / saturation /
sharpness sliders, anti-flicker, and white-balance & exposure mode selects.
Changes are written immediately (debounced); nothing to save.

## AI panel

Devices with on-device detection advertise `ai`; those with a model store
also advertise `ai_models` and show a **model picker** on the live view.
Switching models activates the chosen one immediately (with server-side
rollback on failure) and every open browser tab follows via an
`ai_model_changed` event — no reload needed.

## Chat

Devices running a local LLM advertise `chat` and show a floating chat
button (bottom-right). Type a message; the device answers on-device — no
cloud involved. Recent turns are kept for follow-up questions. Voice-driven
replies arrive in the same panel (see the device's own user guide for the
voice loop).

## Alarms

Real-time notifications appear as toasts (top of the screen, auto-dismiss):

| Toast | Meaning |
|-------|---------|
| Visual alarm | The detector fired (`source: "ai"`) — class list and scores shown. |
| Sound alarm | A watched sound fired (`source: "audio"`) — e.g. Dog, Glass, Siren. |
| Zone alarm | A zone crossing/intrusion — zone name and direction. |
| Alarm description | A one-sentence VLM description of what happened in the alarm frame (device-dependent, arrives seconds after its alarm). |
| Voice transcript | The device heard and transcribed speech after its wake word (device-dependent). |

Alarms are also forwarded to GB28181 platforms when that protocol is enabled
and subscribed (device-side behavior).

## Cameras / Devices / Status

- **Cameras** (multi-camera devices): add, configure, start/stop and remove
  cameras; per-camera options live here (resolution, FPS, quality/substream,
  rotation, watermark, recording). Changes apply without restarting the
  service; some options apply on the next stream start.
- **Devices**: enumerate the host's video (V4L2) and audio (ALSA) devices
  and the formats each supports — use it to pick device indexes before
  adding a camera.
- **Status**: device identity (name/model/firmware/uptime), protocol runtime
  state, and live charts where the device provides metrics.

## Settings

One unified configuration editor. Sections vary by device: protocols
(ONVIF, GB28181, RTMP push, recording, watermark), network, and UI
preferences. Most protocol sections apply **immediately** on save — the
badge on the section says so; a few devices mark a section as applying on
**restart**, and saving one triggers a short service restart (the UI waits
and re-connects automatically — keep the tab open).

## Language & theme

The **zh/en** toggle switches every label instantly and persists per
browser. The theme toggle follows **system preference** by default and can
be forced day or night; the choice persists.

## Troubleshooting

| Symptom | Explanation / fix |
|---------|-------------------|
| A control or view described here doesn't exist | The UI is capability-gated — your device doesn't advertise that feature (model missing, engine off, build without it). Not a bug. |
| Certificate warning on every visit | Self-signed certificate by default; install a real certificate or trust the device's CA. |
| Save triggered a restart and the page reconnected | Expected on devices whose dialect marks that section `config_apply: restart`. Wait for the UI to re-login. |
| Login rejected repeatedly | Exponential lockout after failures — wait a minute, or reset the password on the device (`--reset-password`). |
| Stream is black / won't start | The camera may be stopped or in use by another process; check the Cameras view and the device logs. |
| Zone events never fire | Zones need the AI tracking to be active — check that detection boxes appear and the camera is running. |
