# mibee-webui

MiBee 摄像头设备的**统一 Web 前端 + Web API 规范**唯一真源（single source of truth）。

被三个设备仓库嵌入：

| 设备仓库 | 嵌入位置 | 同步命令 |
|----------|----------|----------|
| `mibee-eye-rs` | `static/`（rust-embed） | `make sync-rs` |
| `mibee-eye-go` | `internal/web/static/`（go:embed） | `make sync-go` |
| `notebook-cam` | `crates/web/static/`（include_dir!） | `make sync-notebook` |

## 内容

- **`SPEC.md`** — 统一 Web API 规范（v1）：响应信封、cookie 会话 + CSRF 认证、
  `/api/cameras` 资源模型、capabilities 能力协商、SSE 事件通道、设备方言附录。
  三端后端实现以此为准；改规范 = 改这里，然后同步实现。
- **`static/`** — ES Modules 模块化 vanilla 前端，零构建零依赖。
  浏览器原生 `import`，无 node / bundler。功能按 `capabilities` 能力门控：
  设备通告什么能力，UI 就出现什么面板（notebook 的多路网格/设备管理，
  Pi 的成像/AI 叠加/PTZ，都是同一份代码的不同投影）。
- **`tools/mock_server.py`** — 规范一致的 mock API 服务器，前端开发不依赖实机。

## 开发流程

```bash
make mock        # http://127.0.0.1:8090 — 首启会走 setup 流程（admin/12345678）
make sync-rs     # 改完 static/ 后同步进设备仓库（再走各仓库构建/测试）
```

## 规则

- 改前端**必须**改这里，再 `make sync-*` 同步；不要直接编辑设备仓库里的拷贝。
- 改 API 行为**必须**先改 `SPEC.md`（同版本只做加法；破坏性变更升 `spec_version`）。
- 本仓库 `tmp/` 存放临时产物（已 gitignore）。
