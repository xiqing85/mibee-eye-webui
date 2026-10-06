# MiBee Camera Web API 统一规范 (SPEC v1)

本规范定义 MiBee 摄像头设备 Web 管理 API 的统一契约，由三个设备实现：

| 实现 | 仓库 | 部署 |
|------|------|------|
| `mibee-eye-rs` | 本工作区 | 树莓派，HTTP :8088，单相机（固定 id `"0"`） |
| `mibee-eye-go` | 本工作区 | 树莓派，HTTP :8088，单相机（固定 id `"0"`） |
| `mibee-eye-notebook` (binary `mibee-eye`) | 本工作区 | 笔记本，HTTPS :8443（TLS 强制），多相机 CRUD |

配套参考前端：本仓库 `static/`（ES Modules，零构建），由三个设备仓库嵌入。

## 0. 总则

- **信封**：所有 JSON 端点（除注明者）使用统一信封。
  - 成功：`{"ok":true,"data":<payload>}`
  - 失败：`{"ok":false,"error":"<机器码>","message":"<人类可读>"}` + 语义化 HTTP 状态码
  - 二进制端点（快照 / MJPEG / MSE / metrics / 静态资源）不套信封。
- **错误码表**（`error` 字段取值，与 HTTP 状态一一对应）：

  | error | HTTP | 含义 |
  |-------|------|------|
  | `bad_request` | 400 | 请求体/参数非法 |
  | `unauthorized` | 401 | 未登录 / 会话过期 / CSRF 校验失败 |
  | `forbidden` | 403 | 已登录但无权执行 |
  | `not_found` | 404 | 资源不存在 |
  | `conflict` | 409 | 状态冲突（流已在运行等） |
  | `setup_required` | 503 | 首次启动，需先完成管理员设置 |
  | `rate_limited` | 429 | 限速 / 登录锁定 |
  | `not_implemented` | 501 | 能力通告存在但后端未实现 |
  | `internal_error` | 500 | 内部错误 |

- **版本化**：`/api/capabilities` 的 `spec_version` 标识本规范版本。同一 `spec_version` 内只允许增量变更；破坏性变更必须升版本。
- **认证**：会话 cookie + CSRF 双提交（见 §2）。除 §1 公开端点外，一切 API 需认证。静态资源（前端本身）公开。
- **扩展机制**：Core 端点三端必须实现；Extension 端点仅实现了的设备存在，且**必须**在 `/api/capabilities` 中如实通告。前端一切功能开关以 capabilities 为准，不做设备探测猜测。

## 1. 公开端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 存活探针。`{"ok":true,"data":{"status":"ok","uptime":<秒>}}` |
| GET | `/metrics` | Prometheus 文本格式（指标名各设备自定义，见附录 A5） |
| GET | `/`、`/style.css`、`/js/*` | 嵌入式前端静态资源 |

## 2. 认证（Core）

模型：单管理员（username + password）→ 服务端会话 → `session` cookie；写操作带 CSRF 头。

| 方法 | 路径 | 请求 | 响应 data |
|------|------|------|-----------|
| GET | `/api/auth/me` | — | 已登录：`{"username":"admin","role":"admin"}`；未登录：401；未初始化：503 `setup_required` |
| POST | `/api/auth/setup` | `{"username","password"}`（密码 ≥ 8 字符） | `{"username"}`；建立会话（下发 cookie）；已初始化时 400 |
| POST | `/api/auth/login` | `{"username","password"}`；`username` 为空/省略时按 `"admin"` 处理（服务端宽容规则，保持 API 兼容；参考前端登录表单渲染显式用户名输入，留空即走该默认） | `{"username"}`；建立会话；错误凭证 401；限速/锁定 429 |
| POST | `/api/auth/logout` | — | 204，清除会话 |
| POST | `/api/auth/reset` | `{"old_password","new_password"}` | `{"username"}`；成功后使所有既有会话失效 |

Cookie 契约：
- `session=<token>`；`HttpOnly`；`Path=/`；`SameSite=Strict`；TLS 部署加 `Secure`；有效期 24h。
- `csrf-token=<token>`；**非** HttpOnly（供 JS 读取）；`Path=/`；`SameSite=Strict`；登录/setup 时随会话一起下发。

CSRF 契约：所有 `POST/PUT/DELETE/PATCH` 到 `/api/*`（auth 族除外：login/setup/logout）必须携带 `X-CSRF-Token` 头且与 `csrf-token` cookie 一致，否则 401。

登录失败保护：至少实现 per-IP 限速；推荐 per-username 指数退避锁定。

## 3. 设备信息（Core）

| 方法 | 路径 | 响应 data |
|------|------|-----------|
| GET | `/api/status` | `{"device_name","model","vendor","firmware","uptime":<秒>, ...}` + 设备专属状态字段（如 `recording`、`gb28181`、`cameras_running`） |
| GET | `/api/capabilities` | 见 §3.1 |

### 3.1 capabilities 超集 schema

```json
{
  "spec_version": "1",
  "device": {"name": "...", "model": "...", "vendor": "..."},
  "auth": {"model": "session", "setup": true},
  "multi_camera": false,
  "camera_management": false,
  "camera_control": false,
  "imaging": false,
  "ai": false,
  "ai_models": false,
  "ai_upload": false,
  "model_manager": false,
  "cloud_ai": false,
  "ptz": false,
  "hls": false,
  "recording": false,
  "watermark": false,
  "devices": false,
  "mjpeg": true,
  "mse": true,
  "substream": false,
  "webrtc": false,
  "events": ["param_changed", "ai_detection"],
  "config_apply": {"default": "restart", "sections": {"imaging": "immediate"}},
  "restart": true,
  "observability": {"metrics": true, "logs": true, "requests": true, "traces": false, "model_metrics": false},
  "conversations": false
}
```

字段语义：
- `multi_camera`：相机数 > 1（前端显示相机列表/网格视图）。
- `camera_management`：支持相机 CRUD（§4.2）。
- `camera_control`：支持 start/stop（§4.3）。
- `imaging` / `ai` / `ptz` / `hls` / `recording` / `devices` / `webrtc`：对应 Extension 端点存在。
- `watermark`：设备支持视频水印（§5.2 配置子树）。烧录发生在编码前，对该设备的全部视频输出（直播 / RTSP / ONVIF / GB28181 / 录制文件 / 快照）生效；生效时机由 `config_apply.sections.watermark` 通告。
- `ai_models`：设备带模型注册表并支持运行时热切换（§4.6 的模型清单 / 激活端点）。仅在 `ai:true` 时有意义；缺省视为 `false`，前端隐藏模型切换 UI。
- `ai_upload`：设备允许运行时上传/删除模型文件（§4.6 的 POST/DELETE）。仅在 `ai_models:true` 时有意义；缺省视为 `false`。设备侧以配置开关（如 `ai.allow_upload`，默认关）控制——模型文件是对推理引擎的不可信输入，生产环境应仅在需要时开启。
- `mjpeg` / `mse`：对应流端点存在（前端回落链 MSE → MJPEG → 快照轮询）。
- `substream`：设备提供低分辨率省流子码流的 MSE 端点（§4.1 `stream.sub.mse`，v1 同版本加法 2026-09-26）。仅在 `mse:true` 时有意义；缺省视为 `false`，前端不渲染清晰度切换。子码流与主码流并存——主码流（录像 / RTSP / ONVIF 主 Profile / GB28181）不受影响。
- `events`：SSE 实际会推送的事件词汇表（§6）。
- `config_apply`：`"restart"`（写后需进程重启生效）/ `"immediate"`（立即生效），按配置节细化；节未列出时用 `default`。前端应在每个配置节标题处标注其生效时机，并在改动了 `restart` 节后向用户提供重启入口（§5.1）。可选布尔 `auto`（缺省 `false`）：为 `true` 时（Go 方言）改动 `restart` 节的**保存会使设备自动立即自重启**（保存响应即带 `applied:"restart"`），前端应进入统一重启等待流程（提示→轮询 `/api/health`→恢复后自动重载），而不是展示手动重启入口。同版本加法（2026-09-25）：Go 方言对**几何不变**的相机节变更（flips、0↔180、90↔270）不再整进程自重启，保存响应为 `applied:"camera_restart"`（就地重建采集/编码管线，见 §5 PUT 行注记）——前端对 `camera_restart` **不得**进入重启等待流程，直播页应改走流重建周期（stop→start）。
- `restart`：设备支持 `POST /api/system/restart`（§5.1）。
- `observability`：可观测能力（§3.2）；缺省（字段或其内键不存在）视为 `false`，前端隐藏资源监控图与日志/请求视图。`traces`：对话级模型调用链追踪端点存在（§3.3）；`model_metrics`：`/metrics` 暴露每模型资源指标族（附录 A5 方言）。二者均为 v1 同版本加法（2026-10-04）。
- `resource`：启动期功能资源门控快照（notebook 方言，附录 A #40）。对象含内存预算与逐功能准入表；键不存在视为无此能力（旧固件/其余方言），前端隐藏资源档位卡。v1 同版本加法（2026-10-04）。
- `conversations`：对话交互记录端点存在（§3.4）——含 SSE `conversation` 事件的通告门控。缺省（键不存在）视为 `false`，前端隐藏对话记录卡。v1 同版本加法（2026-10-06）。

### 3.2 可观测（Extension：`observability`）

