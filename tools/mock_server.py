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
import re
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
         "rtsp_url": "rtsp://localhost:8554/stream", "resolution": "1280x720", "fps": 25,
         "config": {"hflip": False, "vflip": False, "rotation": 0}},
    ],
    "config": {
        "web": {"port": 8088, "username": "admin", "password": "****"},
        "camera": {"mode": "mtxrpicam", "width": 1280, "height": 720, "fps": 25,
                   "bitrate": 2500000, "codec": "h264", "rotation": 0},
        "rtsp": {"port": 8554, "username": "", "password": "****"},
        "onvif": {"port": 8080, "username": "admin", "password": "****",
                   "events_enabled": True, "media2_enabled": True,
                   "deviceio_enabled": True, "http_digest": False,
                   "ip_filter": []},
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
        "scene": {"voice": {"follow_up_window_secs": 12.0,
                            "wake_word": "小蜜蜂"},
                  "tools": {"weather_enabled": True, "weather_city": "Guangzhou",
                            "weather_timeout_secs": 5}},
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
    # AI model manager (SPEC §4.9): installed flags + selection + tasks.
    "model_installed": {"llm/mock-llm-4b": True, "vlm/mock-vlm-2b": True,
                        "voice.asr/mock-asr-tri": True, "ai/nanodet-plus-m-320": True},
    "model_active": {"llm": "mock-llm-4b", "vlm": "mock-vlm-2b",
                     "voice.asr": "mock-asr-tri", "ai": "nanodet-plus-m-320"},
    "model_tasks": {},
    "model_task_seq": 0,
    # Online AI (SPEC §4.10). The key is never returned — only api_key_set.
    "cloud": {"provider": "off", "api_key": "", "chat_model": "openai/gpt-4o-mini",
              "vision_model": "", "fallback_local": True, "timeout_secs": 60},
    "zones": [
        {"name": "door", "kind": "intrusion",
         "points": [[120, 90], [420, 90], [420, 300], [120, 300]], "dwell_secs": 5},
    ],
    # Hearing records (SPEC appendix A #24): persistent text records of
    # what the audio engines recognized.
    "hearing_records": [
        {"id": 2, "kind": "voice", "text": "今天天气怎么样", "score": None,
         "keyword": "小蜜蜂", "speaker": "mickey", "timestamp_ms": 1759000002000,
         "scene": "实时检测：1×person（中间）",
         "media_ref": "recordings/mock-cam_20261001220000.mp4"},
        {"id": 1, "kind": "sound", "text": "Dog", "score": 0.62,
         "keyword": "", "speaker": "", "timestamp_ms": 1759000001000, "scene": ""},
    ],
    # Voiceprint speakers (SPEC appendix A #25). The mock auto-collects
    # one sample per second while an enrollment session is in flight.
    "voice_speakers": [
        {"id": 1, "name": "mickey", "dim": 192, "count": 3,
         "created_at": "2026-09-29 06:00:00"},
    ],
    "enrollment": None,
    # Meetings (SPEC appendix A #27): the mock simulates the async
    # pipeline — stop flips the row to processing with a wall-clock
    # marker; the next list/get after 2 s finalizes it with segments.
    "meetings": [
        {"id": 1, "started_at_ms": 1758998400000, "ended_at_ms": 1758999000000,
         "duration_ms": 600000, "status": "done", "num_speakers": 2,
         "num_segments": 2, "audio_path": "", "error": ""},
    ],
    "meeting_segments": {
        1: [
            {"start_ms": 0, "end_ms": 32000, "speaker_index": 0,
             "speaker": "mickey", "text": "这次发布我们分三步走。"},
            {"start_ms": 35000, "end_ms": 61000, "speaker_index": 1,
             "speaker": "", "text": "好的，我负责测试那一块。"},
        ],
    },
    "meeting_seq": 1,
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
    "audio_ai": True,
    "audio_records": True,
    "voice_speakers": True,
    "face": True,
    "decision": True,
    "meeting": True,
    "zones": True,
    "ocr": True,
    "voice": True,
    "chat": True,
    "vlm": True,
    "ptz": True,
    "hls": False,
    "recording": True,
    "watermark": True,
    "devices": True,
    "mjpeg": True,
    "mse": True,
    "substream": True,
    "webrtc": False,
    "events": ["camera_added", "camera_offlined", "param_changed", "ai_detection",
               "ai_model_changed", "recording", "status", "alarm", "alarm_description", "voice_transcript", "chat_reply", "zone_event", "voice_decision", "meeting_state", "model_task", "audio_level"],
    "config_apply": {"default": "restart", "sections": {"imaging": "immediate",
                                                        # demonstrates the immediate badge on a real config section
                                                        "logging": "immediate",
                                                        "watermark": "restart"}},
    "restart": True,
    "model_manager": True,
    "cloud_ai": True,
    "observability": {"metrics": True, "logs": True, "requests": True,
                      "traces": True, "model_metrics": True},
}

