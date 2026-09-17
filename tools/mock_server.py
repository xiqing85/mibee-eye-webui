#!/usr/bin/env python3
"""SPEC v1 mock server — serves the mibee-webui frontend against a fully
conformant in-memory API, for frontend development without a device.

Implements: auth (session cookie + CSRF), health/status/capabilities,
config, cameras (multi-camera CRUD + start/stop + snapshot/live/stream.mse
stubs), imaging, ptz, detections, devices and SSE events.

Usage: python3 tools/mock_server.py [port]   (default 8090)
"""
import json
import math
import os
import secrets
import struct
import sys
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

START = time.time()

# Testing affordance: MOCK_PREAUTH=1 skips the session check so headless
# screenshot smoke tests can render the authenticated app shell.
PREAUTH = os.environ.get("MOCK_PREAUTH") == "1"

STATE = {
    "setup_done": False,
    "username": "admin",
    "password": "",  # set by /api/auth/setup
    "sessions": {},  # token -> username
    "cameras": [
        {"id": "0", "name": "Front CSI", "status": "online", "camera_type": "csi",
         "rtsp_url": "rtsp://localhost:8554/stream", "resolution": "1280x720", "fps": 25},
    ],
    "config": {
        "web": {"port": 8088, "username": "admin", "password": "****"},
        "camera": {"mode": "mtxrpicam", "width": 1280, "height": 720, "fps": 25,
                   "bitrate": 2500000, "codec": "h264", "rotation": 0},
        "rtsp": {"port": 8554, "username": "", "password": "****"},
        "onvif": {"port": 8080, "username": "admin", "password": "****"},
        "gb28181": {"enabled": False, "platform_sip_address": "192.168.1.100",
                    "platform_sip_port": 5060, "device_id": "34020000001320000001",
                    "channel_id": "34020000001310000001",
                    "sip_domain": "", "password": "****", "local_sip_port": 5060,
                    "register_interval_secs": 3600, "heartbeat_interval_secs": 60,
                    "heartbeat_timeout_count": 3,
                    "alarm_notify_enabled": True, "alarm_cooldown_secs": 30,
                    "position_longitude": "", "position_latitude": "",
                    "talkback_playback": True, "talkback_upstream": False},
        "logging": {"level": "info"},
        "watermark": {"enabled": False, "text": "", "show_timestamp": True,
                      "timestamp_format": "%Y-%m-%d %H:%M:%S", "position": "top-left",
                      "font_size": 24, "font_path": ""},
        "features": {"ai": {"enabled": False, "model": "nanodet-plus-m-320",
                            "model_path": "models/nanodet-m.onnx",
                            "cpu_cores": [2, 3]}},
    },
    "imaging_params": {"Brightness": 0.0, "Contrast": 1.0, "Saturation": 1.0,
                       "Sharpness": 1.0, "AWBMode": "auto", "ExposureMode": "normal",
                       "HFlip": False, "VFlip": False},
    "imaging_options": {
        "Brightness": {"min": -1, "max": 1, "step": 0.01, "default": 0},
        "Contrast": {"min": 0, "max": 2, "step": 0.01, "default": 1},
        "Saturation": {"min": 0, "max": 2, "step": 0.01, "default": 1},
        "Sharpness": {"min": 0, "max": 8, "step": 0.1, "default": 1},
        "AWBMode": {"enums": ["auto", "daylight", "cloudy", "incandescent", "fluorescent"]},
        "ExposureMode": {"enums": ["normal", "night", "sports", "backlight"]},
    },
    "ptz": {"pan": 0.5, "tilt": 0.5, "zoom": 1.0},
    # bbox is in video pixels (SPEC §4.6); the mock camera streams 1280×720.
    "detections": {"detections": [
        {"label": "person", "confidence": 0.87, "bbox": [256, 216, 192, 288]},
    ], "model": "nanodet-plus-m-320", "timestamp": 0},
    # Model registry (SPEC §4.6): mirrors the device registry — two
    # NanoDet exports plus the YOLOX cross-family decoder, and one
    # available:false entry the UI must disable.
    "ai_models": {"active": "nanodet-plus-m-320", "models": [
        {"id": "nanodet-plus-m-320", "family": "nanodet", "input": 320,
         "source": "builtin", "available": True},
        {"id": "nanodet-plus-m-416", "family": "nanodet", "input": 416,
         "source": "builtin", "available": True},
        {"id": "yolox-nano-416", "family": "yolox", "input": 416,
         "source": "builtin", "available": True},
        {"id": "yolox-s-640", "family": "yolox", "input": 640,
         "source": "builtin", "available": False},
    ],
    "upload": {"allowed": True, "max_bytes": 33554432}},
    "sse_queues": [],
}