实时监控与可观测性。所有速率值由设备内置 2s 采样器计算（与调用方请求节奏无关，多次轮询语义稳定）；无历史存储——历史由前端滚动窗口自行保留。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/metrics/summary` | 系统+进程资源实时快照（结构见下），会话鉴权 |
| GET | `/metrics` | Prometheus 文本格式（0.0.4），**公开无鉴权**（抓取惯例；go 保留 9100 独立端口为方言） |
| GET | `/api/logs?limit=&level=` | 内存环形缓冲的最近日志（`limit` 缺省 200 上限 1000；`level` 为最低级别过滤 debug/info/warn/error），会话鉴权 |
| GET | `/api/requests?limit=` | 最近的 Web API 请求追踪摘要（`limit` 缺省 100 上限 500），会话鉴权 |

`/api/metrics/summary` 响应 data：

```json
{
  "ts": 1788320000,
  "interval_ms": 2000,
  "system": {
    "cpu_percent": 23.5,
    "load_avg": [0.4, 0.35, 0.3],
    "memory": {"total": 8589934592, "used": 3200000000, "available": 5389934592},
    "disks": [{"path": "/", "total": 61080000000, "used": 12216000000, "free": 48864000000},
               {"path": "/mnt/data", "total": 240000000000, "used": 9600000000, "free": 230400000000}],
    "network": {"rx_bytes": 123456789, "tx_bytes": 9876543,
                 "rx_rate": 12000.0, "tx_rate": 800.0}
  },
  "process": {
    "cpu_percent": 12.0,
    "rss_bytes": 123456789,
    "open_fds": 42,
    "uptime": 3600,
    "io_read_bytes": 1000000, "io_write_bytes": 2000000,
    "storage_bytes": 9600000000,
    "traffic": {"http_rx_bytes": 5000, "http_tx_bytes": 900000,
                 "rtsp_tx_bytes": 100000000, "gb28181_tx_bytes": 40000000}
  }
}
```

字段语义：
- `system.cpu_percent`：自上一采样周期以来的整机 CPU 占用（0–100，含其它进程）。
- `system.disks`：设备相关挂载点（根分区 + 录像数据分区，如已挂载）。
- `system.network`：聚合网卡计数与速率（字节/秒）。
- `process.cpu_percent` / `rss_bytes` / `open_fds`：本服务进程的 CPU、常驻内存、打开的文件描述符数。
- `process.io_read_bytes` / `io_write_bytes`：进程累计 I/O 字节（Linux `/proc/<pid>/io` 的 rchar/wchar，含文件与套接字）。
- `process.storage_bytes`：本服务的磁盘占用 = 录像数据目录实际大小（按录像索引累计；未启用录像时为 0）。
- `process.traffic`：**应用归因**流量计数（非内核精确值）：HTTP 请求收发字节（中间件统计）、RTSP/RTP 出流字节、GB28181 出流字节。Linux 不提供按进程的内核网络计数，此字段为设备自行埋点的累计值，速率由前端按两次轮询差值计算。

`/api/requests` 响应 data：`{"entries":[{"id":"a1b2c3","method":"GET","path":"/api/status","status":200,"duration_ms":3.2,"ts":1788320000}]}`，按时间倒序。中间件为每个 Web API 请求分配 `request_id`（响应头 `X-Request-Id` 回显），记录方法/路径/状态码/耗时；该 `request_id` 同时出现在 `/api/logs` 的相关条目中，用于设备级调用关联。RTSP/ONVIF/GB28181 独立端口面不在追踪范围（以 `/metrics` 计数器覆盖）。内部调用链的跨进程/跨工具导出（OTLP trace、W3C traceparent 注入）见 §3.3 与附录 A 方言。

### 3.3 模型调用链追踪（Extension：`observability.traces`，v1 同版本加法 2026-10-04）

对话级的**模型间调用链**记录：一次对话（HTTP 一次对话请求 / 语音一次唤醒会话）中每个被调用的模型（LLM / VLM / 云端模型 / 决策分类器 / TTS / 说话人声纹……）产生一个 span，含调用顺序（时间偏移）、嵌套关系（parent）、资源消耗（时长、进程 CPU 增量、token 数）。用于回答「这次回答经过了哪些模型、按什么顺序、各花了多少资源」。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/traces/conversations?limit=` | 最近对话追踪摘要列表（`limit` 缺省 50 上限 200），按开始时间倒序，会话鉴权 |
| GET | `/api/traces/conversations/{id}` | 单条追踪全量 span 列表；未知 `id` → 404，会话鉴权 |

列表项：`{"id":"c_ab12…","origin":"chat"|"voice","started_at_ms":<epoch-ms>,"duration_ms":<总时长>,"turns":<轮数>,"models":["vlm","llm"],"status":"ok"|"partial"|"error"}`。

详情响应 data：

```json
{
  "id": "c_ab12…", "origin": "chat", "started_at_ms": 1788320000000,
  "duration_ms": 4200, "open": false,
  "spans": [
    {"span_id": 1, "parent_id": null, "model": "vlm", "variant": "qwen3-vl-2b",
     "label": "看图直答", "start_ms": 12, "duration_ms": 3800, "cpu_ms": 2900,
     "status": "ok", "tokens_prompt": null, "tokens_completion": null,
     "attributes": {"grounded": "vlm"}}
  ]
}
```

语义：
- **conversation 定义**：`origin:"chat"` = 一次 HTTP 对话请求（`POST /api/chat` 族）；`origin:"voice"` = 一次语音唤醒会话——同一对话窗口（历史仍新鲜的 120 s 槽）内的连续轮次归同一 `id`，含跟问窗口内的轮次。
- **span 时间**：`start_ms` 为相对对话开始的毫秒偏移；spans 按 `start_ms` 升序返回，`span_id` 为该对话内的递增序号。`parent_id` 表达嵌套（当前恒为顶层顺序链，字段为嵌套预留）。
- **资源消耗**：`duration_ms` 恒有；`cpu_ms` 为该次调用期间**进程级 CPU 时间增量**（多线程推理的近似归因，设备尽力而为，可为 null）；`tokens_prompt`/`tokens_completion` 仅 token 计费模型（本地/云端 LLM）有，否则 null。`attributes` 为自由键值（如决策 `choice`、接地路径 `grounded`、TTS 语言）。
- **状态**：span `status` ∈ `ok`|`error`；对话级 `status`：全部 ok=`ok`，含失败模型但整体有回复=`partial`，无任何成功模型=`error`。
- **存储**：设备内存有界环形缓冲（无需持久化承诺），容量与逐对话 span 上限由设备方言定义；溢出丢最旧。
- **外部采集**：同一调用链以 OTLP trace 导出（`observability` 配置的 OTLP endpoint 开启时，每 span 携带 `model`/`conversation.id` 属性，对话为根 span）；每模型聚合指标经 `/metrics`（`model_metrics` 能力）。本 API 面向设备自带 UI 的零依赖可视化。

### 3.4 对话交互记录（Extension：`conversations`，v1 同版本加法 2026-10-06）

人读的**对话逐轮记录**：用户说了什么（HTTP 提问文本或语音转写原文）、设备内部"想了什么"（每次内部模型调用/路由决策的摘要条目，即"思考过程"）、AI 最终答了什么、实际用了哪个引擎。与 §3.3 互补：§3.3（指标向）回答「这轮对话经过哪些模型、按什么顺序、各耗多少资源」；本节（内容向）回答「听到了什么、内部判断了什么、回答了什么」。语音交互发生在浏览器之外（对着设备说话），此端点是这些对话在 Web 界面可见的唯一载体。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/conversations?limit=` | 最近对话轮记录（`limit` 缺省 50 上限 200），按时间倒序，会话鉴权 |

响应 data：

```json
{
  "conversations": [
    {
      "id": 42,
      "conversation_id": "c18f0a2c0001",
      "origin": "voice",
      "started_ms": 1788320000000,
      "user_text": "今天天气怎么样",
      "thinking": [
        {"source": "decision", "model": "laya",
         "note": "意图=answer（置信度 0.93）", "duration_ms": 41},
        {"source": "cloud.chat", "model": "openai/gpt-4o-mini",
         "note": "云端失败：timeout — 回落本地", "duration_ms": 5012},
        {"source": "llm", "model": "qwen3-1.7b",
         "note": "本地应答 · prompt 176 / completion 17 tok", "duration_ms": 2311}
      ],
      "reply_text": "今天广州晴，最高 31 度。",
      "engine": "local"
    }
  ]
}
```

语义：
- **origin**：`"voice"` = 一次语音交互轮（含跟问窗口轮次；`conversation_id` 与 §3.3 的 voice 会话同源——120 s 槽内连续轮次同 id）；`"http"` = 一次 `POST /api/chat` 请求（`conversation_id` = 该请求的 §3.3 chat trace id）。
- **user_text**：用户输入原文（语音轮 = ASR 转写文本）。**无回复轮也记录**：决策判为 `ignore`（噪声/误唤醒）的语音轮 `reply_text`/`engine` 为 `null`，`thinking` 里保留决策结论条目——诚实呈现"这轮为什么没有回答"。
- **thinking**：内部调用人读摘要（**非逐字 prompt**），每条对应一次内部模型调用或路由决策。`source` 与 §3.3 span 的 `model` 同名空间（`decision`/`cloud.chat`/`cloud.vision`/`vlm`/`llm`/`tts.*` 等）；`note` 为设备生成的人读摘要（token 数、置信度、失败原因与回落路径等），截断于 200 字符；`duration_ms` 恒有。引擎回落的失败腿保留为独立条目（如上例 cloud 失败 + llm 兜底）——资源消耗的转移在记录中同样可见。
- **reply_text / engine**：AI 最终回复文本与实际引擎（`"cloud"`|`"local"`|`"vlm"`）；无回复轮两者为 `null`。
- **存储**：设备持久存储（SQLite，FIFO 封顶由方言定义；附录 A #41）；写入失败只记日志，绝不影响对话管线（fail-open）。
- **隐私**：记录含转写与回复原文，仅存设备本机、不外发；方言提供总开关（关闭后能力通告为 false、不记录）。
- **实时**：一轮结束（voice 或 http）经 SSE `conversation` 事件（§6）推送该轮完整对象；通告门控 = `capabilities.conversations`。

## 4. 相机资源（Core）

相机是资源，统一挂在 `/api/cameras` 下。单相机设备恒有一个固定 id `"0"` 的相机。

Camera 文档：

```json
{
  "id": "0",
  "name": "Front Door",
  "status": "online",
  "camera_type": "csi",
  "rtsp_url": "rtsp://host:8554/stream",
  "resolution": "1280x720",
  "fps": 25
}
```

`status`：`online`（采集中）/ `offline`（设备拔出）；带 `camera_control` 的设备（notebook）使用 `running` / `stopped` / `idle` / `offline`。前端"运行中"判定 = `online | running`。`camera_type`：`csi` / `usb` / `rtsp`。

### 4.1 读取与媒体（Core）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cameras` | `data` 为 Camera 数组 |
| GET | `/api/cameras/{id}` | 单个 Camera；404 不存在 |
| GET | `/api/cameras/{id}/snapshot` | JPEG 快照（`image/jpeg`），需认证 |
| GET | `/api/cameras/{id}/live` | MJPEG 流（`multipart/x-mixed-replace; boundary=...`），需认证；能力 `mjpeg` |
| GET | `/api/cameras/{id}/stream.mse` | chunked HTTP fMP4（`video/mp4`），服务端复用、首段为 init segment，需认证；能力 `mse`。客户端用 `fetch` + ReadableStream 追加 MediaSource |
| GET | `/api/cameras/{id}/stream.sub.mse` | 同 `stream.mse` 的 chunked fMP4 契约（init segment 先行、§4.1 无缝重连契约同适用），但携带**低分辨率省流子码流**（如 640×360@15、低码率 H.264）；需认证；能力 `substream`。v1 同版本加法（2026-09-26）。设备未启用子码流时 404 |

MSE 流细则：init segment（`ftyp`+`moov`）只发一次，随后每访问单元一个 `moof`+`mdat`；新订阅者需等待关键帧再开始，init segment 需重发。断连后客户端重连即可（服务端是无状态推流）。

无缝重连契约（加法，2026-09-26）：fMP4 媒体时间戳（`tfdt`，90 kHz）锚定在**相机流级单调时钟**上——每个新（重）连接的起始时间戳严格晚于此前任何连接已发出的时间戳；每次（重）连接都以 init segment 开头。因此客户端可以在传输中断时**不销毁 MediaSource/SourceBuffer**，透明重取该端点并继续追加（重复 init segment 属规范允许的解码器配置刷新）；跨缓冲空洞时自行跳到下一个 buffered range 即可。服务端不得对该端点施加整体写超时（如 `http.Server.WriteTimeout`）——那会周期性掐断长连接，把客户端打回黑屏整重建。