# AI model catalog (SPEC §4.9) — mirrors the notebook dialect shape: a
# couple of downloadable alternatives per capability, one no-source
# entry, and the detection capability with immediate (hot) apply.
MODEL_CATALOG = [
    {"id": "llm", "label": "Dialogue LLM", "apply": "restart", "models": [
        {"id": "mock-llm-4b", "name": "MockLLM-4B Q4", "size_bytes": 2497281120,
         "languages": ["zh", "en"], "license": "Apache-2.0", "notes": "", "downloadable": True},
        {"id": "mock-llm-1b", "name": "MockLLM-1.7B Q4", "size_bytes": 1200000000,
         "languages": ["zh", "en"], "license": "Apache-2.0", "notes": "smaller / faster", "downloadable": True},
    ]},
    {"id": "vlm", "label": "Vision-language model", "apply": "restart", "models": [
        {"id": "mock-vlm-2b", "name": "MockVL-2B Q4 + mmproj", "size_bytes": 1552463168,
         "languages": ["zh", "en"], "license": "Apache-2.0", "notes": "", "downloadable": True},
    ]},
    {"id": "voice.asr", "label": "Speech recognition", "apply": "restart", "models": [
        {"id": "mock-asr-tri", "name": "Mock ASR trilingual", "size_bytes": 244803083,
         "languages": ["zh", "yue", "en"], "license": "Apache-2.0", "notes": "", "downloadable": True},
        {"id": "mock-asr-zh", "name": "Mock ASR zh small", "size_bytes": 81904027,
         "languages": ["zh"], "license": "Apache-2.0", "notes": "", "downloadable": True},
    ]},
    {"id": "face.recog", "label": "Face recognition", "apply": "restart", "models": [
        {"id": "sface-2021dec", "name": "SFace 128-d", "size_bytes": 38696353,
         "languages": [], "license": "Apache-2.0", "notes": "", "downloadable": True},
    ]},
    {"id": "ocr", "label": "Text recognition (OCR)", "apply": "restart", "models": [
        {"id": "ppocr-ch-v4det-v5rec", "name": "PP-OCRv4 det + PP-OCRv5 rec", "size_bytes": 21375344,
         "languages": ["zh", "en"], "license": "Apache-2.0", "notes": "no mirror for this exact export", "downloadable": False},
    ]},
    {"id": "ai", "label": "Visual detection", "apply": "immediate", "models": [
        {"id": "nanodet-plus-m-320", "name": "NanoDet-Plus-m 320", "size_bytes": 4834000,
         "languages": [], "license": "Apache-2.0", "notes": "hot switch", "downloadable": True},
        {"id": "nanodet-plus-m-416", "name": "NanoDet-Plus-m 416", "size_bytes": 4834000,
         "languages": [], "license": "Apache-2.0", "notes": "hot switch", "downloadable": True},
    ]},
]

CLOUD_SUGGEST = {
    "chat": ["openai/gpt-4o-mini", "deepseek/deepseek-chat-v3.1", "qwen/qwen3-8b"],
    "vision": ["openai/gpt-4o-mini", "google/gemini-2.5-flash", "qwen/qwen3-vl-8b"],
}


def model_catalog_document():
    caps = []
    for cap in MODEL_CATALOG:
        active = STATE["model_active"].get(cap["id"])
        models = []
        for m in cap["models"]:
            models.append(dict(m,
                               installed=bool(STATE["model_installed"].get(cap["id"] + "/" + m["id"])),
                               active=active == m["id"]))
        caps.append(dict(cap, active=active, models=models))
    tasks = [dict(t) for t in STATE["model_tasks"].values()
             if t["status"] in ("downloading", "verifying")]
    return {"dir": "models", "capabilities": caps, "tasks": tasks}