CAPS = {
    "spec_version": "1",
    "device": {"name": "Mock Cam", "model": "mock", "vendor": "MiBee Studio"},
    "auth": {"model": "session", "setup": True},
    "multi_camera": True,
    "camera_management": True,
    "camera_control": True,
    "imaging": True,
    "ai": True,
    "ai_models": True,
    "ai_upload": True,
    "ptz": True,
    "hls": False,
    "recording": True,
    "watermark": True,
    "devices": True,
    "mjpeg": True,
    "mse": True,
    "webrtc": False,
    "events": ["camera_added", "camera_offlined", "param_changed", "ai_detection",
               "ai_model_changed", "recording", "status", "alarm"],
    "config_apply": {"default": "restart", "sections": {"imaging": "immediate",
                                                        # demonstrates the immediate badge on a real config section
                                                        "logging": "immediate",
                                                        "watermark": "restart"}},
    "restart": True,
    "observability": {"metrics": True, "logs": True, "requests": True},
}

AUTH_EXEMPT = {"/api/auth/login", "/api/auth/setup", "/api/auth/logout"}

# ── observability mock state ─────────────────────────────────────────
_METRICS_PREV = {"ts": None, "rx": None, "tx": None}
_LOG_SEQ = iter(range(1, 10 ** 9))
_REQ_SEQ = iter(range(1, 10 ** 9))


def _metrics_summary():
    """Synthetic but self-consistent resource snapshot with 2s-sampler rates."""
    now = time.time()
    t = now / 2.0
    rx = int(8_000_000 * (1 + math.sin(t / 7)) + now * 1024)
    tx = int(1_500_000 * (1 + math.cos(t / 5)) + now * 256)
    prev = _METRICS_PREV
    dt = 2.0 if prev["ts"] is None else max(now - prev["ts"], 0.001)
    rx_rate = 0.0 if prev["rx"] is None else max((rx - prev["rx"]) / dt, 0.0)
    tx_rate = 0.0 if prev["tx"] is None else max((tx - prev["tx"]) / dt, 0.0)
    prev.update(ts=now, rx=rx, tx=tx)
    return {
        "ts": int(now),
        "interval_ms": 2000,
        "system": {
            "cpu_percent": round(18 + 14 * math.sin(t / 11) + 4 * math.sin(t * 3.1), 1),
            "load_avg": [round(0.4 + 0.2 * math.sin(t / 30), 2) for _ in range(3)],
            "memory": {"total": 4 * 1024 ** 3, "used": int(1.4 * 1024 ** 3),
                       "available": int(2.6 * 1024 ** 3)},
            "disks": [
                {"path": "/", "total": 60 * 1024 ** 3, "used": 22 * 1024 ** 3,
                 "free": 38 * 1024 ** 3},
                {"path": "/mnt/data", "total": 240 * 1024 ** 3, "used": 8 * 1024 ** 3,
                 "free": 232 * 1024 ** 3},
            ],
            "network": {"rx_bytes": rx, "tx_bytes": tx,
                        "rx_rate": round(rx_rate, 1), "tx_rate": round(tx_rate, 1)},
        },
        "process": {
            "cpu_percent": round(9 + 5 * math.sin(t / 9), 1),
            "rss_bytes": 96 * 1024 ** 2,
            "open_fds": 37,
            "uptime": int(now - START),
            "io_read_bytes": 123_456_789, "io_write_bytes": 8_765_432,
            "storage_bytes": 7_800_000_000,
            "traffic": {"http_rx_bytes": 45_000, "http_tx_bytes": 2_400_000,
                        "rtsp_tx_bytes": 90_000_000, "gb28181_tx_bytes": 31_000_000},
        },
    }