### 4.2 相机 CRUD（Extension：`camera_management`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/cameras` | `{"name","camera_type","config"}` → 201 + Camera |
| PUT | `/api/cameras/{id}` | 部分更新 `{"name"?,"config"?,"status"?}` |
| DELETE | `/api/cameras/{id}` | 删除（须先 stop） |

### 4.3 启停（Extension：`camera_control`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/cameras/{id}/start` | 启动采集；已在运行 409 |
| POST | `/api/cameras/{id}/stop` | 停止采集；幂等 |

### 4.4 录像控制（Extension：`recording`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cameras/{id}/recording` | `{"active":bool,"storage_path"?,"segment_secs"?,"retention_days"?}` |
| POST | `/api/cameras/{id}/recording` | `{"active":bool}`；生效时机由 `config_apply.sections.recording` 决定 |

### 4.5 成像控制（Extension：`imaging`）

参数名沿用 ONVIF PascalCase（`Brightness`、`AWBMode`…）。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cameras/{id}/imaging/params` | `{"<Param>":<value>...}` |
| GET | `/api/cameras/{id}/imaging/options` | 数值参数 `{"min","max","step","default"}`；枚举参数 `{"enums":[...]}` |
| POST | `/api/cameras/{id}/imaging/param` | `{"name","value"}`；立即生效；广播 `param_changed` 事件 |

### 4.6 AI 检测（Extension：`ai`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/detections` | `{"detections":[{"label","confidence","bbox":[x,y,w,h]}],"model","timestamp"}`；未启用时 `{"enabled":false}` |
| GET | `/api/ai/models` | 模型注册表（能力 `ai_models`）：`{"active":"<id>","models":[{"id","family","input":<int>,"source":"builtin"\|"custom"\|"uploaded","available":bool}]}`。`available:false`（模型文件缺失/不可加载）的条目前端应禁选。能力 `ai_upload` 时另附 `"upload":{"allowed":bool,"max_bytes":<int>}` |
| POST | `/api/ai/models/{id}/activate` | 运行时热切换模型：`{"active":"<id>","applied":"immediate"}`。未知 `id` → 404；`available:false` → 409；加载失败**回滚保持旧模型**并 500。成功后写回 `/api/config` 的 `ai.model` 并广播 `ai_model_changed` |
| POST | `/api/ai/models/{id}` | 上传模型（能力 `ai_upload`）：multipart 表单 `family`（`nanodet`\|`yolox`…，须为该设备已实现的解码族）+ `file`（ONNX 二进制）；`id` 须匹配 `^[a-z0-9][a-z0-9-]{0,63}$`。设备**必须先完整加载验证**（会话构建 + 族形状校验）通过后再落盘入册 → `201 {"id","family","input","source":"uploaded"}`；验证失败 400（文件删除不留痕）。id 已存在 → 409；超 `max_bytes` → 413；`family`/id 非法 → 400；能力关闭 → 501 |
| DELETE | `/api/ai/models/{id}` | 移除上传模型（仅 `source:"uploaded"`）：204。内置/自定义条目 → 409；该模型正在运行 → 409；未知 → 404。删除后条目从清单与磁盘移除 |

**bbox 坐标系**：`[x, y, w, h]` 为整数**视频像素**，原点左上角，坐标系为相机原生流分辨率（即 `/api/cameras` 返回的流分辨率，如 1280×720）。不是模型输入分辨率，也不是 0..1 归一化值——设备必须把模型空间坐标映射回视频像素空间后再返回（模型内部将 16:9 帧拉伸进正方形输入时，x/y 轴缩放比不同，映射不可省略）。

**模型标识**：`/api/detections` 响应与 `ai_model_changed` 事件中的 `"model"` 为**模型 id**（即 `/api/ai/models` 条目的 `id`，如 `nanodet-plus-m-320`），不是文件路径。`/api/config` 的 `ai.model` 字段是启动时加载的模型（`PUT /api/config` 直接改它为 restart 语义，重启后生效）；运行时不重启切换必须走 activate 端点。

### 4.7 PTZ（Extension：`ptz`，虚拟或实云台）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/ptz/status` | `{"pan":0.5,"tilt":0.5,"zoom":1.0}`（归一化） |
| POST | `/api/ptz/move` | `{"pan"?,"tilt"?,"zoom"?}` 绝对位置 |

### 4.8 主机设备枚举（Extension：`devices`）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/devices/video` | `[{"index","name","formats":[string]}]` |
| GET | `/api/devices/video/{index}/formats` | `[{"width","height","format","fps"}]` |
| GET | `/api/devices/audio` | `[{"name","supported_configs":[...]}]` |

### 4.9 AI 模型管理（Extension：`model_manager`，v1 同版本加法 2026-10-03）

设备内**全部 AI 能力**的模型目录：每个能力列出设备实际支持运行的多个候选模型，前端提供下载（带进度）、启用切换与删除。目录由设备定义——设备只列自己能加载的模型（跨解码族不兼容的模型不出现），前端不假设目录内容。能力 `model_manager` 通告（缺省 `false`，前端隐藏整个模型管理 UI）。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/models` | `{"dir":"models","capabilities":[…],"tasks":[…]}`。每个 capability：`{"id","label","apply":"restart"\|"immediate","active":"<model-id>"\|null,"models":[…]}`；每个 model：`{"id","name","size_bytes","languages":[…],"license","notes","installed":bool,"active":bool,"downloadable":bool}`。`installed` = 全部文件就位（按目录 size 校验）；`downloadable:false` = 设备未提供下载源（仅可删除已装文件，不可下载/重下） |
| POST | `/api/models/{capability}/{model_id}/download` | 创建异步下载任务 → `202 {"task":<task 对象>}`。未知 capability/model → 404；已安装 → 409（body `{"force":true}` 强制重下）；磁盘余量不足（< 1.1×size_bytes）→ 507 `insufficient_storage`；同模型已有进行中任务 → 409。下载按文件顺序进行，断点续传（HTTP Range，`.part` 临时文件），完成后按 size + （有则）sha256 校验，失败不改 `installed`、不留成品 |
| GET | `/api/models/tasks` | `{"tasks":[…]}`；task 对象：`{"task_id","capability","model_id","model_name","status":"downloading"\|"verifying"\|"done"\|"failed"\|"canceled","progress":0..1,"downloaded_bytes","total_bytes","error"?}` |
| POST | `/api/models/tasks/{task_id}/cancel` | 取消进行中任务（`.part` 文件保留供续传）→ `200 {"status":"canceled"}`；已结束任务 → 409 |
| POST | `/api/models/{capability}/{model_id}/activate` | 启用模型。`apply:"restart"` 能力：持久化选择 → `{"applied":"restart"}`，设备重启后经启动 overlay 落到引擎配置（方言见附录 A #34）；已启用 → 幂等 200 同响应。`apply:"immediate"` 能力（目标检测）：等价 §4.6 activate 热切换 → `{"applied":"immediate","active":"<id>"}` 并广播 `ai_model_changed`。未安装 → 409 |
| DELETE | `/api/models/{capability}/{model_id}` | 删除该模型的已装文件 → 204。正在使用（active）→ 409；未安装 → 404 |

**模型文件路径完全由设备目录决定**，不经前端（前端只传 capability/model id）。下载进度另以 SSE `model_task` 事件推送（§6，节流 ≥0.5s）。

### 4.10 在线 AI（Extension：`cloud_ai`，v1 同版本加法 2026-10-03）

外接云端大模型对话（首个供应商 OpenRouter，OpenAI 兼容 `/v1/chat/completions`）。API 密钥**只写不读**：`GET` 永不回显密钥本体，只返回 `api_key_set`；存储位置不进入 `/api/config` 与 `/api/settings` 的可见面（方言见附录 A #35）。

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/cloud` | `{"provider":"off"\|"openrouter","api_key_set":bool,"chat_model","vision_model","fallback_local":bool,"timeout_secs","suggest":{"chat":[…],"vision":[…]}}`（`suggest` 为设备内置的常用模型 id 建议，前端可自由输入其它值） |
| PUT | `/api/cloud` | 部分合并写。`api_key` 字段 write-only：非空 = 设置，`""` = 清除，缺省 = 不变。校验：`provider` 枚举、model 字符串 ≤128 字节、`timeout_secs` 整数 5..=300、`fallback_local` bool，非法 → 400 整体拒绝。响应 = GET 形状 + `"applied":"immediate"`（对话路由即时切换） |
| POST | `/api/cloud/test` | 用当前存储配置发一次最小补全做连通性测试 → `{"ok":true,"latency_ms","model","reply"}`；无效密钥 / 网络失败 / 超时 → 错误信封如实报错（如 401 invalid_api_key） |

**路由语义**：`provider != "off"` 且密钥已设时，对话优先走云端——文本问答用 `chat_model`，`vision:true` 的问题用 `vision_model`（画面 JPEG 以 data URL 内嵌发送）；HTTP `POST /api/chat` 与设备端语音自动应答同权路由。云端请求失败（网络/鉴权/超时）且 `fallback_local:true`（缺省）时回落本地模型应答，不失败整个对话；`POST /api/chat` 响应增加同版本加法字段 `"engine":"cloud"\|"local"\|"vlm"`。**隐私边界（前端须在启用处明示）**：启用后对话文本（vision 问题含画面帧）会发送到所选云端服务。

## 5. 配置（Core）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/config` | 完整配置文档（各设备 schema 不同），机密字段（`password` 等）脱敏为 `"****"` |
| PUT | `/api/config` | **部分合并**写：只提交要改的子树，深合并到现配置；`"****"` 值原样写回时服务端还原为存储值。响应 `{"applied":"restart"|"immediate"|"camera_restart"}`（`camera_restart` 为 v1 同版本加法，Go 方言：**几何不变**的相机节变更——flips、0↔180、90↔270——保存后设备就地重建采集/编码管线，进程与其余服务面（GB28181 注册、ONVIF、会话）存活，流约 3–6 秒恢复且 SPS 不变；跨几何类或 fps/分辨率/码率/codec/mode 变更仍 `restart`） |

前端职责：读取 → 递归渲染编辑器 → 收集变更子树 → 深合并 → PUT。生效语义从 `capabilities.config_apply` 读取并向用户展示：每个配置节标题处标注「需重启生效」/「立即生效」；改动了 `restart` 节并保存后，若设备通告 `restart` 能力，展示"立即重启"入口。机头字段名统一 `web.username` / `web.password` / `rtsp.*` / `onvif.*` / `gb28181.*`；`watermark.*` 水印子树字段三端同名（定义见 §5.2）（各设备多出的节自由扩展）。

### 5.2 视频水印（Extension：`watermark`）

在编码前把水印（自定义文案 + 实时时间）烧录进设备的**全部视频输出**（直播 / RTSP / ONVIF / GB28181 / 录制文件 / 快照，安防 OSD 惯例）。配置子树：

