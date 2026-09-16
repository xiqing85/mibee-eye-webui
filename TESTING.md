# Multi-Device Testing Guide

Layered testing methodology for the unified web layer (SPEC v1 + shared frontend). Four layers from fast to slow; the further down, the closer to real hardware.

## Environments

| Environment | Address | Credentials | Notes |
|------|------|------|------|
| mibee-eye-rs (Raspberry Pi) | `http://<rs-device-ip>:8088` | `web.username`/`web.password` in device config | service `mibee-eye-rs.service` |
| mibee-eye-go (Raspberry Pi) | `http://<go-device-ip>:8088` | falls back to ONVIF credentials | service `mibee-eye` |
| mibee-eye-notebook (local) | `https://127.0.0.1:8443` | self-created on first-run setup | self-signed TLS, accept the certificate; SQLite `mibee_eye.db` in the working directory |
| mock (no device) | `http://127.0.0.1:8090` | anything | `make mock`, a SPEC-conformant fake backend |

Credentials for the two Pi devices live in each device's config (`[web]` section of the rs TOML config; `web:` section of the go YAML config).

## Layer 0: pure frontend (no device)

```bash
cd mibee-eye-webui && make mock   # :8090
# open http://127.0.0.1:8090 — mock supports MOCK_PREAUTH=1 to skip login
```

Daily loop when changing the frontend: edit `static/` → verify against mock → `make sync-*`.

## Layer 1: API smoke (one command, zero dependencies)

```bash
tools/smoke.sh http://<rs-device-ip>:8088 <password>          # rs
tools/smoke.sh http://<go-device-ip>:8088 <password>          # go
tools/smoke.sh https://127.0.0.1:8443 <password> admin        # notebook
```

Covers: health envelope → auth state (401 / first-run 503→setup) → login → me → capabilities (`spec_version`) → cameras → config → SSE probe → session invalidation after logout. Exit code 0 means all green.

## Layer 2: browser walkthrough (Playwright)

One-time install (no node dependencies; repo-root `.venv/` is gitignored):

```bash
python3 -m venv .venv && .venv/bin/pip install playwright
.venv/bin/playwright install chromium
```

Run (handles first-run setup / wrong password / login / camera start / live video playback progress / all views / language & theme / logout; screenshots land in `tmp/walkthrough-<tag>/`; non-zero exit = something is wrong):

```bash
.venv/bin/python tools/ux_visual_check.py                                  # mock: 47 interaction/visual assertions + screenshots
.venv/bin/python tools/browser_walkthrough.py http://<rs-device-ip>:8088 <password> admin rs
.venv/bin/python tools/browser_walkthrough.py http://<go-device-ip>:8088 <password> admin go
.venv/bin/python tools/browser_walkthrough.py https://127.0.0.1:8443 <password> admin nb
```

Note: the script starts a stopped camera on the cameras page before verifying live video, so a fresh notebook first-run (camera not yet started) also passes in one go.

On non-zero exit, check the `ISSUES:` list and screenshots. For manual review, three screenshots matter most: `04-live.png` (picture is moving), `06-settings.png` (config editor complete), `07-status.png`.

## Layer 3: run the notebook service locally

```bash
cd ../mibee-eye-notebook
cargo build --release          # skip if target/release/mibee-eye already exists
rm -f mibee_eye.db*            # delete the DB to walk the first-run setup flow; keep it to reuse an existing account
(setsid ./target/release/mibee-eye --config config.toml > tmp/run.log 2>&1 < /dev/null &)
curl -sk https://127.0.0.1:8443/api/health
```

The first browser visit lands on the setup form (username + password ≥8 chars + confirmation); creating the account logs you in.

## Layer 4: real-device interop regression (mandatory after deployment)

If the change touches the protocol layer (see the layering table in each repo's contributing docs), the deployed device must pass the NVR interop surface:

```bash
# go device
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' http://<go-device-ip>:8088/snapshot   # 200 video/H264
printf 'DESCRIBE rtsp://<go-device-ip>:8554/stream RTSP/1.0\r\nCSeq: 1\r\n\r\n' | timeout 4 nc <go-device-ip> 8554 | head -1   # 200 OK
ssh <user>@<go-device-ip> 'journalctl -u mibee-eye --since "-3 min" | grep -iE "REGISTER|SUBSCRIBE"'
curl -s -X POST http://<go-device-ip>:8080/onvif/device_service -H 'Content-Type: application/soap+xml' \
  -d '<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Body><GetDeviceInformation xmlns="http://www.onvif.org/ver10/device/wsdl"/></s:Body></s:Envelope>' | head -c 200

# rs device: same checks, service name mibee-eye-rs, address <rs-device-ip>
```

Pass criteria: `/snapshot` returns 200 without auth, RTSP DESCRIBE response element names unchanged (the NVR does local-name matching), GB28181 REGISTER successful + SUBSCRIBE 200 OK, ONVIF responses byte-stable.

## Deploy loop (after code changes)

```bash
# rs: cross-compile on the workstation → push → restart
cd ../mibee-eye-rs && cargo zigbuild --release --target aarch64-unknown-linux-gnu
# then copy target/aarch64-unknown-linux-gnu/release/mibee-eye-rs to the device
# and restart the mibee-eye-rs service

# go:
cd ../mibee-eye-go && GOOS=linux GOARCH=arm64 go build -o build/mibee-eye ./cmd/server
# then copy build/mibee-eye to the device and restart the mibee-eye service

# notebook: cargo build --release and run locally
```

> Do not compile on the Pi. Frontend changes: run `make sync-*` in mibee-eye-webui first, then build the device repo.