def _log_entries():
    level = "info"
    target = "gb28181_rs::server"
    msgs = [
        ("info", "gb28181_rs::server", "gb28181: registration refreshed with platform"),
        ("info", "mibee_eye::recording", "recording: segment closed (600s, 4879 frames)"),
        ("warn", "mibee_eye::ai", "ai: guardrail skip — memory above soft cap"),
        ("error", "mibee_eye::camera", "camera: capture poll timeout, reopening device"),
        ("debug", "mibee_eye::web", "snapshot served (65 KB)"),
    ]
    out = []
    for _ in range(6):
        lvl, tgt, msg = msgs[next(_LOG_SEQ) % len(msgs)]
        out.append({"ts": int(time.time()) - next(_LOG_SEQ) * 3, "level": lvl,
                    "target": tgt, "message": msg, "request_id": None})
    return out


def _request_entries():
    routes = [("GET", "/api/status", 200), ("GET", "/api/cameras/0/stream.mse", 200),
              ("GET", "/api/metrics/summary", 200), ("POST", "/api/auth/login", 401),
              ("GET", "/api/detections", 200)]
    out = []
    for _ in range(8):
        m, p, s = routes[next(_REQ_SEQ) % len(routes)]
        out.append({"id": format(next(_REQ_SEQ), "06x"), "method": m, "path": p,
                    "status": s, "duration_ms": round(0.4 + (next(_REQ_SEQ) % 90) / 10, 1),
                    "ts": int(time.time()) - next(_REQ_SEQ)})
    return out



def sse_broadcast(event, payload):
    data = json.dumps(payload)
    dead = []
    for q in STATE["sse_queues"]:
        try:
            q.put_nowait(f"event: {event}\ndata: {data}\n\n")
        except Exception:
            dead.append(q)
    for q in dead:
        STATE["sse_queues"].remove(q)