| 字段 | 类型 | 缺省 | 说明 |
|------|------|------|------|
| `watermark.enabled` | bool | `false` | 启用水印 |
| `watermark.text` | string ≤128 | `""` | 自定义文案；可含非 ASCII 字符，能否渲染取决于字体（见 `font_path`） |
| `watermark.show_timestamp` | bool | `true` | 烧录实时时间（设备本地时区） |
| `watermark.timestamp_format` | string | `"%Y-%m-%d %H:%M:%S"` | strftime 子集白名单：`%Y` `%m` `%d` `%H` `%M` `%S` `%F` `%T` `%%` + 字面量字符 |
| `watermark.position` | enum | `"top-left"` | `top-left` / `top-right` / `bottom-left` / `bottom-right` |
| `watermark.font_size` | int | `24` | 像素高度，12..96 |
| `watermark.font_path` | string | `""` | 可选 TTF/OTF 路径（如 CJK 字体，启用中文文案）；空 = 设备内嵌 ASCII 字体（非 ASCII 字符渲染为缺字/空白） |

语义：
- 文案与时间戳同行渲染（`text` + 两个空格 + 时间戳；二者可独立关闭）。样式 v1 固定：白字 + 1px 黑描边（OSD 标准），边距为设备常量不可配。
- 校验：`enabled:true` 时须 `text` 非空或 `show_timestamp:true`；`position` / `font_size` / `timestamp_format` 非法 → 400。
- `font_path` 指向的字体加载失败：回落内嵌字体并记录告警，服务不失败（fail-open）。
- 生效时机由 `config_apply.sections.watermark` 通告；设备方言见附录 A14。

### 5.1 服务重启（Extension：`restart`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/system/restart` | 重启设备服务进程（应用所有 `restart` 语义的已保存配置）。响应 `200 {"status":"restarting"}` 后尽快返回，随后进程退出并拉起（正常数秒内）。幂等：重启中重复调用无副作用 |

前端流程：确认对话框 → POST → 轮询 `GET /api/health`（公开）至恢复 → 刷新页面。

## 6. 事件通道（Core：`GET /api/events`，SSE）

- `Content-Type: text/event-stream`；15s keep-alive 注释行；需认证（cookie 由 EventSource 自动携带）。
- 事件格式：`event: <type>\ndata: <json>\n\n`。
- 客户端不应假设事件集合固定 —— 以 `capabilities.events` 通告为准，未知事件类型忽略。

词汇表：

| type | payload | 产生方 |
|------|---------|--------|
| `camera_added` | `{"camera_id","name","device_index"?}` | notebook 热插拔 |
| `camera_offlined` | `{"camera_id"}` | notebook 热插拔 |
| `param_changed` | `{"camera_id","name","value"}` | imaging 参数被任意客户端修改 |
| `ai_detection` | `{"camera_id","detections":[{"label","confidence","bbox"}],"frame_number"?}` | AI 推理帧；bbox 坐标系同 §4.6（视频像素空间） |
| `ai_model_changed` | `{"camera_id","model"}` | 模型热切换完成（§4.6 activate 端点）；`model` 为新模型 id |
| `model_task` | `{"task_id","capability","model_id","status","progress",…}` | 模型下载任务状态变化（§4.9，v1 同版本加法）：`status` ∈ downloading/verifying/done/failed/canceled，节流 ≥0.5s；通告门控 `model_manager` |
| `audio_level` | `{"level":0..1,"timestamp"}` | 麦克风实时音量（v1 同版本加法 2026-10-03，方言附录 A #36；同日修订为感知映射）：**level 为感知刻度，不是线性 RMS**——窗口 RMS 按 dBFS 线性映射（−45…−5 dB → 0…1），门限（<−45 dB）以下恒为 0；≤10 Hz；通告门控 = 设备侧音频监听在运行（voice/audio_ai/meeting 任一活跃）——前端语音对话波形条的数据源 |
| `alarm` | `{"camera_id","active","source","targets","timestamp"}` | 告警上升沿（v1 同版本加法）：与 GB28181 告警 NOTIFY 同源同门控（上升沿 + 冷却 + 运行时开关），在边缘被接受时即推送、与平台侧投递成败无关；`active` 恒为 `true`（上升沿事件），`source` 目前恒为 `"ai"`，`targets` 为触发目标数，`timestamp` epoch-ms。通告门控为 AI 启用（2026-09-20 起不再要求 GB28181 启用，见附录 A #18） |
| `conversation` | `{"id","conversation_id","origin","user_text","thinking":[…],"reply_text","engine"}` | 一轮对话交互完成（v1 同版本加法 2026-10-06，§3.4）：单轮完整记录对象，含无回复轮（`reply_text:null`）；通告门控 `capabilities.conversations`（notebook 方言附录 A #41） |
| `recording` | `{"camera_id","active"}` | 录像启停 |
| `status` | `{"uptime",...}` | 周期状态摘要（可选） |

## 7. 附录 A：接受的设备方言（差异显式清单）

1. **传输层**：Pi 走 HTTP :8088（cookie 无 `Secure`）；notebook 默认 TLS :8443（cookie 带 `Secure`），可另配 `web.http_port` 开启一个附加纯 HTTP 监听用于局域网免证书访问——经该端口颁发的会话 cookie 不带 `Secure`（浏览器拒发 `Secure` cookie 到 http://，带上会导致 HTTP 端口无法登录）。前端以 `location.protocol` 自适应。
2. **Go 遗留 `/snapshot`**（:8088，无认证）保留，专供 NVR 拉流，与 `/api/cameras/0/snapshot` 并存。rs 设备自 2026-09-08 起提供同一方言端点（即 ONVIF GetSnapshotUri 通告的 URI）。
3. **凭证存储**：Pi 存于配置文件（会话为内存态，进程重启即全员下线）；notebook 存 SQLite（bcrypt 哈希，会话持久）。认证协议层面无差别。
4. **Go 无 MJPEG**（H.264 管线无原始帧），`capabilities.mjpeg=false`，前端回落快照轮询。
5. **metrics 指标名**各设备自定义（`mibee_eye_*` / `mibee_*`），不做统一。
6. **Go HLS**（`/hls/*`）与其 metrics 独立端口 :9100 保留为 Go 专属方言。
7. **notebook 协议热切换**：`GET /api/protocols/runtime-status` 为 notebook 扩展端点（ONVIF/GB28181/RTMP 运行态），配置本体已并入 `/api/config` 的 `protocols` 节。
8. **配置文件格式**：Go YAML、Pi Rust TOML、notebook SQLite —— 对前端不可见，仅是 `PUT /api/config` 的落地方式。
9. **设备级翻转（hflip/vflip）**：翻转烘焙进编码流，对所有观看端（RTSP/ONVIF/GB28181/录像/快照）持久生效，与浏览器端仅显示用的直播翻转按钮（localStorage）相互独立。当与旋转（#19）组合时，变换次序为**先 rotation、后 hflip/vflip**（翻转作用于旋转后的画面）。配置位置方言：rs 为 `/api/config` 的 `camera.hflip`/`camera.vflip`（bool，重启生效）；Go 为同名字段（经 libcamera transform，重启生效），且 Go 的成像端点（§4.5）收到 `VFlip`/`HFlip` 时同样转发落地为 `camera.vflip`/`camera.hflip` 并重启生效（响应附 `applied:"restart"`，为 §4.5「立即生效」的显式例外——rpicam-vid 无运行时翻转通道；值与现值相同的翻转请求为幂等 no-op：不写盘、不重启，响应不带 `applied` 字段），即 Go 端两类翻转是同一持久概念；notebook 为每相机 `PUT /api/cameras/{id}` 的 `config.hflip`/`config.vflip`（相机流 (重)启时生效，前端相机卡片提供翻转按钮并自动 stop→start）。
10. **配置生效路径**：Go 保存即自动重启服务（`applied:"restart"` 落地为 SIGTERM 自重启；例外：几何不变的相机节变更（flips、0↔180、90↔270，有效分辨率不变 → SPS 不变）为 `applied:"camera_restart"`——就地换源相机管线，GB 注册/ONVIF/会话存活，不拆 GB 媒体会话；会话持久化在配置同目录的 `web-sessions.json`，自重启（保存/翻转/§5.1 显式重启）后浏览器免重登无感恢复，显式登出或密码重置仍清空全部会话）；rs 保存仅落盘，由用户经 `POST /api/system/restart`（§5.1）显式重启应用（会话同样持久化到配置同目录的 `web-sessions.json`，重启后保持登录）；notebook 按节热应用；2026-10-02 起（#32）提供 `POST /api/system/restart` 并通告 `capabilities.restart=true` + `config_apply.auto=true`——仅 `scene.voice.wake_word`（restart 类）的保存触发自动重启，其余 scene 键仍热生效。
11. **可观测（§3.2）实现方言**：Go 保留 9100 独立 Prometheus 端口（历史抓取配置），同时 `/metrics` 挂在 Web 端口；rs 仅 Web 端口 `/metrics`。日志环形缓冲覆盖 log 门面（Go 为 slog 全量、rs 为协议库 log 门面、notebook 为 tracing 全量）；rs 产品代码的历史 `println!` 输出仅进 journald 不进 `/api/logs`。请求追踪覆盖 Web API 面；RTSP/ONVIF/GB28181 独立端口面以 `/metrics` 计数器覆盖。（2026-10-04 更正：notebook 已实现 §3.2 全部三端点并通告 `capabilities.observability`。）
12. **AI 检测（§4.6）方言**：notebook 为多相机设备，除规范端点 `GET /api/detections`（返回最近一次推理的相机结果）外，另提供逐相机扩展端点 `GET /api/cameras/{id}/detections`（响应结构同 §4.6：`{"detections","model","timestamp"}`，bbox 同为该相机原生流分辨率的视频像素坐标）。`ai_detection` SSE 事件（§6）的 `camera_id` 在 notebook 上为真实相机 UUID；Pi 设备恒为 `"0"`。**模型注册表（§4.6）**：notebook 的内置注册表只含 NanoDet 族条目（其解码器未实现 YOLOX，上传 `family` 仅接受 `nanodet`）；`ai_model_changed` 的 `camera_id` 为 `"all"`（设备级切换）；激活选择持久化在设备数据库 `ai.model` 设置（TOML `[ai]` 仅引导默认），重启后自动覆盖。
13. **notebook GB35114 A 级子树**：`protocols.gb28181.gb35114`（`enabled`、`device_cert_file`、`device_key_file`、`platform_cert_file`、`server_id`），随 `PUT /api/config` 深合并热应用（嵌套节同样拒绝未知字段、兼容字符串布尔）。证书缺失/无效时 GB28181 拒绝启动（fail-closed），Web 与其他协议不受影响。
14. **视频水印（§5.2）方言**：rs 为顶层 `watermark` TOML 节（`PUT /api/config` 落盘、`POST /api/system/restart` 生效，`config_apply.sections.watermark = "restart"`）；notebook 为 `protocols.watermark`（SQLite 持久化，设备级全局、作用于全部相机；相机流 (重)启时读取生效——与 `protocols.recording` 同为 read-at-use。水印启用时 MJPEG 相机的快照直通关闭，改为从带水印的 YUV 重编码）；Go 未实现（`watermark` 能力缺省 `false`，前端不渲染水印设置）——libcamera rpicam-apps 已移除 annotate 通道，实时水印需改造采集管线，待单独立项。
15. **notebook 语音对讲接收（GB/T 28181-2022 §9.2）**：`protocols.gb28181.talkback_playback`（bool，缺省 `true`，随 `PUT /api/config` 深合并、协议重启时生效）。开启且本机存在可用音频输出时，audio-only INVITE 应答 200 OK 并在本地扬声器播放（G.711 A/μ 律解码）；关闭或无输出设备时 fail-open 回落 488（与协议库对未注册 sink 的设计一致），绝不应答 200 后静默丢音。
16. **notebook 告警与位置方言（GB/T 28181-2022 §9.5 / §9.7）**：`protocols.gb28181` 新增四键——`alarm_notify_enabled`（bool，缺省 `true`；平台 DeviceConfig AlarmReport 双开关可运行时覆盖）、`alarm_cooldown_secs`（u64，缺省 `30`；AI 检测上升沿告警的冷却）、`position_longitude` / `position_latitude`（string，缺省空 = 不上报 MobilePosition；非空时随订阅周期以静态位置上报）。AI 检测上升沿触发 §6 `alarm` SSE 事件并在启用时发 Alarm NOTIFY（AlarmPriority 4 / AlarmMethod 5 / AlarmType 2，2022 标准表）。均随 `PUT /api/config` 深合并，协议重启时生效。
17. **notebook 语音对讲上行（GB/T 28181-2022 §9.2 发送半）**：`protocols.gb28181.talkback_upstream`（bool，缺省 `false`，随 `PUT /api/config` 深合并、协议重启时生效）。开启且本机存在可用音频输入时，向协议库注册 G.711 源（8 kHz 单声道、20 ms 帧，无会话期间有界缓存约 1 s 后丢帧）；平台发起的 recvonly 对讲 INVITE 应答 200 并以 20 ms 节拍向 offer 的 c=/m= 地址发送 RTP。关闭或无输入设备时 fail-open——不注册源，库对 recvonly offer 应答 488，绝不静默应答后无声。**上行编码律随会话协商**（2026-09-17 起）：缺省 PCMA（GB 平台事实标准）；平台以 PCMU 发起 offer 时，编码与 RTP payload type 一并切到 μ-law（库协商律共享槽，产品编码器逐块轮询）。

