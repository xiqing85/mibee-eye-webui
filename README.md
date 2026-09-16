# mibee-eye-webui

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Spec](https://img.shields.io/badge/SPEC-v1-informational.svg)](SPEC.md)

**The single source of truth for the unified web frontend and web API specification of the MiBee Eye camera family.**

One codebase serves three sibling device projects. Each device embeds the same
ES-module frontend and implements the same `SPEC v1` API contract; the UI
renders panels based on the device's announced `capabilities`.

| Device repo | Embed location | Sync command |
|----------|----------|----------|
| [mibee-eye-rs](https://github.com/xiqing85/mibee-eye-rs) (Rust, Raspberry Pi & beyond) | `static/` (rust-embed) | `make sync-rs` |
| [mibee-eye-go](https://github.com/xiqing85/mibee-eye-go) (Go, Raspberry Pi & beyond) | `internal/web/static/` (go:embed) | `make sync-go` |
| mibee-eye-notebook (Rust, PC-local agent) | `crates/web/static/` (include_dir!) | `make sync-notebook` |

## What's inside

- **`SPEC.md`** — the unified web API spec (v1): response envelope, cookie
  session + CSRF auth, the `/api/cameras` resource model, capabilities
  negotiation, the SSE event channel, and per-device dialect appendices.
  All three backends implement this contract; to change the API, change this
  file first, then the implementations. (Spec prose is in Chinese.)
- **`static/`** — modular vanilla-JS ES-module frontend. Zero build, zero
  dependencies: native browser `import`, no node, no bundler. Features are
  capability-gated: whatever the device advertises is the UI you get
  (notebook's multi-camera grid & device management, the Pi's imaging /
  AI overlay / PTZ — all projections of the same code).
- **`tools/mock_server.py`** — a spec-conformant mock API server, so frontend
  development never needs real hardware.

## Development

```bash
make mock        # http://127.0.0.1:8090 — first visit walks the setup flow (MOCK_PREAUTH=1 takes MIBEE_WEBUI_PASSWORD or generates one)
make sync-rs     # after editing static/, sync into a device repo, then build/test there
```

See [TESTING.md](TESTING.md) for the layered testing methodology (mock → API
smoke → Playwright walkthrough → real-device interop).

## Rules

- Frontend changes **must** land here first, then `make sync-*`; never edit
  the copies inside device repos.
- API changes **must** change `SPEC.md` first (additive within a spec version;
  bump `spec_version` for breaking changes).
- Transient artifacts go to `tmp/` (gitignored).

## The MiBee Eye family

- [mibee-eye-rs](https://github.com/xiqing85/mibee-eye-rs) — Rust camera service (Raspberry Pi and any Linux board with V4L2)
- [mibee-eye-go](https://github.com/xiqing85/mibee-eye-go) — Go camera service (same mission, sibling implementation)
- Protocol libraries powering both: [gb28181-rs](https://github.com/mickeyzzc/gb28181-rs) · [gb28181-go](https://github.com/mickeyzzc/gb28181-go) · [onvif-rs](https://github.com/mickeyzzc/onvif-rs) · [onvif-go](https://github.com/mickeyzzc/onvif-go)

## 中文说明

本仓库是 MiBee Eye 摄像头家族**统一 Web 前端 + Web API 规范**的唯一真源。
三个设备仓各内嵌同一份 ES Modules 前端（零构建零依赖），并实现同一份
`SPEC v1` 契约；UI 按设备 `capabilities` 门控渲染。改前端必须改这里再
`make sync-*` 同步；改 API 行为必须先改 `SPEC.md`。`make mock` 可在
无实机情况下开发前端（:8090）。测试方法论见 [TESTING.md](TESTING.md)
（mock → API 冒烟 → Playwright 走查 → 真机互联四层）。

## License

Licensed under [Apache-2.0](LICENSE).