def _advance_model_task(task_id, total_bytes):
    """Simulated download: progress ticks land on the SSE bus so the
    frontend progress bar exercises its real update path."""
    import threading

    def run():
        task = STATE["model_tasks"].get(task_id)
        if not task:
            return
        for pct in (5, 17, 33, 48, 62, 78, 91):
            time.sleep(0.35)
            task = STATE["model_tasks"].get(task_id)
            if not task or task["status"] == "canceled":
                return
            task["progress"] = pct / 100
            task["status"] = "downloading"
            task["downloaded_bytes"] = int(total_bytes * pct / 100)
            sse_broadcast("model_task", dict(task))
        time.sleep(0.3)
        task = STATE["model_tasks"].get(task_id)
        if not task or task["status"] == "canceled":
            return
        task["status"] = "verifying"
        task["progress"] = 0.97
        sse_broadcast("model_task", dict(task))
        time.sleep(0.3)
        task = STATE["model_tasks"].get(task_id)
        if not task or task["status"] == "canceled":
            return
        task["status"] = "done"
        task["progress"] = 1.0
        task["downloaded_bytes"] = total_bytes
        STATE["model_installed"][task["capability"] + "/" + task["model_id"]] = True
        sse_broadcast("model_task", dict(task))

    threading.Thread(target=run, daemon=True).start()


# A real, decodable 32x18 gray JPEG (no runtime image libs needed). The
# old SOI+EOI stub failed decode on every multipart frame, which drove
# <img> error feedback loops in the browser harnesses (found while
# debugging the cameras flip check in tools/ux_visual_check.py).
_GRAY_JPEG = b"".fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb0043000a07070807060a"
    "0808080b0a0a0b0e18100e0d0d0e1d15161118231f2524221f2221262b372f26"
    "293429212230413134393b3e3e3e252e4449433c48373d3e3bffc0000b080012"
    "002001011100ffc4001f00000105010101010101000000000000000001020304"
    "05060708090a0bffc400b5100002010303020403050504040000017d01020300"
    "041105122131410613516107227114328191a1082342b1c11552d1f024336272"
    "82090a161718191a25262728292a3435363738393a434445464748494a535455"
    "565758595a636465666768696a737475767778797a838485868788898a929394"
    "95969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9"
    "cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda"
    "0008010100003f0028a28a28a28a28a28affd9"
)

AUTH_EXEMPT = {"/api/auth/login", "/api/auth/setup", "/api/auth/logout"}

# ── conversation trace mock state (SPEC §3.3) ─────────────────────────

_MOCK_TRACES = [
    {
        "id": "c_mock01", "origin": "chat", "started_at_ms": 1788320000000,
        "duration_ms": 4180, "turns": 1, "models": ["vlm", "llm"], "status": "ok",
        "open": False,
        "spans": [
            {"span_id": 1, "parent_id": None, "model": "vlm", "variant": "qwen3-vl-2b",
             "label": "看图直答", "start_ms": 12, "duration_ms": 3720, "cpu_ms": 2950,
             "status": "ok", "tokens_prompt": None, "tokens_completion": None,
             "attributes": {"grounded": "vlm"}},
            {"span_id": 2, "parent_id": None, "model": "llm", "variant": "qwen3-0.6b-q8_0",
             "label": "本地应答", "start_ms": 3740, "duration_ms": 420, "cpu_ms": 390,
             "status": "ok", "tokens_prompt": None, "tokens_completion": None,
             "attributes": {"grounded": "none"}},
        ],
    },
    {
        "id": "c_mock02", "origin": "voice", "started_at_ms": 1788319000000,
        "duration_ms": 6350, "turns": 2, "models": ["decision", "cloud.chat", "llm", "tts.zh"],
        "status": "partial", "open": False,
        "spans": [
            {"span_id": 1, "parent_id": None, "model": "decision", "variant": "laya",
             "label": "意图决策", "start_ms": 5, "duration_ms": 140, "cpu_ms": 120,
             "status": "ok", "tokens_prompt": None, "tokens_completion": None,
             "attributes": {"choice": "answer"}},
            {"span_id": 2, "parent_id": None, "model": "cloud.chat", "variant": "qwen/qwen3-8b",
             "label": "云端应答", "start_ms": 160, "duration_ms": 890, "cpu_ms": 12,
             "status": "error", "tokens_prompt": None, "tokens_completion": None,
             "attributes": {}},
            {"span_id": 3, "parent_id": None, "model": "llm", "variant": "qwen3-0.6b-q8_0",
             "label": "本地回落", "start_ms": 1060, "duration_ms": 4210, "cpu_ms": 3880,
             "status": "ok", "tokens_prompt": 512, "tokens_completion": 96,
             "attributes": {}},
            {"span_id": 4, "parent_id": None, "model": "tts.zh", "variant": "vits-melo-tts",
             "label": "语音播报", "start_ms": 5290, "duration_ms": 1050, "cpu_ms": 60,
             "status": "ok", "tokens_prompt": None, "tokens_completion": None,
             "attributes": {}},
        ],
    },
]