18. **ONVIF Pull-Point 事件方言（三端，2026-09-20 起）**：`onvif.events_enabled`（bool，缺省 `true`；rs 为 TOML `[onvif].events_enabled`、go 为 YAML `onvif.events_enabled`、notebook 为 `protocols.onvif.events_enabled` SQLite 键——旧库缺行按 `true` 解析）启用 ONVIF 事件服务：AI 检测上升沿在既有 GB 告警 NOTIFY 与 §6 `alarm` SSE 之外，再发布 `tns1:VideoSource/MotionAlarm`（`Source`=相机 id——Pi 设备恒 `"0"`、notebook 为相机 UUID；`State` 恒 `true`（上升沿）；`Targets`=目标数）给持有 Pull-Point 订阅的 NVR（无人订阅安全 no-op）。事件端点：设备 service 同址应答 `CreatePullPointSubscription` 等，订阅子树 `/onvif/events_service/sub/<id>`（PullMessages/Renew/Unsubscribe）。**同一变更**：§6 `alarm` SSE 事件的通告门控从「GB28181 启用」改为「AI 启用」——告警桥接与 GB28181 解耦，SSE/ONVIF 告警不再要求 GB 启用（GB NOTIFY 仍自然门控于平台订阅）。
19. **设备级旋转（rotation，2026-09-24 起）**：`rotation`（整数 `0|90|180|270`，顺时针度数；90=顺时针、270=逆时针）与 #9 的翻转同为**烘焙进编码流**的设备级变换——对 RTSP/ONVIF/GB28181/录像/快照统一生效，90/270 时流分辨率宽高互换（ONVIF Profile、`/api/status` 的 `resolution` 等对外宣告同步互换；GB28181/RTSP 分辨率随 SPS 自洽）。历史注记：该键此前未入本规范、仅被前端当作显示用 CSS 旋转消费——2026-09-24 起语义升级为烘焙进流，前端 CSS 旋转消费已同步移除（否则与流内旋转叠加成双重旋转）；直播页的 hflip/vflip 本地显示开关（localStorage）维持独立。配置位置方言：rs 为 `/api/config` 的 `camera.rotation`（重启生效，软件像素转置）；Go 为同名字段（重启生效；rpicamvid 模式 0/180 经 rpicam-vid `--rotation`（libcamera 翻转）烘焙、**90/270 自动切换裸 YUV420 子进程管线**——树莓派 libcamera（vc4/PiSP）不支持 transpose 变换，故子进程出原始 I420 帧、Go 进程内转置后经 V4L2 M2M 硬编（ffmpeg 兜底）——0/180 保持 rpicam-vid H.264 硬编直通零额外 CPU；v4l2 模式为 Go 侧像素转置（全四档）；mtxrpicam/rtsp 模式不支持非 0 值——配置校验拒绝）；notebook 为每相机 `PUT /api/cameras/{id}` 的 `config.rotation`（非法值 400；相机流 (重)启时生效，前端相机卡片提供旋转按钮（0→90→180→270 循环）并自动 stop→start）；**前端在实时页工具栏亦提供设备级旋转按钮**（三方言同形：notebook 对当前相机走卡片同款 stop→start 周期并原地重建直播；Pi 方言 PUT 后按响应分叉——Go 几何不变档（0↔180、90↔270、flips）为 `applied:"camera_restart"` 就地 stop→start 重建直播（服务进程存活），Go 跨几何档（如 0↔90）随自重启流恢复、rs 一律经 §5.1 显式重启后整页恢复——与仅显示用的 hflip/vflip 本地按钮相互独立）。GB28181 平台的 DeviceConfig FrameMirror（A.2.1.22）只覆盖翻转轴、不含旋转，运行时镜像与静态旋转按 #9 次序组合。

20. **低分辨率省流子码流（substream，2026-09-26 起）**：`capabilities.substream=true` 时设备并存两路 H.264——主码流（原分辨率/码率，继续供录像 / RTSP 主挂载 / ONVIF 主 Profile / GB28181）与子码流（约 640×360@15、低码率，供 `stream.sub.mse`、RTSP 子挂载 `/sub`、ONVIF 副 Profile token `sub`）。子码流由主采集帧降采样后经第二编码会话产出（水印/翻转/旋转已在主采集帧上烘焙，子码流自然继承）。子码流不录像、不接 GB28181、AI 与快照仍走主码流/原始帧。配置位置方言：rs 为 TOML `[camera.substream]`（`enabled` 缺省 `false`、`width`/`height`/`fps`/`bitrate`；重启生效）；Go 为 YAML `camera.substream.*` 同键同默认（重启生效；**rpicamvid 模式仅在 YUV 管线（rotation 90/270）与 v4l2 模式下可用**——0/180 的 H.264 直编管线无进程内帧可降采样，模式不支持时 WARN 日志并禁用 fail-open；`enabled=true` 但模式不支持时 `capabilities.substream=false`）；notebook 为每相机 `config.substream`（相机流 (重)启时生效）。前端：直播工具栏提供清晰度切换（主/子），选择持久化 localStorage（缺省主），仅 `substream:true` 时渲染；切换 = 重建 MSE 订阅（stop→start 同款周期）。

21. **notebook 设备端智能扩展（听觉/区域事件/OCR，2026-09-27 起）**：三项新能力，全部 fail-open（模型/运行库缺失 → 对应 capability 缺省，API 如实报 inactive）。① **声音事件（`[audio_ai]` TOML 节，启动态）**：常驻麦克风监听（16 kHz 归一），YAMNet 分类 + 3 窗投票/滞回/逐类冷却，触发时复用 §6 `alarm` 事件——payload 在既有字段外**加法扩展** `class`（AudioSet 显示名，如 `Dog`）与 `score`（投票分），`source` 为 `"audio"`（视觉告警仍为 `"ai"`、payload 不变）；GB Alarm NOTIFY 同既有钉死三元组，description 注明声音类别。配置键：`enabled`（缺省 `false`，隐私 opt-in）、`device`、`classes`（关注的 YAMNet 类名表）、`threshold`（0.3）、`cooldown_secs`（30，逐类）、`model_path`、`vad_model_path`。② **区域事件（zones + `zone_event` SSE）**：`GET/PUT /api/cameras/{id}/zones` 管理用户绘制区域（`[{name, kind:"intrusion"|"line_cross", points:[[x,y]…], dwell_secs}]`，视频像素坐标；intrusion 需 ≥3 点、line 恰 2 点；`applied:"immediate"`）。跟踪层（ByteTrack 子集）挂接在 AI 检测之上（零推理成本），事件经新 SSE `zone_event`（`{camera_id, zone, event:"intrusion"|"loiter"|"line_cross_forward"|"line_cross_backward", track_id, label, timestamp}`）与 GB Alarm NOTIFY 送出；`capabilities.zones` 通告（= AI 启用）。③ **OCR（`[ocr]` TOML 节，启动态）**：PP-OCR（v4 det + v5 rec，中英）,`POST /api/ocr`（body 为 JPEG 原始字节，响应 `{"items":[{text,score,bbox}]}`），`capabilities.ocr` 通告。配套部署自检 CLI（非 SPEC 面）：`--selftest-audio <wav>` / `--selftest-ocr <image>` 离线跑通管线并输出 JSON。

22. **notebook 语音交互（唤醒词 + 离线转写 + 本地 LLM 对话 + TTS，2026-09-27 深夜起）**：四个 opt-in 引擎，全部 fail-open（模型缺失/未编 feature → capability 缺省、API 如实报 inactive）。① **唤醒+转写（`[voice]` TOML 节，需 `voice` feature 构建——sherpa-onnx 静态链随二进制）**：常驻 16 kHz 监听流上跑流式 KWS（zipformer zh-en 3M；`keywords.txt` 追加了 `小蜜蜂`），命中后采集 `capture_secs`（4 s）音频经离线 paraformer-zh int8 转写；完成交互以新 SSE 事件 `voice_transcript`（`{keyword, transcript, timestamp}`）送出。② **LLM 对话（`[llm]` 节，需 `llm` feature——llama-cpp-2 编入）**：Qwen3 GGUF 贪心（temperature-0）单发补全，user 轮自动追加 `/no_think` 关思考；`POST /api/chat`（body `{"text","history":[{role,content}]}` → `{"reply"}`）；voice 转写非空时自动产一条回复，以新 SSE `chat_reply`（`{source:"voice", reply, timestamp}`；HTTP 对话直接返回不走 SSE）。③ **TTS 播报（`[tts]` 节，无需 feature）**：sherpa-onnx-offline-tts CLI **子进程**（GPL espeak-ng 隔离在子进程内）+ vits-melo-tts-zh_en 资产 + `aplay` 播放；LLM 回复到达时播报。④ **能力位**：`voice`/`chat` 布尔 + events 追加 `voice_transcript`/`chat_reply`（各自引擎 active 才通告）。部署自检 CLI：`--selftest-voice <wav>`（KWS+转写全链路 JSON）、`--selftest-llm <prompt>`、`--selftest-tts <text>`。