def ai_thread():
    import queue
    fn = 0
    while True:
        time.sleep(2)
        fn += 1
        bbox = [round(256 + 128 * math.sin(fn / 5)), 216, 192, 288]
        STATE["detections"]["detections"] = [
            {"label": "person", "confidence": 0.7 + 0.2 * abs(math.sin(fn / 7)), "bbox": bbox}]
        sse_broadcast("ai_detection", {"camera_id": "0",
                                       "detections": STATE["detections"]["detections"],
                                       "frame_number": fn})
        # Alarm rising edges (SPEC §6) — the mock person trips an edge every
        # 30s, mirroring the edge cooldown default (alarm_cooldown_secs 30).
        if fn % 15 == 1:
            sse_broadcast("alarm", {"camera_id": "0", "active": True, "source": "ai",
                                    "targets": 1, "timestamp": int(time.time() * 1000)})


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ── plumbing ────────────────────────────────────────────────────
    def log_message(self, fmt, *args):
        sys.stderr.write("[mock] %s\n" % (fmt % args))

    def send_json(self, obj, status=200, extra_headers=None):
        body = (json.dumps(obj) + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or {}).items():
            values = v if isinstance(v, list) else [v]
            for item in values:
                self.send_header(k, item)
        self.end_headers()
        self.wfile.write(body)

    def ok(self, data, status=200, extra_headers=None):
        self.send_json({"ok": True, "data": data}, status, extra_headers)

    def err(self, error, message, status):
        self.send_json({"ok": False, "error": error, "message": message}, status)

    def parse_cookies(self):
        jar = cookies.SimpleCookie()
        for hdr in self.headers.get("Cookie", "").split(";"):
            if hdr.strip():
                jar.load(hdr)
        return {k: morsel.value for k, morsel in jar.items()}

    def body_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length) or b"{}")

    def session_user(self):
        token = self.parse_cookies().get("session")
        return STATE["sessions"].get(token)

    def authed(self):
        return PREAUTH or self.session_user() is not None

    def start_session(self):
        token = secrets.token_urlsafe(24)
        csrf = secrets.token_urlsafe(24)
        STATE["sessions"][token] = STATE["username"]
        return token, csrf

    # ── routing ─────────────────────────────────────────────────────
    def do_GET(self):
        raw = self.path
        path = raw.split("?")[0]
        if path.startswith("/api/"):
            if path in ("/api/health", "/api/auth/me") or self.authed():
                return self.get_api(path)
            return self.err("unauthorized", "not signed in", 401)
        if path == "/metrics":
            # Public Prometheus exposition (SPEC §3.2).
            m = _metrics_summary()
            body = (
                "# HELP mibee_eye_system_cpu_percent System CPU usage percent\n"
                "# TYPE mibee_eye_system_cpu_percent gauge\n"
                f"mibee_eye_system_cpu_percent {m['system']['cpu_percent']}\n"
                "# HELP mibee_eye_process_rss_bytes Process resident set size\n"
                "# TYPE mibee_eye_process_rss_bytes gauge\n"
                f"mibee_eye_process_rss_bytes {m['process']['rss_bytes']}\n"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body.encode())
        if path == "/" or path == "/index.html":
            return self.serve_file("index.html", "text/html; charset=utf-8")
        if path == "/smoke":
            # Screenshot harness: same app, but the stylesheet link carries
            # ?slow=1 so the load event (when `firefox --screenshot` captures)
            # fires AFTER the async boot completes → post-login app shell.
            body = open("static/index.html").read().replace(
                'href="/style.css"', 'href="/style.css?slow=1"')
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/style.css":
            ctype = "text/css"
            if "slow=1" in raw:
                time.sleep(2.5)
            return self.serve_file("style.css", ctype)
        if path.startswith("/js/"):
            return self.serve_file(path.lstrip("/"), "application/javascript")
        self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path.startswith("/api/"):
            if path not in AUTH_EXEMPT and not self.authed():
                return self.err("unauthorized", "not signed in", 401)
            if path not in AUTH_EXEMPT:
                ck = self.parse_cookies().get("csrf-token")
                if not ck or ck != self.headers.get("X-CSRF-Token"):
                    return self.err("unauthorized", "csrf mismatch", 401)
            return self.post_api(path)
        self.send_error(404)

    def do_PUT(self):
        path = self.path.split("?")[0]
        if not self.authed():
            return self.err("unauthorized", "not signed in", 401)
        ck = self.parse_cookies().get("csrf-token")
        if not ck or ck != self.headers.get("X-CSRF-Token"):
            return self.err("unauthorized", "csrf mismatch", 401)
        if path == "/api/config":
            return self.put_config()
        if path.startswith("/api/cameras/") and path.endswith("/recording"):
            return self.ok({"active": bool(self.body_json().get("active"))})
        if path.startswith("/api/cameras/"):
            cid = path.split("/")[3]
            for cam in STATE["cameras"]:
                if cam["id"] == cid:
                    cam.update(self.body_json())
                    return self.ok(cam)
            return self.err("not_found", "no such camera", 404)
        self.send_error(404)

    def do_DELETE(self):
        path = self.path.split("?")[0]
        if not self.authed():
            return self.err("unauthorized", "not signed in", 401)
        if path.startswith("/api/cameras/"):
            cid = path.split("/")[3]
            STATE["cameras"] = [c for c in STATE["cameras"] if c["id"] != cid]
            return self.send_json({"ok": True, "data": {"status": "ok"}}, 200)
        # Delete an uploaded model (SPEC §4.6).
        if path.startswith("/api/ai/models/"):
            model_id = path[len("/api/ai/models/"):]
            models = STATE["ai_models"]["models"]
            entry = next((m for m in models if m["id"] == model_id), None)
            if entry is None:
                return self.err("not_found", "unknown model id", 404)
            if entry["source"] != "uploaded":
                return self.err("conflict", "builtin models cannot be deleted", 409)
            if STATE["ai_models"]["active"] == model_id:
                return self.err("conflict", "cannot delete the active model", 409)
            STATE["ai_models"]["models"] = [m for m in models if m["id"] != model_id]
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_error(404)

    # ── API: GET ────────────────────────────────────────────────────
    def get_api(self, path):
        if path == "/api/health":
            return self.ok({"status": "ok", "uptime": int(time.time() - START)})
        if path == "/api/auth/me":
            if not STATE["setup_done"]:
                return self.err("setup_required", "initial setup required", 503)
            user = self.session_user() or (STATE["username"] if PREAUTH else None)
            if user:
                return self.ok({"username": user, "role": "admin"})
            return self.err("unauthorized", "not signed in", 401)
        if path == "/api/status":
            return self.ok({"device_name": "Mock Cam", "model": "mock",
                            "vendor": "MiBee Studio", "firmware": "0.1.0-mock",
                            "uptime": int(time.time() - START), "recording": False,
                            "gb28181": False})
        if path == "/api/capabilities":
            return self.ok(CAPS)
        if path == "/api/config":
            return self.ok(STATE["config"])
        if path == "/api/cameras":
            return self.ok(STATE["cameras"])
        if path.startswith("/api/cameras/"):
            parts = path.split("/")
            cid = parts[3]
            cam = next((c for c in STATE["cameras"] if c["id"] == cid), None)
            if len(parts) == 4:
                if cam:
                    return self.ok(cam)
                return self.err("not_found", "no such camera", 404)
            if not cam:
                return self.err("not_found", "no such camera", 404)
            sub = parts[4]
            if sub == "imaging":
                if len(parts) == 6 and parts[5] == "options":
                    return self.ok(STATE["imaging_options"])
                return self.ok(STATE["imaging_params"])
            if sub == "recording":
                return self.ok({"active": False, "storage_path": "/tmp/mock",
                                "segment_secs": 900, "retention_days": 7})
            if sub == "snapshot":
                return self.serve_jpeg()
            if sub == "live":
                return self.serve_mjpeg()
            if sub == "stream.mse":
                return self.serve_mse()
        if path == "/api/ptz/status":
            return self.ok(STATE["ptz"])
        if path == "/api/detections":
            return self.ok(STATE["detections"])
        if path == "/api/ai/models":
            import copy
            return self.ok(copy.deepcopy(STATE["ai_models"]))
        if path == "/api/devices/video":
            return self.ok([{"index": 0, "name": "Mock USB Cam", "formats": ["1920x1080", "1280x720"]},
                            {"index": 1, "name": "Mock CSI Cam", "formats": ["1640x1232"]}])
        if path == "/api/devices/video/0/formats":
            return self.ok([{"width": 1920, "height": 1080, "format": "MJPG", "fps": 30},
                            {"width": 1280, "height": 720, "format": "YUYV", "fps": 30}])
        if path == "/api/devices/audio":
            return self.ok([{"name": "default", "supported_configs": [{"channels": 2}]}])
        if path == "/api/events":
            return self.serve_sse()
        if path == "/api/metrics/summary":
            return self.ok(_metrics_summary())
        if path == "/api/logs":
            return self.ok({"entries": _log_entries()})
        if path == "/api/requests":
            return self.ok({"entries": _request_entries()})
        self.send_error(404)

    # ── API: POST ───────────────────────────────────────────────────
    def post_api(self, path):
        # Multipart bodies (model upload) must stay unread for their own
        # parser; JSON bodies are consumed here as before.
        if "multipart/form-data" in (self.headers.get("Content-Type") or ""):
            body = {}
        else:
            body = self.body_json()
        if path == "/api/auth/setup":
            if STATE["setup_done"]:
                return self.err("bad_request", "already configured", 400)
            if not body.get("username") or len(body.get("password") or "") < 8:
                return self.err("bad_request", "invalid credentials", 400)
            STATE["setup_done"] = True
            STATE["username"] = body["username"]
            STATE["password"] = body["password"]
            token, csrf = self.start_session()
            return self.ok({"username": STATE["username"]}, extra_headers=self.cookie_headers(token, csrf))
        if path == "/api/auth/login":
            if not STATE["setup_done"]:
                return self.err("setup_required", "initial setup required", 503)
            # SPEC §2: an empty/omitted username defaults to the stored one.
            sent_user = body.get("username") or STATE["username"]
            if sent_user != STATE["username"] or body.get("password") != STATE["password"]:
                return self.err("unauthorized", "invalid credentials", 401)
            token, csrf = self.start_session()
            return self.ok({"username": STATE["username"]}, extra_headers=self.cookie_headers(token, csrf))
        if path == "/api/auth/logout":
            token = self.parse_cookies().get("session")
            STATE["sessions"].pop(token, None)
            self.send_response(204)
            self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/auth/reset":
            if body.get("old_password") != STATE["password"]:
                return self.err("unauthorized", "wrong password", 401)
            STATE["password"] = body["new_password"]
            STATE["sessions"].clear()
            token, csrf = self.start_session()
            return self.ok({"username": STATE["username"]}, extra_headers=self.cookie_headers(token, csrf))
        # Upload (SPEC §4.6): multipart family+file → uploaded entry.
        if path.startswith("/api/ai/models/") and not path.endswith("/activate"):
            import re as _re
            model_id = path[len("/api/ai/models/"):]
            if not _re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", model_id):
                return self.err("bad_request", "invalid model id", 400)
            if any(m["id"] == model_id for m in STATE["ai_models"]["models"]):
                return self.err("conflict", "model id already exists", 409)
            ctype = self.headers.get("Content-Type", "")
            m = _re.search(r'boundary="?([^";]+)"?', ctype)
            if not m or "multipart" not in ctype:
                return self.err("bad_request", "multipart form required (family+file)", 400)
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            chunks = body.split(b"--" + m.group(1).encode())
            family, has_file = None, False
            for part in chunks:
                if b'form-data; name="family"' in part:
                    family = part.split(b"\r\n\r\n", 1)[1].split(b"\r\n")[0].decode()
                if b'name="file"' in part:
                    has_file = len(part.split(b"\r\n\r\n", 1)[-1]) > 4
            if family not in ("nanodet", "yolox"):
                return self.err("bad_request", "family must be nanodet or yolox", 400)
            if not has_file:
                return self.err("bad_request", "model failed validation: not an ONNX graph", 400)
            STATE["ai_models"]["models"].append(
                {"id": model_id, "family": family, "input": 416,
                 "source": "uploaded", "available": True})
            return self.ok({"id": model_id, "family": family, "input": 416,
                            "source": "uploaded", "available": True}, 201)
        if path.startswith("/api/ai/models/") and path.endswith("/activate"):
            model_id = path.split("/")[4]
            entry = next((m for m in STATE["ai_models"]["models"]
                          if m["id"] == model_id), None)
            if entry is None:
                return self.err("not_found", f"unknown model id: {model_id}", 404)
            if not entry["available"]:
                return self.err("conflict", "model file not available", 409)
            STATE["ai_models"]["active"] = model_id
            STATE["detections"]["model"] = model_id
            STATE["config"]["features"]["ai"]["model"] = model_id
            sse_broadcast("ai_model_changed", {"camera_id": "0", "model": model_id})
            return self.ok({"active": model_id, "applied": "immediate"})
        if path == "/api/cameras":
            cam = {"id": secrets.token_hex(4), "name": body.get("name", "camera"),
                   "status": "idle", "camera_type": body.get("camera_type", "usb"),
                   "config": body.get("config", {})}
            STATE["cameras"].append(cam)
            sse_broadcast("camera_added", {"camera_id": cam["id"], "name": cam["name"]})
            return self.ok(cam, 201)
        parts = path.split("/")
        if path.startswith("/api/cameras/") and len(parts) == 5:
            cid, action = parts[3], parts[4]
            cam = next((c for c in STATE["cameras"] if c["id"] == cid), None)
            if not cam:
                return self.err("not_found", "no such camera", 404)
            if action == "start":
                if cam["status"] == "online":
                    return self.err("conflict", "already running", 409)
                cam["status"] = "online"
                return self.ok(cam)
            if action == "stop":
                cam["status"] = "idle"
                return self.ok(cam)
            if action == "recording":
                active = bool(body.get("active"))
                sse_broadcast("recording", {"camera_id": cid, "active": active})
                return self.ok({"active": active})
            if action == "imaging" and len(parts) == 5:
                return self.ok({"status": "ok"})
        if path.startswith("/api/cameras/") and "/imaging/param" in path:
            name = body.get("name")
            STATE["imaging_params"][name] = body.get("value")
            sse_broadcast("param_changed", {"camera_id": parts[3], "name": name,
                                            "value": body.get("value")})
            return self.ok({"name": name, "value": body.get("value")})
        if path == "/api/ptz/move":
            STATE["ptz"].update({k: v for k, v in body.items() if v is not None})
            return self.ok(STATE["ptz"])
        if path == "/api/system/restart":
            # SPEC §5.1 — respond, then act. The mock has no process to
            # recycle, so simulate it by resetting the uptime clock.
            global START
            START = time.time()
            return self.ok({"status": "restarting"})
        self.send_error(404)

    # ── API: PUT config ─────────────────────────────────────────────
    def put_config(self):
        errors = []

        def merge(dst, src, path=""):
            # Type fidelity: real devices reject a number where the config
            # schema declares a string (SPEC §5 round-trip must preserve
            # types — 20-digit SIP IDs overflow float64 besides).
            for k, v in src.items():
                p = f"{path}.{k}" if path else k
                if isinstance(v, dict) and isinstance(dst.get(k), dict):
                    merge(dst[k], v, p)
                    continue
                if isinstance(dst.get(k), str) and isinstance(v, (int, float)) and not isinstance(v, bool):
                    errors.append(p)
                    continue
                if isinstance(dst.get(k), list) and isinstance(v, dict):
                    errors.append(p)
                    continue
                if v == "****":
                    continue  # masked round-trip (SPEC §5)
                dst[k] = v

        merge(STATE["config"], self.body_json())
        if errors:
            return self.err("bad_request",
                            "invalid config: numeric value for string field(s): "
                            + ", ".join(sorted(errors)), 400)
        return self.ok({"applied": "restart"})

    # ── media stubs ─────────────────────────────────────────────────
    def serve_file(self, rel, ctype):
        try:
            with open("static/" + rel, "rb") as f:
                body = f.read()
        except OSError:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def jpeg_frame(self, w=320, h=180):
        # Tiny valid JPEG (1x1 gray) repeated is fine for smoke purposes.
        return b"\xff\xd8\xff\xd9"

    def serve_jpeg(self):
        body = self.jpeg_frame()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_mjpeg(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=mibeejpeg")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            for _ in range(30):
                frame = self.jpeg_frame()
                self.wfile.write(b"--mibeejpeg\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n")
                self.wfile.flush()
                time.sleep(0.5)
        except Exception:
            pass

    def serve_mse(self):
        # fMP4 stub: a few bytes then hold open; browsers will stall+retry,
        # which exercises the player's reconnect path in smoke tests.
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            for _ in range(20):
                chunk = bytes([0]) * 8
                self.wfile.write(("%x\r\n" % len(chunk)).encode() + chunk + b"\r\n")
                self.wfile.flush()
                time.sleep(0.5)
            self.wfile.write(b"0\r\n\r\n")
        except Exception:
            pass

    def serve_sse(self):
        import queue
        q = queue.Queue(maxsize=64)
        STATE["sse_queues"].append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(msg.encode())
                except queue.Empty:
                    self.wfile.write(": keepalive\n\n")
                self.wfile.flush()
        except Exception:
            pass
        finally:
            STATE["sse_queues"].remove(q)

    @staticmethod
    def cookie_headers(token, csrf):
        return {"Set-Cookie": [
            f"session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=86400",
            f"csrf-token={csrf}; Path=/; SameSite=Strict",
        ]}


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    threading.Thread(target=ai_thread, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock SPEC server on http://127.0.0.1:{port} (first boot: setup flow — the password is whatever you set)")
    STATE["setup_done"] = False
    if PREAUTH:
        # Headless smoke: act as an already-configured, signed-in device.
        # No default credential: take the password from the env or generate
        # a random one (printed below) for the /api/auth/login path.
        STATE["setup_done"] = True
        STATE["password"] = os.environ.get("MIBEE_WEBUI_PASSWORD") or secrets.token_urlsafe(12)
        print(f"PREAUTH mode: mock login password is {STATE['password']!r}")
    server.serve_forever()


if __name__ == "__main__":
    main()