def _trace_summaries():
    out = []
    for tr in _MOCK_TRACES:
        out.append({k: v for k, v in tr.items() if k != "spans"})
    return out


def _trace_detail(conv_id):
    for tr in _MOCK_TRACES:
        if tr["id"] == conv_id:
            return tr
    return None


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
              ("GET", "/api/cameras/0/stream.sub.mse", 200),
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
        # Mic level (SPEC §6 audio_level) — burst pattern: 4s speech-like
        # pulses then 2s silence, so the waveform visibly dances and floors.
        if fn % 3 != 0:
            import math as _m
            level = 0.25 + 0.6 * abs(_m.sin(fn * 1.7))
            sse_broadcast("audio_level", {"level": round(level, 3),
                                          "timestamp": int(time.time() * 1000)})


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
                "# HELP mibee_model_inferences_total Total model invocations\n"
                "# TYPE mibee_model_inferences_total counter\n"
                'mibee_model_inferences_total{model="ai",variant="nanodet-plus-m-320"} 428\n'
                'mibee_model_inferences_total{model="llm",variant="qwen3-0.6b-q8_0"} 12\n'
                'mibee_model_inferences_total{model="vlm",variant="qwen3-vl-2b"} 2\n'
                "# HELP mibee_model_inference_seconds Wall duration of one invocation\n"
                "# TYPE mibee_model_inference_seconds histogram\n"
                'mibee_model_inference_seconds_sum{model="ai",variant="nanodet-plus-m-320"} 96.3\n'
                'mibee_model_inference_seconds_sum{model="llm",variant="qwen3-0.6b-q8_0"} 41.7\n'
                'mibee_model_inference_seconds_sum{model="vlm",variant="qwen3-vl-2b"} 74.2\n'
                "# HELP mibee_model_cpu_seconds CPU delta during one invocation\n"
                "# TYPE mibee_model_cpu_seconds histogram\n"
                'mibee_model_cpu_seconds_sum{model="ai",variant="nanodet-plus-m-320"} 62.1\n'
                'mibee_model_cpu_seconds_sum{model="llm",variant="qwen3-0.6b-q8_0"} 38.4\n'
                'mibee_model_cpu_seconds_sum{model="vlm",variant="qwen3-vl-2b"} 70.9\n'
                'mibee_model_errors_total{model="vlm",variant="qwen3-vl-2b"} 1\n'
                'mibee_model_tokens_total{kind="prompt",model="llm",variant="qwen3-0.6b-q8_0"} 6144\n'
                'mibee_model_tokens_total{kind="completion",model="llm",variant="qwen3-0.6b-q8_0"} 812\n'
                'mibee_model_inflight{model="ai"} 1\n'
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
        if path == "/api/cloud":
            body = self.body_json()
            c = STATE["cloud"]
            if "provider" in body:
                if body["provider"] not in ("off", "openrouter"):
                    return self.err("bad_request", "provider must be off|openrouter", 400)
                c["provider"] = body["provider"]
            if "api_key" in body:
                key = str(body["api_key"] or "")
                if len(key) > 256:
                    return self.err("bad_request", "api_key too long", 400)
                c["api_key"] = key
            for field in ("chat_model", "vision_model"):
                if field in body:
                    v = str(body[field] or "")
                    if len(v) > 128:
                        return self.err("bad_request", field + " too long", 400)
                    c[field] = v
            if "fallback_local" in body:
                c["fallback_local"] = bool(body["fallback_local"])
            if "timeout_secs" in body:
                v = body["timeout_secs"]
                if not isinstance(v, int) or not (5 <= v <= 300):
                    return self.err("bad_request", "timeout_secs must be an int in 5..=300", 400)
                c["timeout_secs"] = v
            return self.ok({
                "provider": c["provider"], "api_key_set": bool(c["api_key"]),
                "chat_model": c["chat_model"], "vision_model": c["vision_model"],
                "fallback_local": c["fallback_local"], "timeout_secs": c["timeout_secs"],
                "suggest": CLOUD_SUGGEST, "applied": "immediate",
            })
        if path == "/api/faces":
            body = self.body_json()
            st = STATE["faces"]
            st["enrollment"] = {"name": body.get("name", ""), "collected": 0, "needed": 8}
            return self.ok({"enrolling": body.get("name", "")})
        if path == "/api/faces/commit":
            st = STATE["faces"]
            if st.get("enrollment"):
                name = st["enrollment"]["name"]
                st["faces"].append({"id": len(st["faces"]) + 1, "name": name, "dim": 128,
                                    "created_at": "2026-10-02T10:00:00Z"})
                st["enrollment"] = None
                return self.ok({"enrolled": name, "dim": 128})
            return self.err("bad_request", "no enrollment in flight", 400)
        if path == "/api/faces/cancel":
            STATE["faces"]["enrollment"] = None
            return self.ok({"cancelled": True})
        if path == "/api/config":
            return self.put_config()
        if path.startswith("/api/cameras/") and path.endswith("/recording"):
            return self.ok({"active": bool(self.body_json().get("active"))})
        parts = path.split("/")
        if path.startswith("/api/cameras/") and len(parts) == 5 and parts[4] == "zones":
            # Device-level storage: camera existence not required. The
            # product contract is a bare JSON array; accept the wrapped
            # form too so older clients stay working.
            body = self.body_json()
            zones = body if isinstance(body, list) else body.get("zones", [])
            for z in zones:
                pts = z.get("points") or []
                need = 2 if z.get("kind") == "line_cross" else 3
                if len(pts) < need:
                    return self.err("invalid", f"zone {z.get('name')!r}: needs >= {need} points", 400)
            STATE["zones"] = zones
            return self.ok({"zones": zones, "applied": "immediate"})
        if path.startswith("/api/cameras/"):
            cid = parts[3]
            for cam in STATE["cameras"]:
                if cam["id"] == cid:
                    cam.update(self.body_json())
                    return self.ok(cam)
            return self.err("not_found", "no such camera", 404)
        self.send_error(404)

    def finalize_mock_meetings(self):
        """Flip processing rows to done 2s after stop (simulated async
        pipeline) with mock segments."""
        for m in STATE["meetings"]:
            if m["status"] == "processing" and time.time() - m.get("_processing_since", 0) >= 2:
                m["status"] = "done"
                m["num_speakers"] = 2
                m["num_segments"] = 2
                STATE["meeting_segments"][m["id"]] = [
                    {"start_ms": 0, "end_ms": m["duration_ms"] // 2, "speaker_index": 0,
                     "speaker": "mickey", "text": "[mock] 会议的第一段发言。"},
                    {"start_ms": m["duration_ms"] // 2 + 1000, "end_ms": m["duration_ms"],
                     "speaker_index": 1, "speaker": "", "text": "[mock] 第二位说话人的回复。"},
                ]

    def do_DELETE(self):
        path = self.path.split("?")[0]
        if not self.authed():
            return self.err("unauthorized", "not signed in", 401)
        m = re.fullmatch(r"/api/models/([a-z.]+)/([a-z0-9-]+)", path)
        if m:
            cap_id, model_id = m.group(1), m.group(2)
            key = cap_id + "/" + model_id
            if not STATE["model_installed"].get(key):
                return self.err("not_found", "model not installed", 404)
            if STATE["model_active"].get(cap_id) == model_id:
                return self.err("conflict", "cannot delete the active model", 409)
            STATE["model_installed"][key] = False
            self.send_response(204)
            self.end_headers()
            return
        # Hearing records (SPEC appendix A #24): clear all.
        if path == "/api/audio/records":
            removed = len(STATE["hearing_records"])
            STATE["hearing_records"] = []
            return self.ok({"applied": "immediate", "removed": removed})
        if path.startswith("/api/faces/"):
            name = path.rsplit("/", 1)[1]
            st = STATE["faces"]
            before = len(st["faces"])
            st["faces"] = [f for f in st["faces"] if f["name"] != name]
            if len(st["faces"]) == before:
                return self.err("not_found", "no such face", 404)
            return self.ok({"deleted": name})
        if path.startswith("/api/meetings/"):
            try:
                mid = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self.err("not_found", "bad meeting id", 404)
            before = len(STATE["meetings"])
            STATE["meetings"] = [m for m in STATE["meetings"] if m["id"] != mid]
            STATE["meeting_segments"].pop(mid, None)
            if len(STATE["meetings"]) == before:
                return self.err("not_found", "meeting not found", 404)
            return self.ok({"applied": "immediate", "deleted": mid})
        if path.startswith("/api/voice/speakers/"):
            name = path[len("/api/voice/speakers/"):]
            before = len(STATE["voice_speakers"])
            STATE["voice_speakers"] = [sp for sp in STATE["voice_speakers"] if sp["name"] != name]
            if len(STATE["voice_speakers"]) == before:
                return self.err("not_found", f"speaker {name!r} not found", 404)
            return self.ok({"applied": "immediate", "removed": name})
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
        if path == "/api/models":
            return self.ok(model_catalog_document())
        if path == "/api/models/tasks":
            return self.ok({"tasks": [dict(t) for t in STATE["model_tasks"].values()]})
        if path == "/api/cloud":
            c = STATE["cloud"]
            return self.ok({
                "provider": c["provider"], "api_key_set": bool(c["api_key"]),
                "chat_model": c["chat_model"], "vision_model": c["vision_model"],
                "fallback_local": c["fallback_local"], "timeout_secs": c["timeout_secs"],
                "suggest": CLOUD_SUGGEST,
            })
        if path == "/api/audio/records":
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            kind = (q.get("kind") or [None])[0]
            try:
                limit = min(max(int((q.get("limit") or ["100"])[0]), 1), 500)
            except ValueError:
                limit = 100
            rows = STATE["hearing_records"]
            if kind in ("sound", "voice"):
                rows = [r for r in rows if r["kind"] == kind]
            rows = sorted(rows, key=lambda r: r["timestamp_ms"], reverse=True)
            return self.ok({"records": rows[:limit], "applied": "immediate"})
        if path == "/api/meetings":
            self.finalize_mock_meetings()
            return self.ok({"meetings": sorted(STATE["meetings"],
                                               key=lambda m: m["started_at_ms"], reverse=True)})
        if path.startswith("/api/meetings/") and path.count("/") == 3:
            try:
                mid = int(path.rsplit("/", 1)[1])
            except ValueError:
                return self.err("not_found", "bad meeting id", 404)
            self.finalize_mock_meetings()
            row = next((m for m in STATE["meetings"] if m["id"] == mid), None)
            if row is None:
                return self.err("not_found", "meeting not found", 404)
            return self.ok({"meeting": row,
                            "segments": STATE["meeting_segments"].get(mid, [])})
        if path == "/api/voice/speakers":
            if STATE["enrollment"] is not None:
                STATE["enrollment"]["collected"] = min(
                    STATE["enrollment"]["collected"] + 1, STATE["enrollment"]["needed"])
            return self.ok({
                "speakers": STATE["voice_speakers"],
                "enrollment": STATE["enrollment"],
                "capable": True,
            })
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
            if len(parts) == 5 and parts[4] == "zones":
                # Zones live in device-level storage; serve them even for
                # camera ids the (stateless) mock no longer lists.
                return self.ok({"zones": STATE["zones"]})
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
            if sub == "stream.sub.mse":
                # Substream stub (SPEC appendix A #20): same fMP4 hold-open
                # shape as the main endpoint.
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
        if path == "/api/traces/conversations":
            return self.ok({"conversations": _trace_summaries()})
        if path.startswith("/api/traces/conversations/"):
            conv_id = path[len("/api/traces/conversations/"):]
            detail = _trace_detail(conv_id)
            if detail is None:
                return self.err("not_found", "unknown conversation trace", 404)
            return self.ok(detail)
        self.send_error(404)

    # ── API: POST ───────────────────────────────────────────────────
    def post_api(self, path):
        # Multipart bodies (model upload) must stay unread for their own
        # parser; JSON bodies are consumed here as before.
        if "multipart/form-data" in (self.headers.get("Content-Type") or ""):
            body = {}
        else:
            body = self.body_json()
        if path == "/api/chat":
            text = str(body.get("text") or "")
            # SPEC appendix A #29: vision=true → grounded "vlm", else the
            # mock always claims scene grounding (it fakes detections).
            vision = bool(body.get("vision"))
            grounded = "vlm" if vision else "scene"
            engine = "cloud" if STATE["cloud"]["provider"] != "off" and STATE["cloud"]["api_key"] else "local"
            return self.ok({"reply": f"[mock:{grounded}] 收到：{text}", "grounded": grounded, "engine": engine})
        if path == "/api/cloud/test":
            c = STATE["cloud"]
            if c["provider"] == "off" or not c["api_key"]:
                return self.err("bad_request", "cloud AI is off or no API key set", 400)
            return self.ok({"ok": True, "latency_ms": 213, "model": c["chat_model"] or "openai/gpt-4o-mini",
                            "reply": "OK"})
        # Model manager (SPEC §4.9).
        m = re.fullmatch(r"/api/models/([a-z.]+)/([a-z0-9-]+)/(download|activate)", path)
        if m:
            cap_id, model_id, action = m.group(1), m.group(2), m.group(3)
            cap = next((c for c in MODEL_CATALOG if c["id"] == cap_id), None)
            spec = next((x for x in (cap or {}).get("models", []) if x["id"] == model_id), None)
            if not cap or not spec:
                return self.err("not_found", "unknown capability or model", 404)
            if action == "download":
                key = cap_id + "/" + model_id
                installed = bool(STATE["model_installed"].get(key))
                if installed and not body.get("force"):
                    return self.err("conflict", "already installed (force=true to re-download)", 409)
                for t in STATE["model_tasks"].values():
                    if t["capability"] == cap_id and t["model_id"] == model_id \
                            and t["status"] in ("downloading", "verifying"):
                        return self.err("conflict", "a task is already running for this model", 409)
                STATE["model_task_seq"] += 1
                tid = f"mt-{STATE['model_task_seq']}"
                task = {"task_id": tid, "capability": cap_id, "model_id": model_id,
                        "model_name": spec["name"], "status": "downloading", "progress": 0.0,
                        "downloaded_bytes": 0, "total_bytes": spec["size_bytes"]}
                STATE["model_tasks"][tid] = task
                _advance_model_task(tid, spec["size_bytes"])
                return self.ok({"task": dict(task)}, status=202)
            # activate
            if not STATE["model_installed"].get(cap_id + "/" + model_id):
                return self.err("conflict", "model not installed", 409)
            STATE["model_active"][cap_id] = model_id
            if cap["apply"] == "immediate":
                sse_broadcast("ai_model_changed", {"camera_id": "0", "model": model_id})
                return self.ok({"applied": "immediate", "active": model_id})
            return self.ok({"applied": "restart", "active": model_id})
        m = re.fullmatch(r"/api/models/tasks/([a-z0-9-]+)/cancel", path)
        if m:
            task = STATE["model_tasks"].get(m.group(1))
            if not task:
                return self.err("not_found", "no such task", 404)
            if task["status"] not in ("downloading", "verifying"):
                return self.err("conflict", "task already finished", 409)
            task["status"] = "canceled"
            sse_broadcast("model_task", dict(task))
            return self.ok({"status": "canceled"})
        # Voiceprint speakers (SPEC appendix A #25)
        if path == "/api/voice/speakers":
            name = str(body.get("name") or "").strip()
            utterances = int(body.get("utterances") or 3)
            if not name or len(name) > 32:
                return self.err("bad_request", "name must be 1..=32 bytes", 400)
            if not 1 <= utterances <= 10:
                return self.err("bad_request", "utterances must be 1..=10", 400)
            if any(sp["name"] == name for sp in STATE["voice_speakers"]):
                return self.err("bad_request", f"speaker {name!r} already enrolled — delete it first", 400)
            if STATE["enrollment"] is not None:
                return self.err("bad_request", "an enrollment session is already in progress", 400)
            STATE["enrollment"] = {"name": name, "collected": 0, "needed": utterances}
            return self.ok({"started": STATE["enrollment"]})
        if path == "/api/meetings/start":
            if any(m["status"] in ("recording", "processing") for m in STATE["meetings"]):
                return self.err("conflict", "a meeting is already recording", 409)
            STATE["meeting_seq"] += 1
            now = int(time.time() * 1000)
            STATE["meetings"].append({"id": STATE["meeting_seq"], "started_at_ms": now,
                                      "ended_at_ms": None, "duration_ms": None,
                                      "status": "recording", "num_speakers": None,
                                      "num_segments": None, "audio_path": "", "error": ""})
            return self.ok({"id": STATE["meeting_seq"], "started_at_ms": now}, status=201)
        if path.startswith("/api/meetings/") and path.endswith("/stop"):
            try:
                mid = int(path.rsplit("/", 2)[1])
            except ValueError:
                return self.err("not_found", "bad meeting id", 404)
            row = next((m for m in STATE["meetings"] if m["id"] == mid), None)
            if row is None:
                return self.err("not_found", "meeting not found", 404)
            if row["status"] != "recording":
                return self.err("conflict", "not recording", 409)
            now = int(time.time() * 1000)
            row["status"] = "processing"
            row["ended_at_ms"] = now
            row["duration_ms"] = now - row["started_at_ms"]
            row["_processing_since"] = time.time()
            return self.ok({"id": mid, "status": "processing"})
        if path == "/api/voice/speakers/commit":
            enr = STATE["enrollment"]
            if enr is None or enr["collected"] < enr["needed"]:
                return self.err("bad_request", "no completed enrollment session", 400)
            STATE["enrollment"] = None
            STATE["voice_speakers"].append({
                "id": len(STATE["voice_speakers"]) + 1, "name": enr["name"],
                "dim": 192, "count": enr["needed"],
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            return self.ok({"enrolled": enr["name"], "samples": enr["needed"], "dim": 192})
        if path == "/api/voice/speakers/cancel":
            STATE["enrollment"] = None
            return self.ok({"applied": "immediate", "enrollment": None})
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

        # Go dialect (SPEC §5 addition, 2026-09-25): a camera-only update
        # whose rotation stays within the same geometry class (0↔180, 90↔270)
        # or only flips — effective dims unchanged — applies via an in-place
        # camera pipeline restart instead of a process restart.
        body = self.body_json()
        # notebook dialect (#32): CHANGING the wake word is restart-class.
        # The settings page always PUTs the full document, so the mere
        # presence of the key must not hijack every save.
        if isinstance(body, dict):
            ww = (body.get("scene") or {}).get("voice", {}).get("wake_word")
            cur = STATE["config"].get("scene", {}).get("voice", {}).get("wake_word")
            if ww is not None and ww != cur:
                merge(STATE["config"], body)
                self.ok({"applied": "restart"})
                return

        old_rot = int(STATE["config"].get("camera", {}).get("rotation", 0) or 0)
        cam_keys = set((body.get("camera") or {}).keys()) if isinstance(body, dict) else set()
        geometry_preserving = (
            isinstance(body, dict)
            and set(body.keys()) == {"camera"}
            and cam_keys <= {"rotation", "hflip", "vflip"}
        )

        merge(STATE["config"], body)
        if errors:
            return self.err("bad_request",
                            "invalid config: numeric value for string field(s): "
                            + ", ".join(sorted(errors)), 400)
        if geometry_preserving:
            new_rot = int(STATE["config"].get("camera", {}).get("rotation", 0) or 0)
            if (old_rot % 180) == (new_rot % 180):
                return self.ok({"applied": "camera_restart"})
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
        # A REAL decodable JPEG: the old SOI+EOI stub (\xff\xd8\xff\xd9)
        # fails decode on every multipart frame, which drove <img> error
        # feedback loops in the browser harnesses (found while debugging
        # the cameras flip check — see tools/ux_visual_check.py).
        return _GRAY_JPEG

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