23. **notebook 告警图片描述（VLM，事件触发式，2026-09-28 起）**：`[vlm]` TOML 节（需 `llm` feature 构建——复用 llama.cpp 载体 + `mtmd` 多模态），Qwen3-VL-2B GGUF（text）+ mmproj GGUF（vision projector）。**事件触发**（非轮询）：§6 `alarm` SSE（`source:"ai"`，即视觉告警）触发时抓取该相机的触发帧 JPEG，异步送 VLM 生成一句「发生了什么」描述（贪心、截断于 `max_tokens`）。告警本身**不等描述**（告警时延优先）；描述完成后以**加法扩展**的新 SSE 事件 `alarm_description`（`{camera_id, alarm_timestamp, description, elapsed_s}`）送出，前端按 `camera_id` + `alarm_timestamp` 关联到最近告警展示。配置键：`enabled`（缺省 `false`）、`model_path`、`mmproj_path`、`max_tokens`（100）、`n_ctx`（2048）、`n_threads`（2）、`repeat_penalty`（1.1，最近 64 token 的重复惩罚，1.0 关闭——贪心解码在困难帧上会循环列举，需要它；2026-10-06 加法）、`prompt`（缺省中文安防描述指令）。**并发语义（2026-10-06）**：告警描述与对话看图直答共享同一推理引擎，全部 VLM 推理经引擎级互斥串行（llama.cpp clip 预处理非线程安全——并发即数据竞争）。`capabilities.vlm` 布尔通告（引擎 active 才 true）；events 追加 `alarm_description` 同门控。加载前内存 guardrail（模型 > 可用内存 2/3 拒载，fail-open）。仅工作站档（llama.cpp 需 AVX2+ CPU）；部署自检 CLI：`--selftest-vlm <jpeg>`（描述全链路 JSON）。

24. **notebook 听觉记录（持久文本记录，2026-09-28 起）**：`[audio_ai]` 声音事件与 `[voice]` 语音转写在既有 SSE 通知之外，落成**可查询的持久文本记录**（SQLite `hearing_records` 表，FIFO 封顶 1000 条、插入时修剪；引擎活跃即记录，无独立开关；写入失败只记日志，绝不影响采集/告警管线）。两类记录同构：`{id, kind:"sound"|"voice", text, score, keyword, timestamp_ms}`——声音记录 `kind:"sound"`、`text`=AudioSet 类名（如 `Dog`）、`score`=投票分、`keyword` 空；语音记录 `kind:"voice"`、`text`=转写文本、`keyword`=命中唤醒词、`score` 空。端点：`GET /api/audio/records?limit=N&kind=sound|voice`（缺省 limit=100、上限 500，按 `timestamp_ms` 倒序）→ `{"records":[…]}`；`DELETE /api/audio/records` → `{"applied":"immediate"}`（清空全部）。会话认证 + CSRF 同 §2。`capabilities.audio_records` 布尔通告（= `audio_ai` 或 `voice` 任一 active）；无新 SSE 事件（复用既有 `alarm`/`voice_transcript`）。

25. **notebook 说话人声纹（注册/验证门控/记录打名，2026-09-29 起）**：`[voice]` 节新增可选声纹能力（需 `voice` feature + 说话人嵌入模型文件 `voice.speaker_embedding_model`，缺省 `models/voice/speaker/campplus.onnx`，3D-Speaker CAM++ zh_en，26MB，Apache-2.0——**文件缺失只禁用声纹特性，KWS+ASR 照常**）。新配置键：`speaker_verify`（缺省 `false`，唤醒门控开关）、`speaker_threshold`（缺省 `0.55`，余弦相似度阈值，建议按麦克风实测标定）、`verify_window_secs`（缺省 `2.0`，取唤醒词前后音频做嵌入的环形缓冲秒数）。**语义**：① 声纹档案存 SQLite `voice_speakers` 表（`{id, name UNIQUE, dim, count, embeddings BLOB(LE f32, count×dim), created_at}`）；② 注册 = 轮询式流程——`POST /api/voice/speakers`（体 `{name, utterances?}`，缺省 3、1..=10；名字 1..=32 字节；已注册名 400）武装引擎，接下来 `utterances` 次唤醒词各采一条嵌入样本，`GET /api/voice/speakers`（**无副作用**）返回 `{speakers:[…], enrollment:{name,collected,needed}|null, capable:bool}` 供轮询进度；`POST /api/voice/speakers/commit`（集满后调用）持久化并入内存 → `{enrolled, samples, dim}`，未集满 400；`POST /api/voice/speakers/cancel` 放弃进行中会话；`DELETE /api/voice/speakers/{name}` 删档案（内存+DB，不存在 404）；③ 门控：`speaker_verify=true` 且有档案时，唤醒词音频嵌入须与某档案匹配过阈值才开 4s 采集窗——陌生人只记日志不打扰；**无档案时 fail-open 放行**（WARN 一次）；④ 打名：每次语音交互对 4s 语句提嵌入、与档案最优匹配，`hearing_records` 新列 `speaker`（TEXT NOT NULL DEFAULT ''，`voice_transcript` SSE 事件同加法加 `speaker` 字段；声音记录恒空串）。`capabilities.voice_speakers` 布尔通告（= `voice` active ∧ 嵌入模型已载）。**诚实边界（产品文档须声明）**：短语音声纹判别弱于整句、相似嗓音家人可能通过——定位为便利性过滤而非安全认证。会话认证 + CSRF 同 §2。

26. **notebook 语音决策辅助（Laya 类型化决策，2026-09-29 起）**：`[decision]` TOML 节引入 Laya 决策引擎（NandhaKishorM/laya，Apache-2.0，多语 mmBERT 322M 经 ONNX Runtime 本地推理——**厂商中立实现**：任何遵循 laya 线协议 `[CLS] "{type} question: …" [SEP] [MASK]选项… [SEP] state [SEP]` 的 ONNX 检查点均可加载）。**用途**：每次语音转写在进入本地 LLM 应答前先做一次类型化意图决策（choice：`answer` 回答 / `device` 控制查询 / `ignore` 噪声误唤醒），`ignore` 直接跳过 LLM 应答（省算力不打扰），`answer`/`device` 照常应答——**决策只分流，不改变既有应答内容**。新 SSE 事件 `voice_decision`：`{camera_id, transcript, choice, confidence, act_probability, timestamp}`（每次决策必发；决策失败/低置信不发——fail-open 维持旧行为）。配置键：`enabled`（缺省 `false`）、`model_path`（缺省 `models/decision/laya_multilingual.int8.onnx`）、`tokenizer_path`、`config_path`（携带 max_len/head_max_len/温度校准的 json）、`min_confidence`（0.35，低于该置信度不采纳决策也不发事件）、`num_threads`（1）。`capabilities.decision` 布尔通告（引擎 active 才 true）。校准语义与上游一致：per-cardinality 温度桶（`{type}:{2|3-5|6-10|11+}`）softmax，置信度 = 校准后 max(p)。

27. **notebook 会议模式（按需录音 + 说话人分离 + 分段转写，2026-09-29 起）**：`[meeting]` TOML 节引入**本地会议纪要**能力（需 `voice` feature；ASR 复用 `[voice]` 的 paraformer 模型路径——**会议依赖语音模型在场**；分离 = pyannote segmentation-3.0（MIT，~7MB）+ CAM++ 嵌入（复用 `voice.speaker_embedding_model`）+ 快速聚类，全部经 sherpa-onnx 本地推理）。**隐私姿态（HARD）**：设备的"待机不录任何音频"承诺不变——录音**仅在显式 `start`→`stop` 会话窗口内**落盘（16kHz i16 WAV，写 `meeting.audio_dir` 缺省 `meetings/` 目录）；`keep_audio` 缺省 `false`：处理完成后音频文件即删除（**处理失败同样按 `keep_audio` 处置**——隐私优先于可重试性，错误详情记录在会议行 `error` 字段），只保留文字纪要；`max_duration_secs`（缺省 7200）到点**自动停止并走同一处理管线**（防遗忘录音）。UI 必须在录音中显著指示。新配置键：`enabled`（缺省 `false`）、`segmentation_model`（缺省 `models/voice/diarization/pyannote.onnx`）、`punctuation_model`（缺省 `models/voice/punct/model.onnx`，空串禁用标点恢复——ct-transformer zh-en int8 ~65MB）、`clustering_threshold`（缺省 `0.5`，聚类距离阈值）、`min_duration_on/off`（0.3/0.5，秒）、`keep_audio`（false）、`max_duration_secs`（7200）、`audio_dir`（`meetings`）、`num_threads`（1）。端点：`POST /api/meetings/start` → `201 {"id", "started_at_ms"}`（已在录 409；能力不可用 501）；`POST /api/meetings/{id}/stop` → `{"id","status":"processing"}`（**处理异步**——立即返回，完成经 SSE 通知；id 非当前会话 409）；`GET /api/meetings` → `{"meetings":[{id, started_at_ms, ended_at_ms, duration_ms, status, num_speakers, num_segments}]}`（倒序，status ∈ `recording|processing|done|failed`）；`GET /api/meetings/{id}` → 详情 + `"segments":[{start_ms, end_ms, speaker_index, speaker, text}]`（按 start_ms 升序；`speaker` 为声纹档案命中名，未命中空串——前端以 `speaker_index` 渲染"说话人 N"）；`DELETE /api/meetings/{id}` 删除记录+分段（及保留的音频文件；不存在 404）。**处理管线**（stop 后 spawn_blocking）：读 WAV → 分离（段级聚类）→ 相邻同说话人段合并（间隔 ≤ `min_duration_off`）→ 逐段 paraformer 转写 →（启用时）ct-transformer 标点恢复 → 每说话人取其各段嵌入对声纹档案**投票**打名（命中多数才用档名）→ 入库。新 SSE 事件 `meeting_state`：`{camera_id, meeting_id, status, timestamp_ms}`（`recording` 在 start 发、`processing` 在 stop 发、`done`/`failed` 在管线结束发）。存储：SQLite `meeting_records` + `meeting_segments` 两表。`capabilities.meeting` 布尔通告（= 引擎 active：`enabled` ∧ 分离模型文件在场 ∧ `[voice]` ASR 模型在场）。**诚实边界**：声学聚类对同嗓音家人可能合并为一个说话人；转写质量同 `[voice]` ASR（无说话人语言模型约束）；长会话处理时长 ≈ RTF 0.15×时长+分离开销。会话认证 + CSRF 同 §2。

28. **ONVIF 能力面扩展键（三端，2026-09-29 起）**：onvif 节新增方言键（均为加法、默认值保证存量行为不变；`config_apply` 语义随 onvif 节现状=重启生效）。`onvif.media2_enabled`（bool，缺省 `true`；rs TOML `[onvif]` / go YAML / notebook SQLite `protocols.onvif` 同名）——启用 ver20 Media2 服务面（`/onvif/media2_service`，Profile T 客户端首选路径；GetServices 加法广告 ver20/media 条目）；`false` 时端点 404、GetServices 无该条目（字节与旧版一致）。`onvif.deviceio_enabled`（bool，缺省 `true`；**仅 go**）——DeviceIO 动作族（无音频/继电器/数字输入硬件→诚实空集应答）。`onvif.http_digest`（bool，缺省 `false`；**仅 rs/notebook**）——启用 HTTP Digest 传输认证（RFC 7616 MD5）：无凭证请求收 401+`WWW-Authenticate: Digest` 挑战，合法摘要头通过认证；与 WS-Security UsernameToken 并存（带令牌时以令牌为准）。`onvif.ip_filter`（字符串数组，缺省空=不启用；**仅 rs/notebook**，rs 为 TOML 数组、notebook 为 SQLite 数组）——IPv4/CIDR 允许列表（Allow 模式），列表外来源在任何 HTTP/SOAP 处理前 403；坏条目 WARN 跳过、全坏 fail-open。

29. **notebook 接地对话（视觉上下文注入 + 看图直答，2026-10-01 起）**：`POST /api/chat`（#22）请求新增可选 `vision`（bool，缺省 `false`）；响应新增 `grounded` 字段（`"vlm" | "scene" | "none"`）。**语义**：① **场景接地（自动、fail-open、零新增延迟）**——llm 引擎活跃时，每轮对话（HTTP 与语音自动应答同源）在历史前注入一条系统回合：人设 + **语言跟随指令**（"用用户所用的语言——普通话/粤语/英语——回复"）+ 【画面】上下文块（实时 AI 检测标签计数如 `2×person, 1×chair`（秒级新鲜，复用既有推理流）+ 最近一次 VLM 告警画面描述（若有，注明滞后）；两者皆无则省略画面块）。`grounded:"scene"` = 回复带画面上下文；`"none"` = 无视觉信息可用（注入仍发生，仅画面块缺省）。**不为此新跑任何推理**。② **看图直答（显式、慢）**——`vision:true` 且 `vlm` 能力在位：取相机当前帧（与快照同源），用户原话连同帧交给 VLM 直接作答（`grounded:"vlm"`）；CPU 设备单次推理可达数十秒，前端须明示慢速预期。VLM 不在/取帧失败/推理失败 → fail-open 回落 ①（grounded 如实标注实际路径）。③ SSE `chat_reply`（语音自动应答）同步新增 `grounded` 字段（恒 ① 路径，值 `"scene"|"none"`）。无新配置键、无新端点。会话认证 + CSRF 同 §2。


30. **notebook 对话场景能力包（连续对话/任务注入/关联记录/三语 TTS/资源分层，2026-10-01 起）**：在 #29 场景接地之上扩展五组能力，全部加法、fail-open。**A. 系统回合注入块**——`POST /api/chat` 与语音自动应答的系统回合在【画面】外可再携带两个块：`【本机】`（恒注入：本地时间〔含星期与时区〕、开机时长、系统负载、可用内存、相机数与状态概要——"问时间"类问题据此作答，不再凭模型幻觉）；`【联网】`（意图门控 + 配置开关：用户话语含天气/weather 等意图且 `[tools] weather_enabled=true` 时，经 wttr.in 拉取 `weather_city` 当前天气注入；拉取失败/超时/未启用 → 无该块，模型如实说不知道）。新配置 `[tools]`：`weather_enabled`（缺省 `false`）、`weather_city`（缺省空）、`timeout_secs`（5）。**B. 连续对话**——新配置 `[voice] follow_up_window_secs`（缺省 0=关；>0 时每次语音应答结束后开启等长跟问窗口，窗口内**无需唤醒词**，silero VAD 端点检测整句，静音即转写应答；窗口过期回到唤醒词模式）与 `vad_model`（缺省 `models/voice/vad/silero_vad.onnx`，缺文件时跟问自动禁用并 WARN）；语音应答带**会话历史**（最近 120 秒内的问答对作为上下文传入）。`voice_transcript` SSE 事件加法新增 `follow_up: bool`（true=跟问窗口内捕获）与 `scene` 字段（见 C）。**C. 听见×看见关联记录**——`hearing_records` 表加列 `scene`（TEXT NOT NULL DEFAULT ''，语音交互发生瞬间的【画面】摘要，sound 告警同）与 `media_ref`（TEXT NOT NULL DEFAULT ''，录像维度：该相机本地录像开启时，填写**覆盖该事件时间戳的当前 MP4 分段文件路径**；录像未开/分段毫秒级轮转竞态/流已停 → 空串）；`GET /api/audio/records` 条目加法返回 `scene` 与 `media_ref`。**D. 三语 TTS**——`[tts]` 新增可选 `yue_model`/`yue_lexicon`/`yue_dict_dir` 与 `en_model`/`en_lexicon`（键缺省空=仅主模型）；`speak` 按回复文本语言选择模型（粤语特征字→yue、纯 ASCII→en、否则主模型），无对应模型回落主模型（如实用普通话声读粤语字）。**E. 资源分层**——新配置 `[resources] auto_tier`（缺省 `false`）与 `[llm] model_path_mid`/`model_path_lite`（缺省空=回落 `model_path`）；auto_tier 开启时按启动时可用内存选档：≥10GiB→`model_path`（full）、≥4GiB→mid、否则 lite；`capabilities` 加法新增 `llm_tier: "full"|"mid"|"lite"|"manual"`。会话认证 + CSRF 同 §2。
31. **notebook 场景能力 Web 可配置（`scene` 配置节，2026-10-02 起）**：`GET/PUT /api/config`（§5）文档加法新增顶层节 `scene`，承载 #30 场景能力中**可在线调整**的键，全部**热生效**（`config_apply` 对该节为 `immediate`，无需重启）：
    ```json
    "scene": {
      "voice": { "follow_up_window_secs": 12.0 },
      "tools": { "weather_enabled": true, "weather_city": "Guangzhou", "weather_timeout_secs": 5 }
    }
    ```
    - `scene.voice.follow_up_window_secs`（number ≥0，0=关闭跟问窗口）：写后**下一次语音应答结束**即按新窗口时长开启；运行时改 0 可即时关闭跟问（改回 >0 恢复——VAD 引擎按启动配置构建，启动时窗口为 0 的进程需重启才能获得 VAD）。
    - `scene.tools.weather_enabled` / `weather_city` / `weather_timeout_secs`：写后下一轮对话即按新值门控【联网】注入。
    - **持久化**：`scene.*` 键以点键形式存入设备配置库（与 `settings` 同库、`scene.` 前缀），**启动时叠加覆盖**同名 TOML 值（TOML 为引导缺省，Web 修改优先生效）；`GET /api/config` 的 `settings` 节**不重复返回** `scene.*` 键。
    - PUT 语义同 §5 部分合并：只提交出现的键；类型/范围校验失败整体 400 且不落盘。`GET` 返回的值取自**运行时共享句柄**（反映热修改后的现值）。
    - **仍为文件配置（不进 `scene`）的重启类键**：`[voice] keywords_threshold`/`speaker_verify`、`[tts]`/`[llm]`/`[vlm]` 模型路径、`[resources] auto_tier` 与 llm 分层档位——属部署期决策，改动需改 TOML 并重启（本条不加线上编辑入口）。
    - 其余端方言不受影响（该节仅 notebook 通告）。

32. **notebook 唤醒词 Web 可配置（`scene.voice.wake_word`，2026-10-02 起）**：#31 的 scene 节新增 `voice.wake_word`（string，**2-6 个汉字、普通话发音**——KWS 为普通话音节模型，粤语/英语发音不可用）。**生效语义为 `restart`**：PUT 校验（不可发音/越界 → 整体 400）、持久化进 settings 袋，保存响应 `applied:"restart"`；设备自此通告 `capabilities.restart=true` 与 `config_apply.auto=true`（见 §5.1/方言 #10 修订），前端走统一自动重启等待流程。启动时非默认值在数据库同目录生成 keywords 覆写文件（`kws-keywords.txt`，声母/带调韵母 token 拼写由设备从汉字自动转换），KWS 指向该文件；默认词继续用随模型分发的 keywords 文件。助手人设的名字随配置的唤醒词（问"你叫什么"答配置名）。
33. **notebook 人脸识别（注册/画面人员接地，2026-10-02 起）**：`[face]` 节新增可选人脸识别（off 缺省；模型 = OpenCV zoo **YuNet** 检测 + **SFace** 128 维嵌入，均 Apache-2.0，文件缺失只禁用特性）。新配置键：`enabled`（false）、`detect_model`（`models/face/face_detection_yunet_2023mar.onnx`）、`recog_model`（`models/face/face_recognition_sface_2021dec.onnx`）、`detect_input`（320，检测输入边长，越大找得到越小的人脸、CPU 二次方增长）、`match_threshold`（0.363，余弦阈值=SFace 参考值）、`enroll_frames`（8，注册采帧数）、`ttl_ms`（10000）。**语义**：① 档案存 SQLite `faces` 表（`{id, name UNIQUE, dim, embedding BLOB(LE f32), created_at}`）；② 注册 = 轮询式流程，与 #25 声纹同构——`POST /api/faces`（体 `{name}`，1..=32 字节，已注册名 400）武装引擎，**受检相机前出现人脸的每一帧自动累积嵌入样本**（无需用户操作），`GET /api/faces`（无副作用）返回 `{faces:[…], enrollment:{name,collected,needed}|null, capable:bool}`，`POST /api/faces/commit`（集满后）把均值归一化模板入库 → `{enrolled, dim}`，未集满 400；`POST /api/faces/cancel` 放弃；`DELETE /api/faces/{name}` 删档案（不存在 404）；③ **画面人员接地**——AI 检测环同帧跑人脸匹配，命中的姓名与未识别计数写入对话接地（【画面】块新增 `画面人员：张三×1、未识别×1` 行，10s TTL）——问「你看到谁」由 LLM 据此作答；④ `capabilities.face` 布尔通告（= 引擎 active）。**诚实边界**：正面清晰人脸的便利性识别，非安防级认证（照片可能通过）；侧面/暗光弱。前端 records 页「已注册人脸」卡（能力门控）。
34. **notebook 全量模型管理（§4.9 能力 `model_manager`，2026-10-03 起）**：全部 AI 能力（llm / vlm / 语音识别 / 三个 TTS 语音 / 人脸检测与识别 / 目标检测 / OCR / 意图分类 / 声纹）的候选模型目录 + 一键下载 + 切换。**存储布局**：模型统一落 `[models] dir`（缺省 `models`，相对工作目录）下由目录定义的子路径；`installed` 判定 = 目录声明的全部文件按 size 就位。**启用语义**：`apply:"restart"` 能力（除目标检测外全部）把选择持久化为 settings 袋的 `model.<capability>` 行（与 `scene.*` 同法：**不出现在 `/api/config` 与 `/api/settings` 响应里**），启动时 overlay 校验目录 id 后落到引擎配置键（如 `llm.model_path`、`voice.paraformer_model`、`tts.yue_model`、`face.detect_model`、`ocr.*_path`、`decision.*_path`），未知 id 告警跳过不阻断启动；`apply:"immediate"` 的 `ai` 能力把下载产物注册进 §4.6 的注册表（`source:"downloaded"`）后走既有 activate 热切换。**目录来源诚实性**：仅收录设备引擎实际支持的模型族（GGUF 用单文件量化档；sherpa 家族用 vits/paraformer 对应型号），无下载源的条目 `downloadable:false`。**激活后自动重启**：沿用 #32 的 `config_apply.auto=true` 统一自动重启等待流程。
36. **notebook 语音波形（§6 `audio_level`，2026-10-03 起；同日修订电平映射）**：对话面板实时麦克风波形条。设备在 16 kHz 监听广播上挂一个电平抽头（`streaming::audio_level::LevelMeter`：100 ms 窗 RMS → **感知映射 −45…−5 dBFS 线性压到 0…1**——线性 RMS 下真实对话只有 0.03–0.1，波形条不可读，这是 2026-10-03 当日修订的原因；attack 0.6/release 0.3 平滑，门限以下恒 0，首个满窗即发不等节流），经 SSE `audio_level` 以 ≤10 Hz 推送 `{"level","timestamp"}`；音频监听随 voice/audio_ai/meeting 任一活跃而运行，`capabilities.events` 相应通告。前端（`waveform.js`）canvas 滚动条形图，10 Hz 输入用逐帧缓动补齐显示帧率，静音时收敛到细基线，说话态（level≥0.3，滞回 900 ms）点亮麦标与条带边缘并绘制峰值帽；数据停滞 6 s 视为过期不再动画。**隐私注记**：这是设备本来就在采集的监听流的幅度摘要——不新增任何音频采样，事件只含标量音量，永不含音频数据。
35. **notebook 在线 AI（§4.10 能力 `cloud_ai`，2026-10-03 起）**：OpenRouter 接入。**存储**：云端配置存独立 SQLite 表（`cloud_config` 单行），**不进 settings 袋**——`GET /api/settings` 与 `/api/config` 均不可见，密钥仅 `api_key_set` 布尔可见。**路由**：provider 有效且密钥已设时，HTTP `POST /api/chat` 与语音自动应答同权走 OpenRouter（文本 `chat_model`；`vision:true` 用 `vision_model` + 最新帧 data URL）；失败回落本地（`fallback_local` 缺省 true）；`engine` 字段披露实际路径。**人设一致性**：云端请求沿用本地同款 system turn（人设/语言跟随/【画面】【本机】【联网】接地块），即接地能力不因上云丢失。`POST /api/cloud/test` 用 5s 级短超时最小补全。密钥长度上限 256 字节；`suggest` 为静态建议表，前端允许自由输入任意模型 id。
37. **OTLP 追踪导出（三端，2026-10-04 起）**：设备内部调用链以 OpenTelemetry trace 经 OTLP gRPC（4317 惯例端口）导出到外部可观测工具（Jaeger/Tempo/SigNoz 等），`service.name` 恒 `mibee-eye`。配置方言：notebook 为 `[observability] otel_endpoint`（既有键）；rs 为 TOML `[observability] otlp_endpoint`（新，缺省空=关）；go 为 YAML `observability.otlp_endpoint`（新，缺省空=关）。开启后：Web API 请求建立根 span（提取 W3C `traceparent` 为父），内部调用链（采集→编码→分发、GB28181 注册/媒体会话、录像分段、AI 推理、对话模型链）为子 span；endpoint 不可达仅告警（fail-open，绝不影响服务）。日志仍走本地环 + Loki（notebook 方言）。
38. **`/metrics` 资源与协议指标补齐（三端，2026-10-04 起）**：rs 既有 `mibee_system_*` / `mibee_process_*` 资源 gauge 族；go 与 notebook 补齐同域资源 gauge（进程 CPU%、RSS、FD 数、系统 CPU/内存/网络计数——指标名各设备自定义，附录 A5 惯例）。go 另接通 GB28181 库 metrics 接缝：注册尝试/成功/失败、心跳失败、INVITE 会话、PS 流出字节计数。存量死计数器（go 的 ONVIF 请求计数、rs 的 `AppMetrics` 族）接通真实埋点。
39. **notebook 每模型资源指标 + 对话调用链（§3.3 实现，2026-10-04 起）**：① `/metrics` 新增 `mibee_model_*` 族：`mibee_model_inferences_total{model,variant}`、`mibee_model_inference_seconds`（直方图，秒）、`mibee_model_cpu_seconds`（直方图，单次调用进程 CPU 增量）、`mibee_model_inflight{model}`、`mibee_model_errors_total{model,variant}`、`mibee_model_tokens_total{model,variant,kind=prompt|completion}`（token 计费模型才有）。`model` = 模型目录能力 id（§4.9 目录 / §4.6 注册表 id，如 `llm`/`vlm`/`ai`/`voice.asr`/`tts.zh`/`face.recog`/`ocr`/`decision`/`speaker`/`audio_ai`/`meeting` + 云端 `cloud.chat`/`cloud.vision`）；`variant` = 具体模型（文件名词干或云端模型 id，用户自定义云端模型 id 会进入标签——基数由部署者自律）。② §3.3 对话追踪环：容量 200 对话 × 每对话 64 span；语音对话沿用既有 120 s 会话槽判同一 `conversation_id`。③ 同一棵 span 树经 OTLP 导出（#37），对话根 span 名 `conversation`，模型 span 名 `model_call` 并带 `model`/`conversation.id` 属性。
40. **notebook 启动期功能资源门控（2026-10-04 起）**：小内存主机不整启全部 AI 功能——按启动时可用内存做预算制准入。配置节 `[resources]`（既有 `auto_tier` 为 LLM 模型选档，见 #30-E）新键：`feature_gate`（`"auto"` 缺省 = 启用门控；`"all"` = 显式全启，即旧行为）与 `reserve_mib`（缺省 512；预算 MiB = 启动时 MemAvailable − reserve——MemAvailable 本身已含可回收页缓存，reserve 只留 OS 抖动余量）。**决策算法（纯启动期）**：按固定优先级 `ai → audio_ai → voice → llm → tts → decision → face → ocr → meeting → vlm` 贪心准入；每功能成本 = 已配置模型文件的**实际字节数**之和 × 1.15（ORT 会话全驻留 / llama.cpp mmap 的近似）+ 每引擎固定开销（40–80 MiB）；累加超出预算即不准入该功能（其后更便宜的功能仍可尝试准入）；`meeting` 与 `decision` 依赖 `voice` 的模型（voice 不准入则二者一并不准入）。LLM 先按 #30-E 解析档位模型，成本按解析后的实际文件计。未获准入的功能按"配置关闭"构造引擎（fail-open 报 inactive），准入原因进 capabilities。模型文件缺失按成本 0 计（引擎自身的 fail-open 原因照常浮现）。**暴露**：`capabilities.resource` 对象 `{mode, available_mib, total_mib, budget_mib, reserve_mib, features:[{name, cost_mib, admitted, reason}]}`（§3.1 加法键；`reason` 为机器码——`""` 准入 / `"off_config"` 配置未启用 / `"off_budget"` 预算不足 / `"dependency"` 依赖功能未准入，前端据此本地化，未知码原样显示）；`/metrics` 新增 gauge `mibee_eye_resource_budget_mib` 与 `mibee_eye_feature_admitted{name}`（1=准入/0=未准予或未配置）——实时可用内存沿用 #38 既有资源 gauge。运行期不做驱逐：门控是启动期决策，重启按届时水位重算；改配置 `[resources]` 需重启生效（`config_apply` restart 节）。
41. **notebook 对话交互记录（§3.4 实现，2026-10-06 起）**：`GET /api/conversations?limit=`（缺省 50 上限 200，倒序）+ SSE `conversation` 事件 + SQLite `conversation_turns` 表（FIFO 封顶 1000 行，插入时修剪，`hearing_records` 同法）。采集点：**语音轮**（120 s 会话槽，`conversation_id` 复用 §3.3 voice trace id）——ASR 转写为 `user_text`，意图决策（`decision`）、云端应答/失败回落（`cloud.chat`）、本地 LLM（`llm`，带 prompt/completion token 数）、TTS 播报（`tts.*`）各产一条 thinking 条目，决策判 `ignore` 的轮次照常落库（`reply_text:null`）；**HTTP 轮**——`POST /api/chat` 每请求一轮，云端看图/云端文本/本地 VLM/本地 LLM 各腿（含失败回落腿）均记 thinking，`conversation_id` = 该请求的 §3.3 chat trace id。`note` 截断 200 字符。配置 `[conversations] enabled`（缺省 `true`，隐私总开关——`false` 时不记录、不通告该能力）。写入失败仅告警（fail-open）。`capabilities.conversations` 通告（= `enabled`）。
42. **notebook 桌面集成（托盘图标 + 桌面通知，2026-10-06 起）**：部署在有桌面会话的 Linux 上时的本机存在感，全部 fail-open。配置 `[desktop]`：`tray`（缺省 `true`）、`notifications`（缺省 `true`，告警桌面通知）、`notify_conversations`（缺省 `false`，语音回复完成时也发桌面通知，正文 = 回复截断）。**会话探测**：`DBUS_SESSION_BUS_ADDRESS` 已设或 `$XDG_RUNTIME_DIR/bus` 存在视为有桌面会话；无 → 托盘与通知整体静默跳过（INFO 一次，headless 服务器行为零变化）。**托盘**（StatusNotifierItem/kstatusnotifieritem，ksni 实现）：图标 + 状态标题，菜单「打开 Web 界面」以 `xdg-open` 打开本机管理 URL（`[web] http_port` 非零时 `http://127.0.0.1:{http_port}`，否则 `https://127.0.0.1:{port}`）；无托盘宿主（如无扩展的 GNOME）时安静等待，不报错。**通知**（org.freedesktop.Notifications）：视觉/声音/区域三类告警上升沿各自发一条（复用 §6 `alarm` 的三处扇出点，视觉 `检测到 N 个目标`、声音 `听到：{类别}`、区域 `区域事件：{zone}`）；发送失败（通知守护尚未就绪——服务可能先于用户登录启动）暂停该通道并于 10 分钟后自动重试，托盘注册失败亦每 60 秒重试直至成功——用户登录、桌面会话就位后二者自动生效，期间不刷日志。无新 API 面、无新 SSE 事件。

## 8. 附录 B：本规范取代的旧端点（迁移对照）

| 旧端点（项目） | 新端点 |
|----------------|--------|
| `GET /health`（go/notebook） | `GET /api/health` |
| `GET /api/version`（go） | 并入 `GET /api/status` 的 `firmware` |
| `POST /api/login` + token（go） | `POST /api/auth/login` + cookie |
| `X-Password` 写门（rs） | cookie 会话 + CSRF |
| `GET /api/settings`、`/api/protocols/{x}` GET/PUT（notebook） | `GET/PUT /api/config`（`protocols` 节） |
| `POST /api/config/onvif`、`/api/config/gb28181`（go） | `PUT /api/config` 部分合并 |
| `GET /api/camera/params` 等（go） | `GET/POST /api/cameras/{id}/imaging/*` |
| `GET /api/stream/ws`（go）、`GET /ws/video`（rs） | `GET /api/cameras/{id}/stream.mse` |
| `GET /ws`（go/rs 控制通道） | `GET /api/events`（SSE） |
| `GET /api/stream`（rs MJPEG）、`/api/cameras/{id}/live`（notebook） | `GET /api/cameras/{id}/live` |
| `GET /api/capture`、`/snapshot.jpg`（rs） | `GET /api/cameras/{id}/snapshot` |
| `GET/POST /api/record`（rs） | `GET/POST /api/cameras/{id}/recording` |
